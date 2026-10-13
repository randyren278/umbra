// For each line of process ids on stdin, prints one line of JSON: the displays and every window
// those processes own, as the window server sees them. The suite keeps one running and uses it to
// prove Umbra's windows stay invisible. "quit PID" instead sends that app Quit, as the Dock does,
// and prints {"sent": true|false}. "press PID TITLE" presses the app's button with that title through
// accessibility (as a person clicking it) and prints {"trusted": …, "pressed": …}; it needs this
// process's terminal to have the Accessibility permission.
import AppKit
import ApplicationServices

func button(_ element: AXUIElement, titled title: String) -> AXUIElement? {
    var role: CFTypeRef?, name: CFTypeRef?, children: CFTypeRef?
    AXUIElementCopyAttributeValue(element, kAXRoleAttribute as CFString, &role)
    AXUIElementCopyAttributeValue(element, kAXTitleAttribute as CFString, &name)
    if (role as? String) == "AXButton", (name as? String) == title { return element }
    if (role as? String) == "AXWebArea" { return nil }  // the page's own buttons aren't Umbra's
    AXUIElementCopyAttributeValue(element, kAXChildrenAttribute as CFString, &children)
    for child in (children as? [AXUIElement]) ?? [] {
        if let found = button(child, titled: title) { return found }
    }
    return nil
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
