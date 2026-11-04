import AppKit
import WebKit

/// Native controls and rendering policy for the time a person owns a web view.
final class BrowserView: NSView, NSToolbarDelegate, NSUserInterfaceValidations {
    let web: WKWebView
    private let origin = NSTextField(labelWithString: "Loading…")
    private let progress = NSProgressIndicator()
    private let failure = NSTextField(wrappingLabelWithString: "")
    private var observations: [NSKeyValueObservation] = []
    private var buttons: [String: NSToolbarItem] = [:]
    private var screenObserver: NSObjectProtocol?
    private var activity: NSObjectProtocol?
    private var renderingFeature: NSObject?
    private var savedRendering: Bool?
    private let zoom: CGFloat
    private let magnification: CGFloat
    private let gestures: Bool
    private let magnifies: Bool
    private let headless: Bool
    private let leading: NSToolbarItem?

    init(_ web: WKWebView, headless: Bool = false, leading: NSToolbarItem? = nil) {
        self.web = web
        self.headless = headless
        self.leading = leading
        zoom = web.pageZoom
        magnification = web.magnification
        gestures = web.allowsBackForwardNavigationGestures
        magnifies = web.allowsMagnification
        super.init(frame: web.frame)
        if web.window?.contentView === web { web.window?.contentView = nil }
        else { web.removeFromSuperview() }
        addSubview(web)
        web.translatesAutoresizingMaskIntoConstraints = false
        progress.style = .bar
        progress.isIndeterminate = false
        progress.maxValue = 1
        failure.textColor = .secondaryLabelColor
        failure.isHidden = true
        for view in [progress, failure] {
            view.translatesAutoresizingMaskIntoConstraints = false
            addSubview(view)
        }
        NSLayoutConstraint.activate([
            web.leadingAnchor.constraint(equalTo: leadingAnchor), web.trailingAnchor.constraint(equalTo: trailingAnchor),
            web.topAnchor.constraint(equalTo: topAnchor), web.bottomAnchor.constraint(equalTo: bottomAnchor),
            progress.leadingAnchor.constraint(equalTo: leadingAnchor), progress.trailingAnchor.constraint(equalTo: trailingAnchor),
            progress.topAnchor.constraint(equalTo: topAnchor), progress.heightAnchor.constraint(equalToConstant: 2),
            failure.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 16),
            failure.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -16),
            failure.topAnchor.constraint(equalTo: topAnchor, constant: 8)
        ])
        observations = [web.observe(\.url, options: [.initial, .new]) { [weak self] _, _ in self?.update() },
                        web.observe(\.isLoading, options: [.new]) { [weak self] _, _ in self?.update() },
                        web.observe(\.estimatedProgress, options: [.new]) { [weak self] _, _ in self?.update() },
                        web.observe(\.canGoBack, options: [.new]) { [weak self] _, _ in self?.update() },
                        web.observe(\.canGoForward, options: [.new]) { [weak self] _, _ in self?.update() }]
    }

    required init?(coder: NSCoder) { fatalError() }

    func present(in window: NSWindow) {
        window.contentView = self
        let toolbar = NSToolbar(identifier: "browser")
        toolbar.delegate = self
        toolbar.displayMode = .iconOnly
        toolbar.allowsUserCustomization = false
        window.toolbar = toolbar
        window.toolbarStyle = .unified
        window.hasShadow = true
        web.allowsBackForwardNavigationGestures = true
        web.allowsMagnification = true
        if headless { occlusion(true) }
        if !unseen {
            activity = ProcessInfo.processInfo.beginActivity(options: .userInitiatedAllowingIdleSystemSleep, reason: "Interactive browser window")
            rendering()
            screenObserver = NotificationCenter.default.addObserver(forName: NSWindow.didChangeScreenNotification, object: window, queue: .main) { [weak self] _ in self?.rendering() }
        }
        window.makeFirstResponder(web)
        update()
    }

    func finish() {
        if let window, let sheet = window.attachedSheet { window.endSheet(sheet, returnCode: .abort) }
        if let screenObserver { NotificationCenter.default.removeObserver(screenObserver) }
        screenObserver = nil
        if let activity { ProcessInfo.processInfo.endActivity(activity) }
        activity = nil
        if let savedRendering { setRendering(savedRendering) }
        observations.removeAll()
        web.allowsBackForwardNavigationGestures = gestures
        web.allowsMagnification = magnifies
        web.pageZoom = zoom
        web.magnification = magnification
        if headless { occlusion(false) }
        window?.toolbar = nil
        web.removeFromSuperview()
        web.translatesAutoresizingMaskIntoConstraints = true
    }

    private func occlusion(_ enabled: Bool) {
        let selector = NSSelectorFromString("_setWindowOcclusionDetectionEnabled:")
        guard web.responds(to: selector) else { return }
        typealias Setter = @convention(c) (AnyObject, Selector, Bool) -> Void
        unsafeBitCast(web.method(for: selector), to: Setter.self)(web, selector, enabled)
    }

    private func rendering() {
        let preferences = web.configuration.preferences
        let type: AnyObject = WKPreferences.self
        let features = NSSelectorFromString("_features"), read = NSSelectorFromString("_isEnabledForFeature:")
        guard type.responds(to: features), preferences.responds(to: read),
              preferences.responds(to: NSSelectorFromString("_setEnabled:forFeature:")) else { return }
        if renderingFeature == nil {
            renderingFeature = (type.perform(features)?.takeUnretainedValue() as? [NSObject])?.first {
                $0.value(forKey: "key") as? String == "PreferPageRenderingUpdatesNear60FPSEnabled"
            }
            if let renderingFeature {
                typealias Getter = @convention(c) (AnyObject, Selector, AnyObject) -> Bool
                savedRendering = unsafeBitCast(preferences.method(for: read), to: Getter.self)(preferences, read, renderingFeature)
            }
        }
        // Keep the system preference everywhere except a human window on a high-refresh display.
        if let savedRendering { setRendering((window?.screen?.maximumFramesPerSecond ?? 60) > 60 ? false : savedRendering) }
    }

    private func setRendering(_ enabled: Bool) {
        guard let renderingFeature else { return }
        let preferences = web.configuration.preferences, selector = NSSelectorFromString("_setEnabled:forFeature:")
        typealias Setter = @convention(c) (AnyObject, Selector, Bool, AnyObject) -> Void
        unsafeBitCast(preferences.method(for: selector), to: Setter.self)(preferences, selector, enabled, renderingFeature)
    }

    private func update() {
        origin.stringValue = web.url?.host ?? "Loading…"
        progress.doubleValue = web.estimatedProgress
        progress.isHidden = !web.isLoading
        if web.isLoading { failure.isHidden = true }
        buttons["browser-back"]?.isEnabled = web.canGoBack
        buttons["browser-forward"]?.isEnabled = web.canGoForward
        let reload = buttons["browser-reload"]
        reload?.label = web.isLoading ? "Stop loading" : "Reload"
        reload?.image = NSImage(systemSymbolName: web.isLoading ? "xmark" : "arrow.clockwise", accessibilityDescription: reload?.label)
    }

    func failed(_ error: Error) {
        let error = error as NSError
        guard error.code != NSURLErrorCancelled, !(error.domain == "WebKitErrorDomain" && error.code == 102) else { return }
        failure.stringValue = "This page couldn’t load. Use Reload to try again."
        failure.isHidden = false
        progress.isHidden = true
    }

    func dialog(_ type: String, message: String, text: String? = nil, done: @escaping (Bool, String?) -> Void) {
        guard let window else { return done(false, nil) }
        let alert = NSAlert()
        alert.messageText = web.url?.host ?? "This page"
        alert.informativeText = message
        alert.addButton(withTitle: "OK")
        if type != "alert" { alert.addButton(withTitle: "Cancel") }
        let field = type == "prompt" ? NSTextField(string: text ?? "") : nil
        if let field {
            field.frame = NSRect(x: 0, y: 0, width: 300, height: 24)
            alert.accessoryView = field
        }
        alert.beginSheetModal(for: window) { response in
            done(response == .alertFirstButtonReturn, field?.stringValue)
        }
        if let field { alert.window.makeFirstResponder(field) }
    }

    func chooseFiles(_ parameters: WKOpenPanelParameters, done: @escaping ([URL]?) -> Void) {
        guard let window else { return done(nil) }
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = parameters.allowsDirectories
        panel.canChooseFiles = true
        panel.beginSheetModal(for: window) { response in done(response == .OK ? panel.urls : nil) }
    }

    @objc func browserBack(_ sender: Any?) { web.goBack() }
    @objc func browserForward(_ sender: Any?) { web.goForward() }
    @objc func browserReload(_ sender: Any?) { web.reload() }
    @objc func browserStop(_ sender: Any?) { web.stopLoading() }
    @objc private func reloadOrStop(_ sender: Any?) {
        if web.isLoading { web.stopLoading() } else { web.reload() }
    }
    @objc func browserZoomIn(_ sender: Any?) { web.pageZoom = min(3, web.pageZoom + 0.1) }
    @objc func browserZoomOut(_ sender: Any?) { web.pageZoom = max(0.5, web.pageZoom - 0.1) }
    @objc func browserActualSize(_ sender: Any?) { web.pageZoom = 1; web.magnification = 1 }
    @objc func browserCredentials(_ sender: Any?) {
        guard let window else { return }
        BrowserCredentials.show(in: window, site: web.url?.host ?? "this website")
    }

    func validateUserInterfaceItem(_ item: NSValidatedUserInterfaceItem) -> Bool {
        switch item.action {
        case #selector(browserBack(_:)): return web.canGoBack
        case #selector(browserForward(_:)): return web.canGoForward
        case #selector(browserStop(_:)): return web.isLoading
        default: return true
        }
    }

    func toolbarDefaultItemIdentifiers(_ toolbar: NSToolbar) -> [NSToolbarItem.Identifier] {
        (leading.map { [$0.itemIdentifier] } ?? []) + [NSToolbarItem.Identifier("browser-back"), NSToolbarItem.Identifier("browser-forward"),
            NSToolbarItem.Identifier("browser-reload"), .flexibleSpace, NSToolbarItem.Identifier("browser-origin"), .flexibleSpace,
            NSToolbarItem.Identifier("browser-credentials")]
    }

    func toolbarAllowedItemIdentifiers(_ toolbar: NSToolbar) -> [NSToolbarItem.Identifier] { toolbarDefaultItemIdentifiers(toolbar) }

    func toolbar(_ toolbar: NSToolbar, itemForItemIdentifier identifier: NSToolbarItem.Identifier,
                 willBeInsertedIntoToolbar flag: Bool) -> NSToolbarItem? {
        if identifier == leading?.itemIdentifier { return leading }
        let item = NSToolbarItem(itemIdentifier: identifier)
        if identifier.rawValue == "browser-origin" {
            origin.lineBreakMode = .byTruncatingMiddle
            origin.setAccessibilityLabel("Current site")
            item.view = origin
            item.label = "Current site"
        } else if identifier.rawValue == "browser-credentials" {
            item.label = "Passwords & Passkeys"
            item.toolTip = "Open Passwords or manage passkey access"
            item.image = NSImage(systemSymbolName: "key", accessibilityDescription: item.label)
            item.target = self
            item.action = #selector(browserCredentials(_:))
            item.isBordered = true
        } else {
            let back = identifier.rawValue == "browser-back", forward = identifier.rawValue == "browser-forward"
            item.label = back ? "Back" : forward ? "Forward" : "Reload"
            item.image = NSImage(systemSymbolName: back ? "chevron.backward" : forward ? "chevron.forward" : "arrow.clockwise", accessibilityDescription: item.label)
            item.target = self
            item.action = back ? #selector(browserBack(_:)) : forward ? #selector(browserForward(_:)) : #selector(reloadOrStop(_:))
            item.isBordered = true
            item.isNavigational = back || forward
            buttons[identifier.rawValue] = item
        }
        return item
    }
}
