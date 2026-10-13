// The one-time sign-in window, and cookie names for diagnosis.
import AppKit
import WebKit

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
        persist { NSApp.reply(toApplicationShouldTerminate: true) }
        return .terminateLater
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
