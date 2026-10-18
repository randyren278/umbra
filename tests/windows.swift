// For each line of process ids on stdin, prints one line of JSON: the displays and every window
// those processes own, as the window server sees them. The suite keeps one running and uses it to
// prove Umbra's windows stay invisible. "quit PID" instead sends that app Quit, as the Dock does,
// and prints {"sent": true|false}. "press PID TITLE" presses the app's button with that title (or
// label, as a toolbar button has) through accessibility (as a person clicking it) and prints
// {"trusted": …, "pressed": …}. "texts PID" prints {"trusted": …, "items": [[role, text], …]}: the
// static texts and buttons of the app's windows, in order. "pick PID LABEL<tab>ITEM" opens the
// pull-down button with that accessibility label, picks the menu item with that title and prints
// {"trusted": …, "picked": …, "checked": …} (whether the item had a checkmark). These need this
// process's terminal to have the Accessibility permission.
import AppKit
import ApplicationServices

func button(_ element: AXUIElement, titled title: String) -> AXUIElement? {
    var role: CFTypeRef?, name: CFTypeRef?, label: CFTypeRef?, children: CFTypeRef?
    AXUIElementCopyAttributeValue(element, kAXRoleAttribute as CFString, &role)
    AXUIElementCopyAttributeValue(element, kAXTitleAttribute as CFString, &name)
    AXUIElementCopyAttributeValue(element, kAXDescriptionAttribute as CFString, &label)
    if (role as? String) == "AXButton", (name as? String) == title || (label as? String) == title { return element }
    if (role as? String) == "AXWebArea" { return nil }  // the page's own buttons aren't Umbra's
    AXUIElementCopyAttributeValue(element, kAXChildrenAttribute as CFString, &children)
    for child in (children as? [AXUIElement]) ?? [] {
        if let found = button(child, titled: title) { return found }
    }
    return nil
}

func attribute(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
    var value: CFTypeRef?
    AXUIElementCopyAttributeValue(element, name as CFString, &value)
    return value
}

func children(_ element: AXUIElement) -> [AXUIElement] {
    (attribute(element, kAXRoleAttribute) as? String) == "AXWebArea" ? [] : attribute(element, kAXChildrenAttribute) as? [AXUIElement] ?? []
}

func find(_ element: AXUIElement, _ match: (AXUIElement) -> Bool) -> AXUIElement? {
    if match(element) { return element }
    for child in children(element) {
        if let found = find(child, match) { return found }
    }
    return nil
}

func controls(_ element: AXUIElement, into list: inout [[String]]) {
    let role = attribute(element, kAXRoleAttribute) as? String ?? ""
    if role == "AXStaticText" { list.append([role, attribute(element, kAXValueAttribute) as? String ?? ""]) }
    if ["AXButton", "AXPopUpButton", "AXMenuButton"].contains(role) {
        let title = attribute(element, kAXTitleAttribute) as? String ?? ""
        list.append([role, title.isEmpty ? attribute(element, kAXDescriptionAttribute) as? String ?? "" : title])
    }
    for child in children(element) { controls(child, into: &list) }
}
import CoreGraphics
import Foundation

while let line = readLine() {
    if line.hasPrefix("press ") {
        let parts = line.split(separator: " ", maxSplits: 2).map(String.init)
        let trusted = AXIsProcessTrusted()
        var pressed = false
        if trusted, parts.count == 3, let pid = Int32(parts[1]), let target = button(AXUIElementCreateApplication(pid), titled: parts[2]) {
            pressed = AXUIElementPerformAction(target, kAXPressAction as CFString) == .success
        }
        print("{\"trusted\": \(trusted), \"pressed\": \(pressed)}")
        fflush(stdout)
        continue
    }
    if line.hasPrefix("texts ") {
        let trusted = AXIsProcessTrusted()
        var items: [[String]] = []
        if trusted, let pid = Int32(line.dropFirst(6)) {
            for window in attribute(AXUIElementCreateApplication(pid), kAXWindowsAttribute) as? [AXUIElement] ?? [] {
                controls(window, into: &items)
            }
        }
        let answer: [String: Any] = ["trusted": trusted, "items": items]
        print(String(decoding: try! JSONSerialization.data(withJSONObject: answer), as: UTF8.self))
        fflush(stdout)
        continue
    }
    if line.hasPrefix("pick ") {
        let parts = line.dropFirst(5).split(separator: " ", maxSplits: 1).map(String.init)
        let names = parts.count == 2 ? parts[1].components(separatedBy: "\t") : []
        let trusted = AXIsProcessTrusted()
        var picked = false, checked = false
        if trusted, names.count == 2, let pid = Int32(parts[0]),
           let pull = find(AXUIElementCreateApplication(pid), { (attribute($0, kAXDescriptionAttribute) as? String) == names[0] }) {
            AXUIElementPerformAction(pull, kAXPressAction as CFString)
            var item: AXUIElement?
            for _ in 0..<40 where item == nil {  // the menu opens on the app's next turn
                item = find(pull) { (attribute($0, kAXRoleAttribute) as? String) == "AXMenuItem" && (attribute($0, kAXTitleAttribute) as? String) == names[1] }
                if item == nil { usleep(50_000) }
            }
            if let item {
                checked = !((attribute(item, kAXMenuItemMarkCharAttribute) as? String) ?? "").isEmpty
                picked = AXUIElementPerformAction(item, kAXPressAction as CFString) == .success
            } else if let menu = find(pull, { (attribute($0, kAXRoleAttribute) as? String) == "AXMenu" }) {
                AXUIElementPerformAction(menu, kAXCancelAction as CFString)
            }
        }
        print("{\"trusted\": \(trusted), \"picked\": \(picked), \"checked\": \(checked)}")
        fflush(stdout)
        continue
    }
    if line.hasPrefix("quit "), let pid = Int32(line.dropFirst(5)) {
        let sent = NSRunningApplication(processIdentifier: pid)?.terminate() ?? false
        print(sent ? "{\"sent\": true}" : "{\"sent\": false}")
        fflush(stdout)
        continue
    }
    let pids = Set(line.split(separator: " ").compactMap { Int32($0) })
    var ids = [CGDirectDisplayID](repeating: 0, count: 32)
    var count: UInt32 = 0
    CGGetActiveDisplayList(32, &ids, &count)
    let screens = ids.prefix(Int(count)).map { CGDisplayBounds($0) }
    let all = CGWindowListCopyWindowInfo([.optionAll], kCGNullWindowID) as? [[String: Any]] ?? []
    let windows = all.filter { pids.contains(($0[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value ?? -1) }.map { info -> [String: Any] in
        var bounds = CGRect.zero
        if let dictionary = info[kCGWindowBounds as String] as? NSDictionary {
            CGRectMakeWithDictionaryRepresentation(dictionary, &bounds)
        }
        return ["id": info[kCGWindowNumber as String] ?? 0,
                "pid": info[kCGWindowOwnerPID as String] ?? 0,
                "layer": info[kCGWindowLayer as String] ?? 0,
                "alpha": info[kCGWindowAlpha as String] ?? 1,
                "onscreen": info[kCGWindowIsOnscreen as String] ?? false,
                "bounds": [bounds.origin.x, bounds.origin.y, bounds.width, bounds.height],
                "visibleArea": screens.map { $0.intersection(bounds) }.filter { !$0.isNull }.map { $0.width * $0.height }.reduce(0, +)]
    }
    let report: [String: Any] = ["screens": screens.map { [$0.origin.x, $0.origin.y, $0.width, $0.height] }, "windows": windows]
    print(String(decoding: try! JSONSerialization.data(withJSONObject: report, options: [.sortedKeys]), as: UTF8.self))
    fflush(stdout)
}
