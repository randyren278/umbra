// The daemon's browser: tabs whose pages believe they are visible, and never are.
import AppKit
import WebKit

/// Snapshot, refs and actions run in this world: pages can neither see nor tamper with it.
let umbraWorld = WKContentWorld.world(name: "umbra")

/// Per-tab instrumentation (console, network, blocking…) that lanes install before a tab's first load.
var tabSetups: [(Tab) -> Void] = []

let downloadsDir: URL = {
    let dir = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Downloads/Umbra" + (profile.map { "/" + $0.replacingOccurrences(of: "/", with: "_") } ?? ""))
    return dir
}()

final class Tab: NSObject, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate, NSWindowDelegate {
    let name: String
    let web: WKWebView
    let window: NSWindow
    unowned let engine: Engine
    var extras: [String: Any] = [:]                  // lanes keep per-tab state here
    var status: Int?                                 // the last main-frame HTTP status
    var loading: [(Result<Void, UmbraError>) -> Void] = []
    var dialogs: [[String: Any]] = []                // what each alert, confirm or prompt said and got
    var nextDialog: (accept: Bool, text: String?)?   // the answer for the next one (default: accept)
    var uploads: [URL] = []                          // what the next file chooser receives
    var downloads: [[String: Any]] = []
    var handoff: (() -> Void)?
    var navigations = 0                              // main-frame navigations started; actions compare before and after
    var lastUsed = Date()                            // when a command on it last finished (Engine.rest)
    var busy = 0                                     // commands on it still running
    private var watchdog = 0                         // the live load watch; older timers and late answers stand down
    private var restarting = false                   // the web process was killed on purpose: not a crash
    private var saving: [ObjectIdentifier: (url: String, path: URL)] = [:]

    init(name: String, configuration: WKWebViewConfiguration, engine: Engine) {
        self.name = name
        self.engine = engine
        let frame = NSRect(origin: .zero, size: engine.size)
        web = WKWebView(frame: frame, configuration: configuration)
        // Ordered in but transparent, click-through and far off screen: WebKit then treats the
        // page as visible (requestAnimationFrame, IntersectionObserver and visibilityState
        // behave as in Safari) while nothing can be seen, clicked or focused.
        window = NSWindow(contentRect: frame.offsetBy(dx: -30000, dy: -30000), styleMask: [.borderless],
                          backing: .buffered, defer: false)
        super.init()
        window.isReleasedWhenClosed = false
        window.hasShadow = false
        window.contentView = web
        window.delegate = self
        conceal()
        let occlusion = NSSelectorFromString("_setWindowOcclusionDetectionEnabled:")
        if web.responds(to: occlusion) { web.perform(occlusion, with: false) }
        web.navigationDelegate = self
        web.uiDelegate = self
        for setup in tabSetups { setup(self) }
    }

    func conceal() {
        window.styleMask = [.borderless]
        window.alphaValue = 0
        window.ignoresMouseEvents = true
        window.collectionBehavior = [.transient, .ignoresCycle, .stationary]
        window.setFrameOrigin(NSPoint(x: -30000, y: -30000))
        window.orderFrontRegardless()
    }

    /// Shows a tab that was resting hidden, and goes on once its page knows it is visible again.
    func wake(then go: @escaping () -> Void) {
        guard !window.isVisible else { return go() }
        window.orderFrontRegardless()
        wait(until: "document.visibilityState == 'visible'", in: umbraWorld, timeout: 2) { _ in go() }
    }

    var summary: [String: Any] {
        ["tab": name, "url": web.url?.absoluteString ?? "", "title": web.title ?? ""]
    }

    // MARK: Navigation

    func load(_ url: URL, done: @escaping (Result<Void, UmbraError>) -> Void) {
        loading.append(done)
        web.load(URLRequest(url: url))
        watch(url, from: navigations, tries: 1)
    }

    /// A watchdog for loads on a starved machine (seen on CI): a load that never starts is
    /// retried, then reported; a page that finished without WebKit saying so is finished; and a
    /// web process that stops answering (seen when several start at once) is replaced, and the
    /// load retried in the new one.
    private func watch(_ url: URL, from started: Int, tries: Int, silent: Int = 0) {
        watchdog += 1
        let generation = watchdog
        DispatchQueue.main.asyncAfter(deadline: .now() + 5) { [weak self] in
            guard let self, generation == self.watchdog, !self.loading.isEmpty else { return }
            if self.navigations == started, !self.web.isLoading {
                guard tries < 3 else { return self.finishLoading(.failure(UmbraError(4, "the page never started loading"))) }
                self.web.load(URLRequest(url: url))
                return self.watch(url, from: started, tries: tries + 1)
            }
            var answered = false
            self.web.evaluateJavaScript("document.readyState") { value, _ in
                answered = true
                guard generation == self.watchdog, !self.loading.isEmpty else { return }
                if (value as? String) == "complete", !self.web.isLoading { return self.finishLoading(.success(())) }
                self.watch(url, from: started, tries: tries)
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 5) {
                guard !answered, generation == self.watchdog, !self.loading.isEmpty else { return }
                // A busy page can be silent for a moment; silent twice running, it is stuck.
                guard silent > 0 else { return self.watch(url, from: started, tries: tries, silent: 1) }
                guard tries < 3, self.restartWebProcess() else {
                    if tries >= 3 { _ = self.restartWebProcess() }  // the next load on this tab starts clean
                    return self.finishLoading(.failure(UmbraError(4, "the page's web process stopped answering")))
                }
                self.web.load(URLRequest(url: url))
                self.watch(url, from: self.navigations, tries: tries + 1)
            }
        }
    }

    /// Ends the page's web process; WebKit starts a new one for the next load. False where
    /// WebKit doesn't offer the call.
    private func restartWebProcess() -> Bool {
        let kill = NSSelectorFromString("_killWebContentProcessAndResetState")
        guard web.responds(to: kill) else { return false }
        restarting = true
        web.perform(kill)
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) { [weak self] in self?.restarting = false }
        return true
    }

    /// Back, forward or reload; fails at once when there is nowhere to go.
    func go(_ step: (WKWebView) -> WKNavigation?, done: @escaping (Result<Void, UmbraError>) -> Void) {
        status = nil  // a page from the back-forward cache brings no response
        loading.append(done)
        if step(web) == nil { finishLoading(.failure(UmbraError(4, "no page to go to"))) }
    }

    func finishLoading(_ result: Result<Void, UmbraError>) {
        let waiting = loading
        loading = []
        waiting.forEach { $0(result) }
    }

    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        navigations += 1
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        finishLoading(.success(()))
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        failed(error)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        failed(error)
    }

    func failed(_ error: Error) {
        let error = error as NSError
        if error.code == NSURLErrorCancelled || restarting { return }  // replaced by a newer navigation, which will answer
        if error.domain == "WebKitErrorDomain" && error.code == 102 {  // became a download
            return finishLoading(.success(()))
        }
        finishLoading(.failure(UmbraError(4, "page failed to load: \(error.localizedDescription)")))
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        decisionHandler(navigationAction.shouldPerformDownload ? .download : .allow)
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        let http = navigationResponse.response as? HTTPURLResponse
        if navigationResponse.isForMainFrame { status = http?.statusCode }
        // As in Safari, an attachment downloads even when WebKit could show it (a CSV export).
        let attachment = http?.value(forHTTPHeaderField: "Content-Disposition")?.lowercased().hasPrefix("attachment") == true
        decisionHandler(navigationResponse.canShowMIMEType && !attachment ? .allow : .download)
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        if restarting { return restarting = false }
        finishLoading(.failure(UmbraError(4, "the page's web process crashed; it will reload on the next navigation")))
    }

    // MARK: Downloads started by the page

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String,
                  completionHandler: @escaping (URL?) -> Void) {
        try? FileManager.default.createDirectory(at: downloadsDir, withIntermediateDirectories: true)
        let base = URL(fileURLWithPath: suggestedFilename).lastPathComponent
        var path = downloadsDir.appendingPathComponent(base.isEmpty ? "download" : base)
        var copy = 1
        while FileManager.default.fileExists(atPath: path.path) {
            copy += 1
            let stem = (base as NSString).deletingPathExtension, ext = (base as NSString).pathExtension
            path = downloadsDir.appendingPathComponent("\(stem) \(copy)" + (ext.isEmpty ? "" : ".\(ext)"))
        }
        saving[ObjectIdentifier(download)] = (response.url?.absoluteString ?? "", path)
        completionHandler(path)
    }

    func downloadDidFinish(_ download: WKDownload) {
        guard let entry = saving.removeValue(forKey: ObjectIdentifier(download)) else { return }
        let size = (try? FileManager.default.attributesOfItem(atPath: entry.path.path)[.size] as? Int) ?? 0
        downloads.append(["url": entry.url, "path": entry.path.path, "bytes": size, "state": "complete"])
    }

    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        let entry = saving.removeValue(forKey: ObjectIdentifier(download))
        downloads.append(["url": entry?.url ?? "", "state": "failed", "error": error.localizedDescription])
    }

    // MARK: Pop-ups, dialogs and file choosers

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        engine.open(name: nil, config: configuration, select: false).web
    }

    func webViewDidClose(_ webView: WKWebView) {
        engine.close(self)
    }

    func answer(_ type: String, _ message: String, default text: String? = nil) -> (accept: Bool, text: String?) {
        let reply = nextDialog ?? (true, text)
        nextDialog = nil
        dialogs.append(["type": type, "message": message, "accepted": reply.accept, "text": reply.text as Any? ?? NSNull()])
        return reply
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        _ = answer("alert", message)
        completionHandler()
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        completionHandler(answer("confirm", message).accept)
    }

    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String, defaultText: String?,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (String?) -> Void) {
        let reply = answer("prompt", prompt, default: defaultText ?? "")
        completionHandler(reply.accept ? reply.text ?? "" : nil)
    }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let files = parameters.allowsMultipleSelection ? uploads : Array(uploads.prefix(1))
        uploads = []
        completionHandler(files.isEmpty ? nil : files)
    }

    // MARK: Scripts

    /// Runs `body` as an async function in `world`; the page world sees the page's own globals.
    func run(_ body: String, _ arguments: [String: Any] = [:], in world: WKContentWorld = .page,
             done: @escaping (Result<Any?, UmbraError>) -> Void) {
        web.callAsyncJavaScript(body, arguments: arguments, in: nil, in: world) { result in
            switch result {
            case .success(let value): done(.success(value))
            case .failure(let error): done(.failure(UmbraError(2, "script failed: \(exceptionText(error))")))
            }
        }
    }

    /// Polls `condition` every 100 ms in whatever document is current; errors count as false,
    /// so redirects and half-loaded pages just mean not yet.
    func wait(until condition: String, _ arguments: [String: Any] = [:], in world: WKContentWorld = .page,
              timeout: Double, done: @escaping (Result<Void, UmbraError>) -> Void) {
        let deadline = Date().addingTimeInterval(timeout)
        func check() {
            run("return Boolean(\(condition))", arguments, in: world) { result in
                if case .success(let value) = result, (value as? Bool) == true { return done(.success(())) }
                if Date() > deadline { return done(.failure(UmbraError(2, "timed out after \(duration(timeout)) waiting for \(condition)"))) }
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.1, execute: check)
            }
        }
        check()
    }

    // MARK: Handoff

    /// Shows the tab as an ordinary window so a person can finish a step Umbra can't: 2FA, a
    /// CAPTCHA, a consent screen. Closing the window hands control back.
    func reveal(_ message: String) {
        // A window the person can use, still no Dock icon (unless the sign-in window has one up).
        if SignInWindow.shown == nil && !unseen { NSApp.setActivationPolicy(.accessory) }
        installMenus()
        window.styleMask = [.titled, .closable, .miniaturizable, .resizable]
        window.title = "Umbra · \(message)"
        window.collectionBehavior = unseen ? [.canJoinAllSpaces] : []  // unseen, it is up on whatever Space the person is on
        window.ignoresMouseEvents = unseen
        window.alphaValue = unseen ? 0 : 1
        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        window.setFrame(NSRect(x: screen.midX - engine.size.width / 2, y: screen.midY - engine.size.height / 2,
                               width: engine.size.width, height: engine.size.height + 28), display: true)
        if unseen { return window.orderFrontRegardless() }
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
    }

    func endHandoff() {
        conceal()
        NSApp.deactivate()  // hand focus back to whatever the person was using
        let finished = handoff
        handoff = nil
        if !engine.tabs.contains(where: { $0.handoff != nil }) && SignInWindow.shown == nil { NSApp.setActivationPolicy(.prohibited) }
        finished?()
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        if handoff != nil { endHandoff() }
        return false  // the tab lives on, hidden again
    }
}

final class Engine {
    var tabs: [Tab] = []
    var current: Tab?
    var size = NSSize(width: 1280, height: 900)
    private var counter = 0
    private var closedIdle: Set<String> = []  // so a command naming one hears why it is gone

    @discardableResult
    func open(name: String?, config: WKWebViewConfiguration? = nil, select: Bool = true) -> Tab {
        counter += 1
        let tab = Tab(name: name ?? (tabs.isEmpty ? "main" : "tab-\(counter)"),
                      configuration: config ?? configuration(), engine: self)
        tabs.append(tab)
        closedIdle.remove(tab.name)
        if select || current == nil { current = tab }
        return tab
    }

    /// What becomes of a tab no command has used for a while. After a third of `limit` it is hidden,
    /// so its timers slow down and its animations pause as in a background tab in Safari (the next
    /// command shows it again). After `limit` it closes, as the daemon itself exits when unused: a
    /// tab an agent left behind doesn't keep a web process going for days.
    func rest(after limit: Double) {
        for tab in tabs where tab.busy == 0 && tab.handoff == nil {
            let idle = Date().timeIntervalSince(tab.lastUsed)
            if idle > limit, tab.loading.isEmpty {
                let wasCurrent = current === tab
                close(tab)
                if wasCurrent { current = nil }  // never some other agent's tab
                closedIdle.insert(tab.name)
            } else if idle > limit / 3, tab.window.isVisible {
                tab.window.orderOut(nil)
            }
        }
    }

    /// Tabs on a site kept signed in look attended, resting or not: some sites sign you out after
    /// minutes with no input in an open page (Air Canada after 15), and an agent's tab has none.
    func attend(_ kept: [Site]) {
        for tab in tabs where tab.busy == 0 && tab.handoff == nil {
            guard let host = tab.web.url?.host, kept.contains(where: { host == $0.domain || host.hasSuffix("." + $0.domain) }) else { continue }
            tab.nudge()
        }
    }

    func close(_ tab: Tab) {
        tab.web.stopLoading()
        tab.finishLoading(.failure(UmbraError(4, "the tab closed")))
        tab.window.orderOut(nil)
        tab.window.contentView = nil
        tabs.removeAll { $0 === tab }
        if current === tab { current = tabs.last }
    }

    /// The tab a command acts on: --tab by name, else the current one, created on demand when
    /// `create` is set.
    func tab(_ args: Args, create: Bool = false) -> Result<Tab, UmbraError> {
        if let name = args.string("tab") {
            if let tab = tabs.first(where: { $0.name == name }) { return .success(tab) }
            if create { return .success(open(name: name)) }
            if closedIdle.contains(name) {
                return .failure(UmbraError(64, "tab \(name) closed after \(duration(idleLimit)) unused; `umbra --tab \(name) open <url>` opens it again"))
            }
            return .failure(UmbraError(64, "no tab named \(name); see `umbra tabs`"))
        }
        if let current { return .success(current) }
        return create ? .success(open(name: nil)) : .failure(UmbraError(64, "no open tab; start with `umbra open <url>`"))
    }
}
