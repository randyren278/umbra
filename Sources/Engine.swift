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

private let conditionWaitScript = #"""
(() => {
  if (globalThis.__umbraConditionWait) return;
  const Promise0 = Promise, Observer = MutationObserver;
  const later = setTimeout.bind(globalThis), clear = clearTimeout.bind(globalThis);
  const add = Function.call.bind(EventTarget.prototype.addEventListener);
  const remove = Function.call.bind(EventTarget.prototype.removeEventListener);
  const observe = Function.call.bind(Observer.prototype.observe);
  const disconnect = Function.call.bind(Observer.prototype.disconnect);
  const entries = new Map(), get = entries.get.bind(entries), set = entries.set.bind(entries), drop = entries.delete.bind(entries);
  const api = {
    cancel(id) { get(id)?.(); },
    slice(id, predicate, remaining) {
      if (entries.size >= 16) return { state: 'budget' };
      return new Promise0(resolve => {
        let ended = false, checking = false, dirty = false, expired = false;
        let sliceTimer, deadlineTimer, eventTimer, observer;
        const listeners = [];
        const finish = state => {
          if (ended) return;
          ended = true; drop(id);
          clear(sliceTimer); clear(deadlineTimer); clear(eventTimer);
          if (observer) disconnect(observer);
          for (const [target, name, listener] of listeners) remove(target, name, listener, true);
          resolve({ state });
        };
        const schedule = () => {
          if (ended) return;
          dirty = true;
          if (checking || eventTimer !== undefined) return;
          eventTimer = later(() => { eventTimer = undefined; check(); }, 0);
        };
        const check = async () => {
          if (ended || checking) return;
          checking = true; dirty = false;
          let matched = false;
          try { matched = await predicate(); } catch {}
          checking = false;
          if (ended) return;
          if (matched) return finish('matched');
          if (expired) return finish('slice');
          if (dirty) schedule();
        };
        const listen = (target, name, listener) => {
          add(target, name, listener, true);
          listeners.push([target, name, listener]);
        };
        set(id, () => finish('cancelled'));
        observer = new Observer(schedule);
        observe(observer, document, { subtree: true, childList: true, attributes: true, characterData: true });
        for (const name of ['input', 'change', 'load', 'readystatechange', 'visibilitychange', 'DOMContentLoaded', 'transitionend', 'animationend']) listen(document, name, schedule);
        for (const name of ['pageshow', 'hashchange', 'popstate', 'resize']) listen(globalThis, name, schedule);
        listen(globalThis, 'pagehide', () => finish('retry'));
        sliceTimer = later(() => { expired = true; if (!checking) finish('slice'); }, 100);
        deadlineTimer = later(() => finish('timeout'), remaining);
        check();
      });
    }
  };
  Object.defineProperty(globalThis, '__umbraConditionWait', { value: Object.freeze(api) });
})();
"""#

private final class FormState: NSObject, WKScriptMessageHandler {
    static let shared = FormState()
    let tabs = NSMapTable<WKWebView, Tab>.weakToWeakObjects()
    static let world = WKContentWorld.world(name: "umbra-form-state")
    static let script = "addEventListener('input', e => { if (e.isTrusted) webkit.messageHandlers.umbraDirty.postMessage(true) }, true); addEventListener('change', e => { if (e.isTrusted) webkit.messageHandlers.umbraDirty.postMessage(true) }, true)"
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        if let web = message.webView { tabs.object(forKey: web)?.dirty = true }
    }
}

final class Tab: NSObject, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate, NSWindowDelegate {
    let id = UUID()
    let name: String
    var opener: UUID?
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
    var operation: String?
    var dirty = false
    var activeDownloads: Int { saving.count }
    var busy = 0                                     // commands on it still running
    private var watchdog = 0                         // the live load watch; older timers and late answers stand down
    private var restarting = false                   // the web process was killed on purpose: not a crash
    private var saving: [ObjectIdentifier: (url: String, path: URL)] = [:]

    init(name: String, configuration: WKWebViewConfiguration, engine: Engine) {
        self.name = name
        self.engine = engine
        let controller = configuration.userContentController
        if !controller.userScripts.contains(where: { $0.source == conditionWaitScript }) {
            controller.addUserScript(WKUserScript(source: conditionWaitScript, injectionTime: .atDocumentStart, forMainFrameOnly: true, in: .page))
        }
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
        FormState.shared.tabs.setObject(self, forKey: web)
        controller.removeScriptMessageHandler(forName: "umbraDirty", contentWorld: FormState.world)
        controller.add(FormState.shared, contentWorld: FormState.world, name: "umbraDirty")
        if !controller.userScripts.contains(where: { $0.source == FormState.script }) {
            controller.addUserScript(WKUserScript(source: FormState.script, injectionTime: .atDocumentStart, forMainFrameOnly: false, in: FormState.world))
        }
        for setup in tabSetups { setup(self) }
    }

    func cancelOperation() {
        operation = nil
        watchdog += 1
        web.stopLoading()
        if handoff != nil { handoff = nil; endHandoff() }
        finishLoading(.failure(UmbraError(2, "request interrupted")))
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
        dirty = false
        emitEvent(self, "url", ["url": webView.url?.absoluteString ?? "", "status": status as Any? ?? NSNull()])
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
        emitEvent(self, "download", ["state": "complete", "bytes": size])
    }

    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        let entry = saving.removeValue(forKey: ObjectIdentifier(download))
        downloads.append(["url": entry?.url ?? "", "state": "failed", "error": error.localizedDescription])
        emitEvent(self, "download", ["state": "failed"])
    }

    // MARK: Pop-ups, dialogs and file choosers

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        guard engine.runtime.admissionError(newTab: true) == nil else { return nil }
        let popup = engine.open(name: nil, config: configuration, select: false)
        popup.opener = id
        if let owner = engine.runtime.lease(self) { engine.runtime.leases[popup.name] = owner }
        return popup.web
    }

    func webViewDidClose(_ webView: WKWebView) {
        engine.close(self)
    }

    func answer(_ type: String, _ message: String, default text: String? = nil) -> (accept: Bool, text: String?) {
        let reply = nextDialog ?? (true, text)
        nextDialog = nil
        dialogs.append(["type": type, "message": message, "accepted": reply.accept, "text": reply.text as Any? ?? NSNull()])
        emitEvent(self, "dialog", ["type": type])
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
        let generation = operation
        web.callAsyncJavaScript(body, arguments: arguments, in: nil, in: world) { result in
            guard generation == self.operation else { return done(.failure(UmbraError(2, "request interrupted"))) }
            switch result {
            case .success(let value): done(.success(value))
            case .failure(let error): done(.failure(UmbraError(2, "script failed: \(exceptionText(error))")))
            }
        }
    }

    /// Events recheck a condition promptly; globals and time keep a 100 ms fallback. Errors and
    /// navigation retry in the current document until the original deadline.
    func wait(until condition: String, _ arguments: [String: Any] = [:], in world: WKContentWorld = .page,
              timeout: Double, done: @escaping (Result<Void, UmbraError>) -> Void) {
        let deadline = Date().addingTimeInterval(timeout), id = UUID().uuidString
        let generation = operation
        var completed = false, retry: DispatchSourceTimer?
        func finish(_ result: Result<Void, UmbraError>) {
            guard !completed else { return }
            completed = true
            retry?.setEventHandler {}; retry?.cancel(); retry = nil
            web.callAsyncJavaScript("globalThis.__umbraConditionWait?.cancel('\(id)')", arguments: [:], in: nil, in: world) { _ in }
            done(result)
        }
        func timedOut() { finish(.failure(UmbraError(2, "timed out after \(duration(timeout)) waiting for \(condition)"))) }
        func check() {
            guard !completed else { return }
            guard generation == self.operation else { return finish(.failure(UmbraError(2, "request interrupted"))) }
            guard deadline > Date() else { return timedOut() }
            let body = conditionWaitScript + "\nreturn globalThis.__umbraConditionWait.slice('\(id)', async () => !!(\(condition)), \(max(0, deadline.timeIntervalSinceNow * 1000)));"
            run(body, arguments, in: world) { result in
                guard !completed else { return }
                guard generation == self.operation else { return finish(.failure(UmbraError(2, "request interrupted"))) }
                if case .success(let raw) = result, let state = (raw as? [String: Any])?["state"] as? String {
                    if state == "matched" { return finish(.success(())) }
                    if state == "timeout" || Date() >= deadline { return timedOut() }
                    if state == "budget" { return finish(.failure(UmbraError(2, "at most 16 condition waits per document"))) }
                    if state == "slice" || state == "retry" { return check() }
                }
                guard deadline > Date() else { return timedOut() }
                let timer = DispatchSource.makeTimerSource(queue: .main)
                retry = timer
                timer.schedule(deadline: .now() + 0.1)
                timer.setEventHandler { timer.setEventHandler {}; timer.cancel(); retry = nil; check() }
                timer.resume()
            }
        }
        if let generation { engine.runtime.onFinish(generation) { finish(.failure(UmbraError(2, "request interrupted"))) } }
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
    lazy var runtime = Runtime(self)
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
        for tab in tabs where tab.busy == 0 && tab.handoff == nil && tab.activeDownloads == 0 && !tab.dirty && runtime.lease(tab) == nil && !runtime.jobs.contains(where: { $0.tab === tab }) {
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

    /// Only abandoned clean tabs may be reclaimed; active work and state belonging to an agent survive.
    @discardableResult
    func reclaim(critical: Bool, pressured: Bool = true) -> Int {
        guard pressured else { return 0 }
        var reclaimed = 0
        for tab in tabs where tab.busy == 0 && tab.handoff == nil && tab.activeDownloads == 0 && !tab.dirty && tab.loading.isEmpty && runtime.lease(tab) == nil && !runtime.jobs.contains(where: { $0.tab === tab }) {
            guard Date().timeIntervalSince(tab.lastUsed) > (critical ? 5 : 30) else { continue }
            if critical {
                let wasCurrent = current === tab
                close(tab)
                if wasCurrent { current = nil }
                closedIdle.insert(tab.name)
                reclaimed += 1
            } else { tab.window.orderOut(nil) }
        }
        return reclaimed
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
        closeEvents(tab)
        guard tabs.contains(where: { $0 === tab }) else { return }
        runtime.leases.removeValue(forKey: tab.name)
        FormState.shared.tabs.removeObject(forKey: tab.web)
        tab.operation = nil
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
