// Snapshot with element refs, and actions: click, fill, type, press, select, check, hover, scroll,
// drag, upload, dialog, focus and get. (Interaction lane.) Input is native: NSEvents sent to the
// web view reach the page with isTrusted true. The page-side half is InteractJS.swift.
import AppKit
import UniformTypeIdentifiers
import WebKit

private var eventNumber = 0

/// The library with this launch's name for forwarded navigate events, which pages can't guess.
let navigateEvent = "umbra-" + UUID().uuidString.prefix(8).lowercased()
let interactLibrary = interactJS.replacingOccurrences(of: "NAV_EVENT", with: navigateEvent)

/// Page world, before page scripts: forwards each navigate event (link, form, location.href, reload)
/// to the umbraWorld library as a DOM event, using built-ins captured before the page can replace them.
let navigateJS = """
    (() => { if (!window.navigation) return; const fire = EventTarget.prototype.dispatchEvent, Event = CustomEvent, json = JSON.stringify, doc = document;
    navigation.addEventListener('navigate', e => fire.call(doc, new Event('\(navigateEvent)', { detail: json({ url: e.destination.url, same: e.destination.sameDocument, download: e.downloadRequest != null }) }))); })();
    """

extension Tab {
    /// Calls `__umbra.<name>(a)` in umbraWorld, installing the library first if this document lacks it.
    func umbra(_ name: String, _ args: [String: Any] = [:], done: @escaping (Result<[String: Any], UmbraError>) -> Void) {
        let operation = self.operation
        let body = "const u = globalThis.__umbra; return u ? u.\(name)(a) : 'missing'"
        let answer: (Result<Any?, UmbraError>) -> Void = { result in
            guard operation == self.operation else { return }
            switch result {
            case .success(let value): done(.success(value as? [String: Any] ?? [:]))
            case .failure(let error): done(.failure(error))
            }
        }
        run(body, ["a": args], in: umbraWorld) { result in
            guard operation == self.operation else { return }
            guard case .success(let value) = result, value as? String == "missing" else { return answer(result) }
            self.run(interactLibrary, in: umbraWorld) { loaded in
                guard operation == self.operation else { return }
                if case .failure(let error) = loaded { return done(.failure(error)) }
                self.run(body, ["a": args], in: umbraWorld, done: answer)
            }
        }
    }

    /// Window coordinates for a point the library reports in CSS pixels.
    func windowPoint(_ info: [String: Any]) -> NSPoint {
        let x = (info["x"] as? Double ?? 0) * web.pageZoom, y = (info["y"] as? Double ?? 0) * web.pageZoom
        return web.convert(NSPoint(x: x, y: web.isFlipped ? y : web.bounds.height - y), to: nil)
    }

    func mouse(_ type: NSEvent.EventType, _ point: NSPoint, clicks: Int = 1, flags: NSEvent.ModifierFlags = [], held: Bool = false) {
        eventNumber += 1
        if type == .otherMouseDown || type == .otherMouseUp { return middle(type == .otherMouseDown, point, clicks: clicks, flags: flags) }
        let pressed = [.leftMouseDown, .rightMouseDown, .otherMouseDown].contains(type) || held
        // WebKit drops mouseMoved: while the app is inactive, but delivers a drag with no pressure as
        // a plain move: the page sees a trusted mousemove with buttons 0.
        guard let event = NSEvent.mouseEvent(with: type == .mouseMoved ? .leftMouseDragged : type, location: point, modifierFlags: flags,
                                             timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: window.windowNumber,
                                             context: nil, eventNumber: eventNumber, clickCount: clicks, pressure: pressed ? 1 : 0)
        else { return }
        switch type {
        case .leftMouseDown: web.mouseDown(with: event)
        case .leftMouseUp: web.mouseUp(with: event)
        case .rightMouseDown: web.rightMouseDown(with: event)
        case .rightMouseUp: web.rightMouseUp(with: event)
        case .otherMouseDown: web.otherMouseDown(with: event)
        case .otherMouseUp: web.otherMouseUp(with: event)
        default: web.mouseDragged(with: event)
        }
    }

    /// NSEvent can't make a middle-button event, so it comes from a CGEvent, placed as in wheel().
    private func middle(_ down: Bool, _ point: NSPoint, clicks: Int, flags: NSEvent.ModifierFlags) {
        let location = CGPoint(x: point.x, y: (NSScreen.screens.first?.frame.height ?? 0) - point.y)
        guard let cg = CGEvent(mouseEventSource: nil, mouseType: down ? .otherMouseDown : .otherMouseUp, mouseCursorPosition: location, mouseButton: .center) else { return }
        cg.setIntegerValueField(.mouseEventClickState, value: Int64(clicks))
        cg.flags = CGEventFlags(rawValue: UInt64(flags.rawValue))
        guard let event = NSEvent(cgEvent: cg) else { return }
        if down { web.otherMouseDown(with: event) } else { web.otherMouseUp(with: event) }
    }

    func key(_ key: Key, down: Bool) {
        eventNumber += 1
        guard let event = NSEvent.keyEvent(with: down ? .keyDown : .keyUp, location: .zero, modifierFlags: key.flags,
                                           timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: window.windowNumber,
                                           context: nil, characters: key.chars, charactersIgnoringModifiers: key.plain,
                                           isARepeat: false, keyCode: key.code)
        else { return }
        if down { web.keyDown(with: event) } else { web.keyUp(with: event) }
    }

    /// A whole key press: modifiers down, the key, modifiers up, as a keyboard sends them.
    func press(_ key: Key) {
        var held: NSEvent.ModifierFlags = []
        for (flag, code) in modifierKeys where key.flags.contains(flag) {
            held.insert(flag)
            self.key(Key(code: code, chars: "", plain: "", flags: held), flags: true)
        }
        self.key(key, down: true)
        self.key(key, down: false)
        for (flag, code) in modifierKeys.reversed() where key.flags.contains(flag) {
            held.remove(flag)
            self.key(Key(code: code, chars: "", plain: "", flags: held), flags: true)
        }
    }

    /// A Shift tap: input a page's idle timer counts as a person's, which changes nothing on the page.
    func nudge() {
        key(Key(code: 56, chars: "", plain: "", flags: .shift), flags: true)
        key(Key(code: 56, chars: "", plain: "", flags: []), flags: true)
    }

    private func key(_ key: Key, flags: Bool) {
        eventNumber += 1
        guard let event = NSEvent.keyEvent(with: .flagsChanged, location: .zero, modifierFlags: key.flags,
                                           timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: window.windowNumber,
                                           context: nil, characters: "", charactersIgnoringModifiers: "", isARepeat: false, keyCode: key.code)
        else { return }
        web.flagsChanged(with: event)
    }

    func wheel(_ point: NSPoint, dx: Double, dy: Double) {
        let clamp = { (v: Double) in v.isFinite ? Int32(max(-100_000, min(100_000, -v.rounded()))) : 0 }
        guard let cg = CGEvent(scrollWheelEvent2Source: nil, units: .pixel, wheelCount: 2, wheel1: clamp(dy), wheel2: clamp(dx), wheel3: 0) else { return }
        // An NSEvent made from a CGEvent has no window, so its locationInWindow is the screen point
        // AppKit derives from the CG location; WebKit reads that as a point in the view's window.
        cg.location = CGPoint(x: point.x, y: (NSScreen.screens.first?.frame.height ?? 0) - point.y)
        if let event = NSEvent(cgEvent: cg) { web.scrollWheel(with: event) }
    }

    /// Runs a WebCore editing command (SelectAll, Copy, Cut, Paste) as the Edit menu would.
    func edit(_ command: String) {
        let selector = NSSelectorFromString("_executeEditCommand:argument:completion:")
        guard let method = web.method(for: selector) else { return }
        typealias Edit = @convention(c) (AnyObject, Selector, NSString, NSString?, (@convention(block) (Bool) -> Void)?) -> Void
        unsafeBitCast(method, to: Edit.self)(web, selector, command as NSString, nil, nil)
    }

    /// Runs `done` once WebKit has delivered every queued mouse or key event to the page.
    func afterInput(_ kind: String, _ done: @escaping () -> Void) {
        let operation = self.operation
        let guarded = { if operation == self.operation { done() } }
        let selector = NSSelectorFromString("_doAfterProcessingAllPending\(kind)Events:")
        guard web.responds(to: selector) else { return DispatchQueue.main.asyncAfter(deadline: .now() + 0.05, execute: guarded) }
        let block: @convention(block) () -> Void = guarded
        web.perform(selector, with: block)
    }

    /// A native click at a point the library reported.
    func click(_ info: [String: Any], button: String = "left", count: Int = 1, flags: NSEvent.ModifierFlags = []) {
        let point = windowPoint(info)
        let (down, up): (NSEvent.EventType, NSEvent.EventType) = button == "right" ? (.rightMouseDown, .rightMouseUp)
            : button == "middle" ? (.otherMouseDown, .otherMouseUp) : (.leftMouseDown, .leftMouseUp)
        mouse(.mouseMoved, point, flags: flags)
        for click in 1...max(1, count) {
            mouse(down, point, clicks: click, flags: flags)
            mouse(up, point, clicks: click, flags: flags)
        }
    }
}

// MARK: Keys

struct Key {
    let code: UInt16
    let chars: String
    let plain: String
    let flags: NSEvent.ModifierFlags
}

private let modifierKeys: [(NSEvent.ModifierFlags, UInt16)] = [(.command, 55), (.shift, 56), (.option, 58), (.control, 59)]

// US layout key codes; a shifted character is its base key with Shift.
private let keyCodes: [Character: UInt16] = [
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14,
    "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25, "7": 26, "-": 27,
    "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41,
    "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46, ".": 47, "`": 50, " ": 49,
]
private let shifted: [Character: Character] = [
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8", "(": "9", ")": "0", "_": "-", "+": "=",
    "{": "[", "}": "]", "|": "\\", ":": ";", "\"": "'", "<": ",", ">": ".", "?": "/", "~": "`",
]
private let namedKeys: [String: (UInt16, String, NSEvent.ModifierFlags)] = {
    var keys: [String: (UInt16, String, NSEvent.ModifierFlags)] = [
        "enter": (36, "\r", []), "return": (36, "\r", []), "tab": (48, "\t", []), "escape": (53, "\u{1b}", []), "esc": (53, "\u{1b}", []),
        "backspace": (51, "\u{7f}", []), "delete": (117, "\u{F728}", .function), "space": (49, " ", []),
        "arrowup": (126, "\u{F700}", [.function, .numericPad]), "arrowdown": (125, "\u{F701}", [.function, .numericPad]),
        "arrowleft": (123, "\u{F702}", [.function, .numericPad]), "arrowright": (124, "\u{F703}", [.function, .numericPad]),
        "home": (115, "\u{F729}", .function), "end": (119, "\u{F72B}", .function),
        "pageup": (116, "\u{F72C}", .function), "pagedown": (121, "\u{F72D}", .function),
    ]
    let fCodes: [UInt16] = [122, 120, 99, 118, 96, 97, 98, 100, 101, 109, 103, 111]
    for (i, code) in fCodes.enumerated() {
        keys["f\(i + 1)"] = (code, String(UnicodeScalar(0xF704 + i)!), .function)
    }
    return keys
}()

func characterKey(_ c: Character, flags: NSEvent.ModifierFlags = []) -> Key {
    var flags = flags
    var base = Character(c.lowercased())
    if let plain = shifted[c] { base = plain; flags.insert(.shift) } else if c.isUppercase { flags.insert(.shift) }
    let text = String(c)
    var chars = text
    if flags.contains(.control), let ascii = base.asciiValue, base.isLetter { chars = String(UnicodeScalar(ascii & 0x1f)) }
    return Key(code: keyCodes[base] ?? 0, chars: chars, plain: flags.contains(.shift) ? text : String(base), flags: flags)
}

/// "Enter", "a", "Shift+Tab", "Meta+a", "Control+Enter": a chord of modifiers and one key.
func parseKey(_ text: String) -> Key? {
    var parts = text.components(separatedBy: "+")
    if text.hasSuffix("+"), text.count > 1 { parts.removeLast(2); parts.append("+") }
    guard let name = parts.popLast(), !name.isEmpty else { return nil }
    var flags: NSEvent.ModifierFlags = []
    for part in parts {
        switch part.lowercased() {
        case "shift": flags.insert(.shift)
        case "control", "ctrl": flags.insert(.control)
        case "alt", "option", "opt": flags.insert(.option)
        case "meta", "command", "cmd", "controlormeta": flags.insert(.command)
        default: return nil
        }
    }
    if let (code, chars, extra) = namedKeys[name.lowercased()] {
        return Key(code: code, chars: chars, plain: chars, flags: flags.union(extra))
    }
    guard name.count == 1, let c = name.first else { return nil }
    return characterKey(c, flags: flags)
}

/// Keys that open a focused <select>'s menu or a date or color picker: a native popup would block the
/// daemon and show on the person's screen, so press checks what has focus before sending them.
func opensPicker(_ key: Key) -> Bool {
    [49, 36, 76, 125, 126].contains(key.code)  // Space, Return, Enter, ArrowDown, ArrowUp
}

/// Command-key chords a Mac app's Edit menu performs; a hidden, inactive app has no menu to do it,
/// so press runs the command itself when the page didn't prevent the key.
func editCommand(_ key: Key) -> String? {
    let flags = key.flags.intersection([.command, .shift, .option, .control])
    switch (key.code, flags) {
    case (0, [.command]): return "SelectAll"
    case (8, [.command]): return "Copy"
    case (7, [.command]): return "Cut"
    case (9, [.command]): return "Paste"
    default: return nil
    }
}

// MARK: Targets and outcomes

func targetParam(_ help: String = "Element: a ref from snapshot (e5 or @e5), text=Visible text, or a CSS selector") -> Param {
    Param(name: "target", kind: .string, help: help, required: true, positional: true)
}

private func withTab(_ engine: Engine, _ args: Args, _ reply: Reply) -> Tab? {
    switch engine.tab(args) {
    case .success(let tab): return tab
    case .failure(let error): reply(.failure(error)); return nil
    }
}

/// Runs `then` once a main-frame navigation already under way has finished (at most `limit`), so a
/// command never acts on a page that is about to go away.
func settled(_ tab: Tab, limit: Double = 5, _ then: @escaping () -> Void) {
    let operation = tab.operation
    guard tab.web.isLoading else { return then() }
    var done = false
    let once = { if !done, operation == tab.operation { done = true; then() } }
    tab.loading.append { _ in once() }
    DispatchQueue.main.asyncAfter(deadline: .now() + limit, execute: once)
}

/// Retries `prepare` until the target is actionable (attached, visible, enabled, in view, not
/// covered) or the deadline passes, then answers with where to act.
func locate(_ tab: Tab, _ args: [String: Any], until deadline: Date, done: @escaping (Result<[String: Any], UmbraError>) -> Void) {
    var tries = 0
    let started = Date(), operation = tab.operation
    func attempt() {
        guard operation == tab.operation else { return }
        tab.umbra("prepare", args) { result in
            tries += 1
            var why = "the page was changing"
            switch result {
            case .success(let info):
                switch info["status"] as? String {
                case "ok": return done(.success(info))
                case "error": return done(.failure(UmbraError(64, info["message"] as? String ?? "can't act on that")))
                default: why = info["message"] as? String ?? why
                }
            case .failure(let error): why = error.message
            }
            guard Date() < deadline else {
                return done(.failure(UmbraError(2, "timed out after \(String(format: "%g", (deadline.timeIntervalSince(started) * 10).rounded() / 10)) s: \(why)")))
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + (tries < 4 ? 0.03 : 0.1), execute: attempt)
        }
    }
    settled(tab, limit: min(5, max(0, deadline.timeIntervalSinceNow)), attempt)
}

func label(_ info: [String: Any]) -> String {
    "\(info["desc"] as? String ?? "element") (\(info["ref"] as? String ?? "?"))"
}

func deadlines(_ args: Args) -> (act: Date, total: Date) {
    let now = Date(), given = args.number("timeout").flatMap { $0.isFinite ? max(0, $0) : nil }
    return (now + (given ?? 10), now + (given ?? 30) - 0.3)
}

/// The page, tabs, dialogs and downloads before an action, to tell what it changed. `info` is what
/// the library said about the page (its URL, and an id that changes with the document).
struct Before {
    let navigations: Int, tabs: [Tab], dialogs: Int, downloads: Int, url: String, doc: String
    init(_ tab: Tab, _ engine: Engine, _ info: [String: Any] = [:]) {
        navigations = tab.navigations
        tabs = engine.tabs
        dialogs = tab.dialogs.count
        downloads = tab.downloads.count
        url = info["url"] as? String ?? ""
        doc = info["doc"] as? String ?? ""
    }
}

/// After native input: reads what the page observed, then waits for a navigation, pop-up or download
/// the input started (a navigate event, a new provisional load, a new tab), up to `deadline`.
func aftermath(_ tab: Tab, _ engine: Engine, _ before: Before, deadline: Date, done: @escaping ([String: Any]) -> Void) {
    let operation = tab.operation
    tab.umbra("observed") { result in
        var seen = (try? result.get()) ?? ["navigating": true]  // the call fails when the page is unloading
        var finished = false
        let started = Date()
        func finish(_ extra: [String: Any]) {
            guard operation == tab.operation else { return }
            guard !finished else { return }
            finished = true
            seen.merge(extra) { $1 }
            done(seen)
        }
        // A download link, or a navigation that left the same document in place (an attachment):
        // wait up to 3 s for the download to finish or fail.
        func downloads(_ extra: [String: Any]) {
            guard operation == tab.operation else { return }
            let stayed = extra["navigated"] as? Bool == true && extra["doc"] as? String == before.doc
            guard stayed || seen["download"] as? Bool == true || tab.downloads.count > before.downloads else { return finish(extra) }
            var extra = extra
            if stayed { extra["navigated"] = nil }
            let waited = Date()
            func check() {
                guard operation == tab.operation else { return }
                if tab.downloads.count > before.downloads {
                    return finish(extra.merging(["downloads": Array(tab.downloads.dropFirst(before.downloads))]) { $1 })
                }
                if Date().timeIntervalSince(waited) > 3 || Date() > deadline { return finish(extra.merging(["downloading": true]) { $1 }) }
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.05, execute: check)
            }
            check()
        }
        func page(_ t: Tab, _ extra: [String: Any]) {
            guard operation == tab.operation else { return }
            t.umbra("where") { result in
                let page = (try? result.get()) ?? [:]
                downloads(extra.merging(["title": page["title"] as? String ?? t.web.title ?? "", "url": page["url"] as? String ?? t.web.url?.absoluteString ?? "",
                                         "doc": page["doc"] ?? ""]) { $1 })
            }
        }
        func follow(_ t: Tab, _ extra: [String: Any]) {
            guard operation == tab.operation else { return }
            // A pop-up starts at about:blank before its own navigation begins.
            if !t.web.isLoading, t !== tab, (t.web.url?.absoluteString ?? "about:blank") == "about:blank",
               Date().timeIntervalSince(started) < 2, Date() < deadline {
                return DispatchQueue.main.asyncAfter(deadline: .now() + 0.01) { follow(t, extra) }
            }
            guard t.web.isLoading else { return page(t, extra) }
            t.loading.append { loaded in
                if case .failure(let error) = loaded { return finish(extra.merging(["error": error.message]) { $1 }) }
                page(t, extra)
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + max(0, deadline.timeIntervalSinceNow)) {
                if !finished { page(t, extra.merging(["loading": true]) { $1 }) }
            }
        }
        func poll() {
            guard operation == tab.operation else { return }
            if tab.navigations != before.navigations { return follow(tab, ["navigated": true]) }
            if let opened = engine.tabs.first(where: { t in !before.tabs.contains { $0 === t } }) {
                return follow(opened, ["newTab": opened.name])
            }
            let waiting = seen["navigating"] as? Bool == true || tab.web.isLoading
            if !waiting || Date().timeIntervalSince(started) > 2 || Date() > deadline { return downloads([:]) }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.01, execute: poll)
        }
        poll()
    }
}

/// The reply for an action: what was done, then where it led.
func outcome(_ tab: Tab, _ before: Before, _ did: String, _ fields: [String: Any]) -> [String: Any] {
    var result = fields
    var text = did
    let title = fields["title"] as? String ?? "", url = fields["url"] as? String ?? ""
    let page = "\(title.isEmpty ? "(no title)" : title) · \(url)"
    if let name = fields["newTab"] as? String {
        text += " → opened tab \(name): \(page) (switch with: tab select \(name))"
    } else if fields["navigated"] as? Bool == true {
        text += " → \(page)" + (fields["loading"] as? Bool == true ? " (still loading)" : "")
    } else if !before.url.isEmpty, !url.isEmpty, url != before.url {
        text += " → \(url)"
    }
    for download in fields["downloads"] as? [[String: Any]] ?? [] {
        if let path = download["path"] as? String {
            text += " → downloaded \((path as NSString).lastPathComponent) to \(path)"
        } else {
            text += " → download failed: \(download["error"] ?? "unknown error")"
        }
    }
    if fields["downloading"] as? Bool == true { text += " → a download is still in progress (see: downloads)" }
    if let error = fields["error"] as? String { text += "\n\(error)" }
    let dialogs = Array(tab.dialogs.dropFirst(before.dialogs))
    for dialog in dialogs {
        text += "\n\(dialog["type"] ?? "dialog") \"\(dialog["message"] ?? "")\" → \(dialog["accepted"] as? Bool == true ? "accepted" : "dismissed")"
    }
    if !dialogs.isEmpty { result["dialogs"] = dialogs }
    if let events = fields["events"] as? [String: Any], !events.isEmpty {
        result["trusted"] = events.values.allSatisfy { ($0 as? Bool) == true }
    }
    var receipt = fields["receipt"] as? [String: Any] ?? [:]
    receipt["urlBefore"] = before.url
    receipt["urlAfter"] = url.isEmpty ? tab.web.url?.absoluteString ?? "" : url
    receipt["navigation"] = fields["navigated"] as? Bool ?? false
    receipt["dialogs"] = dialogs.count
    receipt["downloads"] = (fields["downloads"] as? [Any])?.count ?? 0
    receipt["complete"] = fields["error"] == nil && fields["loading"] as? Bool != true && fields["downloading"] as? Bool != true
    result["receipt"] = receipt
    result["navigating"] = nil
    result["download"] = nil
    result["doc"] = nil
    result["events"] = nil
    result["status"] = nil
    result["text"] = text
    return result
}

func modifierFlags(_ text: String?) -> Result<NSEvent.ModifierFlags, UmbraError> {
    var flags: NSEvent.ModifierFlags = []
    for part in (text ?? "").components(separatedBy: ",").map({ $0.trimmingCharacters(in: .whitespaces) }) where !part.isEmpty {
        guard let key = parseKey(part + "+a") else { return .failure(UmbraError(64, "unknown modifier \(part); use Shift, Control, Alt or Meta")) }
        flags.formUnion(key.flags)
    }
    return .success(flags)
}

// MARK: Typing

/// Types into the element `info` names: insertText (an input method's commit) first, key events if
/// the page saw no input from that, and the value setter as a last resort. Replies with the path used.
func enter(_ text: String, _ tab: Tab, _ info: [String: Any], clear: Bool, clicked: Bool, done: @escaping (Result<[String: Any], UmbraError>) -> Void) {
    let ref = info["ref"] as? String ?? ""
    var start = ""  // what the field held, which type keeps
    let operation = tab.operation
    func check(_ method: String, retry: Bool, next: @escaping () -> Void) {
        guard operation == tab.operation else { return }
        tab.umbra("fillCheck", ["ref": ref]) { result in
            guard case .success(let state) = result, state["error"] == nil else {
                return done(.failure(UmbraError(2, (try? result.get())?["error"] as? String ?? "the field left the page")))
            }
            if state["input"] as? Bool == true || (clear && state["value"] as? String == text) {
                return done(.success(["method": method, "value": state["value"] ?? "", "trusted": state["trusted"] ?? false]))
            }
            if retry { return DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) { check(method, retry: false, next: next) } }
            next()
        }
    }
    func setter() {
        guard operation == tab.operation else { return }
        tab.umbra("setValue", ["ref": ref, "value": clear ? text : start + text]) { result in
            switch result {
            case .success(let state): done(.success(["method": "setter", "value": state["value"] ?? "", "trusted": false]))
            case .failure(let error): done(.failure(error))
            }
        }
    }
    func keys() {
        guard operation == tab.operation else { return }
        tab.umbra("fillStart", ["ref": ref, "clear": clear]) { _ in
            if text.isEmpty { tab.press(parseKey("Backspace")!) } else { text.forEach { tab.press(characterKey($0)) } }
            tab.afterInput("Key") { check("keys", retry: true, next: setter) }
        }
    }
    if info["kind"] as? String == "setter" { return setter() }
    tab.umbra("fillStart", ["ref": ref, "clear": clear, "end": clicked]) { result in
        guard case .success(let state) = result, state["error"] == nil else { return done(.failure(UmbraError(2, "the field left the page"))) }
        start = state["value"] as? String ?? ""
        if text.isEmpty {
            if !clear || start.isEmpty { return done(.success(["method": "none", "value": start])) }
            return keys()
        }
        tab.web.insertText(text)
        check("insertText", retry: true, next: keys)
    }
}

/// fill and type: focus the field with a native click unless it has focus, then enter the text.
func typeInto(_ tab: Tab, _ args: Args, target: String, text: String, clear: Bool, reply: @escaping Reply) {
    let (act, _) = deadlines(args)
    locate(tab, ["target": target, "action": clear ? "fill" : "type"], until: act) { located in
        guard case .success(let info) = located else { if case .failure(let e) = located { reply(.failure(e)) }; return }
        let start = { (clicked: Bool) in
            enter(text, tab, info, clear: clear, clicked: clicked) { result in
                switch result {
                case .success(let fields):
                    let method = fields["method"] as? String ?? ""
                    let value = fields["value"] as? String ?? ""
                    let shown = info["desc"] as? String ?? ""
                    let secret = info["password"] as? Bool == true
                    let said = secret ? "••••••" : "\"\(text)\""
                    let what = clear ? "filled \(label(info)) with \(said)" : "typed \(said) into \(label(info))"
                    var fields = fields
                    fields["ref"] = info["ref"]
                    fields["element"] = shown
                    if secret { fields["value"] = "••••••" }
                    fields["text"] = what + " via \(method)" + (clear && value != text && !secret ? "; the field now holds \"\(value)\"" : "")
                    reply(.success(fields))
                case .failure(let error): reply(.failure(error))
                }
            }
        }
        if info["focused"] as? Bool == true || info["kind"] as? String == "setter" { return start(false) }
        tab.click(info)
        tab.afterInput("Mouse") { start(true) }
    }
}

/// Actions on one tab run one at a time in arrival order: an agent's parallel tool calls must not
/// interleave their mouse and key events. A stuck action frees the tab after `limit` seconds.
final class ActionQueue {
    var busy = false
    var waiting: [() -> Void] = []
}

func serial(_ tab: Tab, limit: Double, _ reply: @escaping Reply, _ body: @escaping (@escaping Reply) -> Void) {
    let queue = tab.extras["actions"] as? ActionQueue ?? ActionQueue()
    tab.extras["actions"] = queue
    let operation = tab.operation
    let start = {
        guard operation == tab.operation, operation.map(tab.engine.runtime.isActive) ?? true else {
            reply(.failure(UmbraError(2, "request interrupted")))
            if !queue.waiting.isEmpty { queue.waiting.removeFirst()() }
            return
        }
        queue.busy = true
        var finished = false
        let next = {
            guard !finished else { return }
            finished = true
            queue.busy = false
            if !queue.waiting.isEmpty { queue.waiting.removeFirst()() }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + limit, execute: next)
        if let operation = tab.operation { tab.engine.runtime.onCancel(operation, next) }
        body { result in
            guard !finished else { return }
            reply(result); next()
        }
    }
    if queue.busy { queue.waiting.append(start) } else { start() }
}

/// Explicit form edits also include setter paths whose DOM events cannot be trusted input.
func formActionReply(_ tab: Tab, _ action: String, _ reply: @escaping Reply) -> Reply {
    guard ["fill", "type", "select", "check", "uncheck"].contains(action) else { return reply }
    let operation = tab.operation, navigations = tab.navigations
    return { result in
        guard operation == tab.operation else { return }
        if case .success(let fields) = result, fields["error"] == nil,
           action != "type" || fields["value"] != nil,
           tab.navigations == navigations, !tab.web.isLoading {
            tab.dirty = true
        }
        reply(result)
    }
}

// MARK: Commands

private let actions: Set = ["click", "fill", "type", "press", "select", "check", "uncheck", "hover", "scroll", "drag", "upload", "focus"]

func interactCommands() -> [Command] {
    tabSetups.append { tab in
        let scripts = tab.web.configuration.userContentController
        guard !scripts.userScripts.contains(where: { $0.source == interactLibrary }) else { return }  // pop-ups share it
        scripts.addUserScript(WKUserScript(source: navigateJS, injectionTime: .atDocumentStart, forMainFrameOnly: true, in: .page))
        scripts.addUserScript(WKUserScript(source: interactLibrary, injectionTime: .atDocumentStart, forMainFrameOnly: true, in: umbraWorld))
    }
    let commands = [
        Command(name: "snapshot", help: """
            Outline the page for reading and acting: one line per element with its role, name, state, value and ref. \
            Lines look like `- button "Sign in" [ref=e5]`, `- textbox "Email" [ref=e3] [required] value="a@b.c"`, \
            `- link "Help" [ref=e8] → /help`, `- heading "Welcome" [level=1]`, `- paragraph: text…`, indented by nesting. \
            Every interactive element gets a ref that the action commands take as their target; a ref stays the same for \
            the same element until the page navigates. Hidden elements are left out; open shadow roots and same-origin \
            iframes are included, cross-origin iframes are marked.
            """,
                params: [Param(name: "interactive", kind: .boolean, help: "Only elements with refs, plus headings: compact"),
                         Param(name: "scope", kind: .string, help: "Only this part of the page: a ref, text=…, or CSS selector"),
                         Param(name: "max", kind: .number, help: "Outline character budget, 1–100000 (default 25000)"),
                         Param(name: "depth", kind: .number, help: "Maximum outline nesting depth, 0–100 (default 100)")]) { args, engine, reply in
            guard let tab = withTab(engine, args, reply) else { return }
            let a: [String: Any]
            switch observationOptions(args) {
            case .success(let options): a = options
            case .failure(let error): return reply(.failure(error))
            }
            settled(tab) { tab.umbra("snapshot", a) { result in
                switch result {
                case .success(let value):
                    if let error = value["error"] as? String { return reply(.failure(UmbraError(2, error))) }
                    let title = value["title"] as? String ?? "", url = value["url"] as? String ?? ""
                    reply(.success(["text": "\(title.isEmpty ? "(no title)" : title) · \(url)\n\(value["text"] as? String ?? "")",
                                    "snapshot": value["text"] ?? "", "refs": value["refs"] ?? 0, "url": url, "title": title,
                                    "truncated": value["truncated"] ?? false, "depthLimited": value["depthLimited"] ?? false]))
                case .failure(let error): reply(.failure(error))
                }
            } }
        },
        Command(name: "click", help: """
            Click an element with a trusted native mouse click. First waits (up to --timeout, default 10 s) until it is \
            visible, enabled, scrolled into view and not covered; if something covers it, the error names the cover's ref. \
            When the click starts a navigation, waits for the new page and reports its title and URL; a link that opens a \
            new tab reports that tab's name. A dialog the click raises is answered as `dialog` set up (accept by default) \
            and reported. A <select> or a date input can't be clicked: use select or fill.
            """,
                params: [targetParam(),
                         Param(name: "button", kind: .string, help: "left (default), right or middle"),
                         Param(name: "count", kind: .number, help: "2 for a double click, 3 for a triple click"),
                         Param(name: "modifiers", kind: .string, help: "Keys held during the click, comma-separated: Shift,Meta,Alt,Control")],
                timeout: 30) { args, engine, reply in
            guard let tab = withTab(engine, args, reply) else { return }
            let button = args.string("button") ?? "left"
            guard ["left", "right", "middle"].contains(button) else { return reply(.failure(UmbraError(64, "--button takes left, right or middle"))) }
            let flags: NSEvent.ModifierFlags
            switch modifierFlags(args.string("modifiers")) { case .success(let f): flags = f; case .failure(let e): return reply(.failure(e)) }
            let count = max(1, min(3, Int(exactly: (args.number("count") ?? 1).rounded()) ?? 1))
            let (act, total) = deadlines(args)
            locate(tab, ["target": args.string("target") ?? "", "action": "click", "arm": button == "right" ? "right" : "click"], until: act) { located in
                guard case .success(let info) = located else { if case .failure(let e) = located { reply(.failure(e)) }; return }
                let before = Before(tab, engine, info)
                tab.click(info, button: button, count: count, flags: flags)
                tab.afterInput("Mouse") {
                    aftermath(tab, engine, before, deadline: total) { fields in
                        let verb = count == 3 ? "triple-clicked" : count == 2 ? "double-clicked" : button == "left" ? "clicked" : "\(button)-clicked"
                        var fields = fields
                        fields["ref"] = info["ref"]
                        fields["element"] = info["desc"]
                        if (fields["events"] as? [String: Any])?["click"] == nil, button == "left" {
                            fields["error"] = "note: no click event reached the page (the element may have moved or been replaced)"
                        }
                        reply(.success(outcome(tab, before, "\(verb) \(label(info))", fields)))
                    }
                }
            }
        },
        Command(name: "fill", help: """
            Replace what a text field, textarea or contenteditable holds with a value, typed as trusted input. Focuses it \
            with a native click, selects the old contents and inserts the value as a keyboard would, so React and other \
            framework-controlled fields see it. An empty value clears the field. Date, time, color and range inputs are \
            set directly. The result says which input path was used.
            """,
                params: [targetParam("Field: a ref (e3), text=Label, or a CSS selector; a label targets its control"),
                         Param(name: "value", kind: .string, help: "Text to put in the field", required: true, positional: true)],
                timeout: 30) { args, engine, reply in
            guard let tab = withTab(engine, args, reply) else { return }
            typeInto(tab, args, target: args.string("target") ?? "", text: args.string("value") ?? "", clear: true, reply: reply)
        },
        Command(name: "type", help: """
            Type text as trusted input without clearing the field first. Types into --target (focused with a native click, \
            caret at the end) or into whatever has focus; with nothing editable focused, the text goes out as key presses.
            """,
                params: [Param(name: "text", kind: .string, help: "Text to type", required: true, positional: true),
                         Param(name: "target", kind: .string, help: "Field to type into (default: the focused element)")],
                timeout: 30) { args, engine, reply in
            guard let tab = withTab(engine, args, reply) else { return }
            let text = args.string("text") ?? ""
            if let target = args.string("target") { return typeInto(tab, args, target: target, text: text, clear: false, reply: reply) }
            tab.umbra("focused") { result in
                let info = (try? result.get()) ?? [:]
                if info["picker"] as? Bool == true {
                    return reply(.failure(UmbraError(64, "\(label(info)) has focus, and typing into it could open a native popup; use select or fill instead")))
                }
                guard info["editable"] as? Bool == true else {
                    text.forEach { tab.press(characterKey($0)) }
                    return tab.afterInput("Key") {
                        reply(.success(["method": "keys", "text": "typed \"\(text)\" as key presses" + (info["ref"] != nil ? " to \(label(info))" : " (nothing editable has focus)")]))
                    }
                }
                enter(text, tab, info, clear: false, clicked: false) { entered in
                    switch entered {
                    case .success(let fields):
                        reply(.success(fields.merging(["ref": info["ref"] ?? "", "text": "typed \"\(text)\" into \(label(info)) via \(fields["method"] ?? "")"]) { $1 }))
                    case .failure(let error): reply(.failure(error))
                    }
                }
            }
        },
        Command(name: "press", help: """
            Press keys or chords as trusted keyboard input, in order, on the focused element (or --target, focused first). \
            Keys: Enter, Tab, Escape, Backspace, Delete, ArrowUp/Down/Left/Right, Home, End, PageUp, PageDown, Space, \
            F1–F12 or a single character; chords: Meta+a, Shift+Tab, Control+Enter. Meta+a/c/x/v select all, copy, cut \
            and paste unless the page handles them. Tab moves as in Safari, between text fields and menus; Alt+Tab also \
            reaches buttons, checkboxes and links. Waits for a navigation the keys start (Enter submitting a form).
            """,
                params: [Param(name: "keys", kind: .array, help: "Keys or chords, pressed one after another", required: true, positional: true),
                         Param(name: "target", kind: .string, help: "Element to focus first")],
                timeout: 30) { args, engine, reply in
            guard let tab = withTab(engine, args, reply) else { return }
            var keys: [Key] = []
            for name in args.strings("keys") {
                guard let key = parseKey(name) else { return reply(.failure(UmbraError(64, "unknown key \(name); use names like Enter, Tab, ArrowDown, a, Meta+a"))) }
                keys.append(key)
            }
            let (act, total) = deadlines(args)
            let go = { (info: [String: Any]) in
                let before = Before(tab, engine, info)
                var rest = keys[...]
                // Plain keys go out together; an editing chord waits to see whether the page took it, and
                // a key that could open a native picker first checks what has focus.
                func next(_ finished: @escaping () -> Void) {
                    while let key = rest.first {
                        if opensPicker(key) {
                            return tab.afterInput("Key") {
                                tab.umbra("focused") { result in
                                    let now = (try? result.get()) ?? [:]
                                    if now["picker"] as? Bool == true {
                                        return reply(.failure(UmbraError(64, "\(label(now)) has focus, and that key would open a native popup; use select or fill instead")))
                                    }
                                    rest.removeFirst()
                                    tab.press(key)
                                    next(finished)
                                }
                            }
                        }
                        rest.removeFirst()
                        tab.press(key)
                        guard let command = editCommand(key) else { continue }
                        return tab.afterInput("Key") {
                            tab.umbra("keyPrevented") { result in
                                if (try? result.get())?["prevented"] as? Bool != true { tab.edit(command) }
                                next(finished)
                            }
                        }
                    }
                    tab.afterInput("Key", finished)
                }
                next {
                    aftermath(tab, engine, before, deadline: total) { fields in
                        var fields = fields
                        if info["ref"] != nil { fields["ref"] = info["ref"]; fields["element"] = info["desc"] }
                        let on = info["ref"] != nil ? " on \(label(info))" : ""
                        reply(.success(outcome(tab, before, "pressed \(args.strings("keys").joined(separator: " "))\(on)", fields)))
                    }
                }
            }
            if let target = args.string("target") {
                return locate(tab, ["target": target, "action": "focus", "arm": "keys"], until: act) { located in
                    switch located { case .success(let info): go(info); case .failure(let e): reply(.failure(e)) }
                }
            }
            tab.umbra("armKeys") { result in go((try? result.get()) ?? [:]) }
        },
        Command(name: "select", help: """
            Choose options in a <select> by value or visible label (several for a multi-select), then fire input and change. \
            A custom (ARIA) combobox or listbox is not a <select>: click it, then click the option.
            """,
                params: [targetParam("The <select>: a ref, text=Label, or a CSS selector"),
                         Param(name: "values", kind: .array, help: "Option values or labels", required: true, positional: true)],
                timeout: 30) { args, engine, reply in
            guard let tab = withTab(engine, args, reply) else { return }
            locate(tab, ["target": args.string("target") ?? "", "action": "select"], until: deadlines(args).act) { located in
                guard case .success(let info) = located else { if case .failure(let e) = located { reply(.failure(e)) }; return }
                tab.umbra("selectOptions", ["ref": info["ref"] ?? "", "values": args.strings("values")]) { result in
                    guard case .success(let chosen) = result else { if case .failure(let e) = result { reply(.failure(e)) }; return }
                    if let error = chosen["error"] as? String { return reply(.failure(UmbraError(64, error))) }
                    let labels = (chosen["selected"] as? [String] ?? []).map { "\"\($0)\"" }.joined(separator: ", ")
                    reply(.success(chosen.merging(["ref": info["ref"] ?? "", "text": "selected \(labels) in \(label(info))"]) { $1 }))
                }
            }
        },
        checkCommand("check", true),
        checkCommand("uncheck", false),
        Command(name: "hover", help: "Move the mouse over an element (a native mouse move), for menus and tooltips that open on hover.",
                params: [targetParam()], timeout: 30) { args, engine, reply in
            guard let tab = withTab(engine, args, reply) else { return }
            locate(tab, ["target": args.string("target") ?? "", "action": "hover", "arm": "hover"], until: deadlines(args).act) { located in
                guard case .success(let info) = located else { if case .failure(let e) = located { reply(.failure(e)) }; return }
                tab.mouse(.mouseMoved, tab.windowPoint(info))
                tab.afterInput("Mouse") {
                    tab.umbra("observed") { result in
                        let seen = (try? result.get()) ?? [:]
                        let fields = outcome(tab, Before(tab, engine), "hovering over \(label(info))", seen.merging(["ref": info["ref"] ?? "", "element": info["desc"] ?? "", "x": info["x"] ?? 0, "y": info["y"] ?? 0]) { $1 })
                        reply(.success(fields))
                    }
                }
            }
        },
        Command(name: "scroll", help: """
            Scroll the page or a scrollable element with native mouse-wheel input. --dy scrolls down by that many pixels \
            (negative: up) and --dx right, over --target or the middle of the page; --to top|bottom jumps there; --target \
            alone scrolls that element into view. With no options, scrolls down most of a screen. Reports the position.
            """,
                params: [Param(name: "target", kind: .string, help: "Element to scroll over, or to bring into view"),
                         Param(name: "dx", kind: .number, help: "Pixels to scroll right (negative: left)"),
                         Param(name: "dy", kind: .number, help: "Pixels to scroll down (negative: up)"),
                         Param(name: "to", kind: .string, help: "top or bottom")],
                timeout: 30) { args, engine, reply in
            guard let tab = withTab(engine, args, reply) else { return }
            let to = args.string("to")
            if let to, !["top", "bottom"].contains(to) { return reply(.failure(UmbraError(64, "--to takes top or bottom"))) }
            let report = { (state: [String: Any], did: String) in
                let what = state["what"] as? String ?? "page"
                reply(.success(state.merging(["text": "\(did) \(what): at y=\(state["y"] ?? 0) of \(state["height"] ?? 0) (view \(state["view"] ?? 0)), x=\(state["x"] ?? 0)"]) { $1 }))
            }
            let wheel = { (info: [String: Any]?) in
                let ref = info?["ref"] as? String
                tab.umbra("scrollState", ref.map { ["ref": $0] } ?? [:]) { start in
                    let startY = (try? start.get())?["y"] as? Int, startX = (try? start.get())?["x"] as? Int
                    let point = info.map(tab.windowPoint) ?? NSPoint(x: tab.web.bounds.midX, y: tab.web.bounds.midY)
                    let view = (try? start.get())?["view"] as? Double ?? Double(tab.web.bounds.height)
                    let dy = args.number("dy") ?? (args.number("dx") == nil ? view * 0.8 : 0)
                    tab.wheel(point, dx: args.number("dx") ?? 0, dy: dy)
                    // Wheel scrolling settles asynchronously: watch until the position holds still.
                    var last: (Int?, Int?) = (startY, startX), still = 0, polls = 0
                    func watch() {
                        tab.umbra("scrollState", ref.map { ["ref": $0] } ?? [:]) { result in
                            let state = (try? result.get()) ?? [:]
                            let now = (state["y"] as? Int, state["x"] as? Int)
                            polls += 1
                            still = now == last ? still + 1 : 0
                            last = now
                            // Done once it holds still after moving, or never moves (already at the end).
                            if still >= 2 && (now != (startY, startX) || polls > 8) || polls > 40 { return report(state, "scrolled") }
                            let operation = tab.operation
                            DispatchQueue.main.asyncAfter(deadline: .now() + 0.015) { if operation == tab.operation { watch() } }
                        }
                    }
                    watch()
                }
            }
            guard let target = args.string("target") else {
                if let to { return tab.umbra("scrollState", ["to": to]) { result in report((try? result.get()) ?? [:], "scrolled") } }
                return wheel(nil)
            }
            locate(tab, ["target": target, "action": "scroll", "hitTest": false], until: deadlines(args).act) { located in
                guard case .success(let info) = located else { if case .failure(let e) = located { reply(.failure(e)) }; return }
                if let to { return tab.umbra("scrollState", ["ref": info["ref"] ?? "", "to": to]) { result in report((try? result.get()) ?? [:], "scrolled") } }
                if args.number("dx") == nil && args.number("dy") == nil {
                    return tab.umbra("scrollState", ["ref": info["ref"] ?? "", "reveal": true]) { result in
                        report(((try? result.get()) ?? [:]).merging(["ref": info["ref"] ?? ""]) { $1 }, "brought \(label(info)) into view;")
                    }
                }
                wheel(info)
            }
        },
        Command(name: "drag", help: """
            Drag one element onto another. Uses a native mouse press, moves and release (sliders, sortable lists, \
            canvases); for HTML5 drag and drop (draggable elements, links, images) it dispatches the drag events in the \
            page instead, since a native drag session would act on the real desktop, and those events are untrusted.
            """,
                params: [Param(name: "from", kind: .string, help: "Element to drag: a ref, text=…, or CSS", required: true, positional: true),
                         Param(name: "to", kind: .string, help: "Element to drop on: a ref, text=…, or CSS", required: true, positional: true)],
                timeout: 30) { args, engine, reply in
            guard let tab = withTab(engine, args, reply) else { return }
            let act = deadlines(args).act
            locate(tab, ["target": args.string("from") ?? "", "action": "drag"], until: act) { from in
                guard case .success(let source) = from else { if case .failure(let e) = from { reply(.failure(e)) }; return }
                locate(tab, ["target": args.string("to") ?? "", "action": "drop", "hitTest": false], until: act) { to in
                    guard case .success(let target) = to else { if case .failure(let e) = to { reply(.failure(e)) }; return }
                    let did = "dragged \(label(source)) to \(label(target))"
                    if source["draggable"] as? Bool == true {
                        return tab.umbra("syntheticDrag", ["from": source["ref"] ?? "", "to": target["ref"] ?? ""]) { result in
                            let accepted = (try? result.get())?["accepted"] as? Bool == true
                            reply(.success(["method": "synthetic", "accepted": accepted, "trusted": false,
                                            "text": did + " with HTML5 drag events" + (accepted ? "" : "; the target did not accept the drop")]))
                        }
                    }
                    let a = tab.windowPoint(source), b = tab.windowPoint(target)
                    tab.mouse(.mouseMoved, a)
                    tab.mouse(.leftMouseDown, a)
                    var step = 0
                    func move() {
                        step += 1
                        let t = Double(step) / 8
                        tab.mouse(.leftMouseDragged, NSPoint(x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t), held: true)
                        if step < 8 { return tab.afterInput("Mouse", move) }
                        tab.mouse(.leftMouseUp, b)
                        tab.afterInput("Mouse") { reply(.success(["method": "native", "trusted": true, "text": did + " with native mouse input"])) }
                    }
                    tab.afterInput("Mouse", move)
                }
            }
        },
        Command(name: "upload", help: """
            Upload files through a file input. Clicks the target (an <input type=file>, its label, or the button that \
            opens the file chooser) natively and answers the chooser with the files; a hidden input that nothing visible \
            opens gets the files set directly, with input and change events.
            """,
                params: [targetParam("File input, its label, or the button that opens the chooser"),
                         Param(name: "files", kind: .array, help: "Paths of files to upload", required: true, positional: true)],
                timeout: 30) { args, engine, reply in
            guard let tab = withTab(engine, args, reply) else { return }
            // Relative paths mean the caller's directory.
            let files = args.strings("files").compactMap { file -> URL? in
                var values: [String: Any] = ["file": file]
                values["cwd"] = args.values["cwd"]
                return Args(values: values).path("file")
            }
            if let missing = files.first(where: { !FileManager.default.isReadableFile(atPath: $0.path) }) {
                return reply(.failure(UmbraError(64, "can't read \(missing.path)")))
            }
            let (act, total) = deadlines(args)
            locate(tab, ["target": args.string("target") ?? "", "action": "upload", "arm": "upload"], until: act) { located in
                guard case .success(let info) = located else { if case .failure(let e) = located { reply(.failure(e)) }; return }
                let done = { (got: [[String: Any]], method: String) in
                    let names = got.map { "\($0["name"] ?? "") (\($0["size"] ?? 0) bytes)" }.joined(separator: ", ")
                    reply(.success(["files": got, "method": method, "ref": info["ref"] ?? "",
                                    "text": "uploaded \(names) to \(label(info)) via \(method)"]))
                }
                let direct = {
                    guard let input = info["input"] as? String else {
                        return reply(.failure(UmbraError(2, "clicking \(label(info)) opened no file chooser; target the <input type=file> or the button that opens it")))
                    }
                    var payload: [[String: Any]] = []
                    for file in files {
                        guard let data = try? Data(contentsOf: file) else { return reply(.failure(UmbraError(1, "can't read \(file.path)"))) }
                        let type = UTType(filenameExtension: file.pathExtension)?.preferredMIMEType ?? "application/octet-stream"
                        payload.append(["name": file.lastPathComponent, "type": type, "data": data.base64EncodedString()])
                    }
                    tab.umbra("setFiles", ["ref": input, "files": payload]) { result in
                        guard case .success(let set) = result, set["error"] == nil else { return reply(.failure(UmbraError(2, "could not set the files"))) }
                        done(set["files"] as? [[String: Any]] ?? [], "a DataTransfer (the input is hidden; events are untrusted)")
                    }
                }
                if info["hidden"] as? Bool == true { return direct() }
                tab.uploads = files
                tab.click(info)
                tab.afterInput("Mouse") {
                    var polls = 0
                    func watch() {
                        tab.umbra("filesOf", ["ref": info["input"] ?? ""]) { result in
                            let state = (try? result.get()) ?? [:]
                            let seen = state["seen"] as? [[String: Any]], got = seen ?? state["files"] as? [[String: Any]] ?? []
                            polls += 1
                            // The chooser has answered once uploads is empty; the page has the files at its change event.
                            if tab.uploads.isEmpty, seen != nil || (polls > 30 && !got.isEmpty) { return done(got, "the file chooser") }
                            if polls > 60 || Date() > total {
                                let opened = tab.uploads.isEmpty
                                tab.uploads = []
                                return opened ? reply(.failure(UmbraError(2, "the file chooser opened but the page received no files"))) : direct()
                            }
                            let operation = tab.operation
                            DispatchQueue.main.asyncAfter(deadline: .now() + (tab.uploads.isEmpty ? 0.02 : 0.03)) { if operation == tab.operation { watch() } }
                        }
                    }
                    watch()
                }
            }
        },
        Command(name: "dialog", help: """
            Set how the next alert, confirm or prompt is answered, and list the dialogs this tab has seen. Run it before \
            the action that raises the dialog. Dialogs never block: by default each is accepted, a prompt with its \
            default text.
            """,
                params: [Param(name: "action", kind: .string, help: "accept or dismiss (omit to only list the dialogs seen)", positional: true),
                         Param(name: "text", kind: .string, help: "Text to answer a prompt with")]) { args, engine, reply in
            guard let tab = withTab(engine, args, reply) else { return }
            var text = ""
            switch args.string("action") {
            case "accept": tab.nextDialog = (true, args.string("text")); text = "the next dialog will be accepted" + (args.string("text").map { " with \"\($0)\"" } ?? "")
            case "dismiss": tab.nextDialog = (false, nil); text = "the next dialog will be dismissed"
            case nil: text = tab.nextDialog.map { "the next dialog will be \($0.accept ? "accepted" : "dismissed")" } ?? "dialogs are accepted by default"
            default: return reply(.failure(UmbraError(64, "dialog takes accept or dismiss")))
            }
            let seen = tab.dialogs.map { "  \($0["type"] ?? "") \"\($0["message"] ?? "")\" → \($0["accepted"] as? Bool == true ? "accepted" : "dismissed")" }
            reply(.success(["dialogs": tab.dialogs, "text": text + (seen.isEmpty ? "\nno dialogs yet" : "\nseen:\n" + seen.joined(separator: "\n"))]))
        },
        Command(name: "focus", help: "Move keyboard focus to an element (for press or type without --target).",
                params: [targetParam()], timeout: 30) { args, engine, reply in
            guard let tab = withTab(engine, args, reply) else { return }
            locate(tab, ["target": args.string("target") ?? "", "action": "focus"], until: deadlines(args).act) { located in
                switch located {
                case .success(let info):
                    let ok = info["focused"] as? Bool == true
                    reply(.success(["ref": info["ref"] ?? "", "focused": ok, "text": (ok ? "focused " : "could not focus ") + label(info)]))
                case .failure(let error): reply(.failure(error))
                }
            }
        },
        Command(name: "get", help: """
            Read one element for a quick check: prints its text, or a field's value. --attr NAME prints that attribute \
            and --attr box prints x,y,width,height in the viewport; the structured result also has its role, name, \
            visibility and state.
            """,
                params: [targetParam(),
                         Param(name: "attr", kind: .string, help: "An attribute name, or box")],
                timeout: 30) { args, engine, reply in
            guard let tab = withTab(engine, args, reply) else { return }
            let attr = args.string("attr")
            var a: [String: Any] = ["target": args.string("target") ?? ""]
            if let attr, attr != "box" { a["attr"] = attr }
            let deadline = deadlines(args).act, operation = tab.operation
            func attempt() {
                guard operation == tab.operation else { return }
                tab.umbra("get", a) { result in
                    let info = (try? result.get()) ?? [:]
                    switch info["status"] as? String {
                    case "ok":
                        var out = info
                        out["status"] = nil
                        let box = info["box"] as? [String: Any] ?? [:]
                        switch attr {
                        case "box": out["text"] = "\(box["x"] ?? 0),\(box["y"] ?? 0),\(box["width"] ?? 0),\(box["height"] ?? 0)"
                        case let name?: out["text"] = info["attr"] as? String ?? "(no \(name) attribute)"
                        case nil: out["text"] = info["value"] as? String ?? info["text"] as? String ?? ""
                        }
                        reply(.success(out))
                    case "error": reply(.failure(UmbraError(64, info["message"] as? String ?? "bad target")))
                    default:
                        guard Date() < deadline else { return reply(.failure(UmbraError(2, info["message"] as? String ?? "not found"))) }
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.1, execute: attempt)
                    }
                }
            }
            attempt()
        },
    ]
    return commands.map { c in
        guard actions.contains(c.name) else { return c }
        return Command(name: c.name, help: c.help, params: c.params + [Param(name: "receipt", kind: .boolean, help: "Capture detailed target state before and after (two additional script calls)")], aliases: c.aliases, usesTab: c.usesTab, mcp: c.mcp, timeout: c.timeout) { args, engine, reply in
            guard case .success(let tab) = engine.tab(args) else { return c.run(args, engine, reply) }
            let limit = args.number("timeout").flatMap { $0.isFinite ? max(0, $0) : nil } ?? c.timeout
            serial(tab, limit: limit + 1, reply) { done in
                let done = formActionReply(tab, c.name, done)
                if !args.bool("receipt") {
                    return c.run(args, engine) { result in
                        guard case .success(var value) = result else { return done(result) }
                        if value["receipt"] == nil {
                            var receipt: [String: Any] = ["action": c.name, "url": tab.web.url?.absoluteString ?? ""]
                            for key in ["ref", "value", "checked", "selected", "files", "trusted", "focused", "method"] { receipt[key] = value[key] }
                            value["receipt"] = receipt
                        }
                        done(.success(value))
                    }
                }
                tab.umbra("receipt", ["target": args.string("target") ?? ""]) { captured in
                    let before = (try? captured.get()) ?? [:]
                    c.run(args, engine) { result in
                        guard case .success(var value) = result else { return done(result) }
                        let state = before["state"] as? [String: Any] ?? [:]
                        tab.umbra("receipt", ["target": state["ref"] as? String ?? args.string("target") ?? "", "document": before["document"] as? String ?? ""]) { captured in
                            let after = (try? captured.get()) ?? [:]
                            var receipt = value["receipt"] as? [String: Any] ?? [:]
                            receipt["before"] = before["state"] ?? NSNull()
                            receipt["after"] = after["state"] ?? NSNull()
                            receipt["urlBefore"] = before["url"] ?? ""
                            receipt["urlAfter"] = after["url"] ?? ""
                            receipt["documentChanged"] = (before["document"] as? String) != (after["document"] as? String)
                            receipt["changed"] = !NSDictionary(dictionary: before).isEqual(to: after)
                            receipt["observed"] = !before.isEmpty && !after.isEmpty
                            value["receipt"] = receipt
                            done(.success(value))
                        }
                    }
                }
            }
        }
    }
}

/// check and uncheck: click only when the state differs, then confirm it changed.
func checkCommand(_ name: String, _ want: Bool) -> Command {
    Command(name: name, help: want
            ? "Make a checkbox, radio button or switch checked: clicks it natively only if it isn't, then verifies. A label works as the target."
            : "Make a checkbox or switch unchecked: clicks it natively only if it is checked, then verifies.",
            params: [targetParam()], timeout: 30) { args, engine, reply in
        guard let tab = withTab(engine, args, reply) else { return }
        let (act, total) = deadlines(args)
        locate(tab, ["target": args.string("target") ?? "", "action": name, "arm": "click"], until: act) { located in
            guard case .success(let info) = located else { if case .failure(let e) = located { reply(.failure(e)) }; return }
            if info["checked"] as? Bool == want {
                tab.umbra("observed") { _ in }
                return reply(.success(["ref": info["ref"] ?? "", "checked": want, "changed": false, "text": "\(label(info)) was already \(name)ed"]))
            }
            if !want, (info["desc"] as? String ?? "").hasPrefix("radio") {
                return reply(.failure(UmbraError(64, "\(label(info)) is a radio button: check another option instead")))
            }
            let before = Before(tab, engine, info)
            tab.click(info)
            tab.afterInput("Mouse") {
                aftermath(tab, engine, before, deadline: total) { fields in
                    tab.umbra("stateOf", ["ref": info["ref"] ?? ""]) { result in
                        let now = (try? result.get())?["checked"] as? Bool
                        guard now == want || fields["navigated"] as? Bool == true else {
                            return reply(.failure(UmbraError(2, "clicked \(label(info)) but it is still \(want ? "unchecked" : "checked")")))
                        }
                        var fields = fields
                        fields["ref"] = info["ref"]
                        fields["checked"] = want
                        fields["changed"] = true
                        reply(.success(outcome(tab, before, "\(name)ed \(label(info))", fields)))
                    }
                }
            }
        }
    }
}
