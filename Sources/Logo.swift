// Each site's own logo for the sign-in window, found as Safari finds one for Favorites: the icons
// its home page names, home-screen icons first, then the largest; else /apple-touch-icon.png; else
// /favicon.ico. Fetched without cookies (never the login page), kept in Application Support, and
// asked for again weekly.
import AppKit

enum Logo {
    private static let folder = supportDir.appendingPathComponent("Logos")
    private static let session = URLSession(configuration: .ephemeral)
    private static var found: [String: NSImage] = [:]
    private static var waiting: [String: [(NSImage) -> Void]] = [:]

    /// Calls `done` on the main thread once the site's logo is known; never, when it has none.
    static func of(_ site: Site, done: @escaping (NSImage) -> Void) {
        guard let login = URL(string: site.login), login.host != nil, let home = URL(string: "https://\(site.domain)/") else { return }
        let key = site.domain
        if let image = found[key] { return done(image) }
        let file = folder.appendingPathComponent(key)
        let age = (try? file.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate).map { -$0.timeIntervalSinceNow }
        if let age, age < 7 * 86400 {
            if let image = (try? Data(contentsOf: file)).flatMap(NSImage.init(data:)) {
                found[key] = image
                done(image)
            }
            return  // an empty file: no logo this week
        }
        guard waiting[key] == nil else { return waiting[key]!.append(done) }
        waiting[key] = [done]
        page(home) { named in
            // The home page may hand over to another host (single sign-on): the site's own icons are on its own hosts.
            var ranked = named
            for base in Set([login, home].compactMap { URL(string: "/", relativeTo: $0)?.absoluteURL }) {
                if let touch = URL(string: "/apple-touch-icon.png", relativeTo: base)?.absoluteURL { ranked.append((touch, 10_100)) }
                if let icon = URL(string: "/favicon.ico", relativeTo: base)?.absoluteURL { ranked.append((icon, 0)) }
            }
            first(ranked.sorted { $0.1 > $1.1 }.map(\.0)) { data in
                try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
                try? (data ?? Data()).write(to: file, options: .atomic)
                let image = data.flatMap(NSImage.init(data:))
                found[key] = image
                for done in waiting.removeValue(forKey: key) ?? [] { image.map(done) }
            }
        }
    }

    /// The icons a page names in its <link> tags, scored: home-screen icons first, then by size.
    static func icons(in html: String, base: URL) -> [(URL, Int)] {
        let tags = html.matches(of: #/<link\b[^>]*>/#.ignoresCase()).map { String($0.output) }
        func attribute(_ name: String, _ tag: String) -> String? {
            (try? Regex("\\b\(name)\\s*=\\s*[\"']([^\"']*)[\"']").ignoresCase().firstMatch(in: tag))
                .flatMap { $0.output[1].substring.map(String.init) }
        }
        return tags.compactMap { tag in
            guard let rel = attribute("rel", tag)?.lowercased(), rel.contains("icon"), !rel.contains("mask"),
                  let href = attribute("href", tag), let url = URL(string: href, relativeTo: base)?.absoluteURL else { return nil }
            let size = attribute("sizes", tag)?.split(separator: "x").first.flatMap { Int($0) }
                ?? (attribute("type", tag)?.contains("svg") == true || href.contains(".svg") ? 512 : 32)
            return (url, size + (rel.contains("apple-touch-icon") ? 10_000 : 0))
        }
    }

    /// How a logo sits in a square tile: `fills` when it covers its square (a home-screen icon), so
    /// it goes edge to edge; otherwise trimmed to what it draws, to sit on white. `small` when it has
    /// too few pixels to fill the tile without blurring.
    static func fit(_ image: NSImage) -> (image: NSImage, fills: Bool, small: Bool) {
        let pixels = image.representations.map(\.pixelsWide).max() ?? 0  // 0: drawn from vectors, sharp at any size
        var area = NSRect(x: 0, y: 0, width: 128, height: 128)  // where a vector logo is drawn to look at it
        let drawn = pixels > 0 ? image.cgImage(forProposedRect: nil, context: nil, hints: nil)
            : image.cgImage(forProposedRect: &area, context: nil, hints: nil)
        guard let cg = drawn else { return (image, false, false) }
        let (w, h) = (cg.width, cg.height)
        var bytes = [UInt8](repeating: 0, count: w * h * 4)
        guard let context = CGContext(data: &bytes, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4,
                                      space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else {
            return (image, false, pixels > 0 && pixels < 64)
        }
        context.draw(cg, in: CGRect(x: 0, y: 0, width: w, height: h))
        func opaque(_ x: Int, _ y: Int) -> Bool { bytes[(y * w + x) * 4 + 3] > 200 }
        var (left, right, top, bottom) = (w, -1, h, -1)
        for y in 0..<h { for x in 0..<w where bytes[(y * w + x) * 4 + 3] > 16 {
            (left, right, top, bottom) = (min(left, x), max(right, x), min(top, y), max(bottom, y))
        } }
        guard right >= left, bottom >= top else { return (image, false, pixels > 0 && pixels < 64) }
        let box = CGRect(x: left, y: top, width: right - left + 1, height: bottom - top + 1)
        // Just inside the corners: a rounded square is opaque there, a glyph on a clear ground isn't.
        let dx = Int(box.width * 0.15), dy = Int(box.height * 0.15)
        let corners: [(Int, Int)] = [(left + dx, top + dy), (right - dx, top + dy), (left + dx, bottom - dy), (right - dx, bottom - dy)]
        let fills = corners.allSatisfy { opaque($0.0, $0.1) }
        guard let trimmed = cg.cropping(to: box) else { return (image, fills, pixels > 0 && pixels < 64) }
        return (NSImage(cgImage: trimmed, size: NSSize(width: box.width, height: box.height)), fills, pixels > 0 && Int(max(box.width, box.height)) < 64)
    }

    private static func page(_ url: URL, done: @escaping ([(URL, Int)]) -> Void) {
        get(url) { data, final in done(data.map { icons(in: String(decoding: $0, as: UTF8.self), base: final ?? url) } ?? []) }
    }

    /// The first URL that answers with something AppKit can draw.
    private static func first(_ urls: [URL], done: @escaping (Data?) -> Void) {
        guard let url = urls.first else { return done(nil) }
        get(url) { data, _ in
            if let data, let image = NSImage(data: data), image.size.width >= 16 { return done(data) }
            first(Array(urls.dropFirst()), done: done)
        }
    }

    private static func get(_ url: URL, done: @escaping (Data?, URL?) -> Void) {
        var request = URLRequest(url: url, timeoutInterval: 10)
        request.setValue("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/\(safariVersion) Safari/605.1.15",
                         forHTTPHeaderField: "User-Agent")
        session.dataTask(with: request) { data, response, _ in
            let ok = (response as? HTTPURLResponse).map { (200..<300).contains($0.statusCode) } ?? false
            DispatchQueue.main.async { done(ok ? data : nil, response?.url) }
        }.resume()
    }
}
