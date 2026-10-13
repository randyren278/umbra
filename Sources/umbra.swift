// Umbra: a headless WebKit browser for macOS.
//
// Background jobs that need a signed-in website use Umbra instead of driving Safari: pages load
// off screen, with no window, no Dock icon and no tab to catch the user's links. Umbra keeps its
// own cookie store, signed in once with `umbra sign-in`; no cookie ever leaves it.
//
//   umbra read <url> [--cookie NAME] [--wait SECONDS] [--timeout SECONDS]  < script.js
//     Loads the page, runs the script as the body of an async function and prints what it
//     returns. With no script, prints the page's HTML. --cookie: exit 3 unless that session
//     cookie is set for the page. --wait: let redirects and page scripts settle first.
//   umbra fetch <url> [--from PAGE] [--timeout SECONDS]
//     GETs the URL with the browser's session, from a page on the same site (default: its
//     /robots.txt), and prints the body. On a non-2xx status the body is still printed.
//   umbra download <url> <file> [--timeout SECONDS]
//     Downloads the URL with the browser's session, replacing <file> once complete.
//   umbra sign-in [site or URL ...]
//     One window with a tab per site and a live signed-in check; sign in once. Sites come
//     from ~/.config/umbra/sites.json (or $UMBRA_SITES); a URL opens an ad hoc tab.
//   umbra cookies [domain]
//     Cookie names, domains and expiry for diagnosis. Never values.
//
// UMBRA_PROFILE=<name> gives a run its own cookie store and lock (tests, separate accounts).
//
// Exit codes: 0 ok, 2 no answer in time or the script threw, 3 signed out, 4 the page or
// download failed, 5 an HTTP error status, 64 usage, 78 a malformed sites file.
import AppKit
import CryptoKit
import WebKit

let usage = """
    usage: umbra read <url> [--cookie NAME] [--wait SECONDS] [--timeout SECONDS]  < script.js
           umbra fetch <url> [--from PAGE] [--timeout SECONDS]
           umbra download <url> <file> [--timeout SECONDS]
           umbra sign-in [site or URL ...]
           umbra cookies [domain]
           umbra --version

    Sites: ~/.config/umbra/sites.json (or $UMBRA_SITES). Separate store: UMBRA_PROFILE=<name>.
    """

func fail(_ code: Int32, _ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(code)
}

let environment = ProcessInfo.processInfo.environment
let profile = environment["UMBRA_PROFILE"].flatMap { $0.isEmpty ? nil : $0 }

func uuid(named name: String) -> UUID {
    let hex = Array(SHA256.hash(data: Data(name.utf8)).map { String(format: "%02X", $0) }.joined())
    return UUID(uuidString: [0..<8, 8..<12, 12..<16, 16..<20, 20..<32].map { String(hex[$0]) }.joined(separator: "-"))!
}

// The store is keyed by this identifier and the bundle identifier in Info.plist, which is
// embedded in the binary: changing either signs every site out. The default identifier came
// from FIRST's helper so its store could move here intact.
let store = WKWebsiteDataStore(forIdentifier: profile.map { uuid(named: "umbra profile " + $0) }
                               ?? UUID(uuidString: "6F1C7A52-3D0B-4E8F-9A61-F1A57C0DE001")!)

struct Site: Decodable {
    let name, domain, login: String
    var cookie: String? = nil     // signed in while this cookie is set (HttpOnly ones included)
    var probe: String? = nil      // or while this same-origin path answers 2xx
    var keepSession: Bool? = nil  // persist session-only cookies, which WebKit drops when a run ends
}

let sitesFile = environment["UMBRA_SITES"].map { URL(fileURLWithPath: $0) }
    ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".config/umbra/sites.json")

let sites: [Site] = {
    guard let data = try? Data(contentsOf: sitesFile) else { return [] }
    do { return try JSONDecoder().decode([Site].self, from: data) }
    catch { fail(78, "could not read \(sitesFile.path): \(error)") }
}()

extension HTTPCookie {
    var bareDomain: String { domain.trimmingCharacters(in: ["."]) }
    func sent(to host: String) -> Bool { host == bareDomain || host.hasSuffix("." + bareDomain) }
    func within(_ domain: String) -> Bool { bareDomain == domain || bareDomain.hasSuffix("." + domain) }
}

func configuration() -> WKWebViewConfiguration {
    let config = WKWebViewConfiguration()
    config.websiteDataStore = store
    // WebKit writes the rest of Safari's user agent around this, so sites see Safari.
    let version = Bundle(path: "/Applications/Safari.app")?.infoDictionary?["CFBundleShortVersionString"] as? String ?? "26.0"
    config.applicationNameForUserAgent = "Version/\(version) Safari/605.1.15"
    return config
}

func keepSessions(then done: @escaping () -> Void) {
    store.httpCookieStore.getAllCookies { cookies in
        let lasting = cookies.filter { cookie in
            cookie.isSessionOnly && sites.contains { $0.keepSession == true && cookie.within($0.domain) }
        }.compactMap { cookie -> HTTPCookie? in
            var properties = cookie.properties ?? [:]
            properties[.discard] = nil
            properties[.expires] = Date(timeIntervalSinceNow: 30 * 86400)
            return HTTPCookie(properties: properties)
        }
        let group = DispatchGroup()
        for cookie in lasting {
            group.enter()
            store.httpCookieStore.setCookie(cookie) { group.leave() }
        }
        group.notify(queue: .main, execute: done)
    }
}

var leaving = false

/// Ends a headless run once session cookies are kept.
func leave(_ code: Int32, _ message: String = "") {
    guard !leaving else { return }
    leaving = true
    if !message.isEmpty { FileHandle.standardError.write(Data((message + "\n").utf8)) }
    keepSessions { exit(code) }
    DispatchQueue.main.asyncAfter(deadline: .now() + 5) { exit(code) }
}

/// Runs take turns: two WebKit processes on one store is untested ground.
func takeLock() {
    let dir = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Umbra")
    try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    let name = profile.map { "store-" + $0.replacingOccurrences(of: "/", with: "_") + ".lock" } ?? "store.lock"
    let fd = open(dir.appendingPathComponent(name).path, O_RDWR | O_CREAT, 0o600)
    if fd < 0 || flock(fd, LOCK_EX) != 0 { fail(1, "could not lock Umbra's cookie store") }
}

func exceptionText(_ error: Error) -> String {
    let info = (error as NSError).userInfo
    return info["WKJavaScriptExceptionMessage"] as? String ?? error.localizedDescription
}

final class Page: NSObject, WKNavigationDelegate {
    let web = WKWebView(frame: NSRect(x: 0, y: 0, width: 1280, height: 900), configuration: configuration())
    let wait: Double
    let loaded: (WKWebView) -> Void
    var started = false

    init(_ url: URL, wait: Double = 0, loaded: @escaping (WKWebView) -> Void) {
        self.wait = wait
        self.loaded = loaded
        super.init()
        web.navigationDelegate = self
        web.load(URLRequest(url: url))
    }

    // didFinish fires again on redirects and single-page apps: only the first one counts.
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard !started else { return }
        started = true
        DispatchQueue.main.asyncAfter(deadline: .now() + wait) { self.loaded(webView) }
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        failed(error)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        failed(error)
    }

    // A script redirect cancels the navigation it replaces; only a failure before the page loads counts.
    func failed(_ error: Error) {
        guard !started, (error as NSError).code != NSURLErrorCancelled else { return }
        leave(4, "page failed to load: \(error.localizedDescription)")
    }
}

func emit(_ value: Any?) {
    guard let value, !(value is NSNull) else { return }
    let text: String
    switch value {
    case let string as String: text = string
    case let object where JSONSerialization.isValidJSONObject(object):
        text = String(data: try! JSONSerialization.data(withJSONObject: object), encoding: .utf8)!
    default: text = String(describing: value)
    }
    FileHandle.standardOutput.write(Data((text.hasSuffix("\n") ? text : text + "\n").utf8))
}

func read(_ url: URL, script: String, cookie: String?, wait: Double) -> Page {
    Page(url, wait: wait) { web in
        store.httpCookieStore.getAllCookies { cookies in
            let host = web.url?.host ?? ""
            if let cookie, !cookies.contains(where: { $0.name == cookie && $0.sent(to: host) }) {
                return leave(3, "signed out of \(host)")
            }
            let body = script.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                ? "return document.documentElement.outerHTML" : script
            web.callAsyncJavaScript(body, arguments: [:], in: nil, in: .page) { result in
                switch result {
                case .success(let value): emit(value); leave(0)
                case .failure(let error): leave(2, "the script failed: \(exceptionText(error))")
                }
            }
        }
    }
}

func fetch(_ url: URL, from page: URL) -> Page {
    Page(page) { web in
        let script = "const response = await fetch(url, {credentials: 'include'}); return [response.status, await response.text()]"
        web.callAsyncJavaScript(script, arguments: ["url": url.absoluteString], in: nil, in: .page) { result in
            guard case .success(let value) = result, let pair = value as? [Any],
                  let status = pair.first as? Int, let body = pair.last as? String else {
                if case .failure(let error) = result { return leave(4, "request failed: \(exceptionText(error))") }
                return leave(4, "request failed")
            }
            FileHandle.standardOutput.write(Data(body.utf8))
            (200..<300).contains(status) ? leave(0) : leave(5, "HTTP \(status)")
        }
    }
}

final class Download: NSObject, WKDownloadDelegate {
    let web = WKWebView(frame: .zero, configuration: configuration())
    let file: URL
    let part: URL

    init(_ url: URL, to file: URL) {
        self.file = file
        part = file.deletingLastPathComponent().appendingPathComponent("." + file.lastPathComponent + ".umbra-part")
        super.init()
        web.startDownload(using: URLRequest(url: url)) { $0.delegate = self }
    }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String,
                  completionHandler: @escaping (URL?) -> Void) {
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            completionHandler(nil)
            return leave(5, "HTTP \(http.statusCode)")
        }
        try? FileManager.default.removeItem(at: part)
        completionHandler(part)
    }

    func downloadDidFinish(_ download: WKDownload) {
        rename(part.path, file.path) == 0 ? leave(0) : leave(4, "could not move the download to \(file.path)")
    }

    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        try? FileManager.default.removeItem(at: part)
        leave(4, "download failed: \(error.localizedDescription)")
    }
}

final class SignIn: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKHTTPCookieStoreObserver {
    let chosen: [Site]
    let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1040, height: 780),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
    let tabs: NSSegmentedControl
    let note = NSTextField(labelWithString: "")
    let done = NSButton(title: "Done", target: nil, action: nil)
    let webs: [WKWebView]
    var state: [Bool]

    init(_ chosen: [Site]) {
        self.chosen = chosen
        tabs = NSSegmentedControl(labels: chosen.map(\.name), trackingMode: .selectOne, target: nil, action: nil)
        webs = chosen.map { _ in WKWebView(frame: .zero, configuration: configuration()) }
        state = chosen.map { _ in false }
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        installEditMenu()
        layOut()
        store.httpCookieStore.add(self)
        for (web, site) in zip(webs, chosen) {
            web.navigationDelegate = self
            web.load(URLRequest(url: URL(string: site.login)!))
        }
        show(0)
        update()
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    // An accessory app shows no menu bar, but Command-V and friends still route through
    // the main menu: without an Edit menu, a password could not be pasted.
    func installEditMenu() {
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        edit.addItem(withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        let item = NSMenuItem()
        item.submenu = edit
        NSApp.mainMenu = NSMenu()
        NSApp.mainMenu?.addItem(item)
    }

    func layOut() {
        window.title = "Sign in to Umbra"
        window.titlebarSeparatorStyle = .none
        window.isReleasedWhenClosed = false
        window.minSize = NSSize(width: 720, height: 560)
        let header = NSVisualEffectView()
        header.material = .titlebar
        header.blendingMode = .withinWindow
        note.textColor = .secondaryLabelColor
        note.font = .systemFont(ofSize: 12)
        note.lineBreakMode = .byTruncatingTail
        tabs.target = self
        tabs.action = #selector(select(_:))
        tabs.segmentStyle = .automatic
        done.target = self
        done.action = #selector(finish(_:))
        done.bezelStyle = .push
        let line = NSBox()
        line.boxType = .separator
        let pages = NSView()
        for web in webs {
            web.translatesAutoresizingMaskIntoConstraints = false
            pages.addSubview(web)
            NSLayoutConstraint.activate([web.topAnchor.constraint(equalTo: pages.topAnchor),
                                         web.bottomAnchor.constraint(equalTo: pages.bottomAnchor),
                                         web.leadingAnchor.constraint(equalTo: pages.leadingAnchor),
                                         web.trailingAnchor.constraint(equalTo: pages.trailingAnchor)])
        }
        let root = NSView()
        for view in [header, note, tabs, done, line, pages] as [NSView] {
            view.translatesAutoresizingMaskIntoConstraints = false
        }
        for view in [note, tabs, done] as [NSView] {
            header.addSubview(view)
        }
        for view in [header, line, pages] as [NSView] {
            root.addSubview(view)
        }
        NSLayoutConstraint.activate([
            header.topAnchor.constraint(equalTo: root.topAnchor),
            header.leadingAnchor.constraint(equalTo: root.leadingAnchor),
            header.trailingAnchor.constraint(equalTo: root.trailingAnchor),
            header.heightAnchor.constraint(equalToConstant: 48),
            tabs.centerYAnchor.constraint(equalTo: header.centerYAnchor),
            tabs.leadingAnchor.constraint(equalTo: header.leadingAnchor, constant: 16),
            note.centerYAnchor.constraint(equalTo: header.centerYAnchor),
            note.leadingAnchor.constraint(equalTo: tabs.trailingAnchor, constant: 14),
            note.trailingAnchor.constraint(lessThanOrEqualTo: done.leadingAnchor, constant: -14),
            done.centerYAnchor.constraint(equalTo: header.centerYAnchor),
            done.trailingAnchor.constraint(equalTo: header.trailingAnchor, constant: -16),
            line.topAnchor.constraint(equalTo: header.bottomAnchor),
            line.leadingAnchor.constraint(equalTo: root.leadingAnchor),
            line.trailingAnchor.constraint(equalTo: root.trailingAnchor),
            pages.topAnchor.constraint(equalTo: line.bottomAnchor),
            pages.leadingAnchor.constraint(equalTo: root.leadingAnchor),
            pages.trailingAnchor.constraint(equalTo: root.trailingAnchor),
            pages.bottomAnchor.constraint(equalTo: root.bottomAnchor),
        ])
        note.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        window.contentView = root
    }

    func update() {
        for (index, site) in chosen.enumerated() {
            let ok = state[index]
            let symbol = NSImage(systemSymbolName: ok ? "checkmark.circle.fill" : "circle",
                                 accessibilityDescription: ok ? "signed in" : "not signed in")
            tabs.setImage(symbol?.withSymbolConfiguration(.init(paletteColors: ok ? [.white, .systemGreen] : [.tertiaryLabelColor])),
                          forSegment: index)
            tabs.setLabel(site.name, forSegment: index)
            tabs.setWidth(0, forSegment: index)
        }
        let all = !state.contains(false)
        note.stringValue = all ? "All set. Umbra now works in the background, with no windows. You can close this."
                               : "Sign in once to each. After that Umbra works in the background, with no windows."
        done.keyEquivalent = all ? "\r" : ""
    }

    /// Re-checks every site; on a fresh sign-in, moves on to the next site still signed out.
    func refresh() {
        keepSessions {}
        store.httpCookieStore.getAllCookies { cookies in
            for (index, site) in self.chosen.enumerated() {
                if let name = site.cookie {
                    self.set(index, cookies.contains { $0.name == name && $0.within(site.domain) })
                } else if let path = site.probe {
                    let web = self.webs[index]
                    guard let host = web.url?.host, host == site.domain || host.hasSuffix("." + site.domain) else {
                        self.set(index, false)
                        continue
                    }
                    let script = "const response = await fetch(path, {credentials: 'include'}); return response.status"
                    web.callAsyncJavaScript(script, arguments: ["path": path], in: nil, in: .page) { result in
                        self.set(index, (try? result.get() as? Int).map { (200..<300).contains($0) } ?? false)
                    }
                }
            }
        }
    }

    func set(_ index: Int, _ signedIn: Bool) {
        guard state[index] != signedIn else { return }
        state[index] = signedIn
        update()
        if signedIn, tabs.selectedSegment == index, let next = state.firstIndex(of: false) {
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { self.show(next) }
        }
    }

    func cookiesDidChange(in cookieStore: WKHTTPCookieStore) {
        refresh()
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        refresh()
    }

    @objc func select(_ sender: NSSegmentedControl) {
        show(sender.selectedSegment)
    }

    func show(_ index: Int) {
        tabs.selectedSegment = index
        for (other, web) in webs.enumerated() {
            web.isHidden = other != index
        }
    }

    @objc func finish(_ sender: Any?) {
        window.performClose(sender)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        keepSessions { NSApp.reply(toApplicationShouldTerminate: true) }
        return .terminateLater
    }
}

func cookies(_ domain: String?) {
    store.httpCookieStore.getAllCookies { cookies in
        for cookie in cookies.sorted(by: { ($0.bareDomain, $0.name) < ($1.bareDomain, $1.name) })
        where domain.map(cookie.within) ?? true {
            let expiry = cookie.expiresDate.map { ISO8601DateFormatter().string(from: $0) } ?? "session"
            print("\(cookie.name)\t\(cookie.domain)\t\(expiry)")
        }
        exit(0)
    }
}

// MARK: Command line

var args = Array(CommandLine.arguments.dropFirst().filter { !$0.hasPrefix("-psn_") })
let command = args.isEmpty ? "sign-in" : args.removeFirst()

func option(_ name: String) -> String? {
    guard let index = args.firstIndex(of: name), index + 1 < args.count else { return nil }
    defer { args.removeSubrange(index...index + 1) }
    return args[index + 1]
}

func seconds(_ name: String, default value: Double) -> Double {
    guard let text = option(name) else { return value }
    guard let number = Double(text), number >= 0 else { fail(64, "\(name) takes a number of seconds\n\n\(usage)") }
    return number
}

/// Runs a headless command: no window, no Dock icon, never takes focus.
func headless(timeout: Double, _ start: () -> AnyObject) -> Never {
    app.setActivationPolicy(.prohibited)
    takeLock()
    let job = start()
    DispatchQueue.main.asyncAfter(deadline: .now() + timeout) { leave(2, "no answer in \(Int(timeout)) seconds") }
    withExtendedLifetime(job) { app.run() }
    exit(0)
}

let app = NSApplication.shared
switch command {
case "read":
    let cookie = option("--cookie")
    let wait = seconds("--wait", default: 0)
    let timeout = seconds("--timeout", default: 90)
    guard args.count == 1, let url = URL(string: args[0]) else { fail(64, usage) }
    let script = isatty(STDIN_FILENO) != 0 ? "" : String(decoding: FileHandle.standardInput.readDataToEndOfFile(), as: UTF8.self)
    headless(timeout: timeout + wait) { read(url, script: script, cookie: cookie, wait: wait) }
case "fetch":
    let from = option("--from")
    let timeout = seconds("--timeout", default: 90)
    guard args.count == 1, let url = URL(string: args[0]), url.host != nil,
          let page = URL(string: from ?? "/robots.txt", relativeTo: url)?.absoluteURL else { fail(64, usage) }
    headless(timeout: timeout) { fetch(url, from: page) }
case "download":
    let timeout = seconds("--timeout", default: 300)
    guard args.count == 2, let url = URL(string: args[0]) else { fail(64, usage) }
    let file = URL(fileURLWithPath: args[1]).standardizedFileURL
    headless(timeout: timeout) { Download(url, to: file) }
case "sign-in":
    // A URL opens an ad hoc tab (no live check); a name picks a site from the sites file.
    let chosen = args.isEmpty ? sites : args.compactMap { arg -> Site? in
        if let url = URL(string: arg), ["http", "https"].contains(url.scheme ?? ""), let host = url.host {
            return Site(name: host, domain: host, login: arg)
        }
        guard let site = sites.first(where: { $0.name.caseInsensitiveCompare(arg) == .orderedSame }) else {
            fail(64, "no site named \(arg) in \(sitesFile.path); known: \(sites.map(\.name).joined(separator: ", "))")
        }
        return site
    }
    guard !chosen.isEmpty else {
        fail(64, "nothing to sign in to: name a URL (umbra sign-in https://example.com/login) or list sites in \(sitesFile.path)")
    }
    app.setActivationPolicy(.accessory)  // a window, but no Dock icon and no Command-Tab entry
    takeLock()
    let delegate = SignIn(chosen)
    app.delegate = delegate  // a weak reference; withExtendedLifetime keeps it alive
    withExtendedLifetime(delegate) { app.run() }
case "cookies":
    app.setActivationPolicy(.prohibited)
    takeLock()
    cookies(args.first)
    app.run()
case "help", "-h", "--help":
    print(usage)
case "version", "--version":
    print("umbra " + (Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "unknown"))
default:
    fail(64, usage)
}
