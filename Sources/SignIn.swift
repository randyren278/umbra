// The sign-in window, the one place a person sees Umbra. It lists the sites from the sites file and
// whether each is signed in, and never loads a site that is: a site's page appears only to sign
// in, and is gone the moment the sign-in lands, so the window can't be used to browse. The daemon
// shows it, so agents keep working while a person signs in.
import AppKit
import WebKit

enum Signed { case checking, yes, no, unknown }

extension Site {
    /// The probe on the site itself: the login page's origin when that is on the site's domain
    /// (keeping its scheme and port), else https on the domain.
    var probeURL: URL? {
        guard let probe, let login = URL(string: login) else { return nil }
        let base = login.host.map { $0 == domain || $0.hasSuffix("." + domain) } == true ? login : URL(string: "https://" + domain)!
        return URL(string: probe, relativeTo: base)?.absoluteURL
    }

    var checkable: Bool { cookie != nil || probe != nil }

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
    entry["keepSession"] = site.keepSession
    if let index = list.firstIndex(where: { ($0["name"] as? String)?.caseInsensitiveCompare(site.name) == .orderedSame }) {
        list[index].merge(entry) { $1 }
    } else {
        list.append(entry)
    }
    let order = ["name", "domain", "login", "cookie", "probe", "keepSession"]
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
    private let web = WKWebView(frame: .zero, configuration: configuration())
    private let target: URL
    private var done: ((Signed) -> Void)?

    static func run(_ site: Site, done: @escaping (Signed) -> Void) {
        guard let url = site.probeURL else {
            guard let name = site.cookie else { return done(.unknown) }
            return store.httpCookieStore.getAllCookies { cookies in
                done(cookies.contains { $0.name == name && $0.within(site.domain) } ? .yes : .no)
            }
        }
        guard let name = site.cookie else {
            running.insert(Check(url, done))
            return
        }
        store.httpCookieStore.getAllCookies { cookies in
            guard cookies.contains(where: { $0.name == name && $0.within(site.domain) }) else { return done(.no) }
            running.insert(Check(url, done))
        }
    }

    private init(_ url: URL, _ done: @escaping (Signed) -> Void) {
        target = url
        self.done = done
        super.init()
        web.navigationDelegate = self
        web.load(URLRequest(url: url))
        DispatchQueue.main.asyncAfter(deadline: .now() + 20) { [weak self] in self?.finish(.unknown) }
    }

    private func finish(_ result: Signed) {
        guard let done else { return }
        self.done = nil
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
        finish(redirected || [401, 403].contains(status) ? .no : (200..<300).contains(status) ? .yes : .unknown)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        finish(.unknown)
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

    private let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 520, height: 320),
                                  styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
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
    private var baseline: Set<String>?         // their domain's cookie names once the login page loaded
    private var rows: [String: (icon: NSImageView, label: NSTextField, spinner: NSProgressIndicator, button: NSButton)] = [:]

    // MARK: Requests

    /// With no names, shows every site for the person, who closes the window when done. With names,
    /// signs in to each one still signed out and answers once all are; the window closes then
    /// unless the person had it open. Signed-in sites are never shown, and alone open nothing.
    static func request(_ chosen: [Site], named: Bool, save: String? = nil, file: URL = sitesFile, timeout: Double,
                        reply: @escaping Reply) {
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
        if !named || save != nil { (shown ?? SignInWindow()).file = file }
        if !named {
            let sign = shown ?? SignInWindow()
            sign.add(chosen)
            sign.pinned = true
            sign.present()
            return reply(.success(["text": "Umbra's sign-in window is open"]))
        }
        let group = DispatchGroup()
        var found: [String: Signed] = [:]
        for site in chosen {
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
        window.center()
        store.httpCookieStore.add(self)
    }

    private func add(_ chosen: [Site], known: [String: Signed] = [:]) {
        for site in chosen where !sites.contains(where: { $0.name == site.name }) {
            sites.append(site)
            states[site.name] = known[site.name] ?? .checking
            if known[site.name] == nil { Check.run(site) { self.update([site.name: $0]) } }
        }
        if page == nil { showList() }
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
        showPage(site, web)
        // A session cookie still here may belong to a session the site ended, or the check may just
        // have failed: it stays, and only a new value counts as a sign-in.
        guard let name = site.cookie else {
            web.load(URLRequest(url: url))
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
        persist {}  // keep a session-only sign-in, and write it to disk now rather than at exit
        answer(closing: false)
        advance()
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

    private func expire(_ id: UUID, after timeout: Double) {
        guard let index = waiters.firstIndex(where: { $0.id == id }) else { return }
        let waiter = waiters.remove(at: index)
        let missing = waiter.names.filter { states[$0] != .yes }
        waiter.reply(.failure(UmbraError(2, "no sign-in within \(duration(timeout)): still signed out of \(missing.joined(separator: ", "))")))
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

    func cookiesDidChange(in cookieStore: WKHTTPCookieStore) {
        cookiesChanged()
    }

    private func cookiesChanged() {
        guard let site = current, site.cookie != nil, let web = page, web.url != nil else { return }
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
                self.learning.remove(site.name)
                self.states[site.name] = .yes
                try? saveSite(self.sites[index], to: self.file)
                self.paint(self.sites[index])
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
                self.answer(closing: false)
                self.advance()
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
        begin(site)
    }

    @objc private func signOut(_ sender: NSButton) {
        guard let site = sites.first(where: { $0.name == sender.identifier?.rawValue }) else { return }
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

    // MARK: Layout

    private func showList() {
        rows = [:]
        let icon = NSImageView(image: NSApp.applicationIconImage)
        icon.imageScaling = .scaleProportionallyUpOrDown
        let title = NSTextField(labelWithString: "Umbra")
        title.font = .systemFont(ofSize: 22, weight: .semibold)
        let blurb = NSTextField(wrappingLabelWithString: "Sign in to each site once. Agents use these sign-ins in the background, and this window never opens a site you're signed in to.")
        blurb.textColor = .secondaryLabelColor
        blurb.font = .systemFont(ofSize: 12)
        blurb.preferredMaxLayoutWidth = 520 - 48 - 64 - 14
        let heading = NSStackView(views: [title, blurb])
        heading.orientation = .vertical
        heading.alignment = .leading
        heading.spacing = 3
        let top = NSStackView(views: [icon, heading])
        top.alignment = .centerY
        top.spacing = 14

        let list = NSStackView()
        list.orientation = .vertical
        list.alignment = .leading
        list.spacing = 8
        for (index, site) in sites.enumerated() {
            if index > 0 { list.addArrangedSubview(separator()) }
            list.addArrangedSubview(row(site))
        }
        if sites.isEmpty {
            let empty = NSTextField(wrappingLabelWithString: "No sites yet. Click Add Account to sign in to one; Umbra keeps it in the file below.")
            empty.textColor = .secondaryLabelColor
            empty.preferredMaxLayoutWidth = 520 - 48
            list.addArrangedSubview(empty)
        }
        let path = file.path.replacingOccurrences(of: FileManager.default.homeDirectoryForCurrentUser.path, with: "~")
        let file = NSTextField(labelWithString: "Sites come from \(path)")
        file.font = .systemFont(ofSize: 11)
        file.textColor = .tertiaryLabelColor
        file.lineBreakMode = .byTruncatingMiddle
        file.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        let add = NSButton(title: "Add Account…", image: NSImage(systemSymbolName: "plus", accessibilityDescription: nil)!,
                           target: self, action: #selector(addAccount(_:)))
        add.bezelStyle = .push
        add.imagePosition = .imageLeading
        add.setContentCompressionResistancePriority(.required, for: .horizontal)
        let gap = NSView()
        gap.setContentHuggingPriority(.init(1), for: .horizontal)
        let footer = NSStackView(views: [add, gap, file])
        footer.alignment = .centerY

        let root = NSStackView(views: [top, separator(), list, separator(), footer])
        root.orientation = .vertical
        root.alignment = .leading
        root.spacing = 14
        root.edgeInsets = NSEdgeInsets(top: 22, left: 24, bottom: 18, right: 24)
        for view in [top, list, footer] + root.arrangedSubviews.filter({ $0 is NSBox }) {
            view.widthAnchor.constraint(equalTo: root.widthAnchor, constant: -48).isActive = true
        }
        for view in list.arrangedSubviews {
            view.widthAnchor.constraint(equalTo: list.widthAnchor).isActive = true
        }
        NSLayoutConstraint.activate([icon.widthAnchor.constraint(equalToConstant: 64), icon.heightAnchor.constraint(equalToConstant: 64),
                                     root.widthAnchor.constraint(equalToConstant: 520)])
        window.title = "Umbra"
        window.styleMask.remove(.resizable)
        window.contentView = root
        resize(to: root.fittingSize)
        for site in sites { paint(site) }
    }

    private func row(_ site: Site) -> NSView {
        let badge = NSTextField(labelWithString: String(site.name.prefix(1)).uppercased())
        badge.font = .systemFont(ofSize: 15, weight: .semibold)
        badge.textColor = .controlAccentColor
        badge.alignment = .center
        let disc = NSView()
        disc.wantsLayer = true
        disc.layer?.cornerRadius = 8
        disc.layer?.backgroundColor = NSColor.controlAccentColor.withAlphaComponent(0.14).cgColor
        badge.translatesAutoresizingMaskIntoConstraints = false
        disc.addSubview(badge)
        NSLayoutConstraint.activate([disc.widthAnchor.constraint(equalToConstant: 34), disc.heightAnchor.constraint(equalToConstant: 34),
                                     badge.centerXAnchor.constraint(equalTo: disc.centerXAnchor),
                                     badge.centerYAnchor.constraint(equalTo: disc.centerYAnchor)])
        let name = NSTextField(labelWithString: site.name)
        name.font = .systemFont(ofSize: 13, weight: .semibold)
        let domain = NSTextField(labelWithString: site.domain)
        domain.font = .systemFont(ofSize: 11)
        domain.textColor = .secondaryLabelColor
        let names = NSStackView(views: [name, domain])
        names.orientation = .vertical
        names.alignment = .leading
        names.spacing = 1
        let icon = NSImageView()
        let label = NSTextField(labelWithString: "")
        label.font = .systemFont(ofSize: 12)
        let spinner = NSProgressIndicator()
        spinner.style = .spinning
        spinner.controlSize = .small
        let button = NSButton(title: "Sign In", target: self, action: #selector(signIn(_:)))
        button.identifier = NSUserInterfaceItemIdentifier(site.name)
        button.bezelStyle = .push
        button.widthAnchor.constraint(greaterThanOrEqualToConstant: 92).isActive = true
        let spacer = NSView()
        spacer.setContentHuggingPriority(.init(1), for: .horizontal)
        let row = NSStackView(views: [disc, names, spacer, spinner, icon, label, button])
        row.alignment = .centerY
        row.spacing = 10
        row.setCustomSpacing(12, after: disc)
        row.setCustomSpacing(5, after: icon)
        row.setCustomSpacing(14, after: label)
        row.edgeInsets = NSEdgeInsets(top: 10, left: 0, bottom: 10, right: 0)
        rows[site.name] = (icon, label, spinner, button)
        return row
    }

    private func paint(_ site: Site) {
        guard let row = rows[site.name] else { return }
        let state = states[site.name] ?? .unknown
        var symbol: String? = nil
        var text = ""
        var color = NSColor.secondaryLabelColor
        switch state {
        case .checking: text = "Checking…"
        case .yes: (symbol, text, color) = ("checkmark.circle.fill", "Signed in", .systemGreen)
        case .no: (symbol, text) = ("circle", "Signed out")
        case .unknown where site.checkable: (symbol, text, color) = ("exclamationmark.triangle.fill", "Couldn't check", .systemOrange)
        case .unknown: break
        }
        row.icon.image = symbol.flatMap { NSImage(systemSymbolName: $0, accessibilityDescription: text) }
        row.icon.contentTintColor = color
        row.icon.isHidden = symbol == nil
        row.label.stringValue = text
        row.label.textColor = state == .yes ? .labelColor : color
        row.spinner.isHidden = state != .checking
        state == .checking ? row.spinner.startAnimation(nil) : row.spinner.stopAnimation(nil)
        row.button.isHidden = state == .checking
        row.button.title = state == .yes ? "Sign Out" : "Sign In"
        row.button.action = state == .yes ? #selector(signOut(_:)) : #selector(signIn(_:))
    }

    private func showPage(_ site: Site, _ web: WKWebView) {
        let back = NSButton(title: "Sites", image: NSImage(systemSymbolName: "chevron.left", accessibilityDescription: "Back")!,
                            target: self, action: #selector(back(_:)))
        back.bezelStyle = .push
        back.imagePosition = .imageLeading
        if !site.checkable { back.title = "Done"; back.image = nil }
        let title = NSTextField(labelWithString: "Sign in to \(site.name)")
        title.font = .systemFont(ofSize: 13, weight: .semibold)
        let note = NSTextField(labelWithString: site.checkable ? "This page closes as soon as you're signed in."
                               : learning.contains(site.name) ? "Sign in, then click Done. Umbra remembers this account." : "Click Done when you're signed in.")
        note.font = .systemFont(ofSize: 11)
        note.textColor = .secondaryLabelColor
        let spacer = NSView()
        spacer.setContentHuggingPriority(.init(1), for: .horizontal)
        let bar = NSStackView(views: [back, title, spacer, note])
        bar.alignment = .centerY
        bar.spacing = 12
        bar.edgeInsets = NSEdgeInsets(top: 8, left: 12, bottom: 8, right: 16)
        let root = NSStackView(views: [bar, separator(), web])
        root.orientation = .vertical
        root.spacing = 0
        for view in root.arrangedSubviews {
            view.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true
        }
        web.setContentHuggingPriority(.init(1), for: .vertical)
        window.title = "Sign in to \(site.name)"
        window.styleMask.insert(.resizable)
        window.contentView = root
        resize(to: NSSize(width: 1040, height: 780))
    }

    private func separator() -> NSBox {
        let line = NSBox()
        line.boxType = .separator
        return line
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
                usesTab: false, timeout: 900) { args, _, reply in
            let names = args.strings("sites")
            let file = args.string("sitesFile").map { URL(fileURLWithPath: $0) } ?? sitesFile  // the caller's, like cwd
            requestSitesFiles.insert(file)  // its keepSession sites are kept too
            switch resolveSites(names, file: file) {
            case .failure(let error): reply(.failure(error))
            case .success(let chosen):
                SignInWindow.request(chosen, named: !names.isEmpty, save: args.string("save"), file: file,
                                     timeout: args.number("timeout") ?? 900, reply: reply)
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
