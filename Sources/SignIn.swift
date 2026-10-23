// The sign-in window, the one place a person sees Umbra. It lists the sites from the sites file and
// whether each is signed in, and never loads a site that is: a site's page appears only to sign
// in, and is gone the moment the sign-in lands, so the window can't be used to browse. The daemon
// shows it, so agents keep working while a person signs in.
import AppKit
import WebKit

enum Signed { case checking, yes, no, unknown }

extension Site {
    /// The site itself: the login page's origin when that is on the site's domain (keeping its
    /// scheme and port), else https on the domain. Probes and visits go there.
    var base: URL? {
        guard let login = URL(string: login) else { return nil }
        return owns(login) ? login : URL(string: "https://" + domain)
    }

    var probeURL: URL? {
        guard let probe, let base else { return nil }
        return URL(string: probe, relativeTo: base)?.absoluteURL
    }

    var checkable: Bool { cookie != nil || probe != nil || probeScript != nil }

    func owns(_ url: URL?) -> Bool {
        url?.host.map { $0 == domain || $0.hasSuffix("." + domain) } ?? false
    }
}

/// Sites by name from the sites file, read again (it may have changed since the daemon started),
/// or ad hoc sites from login URLs; every site in the file when none are named. The file is the
/// caller's: a client sends its own, since the daemon may have started with another.
func resolveSites(_ names: [String], file: URL = sitesFile) -> Result<[Site], UmbraError> {
    let known: [Site]
    do { known = try readSites(file) } catch { return .failure(UmbraError(78, "could not read \(file.path): \(error)")) }
    if names.isEmpty { return .success(known) }  // none yet: the window opens empty, with Add Account
    var chosen: [Site] = []
    for name in names {
        if let url = URL(string: name), ["http", "https"].contains(url.scheme ?? ""), let host = url.host {
            chosen.append(Site(name: host, domain: host, login: name))
        } else if let site = known.first(where: { $0.name.caseInsensitiveCompare(name) == .orderedSame }) {
            chosen.append(site)
        } else {
            return .failure(UmbraError(64, "no site named \(name) in \(file.path); known: \(known.map(\.name).joined(separator: ", "))"))
        }
    }
    return .success(chosen)
}

/// A site for a pasted login link: its host without "www." as the domain, and unless one is given,
/// a name from the host ("github.com" → "Github", "app.slack.com" → "Slack").
func account(link: String, name: String?, file: URL = sitesFile) -> Result<Site, UmbraError> {
    guard let url = URL(string: link.trimmingCharacters(in: .whitespacesAndNewlines)),
          ["http", "https"].contains(url.scheme?.lowercased() ?? ""), let host = url.host?.lowercased(), !host.isEmpty else {
        return .failure(UmbraError(64, "\(link) is not a web link: paste the site's login page, such as https://github.com/login"))
    }
    let domain = host.hasPrefix("www.") ? String(host.dropFirst(4)) : host
    let labels = domain.split(separator: ".").map(String.init)
    let generic: Set = ["app", "login", "accounts", "account", "auth", "my", "signin", "sso", "id", "secure", "portal", "web", "m"]
    let label = labels.count > 2 ? labels.first { !generic.contains($0) } ?? labels[0] : labels[0]
    let chosen = name?.trimmingCharacters(in: .whitespaces).isEmpty == false ? name!.trimmingCharacters(in: .whitespaces)
        : label.prefix(1).uppercased() + label.dropFirst()
    let known = (try? readSites(file)) ?? []
    if let same = known.first(where: { $0.name.caseInsensitiveCompare(chosen) == .orderedSame || $0.domain == domain }) {
        return .failure(UmbraError(64, "\(same.name) (\(same.domain)) is already one of your sites"))
    }
    return .success(Site(name: chosen, domain: domain, login: url.absoluteString))
}

/// Adds a site to the sites file, or updates the entry with its name, leaving every other entry as
/// it was. One site per line, name first, as the file is written by hand.
func saveSite(_ site: Site, to file: URL = sitesFile) throws {
    var list: [[String: Any]] = []
    if let data = try? Data(contentsOf: file), !data.allSatisfy({ [9, 10, 13, 32].contains($0) }) {
        guard let parsed = try JSONSerialization.jsonObject(with: data) as? [[String: Any]] else {
            throw UmbraError(78, "\(file.path) is not a list of sites")
        }
        list = parsed
    }
    var entry: [String: Any] = ["name": site.name, "domain": site.domain, "login": site.login]
    entry["cookie"] = site.cookie
    entry["probe"] = site.probe
    entry["probeScript"] = site.probeScript
    entry["keepSession"] = site.keepSession
    entry["keepSignedIn"] = site.keepSignedIn == true ? true : nil
    if let index = list.firstIndex(where: { ($0["name"] as? String)?.caseInsensitiveCompare(site.name) == .orderedSame }) {
        list[index].merge(entry) { $1 }
        if site.keepSignedIn == false { list[index]["keepSignedIn"] = nil }  // turned off: the key goes
    } else {
        list.append(entry)
    }
    let order = ["name", "domain", "login", "cookie", "probe", "probeScript", "keepSession", "keepSignedIn"]
    let lines = try list.map { item -> String in
        let keys = order.filter { item[$0] != nil } + item.keys.filter { !order.contains($0) }.sorted()
        let fields = try keys.map { key -> String in
            let value = try JSONSerialization.data(withJSONObject: item[key]!, options: [.fragmentsAllowed, .withoutEscapingSlashes])
            return "\"\(key)\": " + String(decoding: value, as: UTF8.self)
        }
        return "  {" + fields.joined(separator: ", ") + "}"
    }
    try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
    try Data(("[\n" + lines.joined(separator: ",\n") + "\n]\n").utf8).write(to: file, options: .atomic)
}

/// The cookie a first sign-in set, to recognise the account by from then on: one that appeared on
/// the site's domain after its login page loaded, preferring session-like names, or else the only
/// new HttpOnly one.
func learnedCookie(_ cookies: [HTTPCookie], _ site: Site, since baseline: Set<String>) -> String? {
    let ignored = "csrf|xsrf|^_ga|^_gid|consent|^__cf|^cf_|^_fb|lang|locale|theme|timezone|^tz"
    let fresh = cookies.filter {
        $0.within(site.domain) && !baseline.contains($0.name) && !$0.value.isEmpty
            && $0.name.range(of: ignored, options: [.regularExpression, .caseInsensitive]) == nil
    }
    for hint in ["sess", "auth", "token", "sid", "login", "user", "jwt"] {
        if let found = fresh.first(where: { $0.name.lowercased().contains(hint) }) { return found.name }
    }
    let httpOnly = fresh.filter(\.isHTTPOnly)
    return httpOnly.count == 1 ? httpOnly[0].name : nil
}

/// Whether the store is signed in to a site, found without showing or rendering anything: its
/// cookie, and a top-level request for its probe, cancelled as soon as the status is known. With
/// both, the cookie answers "signed out" cheaply and the probe catches a session the site ended
/// while its cookie stayed.
final class Check: NSObject, WKNavigationDelegate {
    private static var running: Set<Check> = []
    /// Why the latest check of each site couldn't tell ("HTTP 503"), for the window and the log.
    static var why: [String: String] = [:]
    private let web = WKWebView(frame: .zero, configuration: configuration())
    private let target: URL
    private let site: String?
    private var done: ((Signed) -> Void)?

    static func run(_ site: Site, done: @escaping (Signed) -> Void) {
        let done = { (result: Signed) in
            if result != .unknown { why[site.name] = nil }
            done(result)
        }
        if site.probeScript != nil {
            return Arrival.run(site) { result, reason in
                if result == .unknown { why[site.name] = reason }
                done(result)
            }
        }
        guard let url = site.probeURL else {
            guard let name = site.cookie else {
                return Arrival.run(site) { result, reason in
                    if result == .unknown { why[site.name] = reason }
                    done(result)
                }
            }
            return store.httpCookieStore.getAllCookies { cookies in
                done(cookies.contains { $0.name == name && $0.within(site.domain) } ? .yes : .no)
            }
        }
        guard let name = site.cookie else {
            running.insert(Check(url, site.name, done))
            return
        }
        store.httpCookieStore.getAllCookies { cookies in
            guard cookies.contains(where: { $0.name == name && $0.within(site.domain) }) else { return done(.no) }
            running.insert(Check(url, site.name, done))
        }
    }

    /// A request for the URL, cancelled once the site answers, whatever it answers.
    static func visit(_ url: URL, done: @escaping () -> Void) {
        running.insert(Check(url, nil) { _ in done() })
    }

    private init(_ url: URL, _ site: String?, _ done: @escaping (Signed) -> Void) {
        target = url
        self.site = site
        self.done = done
        super.init()
        web.navigationDelegate = self
        web.load(URLRequest(url: url))
        DispatchQueue.main.asyncAfter(deadline: .now() + 20) { [weak self] in self?.finish(.unknown, "no answer in 20 seconds") }
    }

    private func finish(_ result: Signed, _ reason: String? = nil) {
        guard let done else { return }
        self.done = nil
        if let site, result == .unknown { Check.why[site] = reason }
        web.stopLoading()
        // Released on the next turn: freeing a web view inside its own policy callback crashes WebKit.
        DispatchQueue.main.async { Check.running.remove(self) }
        done(result)
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        let status = (navigationResponse.response as? HTTPURLResponse)?.statusCode ?? 0
        decisionHandler(.cancel)
        // Redirected (to a login page, say) or refused means signed out, whatever that page answered;
        // any other answer (a 503, a 429, a bot check) can't tell.
        let redirected = navigationResponse.response.url?.path != target.path
        finish(redirected || [401, 403].contains(status) ? .no : (200..<300).contains(status) ? .yes : .unknown, "HTTP \(status)")
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        finish(.unknown, error.localizedDescription)
    }
}

/// How a site's page looks to someone who may already be signed in: a site takes a signed-in
/// person off its login page into itself, or offers them a way to sign out, and keeps anyone else
/// at a sign-in step.
enum Landing {
    private static let steps = #/log-?[io]n|sign-?in|sign_in|sso|saml|oauth|openid|auth|session|password|verif|mfa|2fa|two-?factor|otp|challenge|checkpoint|consent|captcha/#.ignoresCase()
    // Run every quarter second in the same page, so nothing it declares may outlive the call.
    private static let script = #"""
        (() => {
          const shown = e => e.getClientRects().length > 0;
          const path = u => { try { return new URL(u, location.href).pathname } catch { return '' } };
          const exit = /(^|[/_.-])(log|sign)[-_]?(out|off)\b/i;
          return [location.href, document.readyState == 'complete',
            [...document.querySelectorAll('input[type=password]')].some(shown),
            [...document.querySelectorAll('a[href], form[action]')].some(e => shown(e) && exit.test(path(e.getAttribute('href') ?? e.getAttribute('action'))))
              || [...document.querySelectorAll('a, button, input[type=submit], [role=button], [role=menuitem]')]
                   .some(e => shown(e) && /^(log|sign) ?-?(out|off)$/i.test((e.innerText || e.value || '').trim()))];
        })()
        """#

    /// .yes on one of the site's own pages that asks for no password and either offers to sign out
    /// (a site may keep someone signed in on its login page, as WRDS does) or has moved off the login
    /// page to one that isn't a sign-in step; .no on anything else; nil while the page is still loading.
    static func look(_ web: WKWebView, _ site: Site, done: @escaping (Signed?) -> Void) {
        web.evaluateJavaScript(script, in: nil, in: .defaultClient) { result in
            guard case .success(let value) = result, let parts = value as? [Any], parts.count == 4, parts[1] as? Bool == true,
                  let url = (parts[0] as? String).flatMap(URL.init(string:)) else { return done(nil) }
            let login = URL(string: site.login)?.path ?? ""
            let moved = url.path.trimmingCharacters(in: ["/"]) != login.trimmingCharacters(in: ["/"])
            let inside = parts[3] as? Bool == true || moved && url.path.firstMatch(of: steps) == nil
            done(parts[2] as? Bool != true && site.owns(url) && inside ? .yes : .no)
        }
    }
}

/// Whether a site with no cookie or probe is signed in, asked as a person would: its login page,
/// loaded where nobody sees it, watched until it lets the person in (a second) or stays at a
/// sign-in step (two and a half).
final class Arrival: NSObject, WKNavigationDelegate {
    private static var running: Set<Arrival> = []
    private let web = WKWebView(frame: NSRect(x: 0, y: 0, width: 1280, height: 900), configuration: configuration())
    private let site: Site
    private var done: ((Signed, String?) -> Void)?
    private var seen: (look: Signed, times: Int)?
    private var timer: Timer?
    private var probing = false
    private var loaded = false

    static func run(_ site: Site, done: @escaping (Signed, String?) -> Void) {
        guard let url = site.probeScript == nil ? URL(string: site.login) : site.probeURL ?? URL(string: site.login) else {
            return done(.unknown, "its login page isn't a link")
        }
        running.insert(Arrival(site, url, done))
    }

    private init(_ site: Site, _ url: URL, _ done: @escaping (Signed, String?) -> Void) {
        self.site = site
        self.done = done
        super.init()
        web.navigationDelegate = self
        web.load(URLRequest(url: url))
        timer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in self?.watch() }
        let timeout = site.probeScript == nil ? 15 : 30
        DispatchQueue.main.asyncAfter(deadline: .now() + .seconds(timeout)) { [weak self] in
            self?.finish(.unknown, "no answer in \(timeout) seconds")
        }
    }

    private func watch() {
        if let script = site.probeScript {
            guard loaded, !probing else { return }
            probing = true
            return web.evaluateJavaScript("document.readyState === 'complete'", in: nil, in: .defaultClient) { [weak self] result in
                guard let self, self.done != nil else { return }
                guard case .success(let ready) = result, ready as? Bool == true else {
                    self.probing = false
                    return
                }
                self.web.callAsyncJavaScript(script, arguments: [:], in: nil, in: .page) { [weak self] result in
                    guard let self, self.done != nil else { return }
                    guard case .success(let value) = result, let signedIn = value as? NSNumber,
                          CFGetTypeID(signedIn) == CFBooleanGetTypeID() else {
                        return self.finish(.unknown, "the signed-in script failed or didn't return a boolean")
                    }
                    self.finish(signedIn.boolValue ? .yes : .no)
                }
            }
        }
        Landing.look(web, site) { [weak self] look in
            guard let self else { return }
            guard let look else { return self.seen = nil }
            let times = self.seen?.look == look ? self.seen!.times + 1 : 1
            self.seen = (look, times)
            if times >= (look == .yes ? 4 : 10) { self.finish(look) }
        }
    }

    private func finish(_ result: Signed, _ reason: String? = nil) {
        guard let done else { return }
        self.done = nil
        timer?.invalidate()
        web.stopLoading()
        DispatchQueue.main.async { Arrival.running.remove(self) }  // not inside a WebKit callback
        done(result, reason)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        if (error as NSError).code != NSURLErrorCancelled { finish(.unknown, "the page failed to load") }
    }

    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        loaded = false
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        loaded = true
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        if (error as NSError).code != NSURLErrorCancelled { finish(.unknown, "the page failed to load") }
    }
}

final class SignInWindow: NSObject, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate, WKHTTPCookieStoreObserver {
    static var shown: SignInWindow?
    /// Whether a handoff window is up; set by the daemon, which owns the tabs.
    static var handingOff: () -> Bool = { false }

    private struct Waiter {
        let id = UUID()
        let names: [String]
        let reply: Reply
    }

    private let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 640, height: 400),
                                  styleMask: [.titled, .closable, .miniaturizable, .fullSizeContentView], backing: .buffered, defer: false)
    private var sites: [Site] = []
    private var states: [String: Signed] = [:]
    private var pending: [String] = []         // named sites still to sign in, in order
    private var waiters: [Waiter] = []
    private var pinned = false                 // opened by the person: stays until they close it
    private var page: WKWebView?
    private var current: Site?
    private var poll: Timer?                   // re-checks a cookie site while its login page is up
    private var file = sitesFile               // the sites file of the latest request: added accounts go there
    private var stale: String?                 // the cookie site's cookie value when its login page opened
    private var finished: Set<String> = []     // sites whose page ended with Done (or the window closing)
    private var navigated = false              // a page loaded after the baseline: a sign-in, not just the login page
    private var learning: Set<String> = []     // added accounts whose sign-in cookie isn't known yet
    private var arrived = 0                    // looks in a row at a page a site with no check moved on to
    private var verifying = false
    private var verifiedAt = Date.distantPast
    private var refreshed = Date.distantPast
    private var baseline: Set<String>?         // their domain's cookie names once the login page loaded
    private var rows: [String: (dot: NSImageView, spinner: NSProgressIndicator, label: NSTextField, column: NSView, button: NSButton)] = [:]

    // MARK: Requests

    /// With no names, shows every site for the person, who closes the window when done. With names,
    /// signs in to each one still signed out and answers once all are; the window closes then
    /// unless the person had it open. Signed-in sites are never shown, and alone open nothing.
    static func request(_ chosen: [Site], named: Bool, save: String? = nil, file: URL = sitesFile, timeout: Double,
                        onWaiter: ((UUID) -> Void)? = nil, reply: @escaping Reply) {
        var chosen = chosen
        if let save {  // a new account: remembered now, its cookie learned as the person signs in
            guard chosen.count == 1, !chosen[0].checkable else { return reply(.failure(UmbraError(64, "--save takes one login URL"))) }
            switch account(link: chosen[0].login, name: save, file: file) {
            case .failure(let error): return reply(.failure(error))
            case .success(let site):
                do { try saveSite(site, to: file) } catch { return reply(.failure(error as? UmbraError ?? UmbraError(1, "could not save \(file.path): \(error)"))) }
                chosen = [site]
                (shown ?? SignInWindow()).learning.insert(site.name)
            }
        }
        (shown ?? SignInWindow()).file = file  // the caller's: what it learns or keeps is saved there
        if !named {
            let sign = shown ?? SignInWindow()
            sign.add(chosen)
            sign.refresh()
            sign.pinned = true
            sign.present()
            return reply(.success(["text": "Umbra's sign-in window is open"]))
        }
        let group = DispatchGroup()
        var found: [String: Signed] = [:]
        for site in chosen {
            // An account being added opens its login page: its sign-in is what Umbra learns from.
            if shown?.learning.contains(site.name) == true {
                found[site.name] = .no
                continue
            }
            group.enter()
            Check.run(site) { found[site.name] = $0; group.leave() }
        }
        group.notify(queue: .main) {
            if chosen.allSatisfy({ found[$0.name] == .yes }) {
                shown?.update(found)
                return reply(.success(answer(chosen, found)))
            }
            // Can't tell (the site answered 503, or didn't answer): opening its page could show a site
            // that is signed in after all, so nothing opens.
            let unclear = chosen.filter { $0.checkable && found[$0.name] == .unknown }
            if !unclear.isEmpty {
                shown?.update(found)
                return reply(.failure(UmbraError(4, "couldn't tell whether \(unclear.map(\.name).joined(separator: ", ")) is signed in: "
                                                 + "the site didn't answer as expected, so nothing was opened. Try again shortly.")))
            }
            let sign = shown ?? SignInWindow()
            sign.add(chosen, known: found)
            sign.update(found)
            let waiter = Waiter(names: chosen.map(\.name), reply: reply)
            sign.waiters.append(waiter)
            sign.pending += chosen.map(\.name).filter { found[$0] != .yes && !sign.pending.contains($0) }
            onWaiter?(waiter.id)
            guard sign.waiters.contains(where: { $0.id == waiter.id }) else { return }
            DispatchQueue.main.asyncAfter(deadline: .now() + timeout) { [weak sign] in sign?.expire(waiter.id, after: timeout) }
            if sign.page == nil { sign.advance() }
            sign.present()
        }
    }

    private static func answer(_ chosen: [Site], _ states: [String: Signed]) -> [String: Any] {
        let list = chosen.map { site -> [String: Any] in ["site": site.name, "signedIn": states[site.name] == .yes] }
        let text = chosen.map { site in
            "\(site.name): " + (states[site.name] == .yes ? "signed in" : site.checkable ? "not signed in" : "done (no check for a URL)")
        }
        return ["sites": list, "text": text.joined(separator: "\n")]
    }

    private override init() {
        super.init()
        SignInWindow.shown = self
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.title = "Umbra"
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.isMovableByWindowBackground = true
        window.center()
        store.httpCookieStore.add(self)
    }

    private func add(_ chosen: [Site], known: [String: Signed] = [:]) {
        for site in chosen where !sites.contains(where: { $0.name == site.name }) {
            sites.append(site)
            states[site.name] = known[site.name] ?? .checking
            if known[site.name] == nil { check(site) }
        }
        if page == nil { showList() }
    }

    /// Checks every listed site again: a window left open, or opened again, shows what is true now.
    func refresh() {
        refreshed = Date()
        for site in sites where states[site.name] != .checking && site.name != current?.name { check(site) }
    }

    private func check(_ site: Site) {
        states[site.name] = .checking
        paint(site)
        Check.run(site) { self.update([site.name: $0]) }
    }

    func windowDidBecomeKey(_ notification: Notification) {
        if page == nil, Date().timeIntervalSince(refreshed) > 30 { refresh() }
    }

    func close() {
        window.close()
    }

    private func update(_ found: [String: Signed]) {
        for (name, state) in found where states[name] != nil { states[name] = state }
        for site in sites { paint(site) }
    }

    // MARK: Signing in

    private func advance() {
        pending.removeAll { states[$0] == .yes }
        if let name = pending.first, let site = sites.first(where: { $0.name == name }) { return begin(site) }
        if !pinned && waiters.isEmpty { return window.close() }
        showList()
    }

    private func begin(_ site: Site) {
        closePage()
        guard let url = URL(string: site.login) else { return }
        current = site
        let web = WKWebView(frame: .zero, configuration: configuration())
        web.navigationDelegate = self
        web.uiDelegate = self
        page = web
        baseline = nil
        navigated = false
        stale = nil
        finished.remove(site.name)
        verifying = false
        verifiedAt = .distantPast
        showPage(site, web)
        // A session cookie still here may belong to a session the site ended, or the check may just
        // have failed: it stays, and only a new value counts as a sign-in.
        if site.probeScript != nil {
            web.load(URLRequest(url: url))
            poll = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in self?.cookiesChanged() }
            return
        }
        guard let name = site.cookie else {
            web.load(URLRequest(url: url))
            // No cookie or probe: the page says when the site has let the person in. An account being
            // added waits for Done instead, which is when its cookie is learned.
            if !site.checkable, !learning.contains(site.name) {
                arrived = 0
                poll = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in self?.arriving(site, web) }
            }
            return
        }
        store.httpCookieStore.getAllCookies { cookies in
            guard web === self.page else { return }
            self.stale = cookies.first { $0.name == name && $0.within(site.domain) }?.value
            web.load(URLRequest(url: url))
        }
        // The store's change notices can come late or not at all (seen on macOS 15), and a sign-in
        // by script sets its cookie with no navigation to check: so look twice a second too.
        poll = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in self?.cookiesChanged() }
    }

    private func signedIn(_ site: Site) {
        guard current?.name == site.name else { return }
        states[site.name] = .yes
        closePage()
        paint(site)
        // Keep a session-only sign-in, and write it to disk now rather than at exit, before anyone hears of it.
        keepIfSessionOnly(site) {
            persist {
                self.answer(closing: false)
                self.advance()
            }
        }
    }

    /// A site whose sign-in cookie is session-only would be signed out when the daemon next exits:
    /// it gets keepSession, in the file and here.
    private func keepIfSessionOnly(_ site: Site, then done: @escaping () -> Void) {
        guard let name = site.cookie, site.keepSession != true, site.keepSignedIn != true,
              ((try? readSites(file)) ?? []).contains(where: { $0.name == site.name }) else { return done() }
        store.httpCookieStore.getAllCookies { cookies in
            if cookies.contains(where: { $0.name == name && $0.within(site.domain) && $0.isSessionOnly }),
               let index = self.sites.firstIndex(where: { $0.name == site.name }) {
                self.sites[index].keepSession = true
                var kept = self.sites[index]
                kept.keepSignedIn = nil
                try? saveSite(kept, to: self.file)
            }
            done()
        }
    }

    /// Answers each waiter whose sites are all signed in, or settled without a sign-in: closing the
    /// window settles every site; Done settles a URL or an added account.
    private func answer(closing: Bool) {
        let named = Dictionary(sites.map { ($0.name, $0) }, uniquingKeysWith: { first, _ in first })
        waiters.removeAll { waiter in
            let chosen = waiter.names.compactMap { named[$0] }
            // A URL has nothing to check: Done or closing ends it. An added account needs its cookie learned.
            let missing = chosen.filter { states[$0.name] != .yes
                && ($0.checkable || learning.contains($0.name) || !(closing || finished.contains($0.name))) }
            if missing.isEmpty {
                waiter.reply(.success(SignInWindow.answer(chosen, states)))
                return true
            }
            guard closing || missing.allSatisfy({ !$0.checkable && finished.contains($0.name) }) else { return false }
            let unlearned = missing.filter { learning.contains($0.name) }.map(\.name)
            waiter.reply(.failure(UmbraError(3, unlearned.isEmpty
                ? "not signed in to \(missing.map(\.name).joined(separator: ", ")): the window was closed first"
                : "no sign-in cookie appeared for \(unlearned.joined(separator: ", ")), so Umbra can't tell it's signed in; "
                  + "it stays in your sites, and `umbra sign-in \(unlearned[0])` tries again")))
            return true
        }
    }

    static func cancel(_ id: UUID) {
        guard let sign = shown, let index = sign.waiters.firstIndex(where: { $0.id == id }) else { return }
        sign.waiters.remove(at: index)
        sign.prunePending()
    }

    private func expire(_ id: UUID, after timeout: Double) {
        guard let index = waiters.firstIndex(where: { $0.id == id }) else { return }
        let waiter = waiters.remove(at: index)
        let missing = waiter.names.filter { states[$0] != .yes }
        waiter.reply(.failure(UmbraError(2, "no sign-in within \(duration(timeout)): still signed out of \(missing.joined(separator: ", "))")))
        prunePending()
    }

    private func prunePending() {
        pending.removeAll { name in !waiters.contains { $0.names.contains(name) } }  // nobody waits for them now
        guard !pinned else { return }  // the person opened the window: what they see is theirs
        if waiters.isEmpty { return window.close() }
        if let site = current, !pending.contains(site.name) {  // its page served the expired request: move on
            closePage()
            advance()
        }
    }

    private func closePage() {
        poll?.invalidate()
        poll = nil
        guard let page else { return }
        page.stopLoading()
        page.navigationDelegate = nil
        page.uiDelegate = nil
        page.removeFromSuperview()
        DispatchQueue.main.async { _ = page }  // freed after any WebKit callback under way returns
        self.page = nil
        current = nil
    }

    private func arriving(_ site: Site, _ web: WKWebView) {
        Landing.look(web, site) { look in
            guard web === self.page else { return }
            self.arrived = look == .yes ? self.arrived + 1 : 0
            if self.arrived == 4 { self.signedIn(site) }
        }
    }

    func cookiesDidChange(in cookieStore: WKHTTPCookieStore) {
        cookiesChanged()
    }

    private func cookiesChanged() {
        guard let site = current, let web = page, web.url != nil else { return }
        if site.probeScript != nil {
            guard site.owns(web.url), !verifying, Date().timeIntervalSince(verifiedAt) >= 5 else { return }
            verifying = true
            verifiedAt = Date()
            return Check.run(site) { result in
                guard web === self.page else { return }
                self.verifying = false
                if result == .yes { self.signedIn(site) }
            }
        }
        guard site.cookie != nil else { return }
        freshSignIn(site) { if $0, web === self.page { self.signedIn(site) } }
    }

    /// For a cookie site: its cookie is set, with a value it didn't have when the login page opened.
    private func freshSignIn(_ site: Site, done: @escaping (Bool) -> Void) {
        let before = stale
        store.httpCookieStore.getAllCookies { cookies in
            let value = cookies.first { $0.name == site.cookie && $0.within(site.domain) }?.value
            done(value != nil && value != before)
        }
    }

    /// Every page the sign-in lands on is checked before it is shown: once the site counts as signed
    /// in, the page is never loaded. A probe asks the site itself, so it waits until the flow is
    /// back on the site's domain (single sign-on pages often live elsewhere).
    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        guard navigationResponse.isForMainFrame, webView === page, let site = current, site.checkable,
              site.cookie != nil || site.owns(navigationResponse.response.url) else { return decisionHandler(.allow) }
        // A script needs the app to finish initializing; a changed cookie alone is not its session.
        if site.probeScript != nil { return decisionHandler(.allow) }
        let decide = { (signed: Bool) in
            guard webView === self.page, signed else { return decisionHandler(webView === self.page ? .allow : .cancel) }
            decisionHandler(.cancel)
            self.signedIn(site)
        }
        if site.cookie != nil { return freshSignIn(site, done: decide) }
        Check.run(site) { decide($0 == .yes) }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard webView === page, let site = current, learning.contains(site.name) else { return }
        if baseline != nil { return navigated = true }  // past the login page: a sign-in may have happened
        store.httpCookieStore.getAllCookies { cookies in
            if webView === self.page, self.baseline == nil { self.baseline = Set(cookies.filter { $0.within(site.domain) }.map(\.name)) }
        }
    }

    /// When an added account's sign-in ends with Done, keeps the cookie it set, so from now on Umbra
    /// can tell whether it is signed in. Closing the window instead learns nothing: a login page can
    /// set session-like cookies of its own, even across a redirect, while nobody signs in.
    private func learn(_ site: Site?, then done: @escaping () -> Void) {
        // Only a cookie that came with a page after the login page: a login page can set session-like
        // cookies of its own while nobody signs in.
        guard let site, learning.contains(site.name), let baseline, navigated else { return done() }
        store.httpCookieStore.getAllCookies { cookies in
            if let name = learnedCookie(cookies, site, since: baseline), let index = self.sites.firstIndex(where: { $0.name == site.name }) {
                self.sites[index].cookie = name
                // A session-only cookie dies with the daemon: keep it, or the sign-in is gone by tomorrow.
                if cookies.contains(where: { $0.name == name && $0.within(site.domain) && $0.isSessionOnly }) { self.sites[index].keepSession = true }
                self.learning.remove(site.name)
                self.states[site.name] = .yes
                var learned = self.sites[index]
                learned.keepSignedIn = nil  // the file's own, which the checkbox or command line may have set since
                try? saveSite(learned, to: self.file)
                self.paint(self.sites[index])
                persist {}
            }
            done()
        }
    }

    /// Pop-ups (some sign-ins open one) load in the same page.
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        webView.load(navigationAction.request)
        return nil
    }

    // MARK: Window

    func present() {
        if unseen {
            window.alphaValue = 0
            window.ignoresMouseEvents = true
            window.collectionBehavior = [.canJoinAllSpaces]
            return window.orderFrontRegardless()
        }
        NSApp.setActivationPolicy(.regular)  // a Dock icon and a menu bar while the window is up
        installMenus()
        window.makeKeyAndOrderFront(nil)
        window.orderFrontRegardless()
        NSApp.activate(ignoringOtherApps: true)
    }

    func windowWillClose(_ notification: Notification) {
        closePage()
        pending = []
        finished.formUnion(sites.map(\.name))
        SignInWindow.shown = nil
        answer(closing: true)  // closing isn't Done: an account being added learns nothing, and stays without a check
        store.httpCookieStore.remove(self)
        persist {}
        NSApp.setActivationPolicy(SignInWindow.handingOff() ? .accessory : .prohibited)  // as a handoff, or as before
    }

    @objc private func back(_ sender: Any?) {
        if let site = current, !site.checkable {  // a URL or an added account: Done ends it
            pending.removeAll { $0 == site.name }
            finished.insert(site.name)
            closePage()
            return learn(site) {
                let now = self.sites.first { $0.name == site.name } ?? site  // with the cookie just learned, if any
                self.states[site.name] = .checking
                self.paint(now)
                Check.run(now) { state in
                    self.update([site.name: state])
                    self.answer(closing: false)
                    self.advance()
                }
            }
        }
        if let site = current { pending.removeAll { $0 == site.name } }  // backed out of this one; others still wait
        closePage()
        showList()
    }

    /// Asks for a login link (and a name), lists the site, and opens its login page.
    @objc private func addAccount(_ sender: Any?) {
        let link = NSTextField(string: "")
        link.placeholderString = "https://github.com/login"
        let name = NSTextField(string: "")
        name.placeholderString = "Name (optional)"
        for field in [link, name] { field.widthAnchor.constraint(equalToConstant: 300).isActive = true }
        let fields = NSStackView(views: [link, name])
        fields.orientation = .vertical
        fields.spacing = 8
        fields.frame = NSRect(x: 0, y: 0, width: 300, height: 56)
        let alert = NSAlert()
        alert.messageText = "Add an account"
        alert.informativeText = "Paste the link to the site's login page. You sign in once; Umbra remembers it, and agents can use it."
        alert.accessoryView = fields
        alert.addButton(withTitle: "Add")
        alert.addButton(withTitle: "Cancel")
        alert.window.initialFirstResponder = link
        alert.beginSheetModal(for: window) { response in
            guard response == .alertFirstButtonReturn else { return }
            switch account(link: link.stringValue, name: name.stringValue, file: self.file) {
            case .failure(let error):
                let problem = NSAlert()
                problem.messageText = "Couldn't add that account"
                problem.informativeText = error.message
                problem.beginSheetModal(for: self.window)
            case .success(let site):
                do { try saveSite(site, to: self.file) } catch {
                    let problem = NSAlert(error: error)
                    return problem.beginSheetModal(for: self.window)
                }
                self.learning.insert(site.name)
                self.pinned = true
                self.add([site], known: [site.name: .unknown])
                self.begin(site)
            }
        }
    }

    @objc private func signIn(_ sender: NSButton) {
        guard let site = sites.first(where: { $0.name == sender.identifier?.rawValue }) else { return }
        pinned = true  // the person is using the window now: an agent's request ending won't close it
        guard !site.checkable else { return begin(site) }
        states[site.name] = .checking
        paint(site)
        Check.run(site) { state in
            self.update([site.name: state])
            if state != .yes { self.begin(site) }
        }
    }

    private func site(_ item: NSMenuItem) -> Site? {
        sites.first { $0.name == item.representedObject as? String }
    }

    @objc private func signOut(_ sender: NSMenuItem) {
        guard let site = site(sender) else { return }
        let alert = NSAlert()
        alert.messageText = "Sign out of \(site.name)?"
        alert.informativeText = "Agents and scripts that use \(site.name) will stop working until you sign in again."
        alert.addButton(withTitle: "Sign Out")
        alert.addButton(withTitle: "Cancel")
        alert.beginSheetModal(for: window) { response in
            guard response == .alertFirstButtonReturn else { return }
            removeCookies(of: site) { _ in
                self.states[site.name] = .no
                self.paint(site)
            }
        }
    }

    /// Saves the choice to the sites file, and installs or removes the launch agent that visits.
    @objc private func keepSignedIn(_ sender: NSMenuItem) {
        guard let site = site(sender) else { return }
        if case .failure(let error) = keep([site.name], sender.state != .on, file: file) {
            let problem = NSAlert()
            problem.messageText = "Couldn't change \(site.name)"
            problem.informativeText = error.message
            return problem.beginSheetModal(for: window)
        }
        if page == nil { showList() }
    }

    @objc private func checkAgain(_ sender: NSMenuItem) {
        if let site = site(sender) { check(site) }
    }

    // MARK: Layout

    private let width: CGFloat = 600
    private var titlebar: CGFloat { window.frame.height - window.contentLayoutRect.height }

    private func showList() {
        rows = [:]
        window.toolbar = nil
        window.styleMask.insert(.fullSizeContentView)
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.subtitle = ""
        let icon = Artwork(image: NSApp.applicationIconImage)
        icon.imageScaling = .scaleProportionallyUpOrDown
        let title = NSTextField(labelWithString: "Accounts")
        title.font = .systemFont(ofSize: 20, weight: .bold)
        let blurb = NSTextField(labelWithString: "Agents use these sign-ins in the background.")
        blurb.font = .systemFont(ofSize: 12)
        blurb.textColor = .secondaryLabelColor
        let heading = NSStackView(views: [title, blurb])
        heading.orientation = .vertical
        heading.alignment = .leading
        heading.spacing = 2
        let top = NSStackView(views: [icon, heading])
        top.alignment = .centerY
        top.spacing = 15  // 24 + 45 + 15: the title starts where the names do

        let list = NSStackView()
        list.orientation = .vertical
        list.spacing = 0
        // The file says which sites are kept (the command line may have changed it); a one-off URL can't be.
        let listed = Dictionary(((try? readSites(file)) ?? []).map { ($0.name, $0.keepSignedIn == true) }, uniquingKeysWith: { first, _ in first })
        for (index, site) in sites.enumerated() {
            if index > 0 { list.addArrangedSubview(hairline(inset: 60)) }
            list.addArrangedSubview(row(site, kept: listed[site.name]))
        }
        if sites.isEmpty {
            let empty = NSTextField(wrappingLabelWithString: "No sites yet. Click Add Account to sign in to one; Umbra keeps it in the file below.")
            empty.textColor = .secondaryLabelColor
            empty.alignment = .center
            empty.preferredMaxLayoutWidth = width - 48 - 48
            let holder = NSStackView(views: [empty])
            holder.edgeInsets = NSEdgeInsets(top: 28, left: 24, bottom: 28, right: 24)
            list.addArrangedSubview(holder)
        }
        for view in list.arrangedSubviews {
            view.widthAnchor.constraint(equalTo: list.widthAnchor).isActive = true
        }

        let add = NSButton(title: "Add Account…", image: NSImage(systemSymbolName: "plus", accessibilityDescription: nil)!,
                           target: self, action: #selector(addAccount(_:)))
        add.imagePosition = .imageLeading
        glass(add)
        let path = NSTextField(labelWithString: file.path.replacingOccurrences(of: FileManager.default.homeDirectoryForCurrentUser.path, with: "~"))
        path.font = .systemFont(ofSize: 11)
        path.textColor = .tertiaryLabelColor
        path.lineBreakMode = .byTruncatingMiddle
        path.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        path.toolTip = "Umbra reads your sites from this file"
        let gap = NSView()
        gap.setContentHuggingPriority(.init(1), for: .horizontal)
        let footer = NSStackView(views: [add, gap, path])
        footer.alignment = .centerY

        let card = group(list)
        let root = NSStackView(views: [top, card, footer])
        root.orientation = .vertical
        root.alignment = .leading
        root.spacing = 20
        root.setCustomSpacing(16, after: card)
        root.edgeInsets = NSEdgeInsets(top: titlebar + 4, left: 24, bottom: 20, right: 24)
        for view in root.arrangedSubviews {
            view.widthAnchor.constraint(equalTo: root.widthAnchor, constant: -48).isActive = true
        }
        NSLayoutConstraint.activate([icon.widthAnchor.constraint(equalToConstant: 45), icon.heightAnchor.constraint(equalToConstant: 45),
                                     root.widthAnchor.constraint(equalToConstant: width)])
        window.title = "Umbra"
        window.styleMask.remove(.resizable)
        window.contentView = Backdrop(root)
        resize(to: root.fittingSize)
        for site in sites { paint(site) }
    }

    private func row(_ site: Site, kept: Bool?) -> NSView {
        let tile = Fill(.systemGray, radius: 8)
        let letter = NSTextField(labelWithString: String(site.name.prefix(1)).uppercased())
        let rounded = NSFont.systemFont(ofSize: 14, weight: .semibold).fontDescriptor.withDesign(.rounded)
        letter.font = rounded.flatMap { NSFont(descriptor: $0, size: 14) } ?? .systemFont(ofSize: 14, weight: .semibold)
        letter.textColor = .white
        let logo = NSImageView()
        logo.imageScaling = .scaleProportionallyUpOrDown
        logo.isHidden = true
        for view in [letter, logo] {
            view.translatesAutoresizingMaskIntoConstraints = false
            tile.addSubview(view)
        }
        let side = logo.widthAnchor.constraint(equalToConstant: 32)
        NSLayoutConstraint.activate([tile.widthAnchor.constraint(equalToConstant: 32), tile.heightAnchor.constraint(equalToConstant: 32),
                                     letter.centerXAnchor.constraint(equalTo: tile.centerXAnchor),
                                     letter.centerYAnchor.constraint(equalTo: tile.centerYAnchor),
                                     logo.centerXAnchor.constraint(equalTo: tile.centerXAnchor),
                                     logo.centerYAnchor.constraint(equalTo: tile.centerYAnchor),
                                     side, logo.heightAnchor.constraint(equalTo: logo.widthAnchor)])
        Logo.of(site) { image in
            // A home-screen icon goes edge to edge; a glyph sits on white; a tiny one stays small rather than blur.
            let fit = Logo.fit(image)
            logo.image = fit.image
            logo.isHidden = false
            letter.isHidden = true
            side.constant = fit.small ? 16 : fit.fills ? 32 : 26
            tile.color = fit.fills && !fit.small ? .clear : .white
            tile.border = !(fit.fills && !fit.small)
        }

        let name = NSTextField(labelWithString: site.name)
        name.font = .systemFont(ofSize: 13, weight: .medium)
        let detail = NSStackView(views: [quiet(site.domain)])
        detail.spacing = 4
        if kept == true {
            let loop = NSImageView(image: NSImage(systemSymbolName: "arrow.triangle.2.circlepath", accessibilityDescription: nil)!)
            loop.symbolConfiguration = .init(pointSize: 9, weight: .semibold)
            loop.contentTintColor = .secondaryLabelColor
            for view in [quiet("·"), loop, quiet("Kept signed in")] { detail.addArrangedSubview(view) }
            detail.setCustomSpacing(3, after: loop)
        }
        let names = NSStackView(views: [name, detail])
        names.orientation = .vertical
        names.alignment = .leading
        names.spacing = 1

        // The status: a dot (or a spinner in its place) and a word, in a column of its own.
        let dot = NSImageView(image: NSImage(systemSymbolName: "circle.fill", accessibilityDescription: nil)!)
        dot.symbolConfiguration = .init(pointSize: 7, weight: .regular)
        let spinner = NSProgressIndicator()
        spinner.style = .spinning
        spinner.controlSize = .mini
        let mark = NSView()
        for view in [dot, spinner] {
            view.translatesAutoresizingMaskIntoConstraints = false
            mark.addSubview(view)
            NSLayoutConstraint.activate([view.centerXAnchor.constraint(equalTo: mark.centerXAnchor), view.centerYAnchor.constraint(equalTo: mark.centerYAnchor)])
        }
        NSLayoutConstraint.activate([mark.widthAnchor.constraint(equalToConstant: 12), mark.heightAnchor.constraint(equalToConstant: 12)])
        let label = NSTextField(labelWithString: "")
        label.font = .systemFont(ofSize: 11)
        let status = NSStackView(views: [mark, label])
        status.spacing = 5
        let column = NSView()
        status.translatesAutoresizingMaskIntoConstraints = false
        column.addSubview(status)
        NSLayoutConstraint.activate([column.widthAnchor.constraint(equalToConstant: 100), column.heightAnchor.constraint(equalToConstant: 20),
                                     status.leadingAnchor.constraint(equalTo: column.leadingAnchor),
                                     status.centerYAnchor.constraint(equalTo: column.centerYAnchor)])

        let button = NSButton(title: "Sign In", target: self, action: #selector(signIn(_:)))
        button.identifier = NSUserInterfaceItemIdentifier(site.name)
        button.bezelStyle = .push  // on the glass card: a standard button, not glass on glass
        if #available(macOS 26, *) { button.borderShape = .capsule }  // one shape for every action
        button.widthAnchor.constraint(equalToConstant: 76).isActive = true

        let more = NSPopUpButton(frame: .zero, pullsDown: true)
        more.menu = menu(site, kept: kept)
        more.isBordered = false
        more.imagePosition = .imageOnly
        (more.cell as? NSPopUpButtonCell)?.arrowPosition = .noArrow
        more.toolTip = "More"
        more.setAccessibilityLabel("More for \(site.name)")
        NSLayoutConstraint.activate([more.widthAnchor.constraint(equalToConstant: 24), more.heightAnchor.constraint(equalToConstant: 24)])

        let spacer = NSView()
        spacer.setContentHuggingPriority(.init(1), for: .horizontal)
        let row = NSStackView(views: [tile, names, spacer, column, button, more])
        row.alignment = .centerY
        row.spacing = 8
        row.setCustomSpacing(12, after: tile)
        row.setCustomSpacing(12, after: button)
        row.detachesHiddenViews = false  // a hidden Sign In keeps its place, so the statuses line up
        row.edgeInsets = NSEdgeInsets(top: 0, left: 16, bottom: 0, right: 14)
        row.heightAnchor.constraint(equalToConstant: 52).isActive = true  // a stack's cross-axis insets give way inside the glass
        rows[site.name] = (dot, spinner, label, column, button)
        return row
    }

    private func menu(_ site: Site, kept: Bool?) -> NSMenu {
        let menu = NSMenu()
        let image = NSImage(systemSymbolName: "ellipsis.circle", accessibilityDescription: "More")?
            .withSymbolConfiguration(NSImage.SymbolConfiguration(pointSize: 15, weight: .regular).applying(.init(hierarchicalColor: .secondaryLabelColor)))
        menu.addItem(withTitle: "", action: nil, keyEquivalent: "").image = image
        let keep = menu.addItem(withTitle: "Keep Signed In", action: kept == nil ? nil : #selector(keepSignedIn(_:)), keyEquivalent: "")
        keep.state = kept == true ? .on : .off
        keep.toolTip = "Umbra visits \(site.name) every ten minutes, so it never signs you out for being idle."
        menu.addItem(withTitle: "Check Again", action: #selector(checkAgain(_:)), keyEquivalent: "")
        menu.addItem(.separator())
        menu.addItem(withTitle: "Sign Out…", action: #selector(signOut(_:)), keyEquivalent: "")
        for item in menu.items.dropFirst() {
            item.target = self
            item.representedObject = site.name
        }
        return menu
    }

    private func paint(_ site: Site) {
        guard let row = rows[site.name] else { return }
        let state = states[site.name] ?? .unknown
        var (text, color, tip): (String, NSColor, String?) = ("", .tertiaryLabelColor, nil)
        switch state {
        case .checking: text = "Checking…"
        case .yes: (text, color) = ("Signed in", .systemGreen)
        case .no: text = "Signed out"
        case .unknown:
            (text, color) = ("Couldn't check", .systemOrange)
            tip = "The site didn't answer as expected" + (Check.why[site.name].map { ": \($0)" } ?? "") + ". Check Again in the … menu tries now."
        }
        row.dot.contentTintColor = color
        row.dot.isHidden = state == .checking
        row.spinner.isHidden = state != .checking
        state == .checking ? row.spinner.startAnimation(nil) : row.spinner.stopAnimation(nil)
        row.label.stringValue = text
        row.label.textColor = state == .yes || color == .systemOrange ? .labelColor : .secondaryLabelColor
        row.label.toolTip = tip
        row.column.toolTip = tip
        row.button.isHidden = state == .yes || state == .checking
    }

    private func showPage(_ site: Site, _ web: WKWebView) {
        if #unavailable(macOS 26) { window.styleMask.remove(.fullSizeContentView) }
        window.styleMask.insert(.resizable)
        window.titlebarAppearsTransparent = false
        window.titleVisibility = .visible
        window.title = "Sign in to \(site.name)"
        window.subtitle = site.checkable ? "This page closes as soon as you're signed in."
            : learning.contains(site.name) ? "Sign in, then click Done. Umbra remembers this account."
            : "This page closes once you're signed in, or click Done."
        let toolbar = NSToolbar(identifier: "page")
        toolbar.delegate = self
        toolbar.displayMode = .iconOnly
        toolbar.allowsUserCustomization = false
        window.toolbar = toolbar
        window.toolbarStyle = .unified
        window.contentView = web
        resize(to: NSSize(width: 1040, height: 780))
        if #available(macOS 26, *) { web.obscuredContentInsets = NSEdgeInsets(top: titlebar, left: 0, bottom: 0, right: 0) }
    }

    /// One grouped card: glass from macOS 26, a rounded box before.
    private func group(_ content: NSView) -> NSView {
        let card: NSView
        if #available(macOS 26, *) {
            let glass = NSGlassEffectView()
            glass.cornerRadius = 18
            glass.contentView = content
            card = glass
        } else {
            let box = NSBox()
            box.boxType = .custom
            box.cornerRadius = 12
            box.borderColor = .separatorColor
            box.fillColor = .controlBackgroundColor.withAlphaComponent(0.6)
            box.contentViewMargins = .zero
            box.contentView = content
            card = box
        }
        pin(content, to: card)
        return card
    }

    private func glass(_ button: NSButton, prominent: Bool = false, circle: Bool = false) {
        if #available(macOS 26, *) {
            button.bezelStyle = .glass
            button.borderShape = circle ? .circle : .capsule
            button.tintProminence = prominent ? .primary : .automatic
        } else {
            button.bezelStyle = .push
            button.bezelColor = prominent ? .controlAccentColor : nil
        }
    }

    private func hairline(inset: CGFloat) -> NSView {
        let line = Fill(.separatorColor, radius: 0)
        let holder = NSView()
        line.translatesAutoresizingMaskIntoConstraints = false
        holder.addSubview(line)
        NSLayoutConstraint.activate([line.leadingAnchor.constraint(equalTo: holder.leadingAnchor, constant: inset),
                                     line.trailingAnchor.constraint(equalTo: holder.trailingAnchor, constant: inset > 0 ? -16 : 0),
                                     line.topAnchor.constraint(equalTo: holder.topAnchor),
                                     line.bottomAnchor.constraint(equalTo: holder.bottomAnchor),
                                     line.heightAnchor.constraint(equalToConstant: 1 / (NSScreen.main?.backingScaleFactor ?? 2))])
        return holder
    }

    private func quiet(_ text: String) -> NSTextField {
        let label = NSTextField(labelWithString: text)
        label.font = .systemFont(ofSize: 11)
        label.textColor = .secondaryLabelColor
        return label
    }

    private func pin(_ view: NSView, to box: NSView) {
        view.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([view.leadingAnchor.constraint(equalTo: box.leadingAnchor), view.trailingAnchor.constraint(equalTo: box.trailingAnchor),
                                     view.topAnchor.constraint(equalTo: box.topAnchor), view.bottomAnchor.constraint(equalTo: box.bottomAnchor)])
    }

    /// Keeps the window's center where it was, and on screen.
    private func resize(to size: NSSize) {
        let old = window.frame
        var frame = window.frameRect(forContentRect: NSRect(origin: .zero, size: size))
        frame.origin = NSPoint(x: old.midX - frame.width / 2, y: old.maxY - frame.height)
        if let screen = (window.screen ?? NSScreen.main)?.visibleFrame {
            frame.origin.x = min(max(frame.origin.x, screen.minX), screen.maxX - frame.width)
            frame.origin.y = min(max(frame.origin.y, screen.minY), screen.maxY - frame.height)
        }
        window.setFrame(frame, display: true, animate: window.isVisible)
    }
}

/// A rounded fill in a color that follows the appearance, with an optional hairline edge.
private final class Fill: NSView {
    var color: NSColor { didSet { needsDisplay = true } }
    var border = false { didSet { needsDisplay = true } }

    init(_ color: NSColor, radius: CGFloat) {
        self.color = color
        super.init(frame: .zero)
        wantsLayer = true
        layer?.cornerRadius = radius
        layer?.cornerCurve = .continuous
        layer?.masksToBounds = true
    }

    required init?(coder: NSCoder) { fatalError() }
    override var wantsUpdateLayer: Bool { true }
    override func updateLayer() {
        layer?.backgroundColor = color.cgColor
        layer?.borderWidth = border ? 1 / (window?.backingScaleFactor ?? 2) : 0
        layer?.borderColor = NSColor.separatorColor.cgColor
    }
}

/// The app icon, aligned by its art: the icon file has a transparent margin around it.
private final class Artwork: NSImageView {
    override var alignmentRectInsets: NSEdgeInsets { NSEdgeInsets(top: 5.5, left: 5.5, bottom: 5.5, right: 5.5) }
}

/// The window's backdrop: one soft glow, so the glass has something to refract.
private final class Backdrop: NSView {
    init(_ content: NSView) {
        super.init(frame: .zero)
        addSubview(content)
        content.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([content.leadingAnchor.constraint(equalTo: leadingAnchor), content.trailingAnchor.constraint(equalTo: trailingAnchor),
                                     content.topAnchor.constraint(equalTo: topAnchor), content.bottomAnchor.constraint(equalTo: bottomAnchor)])
    }

    required init?(coder: NSCoder) { fatalError() }

    override func draw(_ dirtyRect: NSRect) {
        let dark = effectiveAppearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua
        NSColor.windowBackgroundColor.setFill()
        bounds.fill()
        let corner = NSPoint(x: 0, y: bounds.height)
        NSGradient(colors: [NSColor.systemIndigo.withAlphaComponent(dark ? 0.12 : 0.07), NSColor.systemIndigo.withAlphaComponent(0)])!
            .draw(fromCenter: corner, radius: 0, toCenter: corner, radius: max(bounds.width, bounds.height) * 0.5, options: [])
    }
}

/// Removes the cookies within a site's domain from this profile's store.
func removeCookies(of site: Site, done: @escaping (Int) -> Void) {
    store.httpCookieStore.getAllCookies { cookies in
        let doomed = cookies.filter { $0.within(site.domain) }
        let group = DispatchGroup()
        for cookie in doomed {
            group.enter()
            store.httpCookieStore.delete(cookie) { group.leave() }
        }
        group.notify(queue: .main) { persist { done(doomed.count) } }
    }
}

func signInCommands() -> [Command] {
    [
        Command(name: "sign-in", help: "Show the person Umbra's sign-in window. With site names or login URLs, it opens each one still signed out and answers once all are signed in (exit 3 if the window is closed first); a site already signed in answers at once and is never shown. With no sites, the window lists every site.",
                params: [Param(name: "sites", kind: .array, help: "Site names from the sites file, or login URLs", positional: true),
                         Param(name: "save", kind: .string, help: "Add a login URL to the sites file under this name, as the window's Add Account does: Umbra learns its sign-in cookie, and agents can use it")],
                usesTab: false, timeout: 900, deadlineError: { args in
                    let names = args.strings("sites").map { name in
                        name.contains("://") ? URL(string: name)?.host ?? "requested site" : String(name.prefix(128))
                    }
                    return UmbraError(2, "sign-in exceeded its request deadline while waiting for \(names.isEmpty ? "the requested sites" : names.joined(separator: ", "))")
                }) { args, engine, reply in
            let names = args.strings("sites")
            let file = args.string("sitesFile").map { URL(fileURLWithPath: $0) } ?? sitesFile  // the caller's, like cwd
            requestSitesFiles.insert(file)  // its keepSession sites are kept too
            switch resolveSites(names, file: file) {
            case .failure(let error): reply(.failure(error))
            case .success(let chosen):
                SignInWindow.request(chosen, named: !names.isEmpty, save: args.string("save"), file: file,
                                     timeout: args.number("timeout") ?? 900, onWaiter: { id in
                    engine.runtime.onCancel(args.operation ?? "") { SignInWindow.cancel(id) }
                }, reply: reply)
            }
        },
        Command(name: "sign-out", help: "Remove a site's cookies from this profile, so its next sign-in starts fresh. The person's sign-in is gone afterwards: ask them first.",
                params: [Param(name: "site", kind: .string, help: "A site name from the sites file, or a domain", required: true, positional: true)],
                usesTab: false, mcp: false) { args, _, reply in
            let name = args.string("site") ?? ""
            let file = args.string("sitesFile").map { URL(fileURLWithPath: $0) } ?? sitesFile
            let known = (try? readSites(file)) ?? []
            guard let site = known.first(where: { $0.name.caseInsensitiveCompare(name) == .orderedSame })
                ?? (name.contains(".") ? Site(name: name, domain: name, login: "https://\(name)/") : nil) else {
                return reply(.failure(UmbraError(64, "no site named \(name) in \(file.path); name one (\(known.map(\.name).joined(separator: ", "))) or a domain")))
            }
            removeCookies(of: site) { count in
                SignInWindow.forget(site)
                reply(.success(["site": site.name, "removed": count, "text": "signed out of \(site.name) (\(count) cookie\(count == 1 ? "" : "s") removed)"]))
            }
        },
    ]
}

extension SignInWindow {
    /// After a sign-out from the command line, the open window shows the site signed out.
    static func forget(_ site: Site) {
        guard let shown, shown.states[site.name] != nil else { return }
        shown.update([site.name: .no])
    }
}

func printCookies(_ domain: String?) {
    store.httpCookieStore.getAllCookies { cookies in
        for cookie in cookies.sorted(by: { ($0.bareDomain, $0.name) < ($1.bareDomain, $1.name) })
        where domain.map(cookie.within) ?? true {
            let expiry = cookie.expiresDate.map { ISO8601DateFormatter().string(from: $0) } ?? "session"
            print("\(cookie.name)\t\(cookie.domain)\t\(expiry)")
        }
        exit(0)
    }
}

extension SignInWindow: NSToolbarDelegate {
    private static let leave = NSToolbarItem.Identifier("leave")

    func toolbar(_ toolbar: NSToolbar, itemForItemIdentifier identifier: NSToolbarItem.Identifier,
                 willBeInsertedIntoToolbar flag: Bool) -> NSToolbarItem? {
        let item = NSToolbarItem(itemIdentifier: identifier)
        item.target = self
        item.action = #selector(back(_:))
        item.isBordered = true
        if let site = current, !site.checkable {  // nothing to check: the person says when they're done
            item.title = "Done"
            item.label = "Done"
            if #available(macOS 26, *) { item.style = .prominent }
        } else {
            item.image = NSImage(systemSymbolName: "chevron.backward", accessibilityDescription: "Sites")
            item.label = "Sites"
            item.toolTip = "Back to your sites"
            item.isNavigational = true
        }
        return item
    }

    func toolbarDefaultItemIdentifiers(_ toolbar: NSToolbar) -> [NSToolbarItem.Identifier] { [SignInWindow.leave] }
    func toolbarAllowedItemIdentifiers(_ toolbar: NSToolbar) -> [NSToolbarItem.Identifier] { [SignInWindow.leave] }
}
