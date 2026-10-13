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

    var summary: [String: Any] {
        ["tab": name, "url": web.url?.absoluteString ?? "", "title": web.title ?? ""]
    }

    // MARK: Navigation

    func load(_ url: URL, done: @escaping (Result<Void, UmbraError>) -> Void) {
        loading.append(done)
        web.load(URLRequest(url: url))
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
        if error.code == NSURLErrorCancelled { return }  // replaced by a newer navigation, which will answer
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
        if navigationResponse.isForMainFrame {
            status = (navigationResponse.response as? HTTPURLResponse)?.statusCode
        }
        decisionHandler(navigationResponse.canShowMIMEType ? .allow : .download)
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
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
                if Date() > deadline { return done(.failure(UmbraError(2, "timed out after \(Int(timeout)) s waiting for \(condition)"))) }
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.1, execute: check)
            }
        }
        check()
    }

    // MARK: Handoff

    /// Shows the tab as an ordinary window so a person can finish a step Umbra can't: 2FA, a
    /// CAPTCHA, a consent screen. Closing the window hands control back.
    func reveal(_ message: String) {
        window.styleMask = [.titled, .closable, .miniaturizable, .resizable]
        window.title = "Umbra · \(message)"
        window.collectionBehavior = []
        window.ignoresMouseEvents = false
        window.alphaValue = 1
        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        window.setFrame(NSRect(x: screen.midX - engine.size.width / 2, y: screen.midY - engine.size.height / 2,
                               width: engine.size.width, height: engine.size.height + 28), display: true)
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
    }

    func endHandoff() {
        conceal()
        NSApp.hide(nil)  // hand focus back to whatever the person was using
        let finished = handoff
        handoff = nil
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

    @discardableResult
    func open(name: String?, config: WKWebViewConfiguration? = nil, select: Bool = true) -> Tab {
        counter += 1
        let tab = Tab(name: name ?? (tabs.isEmpty ? "main" : "tab-\(counter)"),
                      configuration: config ?? configuration(), engine: self)
        tabs.append(tab)
        if select || current == nil { current = tab }
        return tab
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
            return create ? .success(open(name: name)) : .failure(UmbraError(64, "no tab named \(name); see `umbra tabs`"))
        }
        if let current { return .success(current) }
        return create ? .success(open(name: nil)) : .failure(UmbraError(64, "no open tab; start with `umbra open <url>`"))
    }
}
