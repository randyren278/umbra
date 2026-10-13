// For each line of process ids on stdin, prints one line of JSON: the displays and every window
// those processes own, as the window server sees them. The suite keeps one running and uses it to
// prove Umbra's windows stay invisible.
import CoreGraphics
import Foundation

while let line = readLine() {
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
