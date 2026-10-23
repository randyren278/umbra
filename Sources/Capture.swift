// Screenshots, PDF, text, markdown, links, HTML, console and network logs, content blocking and
// viewport. (Capture lane.)
import AppKit
import WebKit

func captureCommands() -> [Command] {
    tabSetups.append(instrument)
    var commands: [Command] = [screenshotCommand, pdfCommand]
    commands += readingCommands
    commands += [consoleCommand, networkCommand, blockCommand, viewportCommand, infoCommand]
    return commands  // built in steps: one long + chain is slow to type-check on older compilers
}

/// The tab a command acts on; replies with the reason when there is none.
func pick(_ args: Args, _ engine: Engine, _ reply: Reply) -> Tab? {
    switch engine.tab(args) {
    case .success(let tab): return tab
    case .failure(let error): reply(.failure(error)); return nil
    }
}

/// Writes a file the caller asked for; a folder that does not exist is a usage error.
func save(_ data: Data, to file: URL) -> UmbraError? {
    var folder: ObjCBool = false
    guard FileManager.default.fileExists(atPath: file.deletingLastPathComponent().path, isDirectory: &folder), folder.boolValue else {
        return UmbraError(64, "there is no folder \(file.deletingLastPathComponent().path) to save \(file.lastPathComponent) in")
    }
    do { try data.write(to: file, options: .atomic) } catch {
        return UmbraError(1, "could not write \(file.path): \(error.localizedDescription)")
    }
    return nil
}

func byteSize(_ count: Int) -> String {
    count < 1024 ? "\(count) B" : count < 1 << 20 ? String(format: "%.1f KB", Double(count) / 1024)
        : String(format: "%.1f MB", Double(count) / 1048576)
}

/// Binds `el` to the --target element in umbraWorld (null without a target), resolved as the
/// interaction lane resolves targets: an e<N> ref from `snapshot`, text=…, or a CSS selector. A
/// target that resolves to nothing makes the script return {__missing: why}.
let findJS = """
    const find = t => {
      if (globalThis.__umbra?.find) return globalThis.__umbra.find(t);
      try { const el = document.querySelector(t); return el ? {el} : {error: `nothing matches ${t}`}; }
      catch { return {error: `not a valid CSS selector: ${t}`, fatal: true}; }
    };
    const found = target === null ? {el: null} : find(target);
    if (found.error) return {__missing: found.error, fatal: !!found.fatal};
    const el = found.el;

    """

/// The error for a --target that resolved to nothing; exit codes match the interaction commands.
func targetError(_ value: Any?) -> UmbraError? {
    guard let answer = value as? [String: Any], let why = answer["__missing"] as? String else { return nil }
    return UmbraError(answer["fatal"] as? Bool == true ? 64 : 2, why)
}

/// Runs `body` in umbraWorld with `el` bound to the --target element (null without one). A target
/// that resolves to nothing, or a failing script, is answered here; `then` gets the rest.
func examine(_ tab: Tab, _ args: Args, _ body: String, _ values: [String: Any] = [:], _ reply: @escaping Reply,
             then: @escaping (Any) -> Void) {
    tab.run(findJS + body, values.merging(["target": args.string("target") ?? NSNull()]) { $1 }, in: umbraWorld) { result in
        switch result {
        case .failure(let error): reply(.failure(error))
        case .success(let value):
            if let error = targetError(value) { return reply(.failure(error)) }
            then(value ?? NSNull())
        }
    }
}

// MARK: Screenshot

let tallest: CGFloat = 16384

/// Where the element (or the viewport) is, after scrolling it into view, with the scroll position,
/// the page's height, the view's size, the device pixel ratio and the page's width. With `height`, first waits for
/// the view to grow to it, then brings the element (or the page's top) to the top.
let locateJS = findJS + """
    for (let i = 0; i < 200 && height !== null && innerHeight !== height; i++) await new Promise(r => setTimeout(r, 10));
    if (el) el.scrollIntoView({block: height === null ? 'nearest' : 'start', inline: 'nearest', behavior: 'instant'});
    else if (height !== null) scrollTo({left: 0, top: 0, behavior: 'instant'});
    const r = el ? el.getBoundingClientRect() : {left: 0, top: 0, width: innerWidth, height: innerHeight};
    let x = r.left, y = r.top;
    // In an iframe the rect is relative to the frame: add each frame's position, border and padding.
    for (let w = el?.ownerDocument.defaultView; w && w !== window && w.frameElement; w = w.parent) {
      const f = w.frameElement, b = f.getBoundingClientRect(), s = getComputedStyle(f);
      x += b.left + f.clientLeft + parseFloat(s.paddingLeft);
      y += b.top + f.clientTop + parseFloat(s.paddingTop);
    }
    const d = document.documentElement;
    return [x, y, r.width, r.height, scrollX, scrollY, Math.max(d.scrollHeight, document.body?.scrollHeight ?? 0),
            innerWidth, innerHeight, devicePixelRatio || 1, Math.max(d.scrollWidth, document.body?.scrollWidth ?? 0)];
    """

let restoreJS = """
    for (let i = 0; i < 200 && innerHeight !== height; i++) await new Promise(r => setTimeout(r, 10));
    scrollTo({left: x, top: y, behavior: 'instant'});
    """

struct Shot {
    let image: CGImage
    let scale: CGFloat  // image pixels per CSS pixel
    let cut: Bool       // the page or element was taller than the 16384 px limit
}

/// WebKit can snapshot the whole page rather than the view: it paints as laid out for the current
/// viewport, so nothing resizes, 100vh stays 100vh and a fixed header appears once, at the top.
let pageSnapshots = WKSnapshotConfiguration().responds(to: NSSelectorFromString("_setUsesContentsRect:"))

/// Snapshots the viewport, one element (scrolled into view) or the whole page. --full, or an
/// element taller than the view, crops a whole-page snapshot. A page taller or wider than
/// 16384 px (which would make that bitmap huge), or a WebKit without whole-page snapshots, grows the tab's window and web view instead (still off
/// screen and transparent), then shrinks them back and returns to the scroll position.
func shoot(_ tab: Tab, full: Bool, target: String?, done: @escaping (Result<Shot, UmbraError>) -> Void) {
    let operation = tab.operation
    var cancelled = false
    if let operation { tab.engine.runtime.onCancel(operation) { cancelled = true } }
    let active = { !cancelled && (operation.map { tab.operation == $0 && tab.engine.runtime.isActive($0) } ?? true) }
    func locate(height: CGFloat?, then: @escaping (Result<[CGFloat], UmbraError>) -> Void) {
        tab.run(locateJS, ["target": target ?? NSNull(), "height": height.map { Double($0) } ?? NSNull()], in: umbraWorld) { result in
            switch result {
            case .failure(let error): then(.failure(error))
            case .success(let value):
                if let error = targetError(value) { return then(.failure(error)) }
                guard let numbers = value as? [NSNumber], numbers.count == 11 else { return then(.failure(UmbraError(1, "could not measure the page"))) }
                then(.success(numbers.map { CGFloat($0.doubleValue) }))
            }
        }
    }
    /// The rect in view coordinates, or with `page`, the rect cut from a whole-page snapshot.
    func snap(_ rect: CGRect, page: Bool = false, scale: CGFloat, cut: Bool, then: @escaping (Result<Shot, UmbraError>) -> Void) {
        guard active() else { return }
        guard rect.width >= 1, rect.height >= 1 else {
            return then(.failure(UmbraError(4, "\(target ?? "the page") has nothing visible to capture")))
        }
        let config = WKSnapshotConfiguration()
        config.rect = rect.integral
        if page {
            let selector = NSSelectorFromString("_setUsesContentsRect:")
            typealias Setter = @convention(c) (AnyObject, Selector, Bool) -> Void
            unsafeBitCast(config.method(for: selector), to: Setter.self)(config, selector, true)
        }
        tab.web.takeSnapshot(with: config) { image, error in
            guard active() else { return }
            guard var cg = image?.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
                return then(.failure(UmbraError(1, "screenshot failed: \(error?.localizedDescription ?? "no image")")))
            }
            if page {
                let pixels = CGRect(x: rect.minX * scale, y: rect.minY * scale, width: rect.width * scale, height: rect.height * scale)
                guard let part = cg.cropping(to: pixels.integral) else {
                    return then(.failure(UmbraError(4, "\(target ?? "the page") has nothing visible to capture")))
                }
                cg = part
            }
            then(.success(Shot(image: cg, scale: scale, cut: cut)))
        }
    }
    locate(height: nil) { located in
        guard active() else { return }
        let m: [CGFloat]
        switch located {
        case .failure(let error): return done(.failure(error))
        case .success(let numbers): m = numbers
        }
        let view = tab.web.bounds.size
        if !full && m[3] <= view.height {
            let rect = CGRect(x: m[0], y: m[1], width: m[2], height: m[3]).intersection(CGRect(origin: .zero, size: view))
            return snap(rect, scale: m[9], cut: false, then: done)
        }
        if pageSnapshots && m[6] <= tallest && m[10] <= tallest {
            let rect = target == nil ? CGRect(x: 0, y: 0, width: view.width, height: m[6])
                                     : CGRect(x: m[0] + m[4], y: m[1] + m[5], width: m[2], height: m[3])
            return snap(rect, page: true, scale: m[9], cut: false, then: done)
        }
        let saved = tab.window.frame
        let height = min(max(m[6], view.height), tallest)
        tab.window.setFrame(NSRect(x: saved.minX, y: saved.minY, width: saved.width, height: height), display: false)
        if let operation {
            tab.engine.runtime.onCancel(operation) {
                guard tab.operation == operation, tab.engine.tabs.contains(where: { $0 === tab }) else { return }
                tab.window.setFrame(saved, display: false)
                tab.run("scrollTo({left: x, top: y, behavior: 'instant'});", ["x": Double(m[4]), "y": Double(m[5])], in: umbraWorld) { _ in }
            }
        }
        // The reply waits for the restore, so the next command sees the page as it was.
        let restore = { (result: Result<Shot, UmbraError>) in
            guard active() else { return }
            tab.window.setFrame(saved, display: false)
            tab.run(restoreJS, ["height": Double(view.height), "x": Double(m[4]), "y": Double(m[5])], in: umbraWorld) { _ in
                guard active() else { return }
                done(result)
            }
        }
        locate(height: height) { located in
            guard active() else { return }
            switch located {
            case .failure(let error): restore(.failure(error))
            case .success(let g):
                let grown = CGRect(x: 0, y: 0, width: g[7], height: g[8])
                let rect = target == nil ? grown : CGRect(x: g[0], y: g[1], width: g[2], height: g[3]).intersection(grown)
                snap(rect, scale: g[9], cut: (target == nil ? g[6] : g[3]) > g[8], then: restore)
            }
        }
    }
}

/// PNG or JPEG bytes of a snapshot.
func encode(_ image: CGImage, jpeg: Bool, quality: Double) -> Data? {
    let rep = NSBitmapImageRep(cgImage: image)
    return jpeg ? rep.representation(using: .jpeg, properties: [.compressionFactor: quality])
                : rep.representation(using: .png, properties: [:])
}

let screenshotCommand: Command = Command(
    name: "screenshot",
    help: "Capture the page as an image: the visible viewport by default, the whole scrolling page with --full (up to 16384 CSS px tall), or one element with --target (an e<N> ref from `snapshot`, text=…, or a CSS selector; it is scrolled into view first). With a path the image is written there; without one it comes back base64-encoded in `data` with its `mime`. `width` and `height` are CSS pixels; `scale` is image pixels per CSS pixel. The tab stays invisible throughout.",
    params: [Param(name: "path", kind: .string, help: "File to write (.png or .jpg); omit to get base64 in `data`", positional: true),
             Param(name: "full", kind: .boolean, help: "The whole page, not just the viewport"),
             Param(name: "target", kind: .string, help: "One element to capture: an e<N> ref from `snapshot`, text=…, or a CSS selector"),
             Param(name: "format", kind: .string, help: "png (default) or jpeg; a .jpg path implies jpeg"),
             Param(name: "quality", kind: .number, help: "JPEG quality from 0 to 1 (default 0.8)")],
    timeout: 60
) { args, engine, reply in
    guard let tab = pick(args, engine, reply) else { return }
    let file = args.path("path")
    let format = args.string("format")?.lowercased()
        ?? (["jpg", "jpeg"].contains(file?.pathExtension.lowercased() ?? "") ? "jpeg" : "png")
    guard ["png", "jpeg", "jpg"].contains(format) else { return reply(.failure(UmbraError(64, "--format takes png or jpeg"))) }
    let jpeg = format != "png"
    let quality = min(max(args.number("quality") ?? 0.8, 0), 1)
    let operation = tab.operation
    shoot(tab, full: args.bool("full"), target: args.string("target")) { shot in
        guard operation.map({ tab.operation == $0 && engine.runtime.isActive($0) }) ?? true else { return }
        switch shot {
        case .failure(let error): reply(.failure(error))
        case .success(let shot):
            guard let data = encode(shot.image, jpeg: jpeg, quality: quality) else {
                return reply(.failure(UmbraError(1, "could not encode the screenshot")))
            }
            let (width, height) = (shot.image.width, shot.image.height)
            let label = "\(width)×\(height) \(jpeg ? "JPEG" : "PNG")" + (shot.cut ? " (cut at \(Int(tallest)) px)" : "")
            var result: [String: Any] = ["width": Int((CGFloat(width) / shot.scale).rounded()),
                                         "height": Int((CGFloat(height) / shot.scale).rounded()), "scale": shot.scale,
                                         "mime": jpeg ? "image/jpeg" : "image/png", "bytes": data.count, "cut": shot.cut]
            if let file {
                if let error = save(data, to: file) { return reply(.failure(error)) }
                result["path"] = file.path
                result["text"] = "\(file.path) · \(label) · \(byteSize(data.count))"
            } else {
                result["data"] = data.base64EncodedString()
                result["text"] = "\(label) (base64 in data)"
            }
            reply(.success(result))
        }
    }
}

// MARK: PDF

let pdfCommand: Command = Command(
    name: "pdf",
    help: "Save the whole page (not just the viewport) as a PDF file, as WebKit renders it on screen: one tall page, selectable text and working links. Returns the path, page count and size in bytes.",
    params: [Param(name: "path", kind: .string, help: "Where to write the PDF", required: true, positional: true)],
    timeout: 60
) { args, engine, reply in
    guard let tab = pick(args, engine, reply) else { return }
    guard let file = args.path("path") else { return reply(.failure(UmbraError(64, "pdf needs a path"))) }
    let operation = tab.operation
    var cancelled = false
    if let operation { engine.runtime.onCancel(operation) { cancelled = true } }
    tab.web.createPDF(configuration: WKPDFConfiguration()) { result in
        guard !cancelled, operation.map({ tab.operation == $0 && engine.runtime.isActive($0) }) ?? true else { return }
        switch result {
        case .failure(let error): reply(.failure(UmbraError(1, "PDF failed: \(error.localizedDescription)")))
        case .success(let data):
            if let error = save(data, to: file) { return reply(.failure(error)) }
            let pages = CGDataProvider(data: data as CFData).flatMap { CGPDFDocument($0) }?.numberOfPages ?? 0
            reply(.success(["path": file.path, "pages": pages, "bytes": data.count,
                            "text": "\(file.path) · \(pages) page\(pages == 1 ? "" : "s") · \(byteSize(data.count))"]))
        }
    }
}

// MARK: Viewport

/// Safari on iPhone. Since iOS 26 Safari reports iOS 18.6 whatever the real version.
let mobileAgent: String = {
    let major = Int(safariVersion.prefix { $0 != "." }) ?? 26
    let os = major >= 26 ? "18_6" : safariVersion.replacingOccurrences(of: ".", with: "_")
    return "Mozilla/5.0 (iPhone; CPU iPhone OS \(os) like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/\(safariVersion) Mobile/15E148 Safari/604.1"
}()

/// The device pixel ratio the page sees (0 restores the screen's own); false where WebKit lacks the override.
func overrideScale(_ web: WKWebView, _ scale: CGFloat) -> Bool {
    let selector = NSSelectorFromString("_setOverrideDeviceScaleFactor:")
    guard web.responds(to: selector) else { return false }
    typealias Setter = @convention(c) (AnyObject, Selector, CGFloat) -> Void
    unsafeBitCast(web.method(for: selector), to: Setter.self)(web, selector, scale)
    return true
}

let viewportJS = """
    for (let i = 0; i < 200 && (innerWidth !== width || innerHeight !== height); i++) await new Promise(r => setTimeout(r, 10));
    return [innerWidth, innerHeight, devicePixelRatio]
    """

let viewportCommand: Command = Command(
    name: "viewport",
    help: "Set the tab's viewport size in CSS pixels, as a responsive-design check would. --mobile sends iPhone Safari's user agent and defaults to 390×844; --scale sets the device pixel ratio (1, 2, 3). Each call sets the whole state: without --mobile the desktop user agent returns, without --scale the screen's own ratio. The user agent applies from the next navigation (reload to see a site's mobile version). With no arguments, reports the current viewport.",
    params: [Param(name: "width", kind: .number, help: "Width in CSS pixels", positional: true),
             Param(name: "height", kind: .number, help: "Height in CSS pixels", positional: true),
             Param(name: "mobile", kind: .boolean, help: "iPhone Safari user agent, 390×844 unless sized"),
             Param(name: "scale", kind: .number, help: "Device pixel ratio, for example 2 or 3")]
) { args, engine, reply in
    guard let tab = pick(args, engine, reply) else { return }
    let mobile = args.bool("mobile")
    let change = mobile || ["width", "height", "scale"].contains { args.values[$0] != nil }
    let current = tab.web.bounds.size
    let width = args.number("width") ?? (mobile ? 390 : Double(current.width))
    let height = args.number("height") ?? (mobile ? 844 : Double(current.height))
    guard (100...Double(tallest)).contains(width), (100...Double(tallest)).contains(height) else {
        return reply(.failure(UmbraError(64, "viewport sizes run from 100 to \(Int(tallest)) CSS pixels")))
    }
    if change {
        if let scale = args.number("scale"), !(0.5...5).contains(scale) { return reply(.failure(UmbraError(64, "--scale runs from 0.5 to 5"))) }
        guard overrideScale(tab.web, CGFloat(args.number("scale") ?? 0)) || args.number("scale") == nil else {
            return reply(.failure(UmbraError(1, "this version of WebKit cannot change the device pixel ratio")))
        }
        let frame = tab.window.frame
        tab.window.setFrame(NSRect(x: frame.minX, y: frame.minY, width: width, height: height), display: false)
        tab.web.customUserAgent = mobile ? mobileAgent : nil
    }
    tab.run(viewportJS, ["width": width, "height": height], in: umbraWorld) { result in
        guard case .success(let value) = result, let numbers = value as? [NSNumber], numbers.count == 3 else {
            if case .failure(let error) = result { return reply(.failure(error)) }
            return reply(.failure(UmbraError(1, "could not read the viewport")))
        }
        let (w, h, scale) = (numbers[0].intValue, numbers[1].intValue, numbers[2].doubleValue)
        let isMobile = tab.web.customUserAgent == mobileAgent
        let note = change && mobile ? " (reload to load the mobile site)" : ""
        reply(.success(["width": w, "height": h, "scale": scale, "mobile": isMobile,
                        "userAgent": tab.web.customUserAgent ?? NSNull(),
                        "text": "\(w)×\(h) @\(scale.formatted())x" + (isMobile ? " · iPhone Safari" : "") + note]))
    }
}

// MARK: Info

let infoJS = """
    const d = document.documentElement, b = document.body;
    return {url: location.href, title: document.title, readyState: document.readyState, visibilityState: document.visibilityState,
      viewport: {width: innerWidth, height: innerHeight, scale: devicePixelRatio},
      scroll: {x: Math.round(scrollX), y: Math.round(scrollY)},
      document: {width: Math.max(d.scrollWidth, b?.scrollWidth ?? 0), height: Math.max(d.scrollHeight, b?.scrollHeight ?? 0)},
      counts: {links: document.links.length, forms: document.forms.length, images: document.images.length,
               iframes: document.querySelectorAll('iframe, frame').length}}
    """

let infoCommand: Command = Command(
    name: "info",
    help: "A quick orientation on the current page: URL, title, HTTP status, viewport and device pixel ratio, scroll position, document size, readyState, visibilityState, and how many links, forms, images and iframes it has.",
    params: []
) { args, engine, reply in
    guard let tab = pick(args, engine, reply) else { return }
    tab.run(infoJS, in: umbraWorld) { result in
        guard case .success(let value) = result, var info = value as? [String: Any] else {
            if case .failure(let error) = result { return reply(.failure(error)) }
            return reply(.failure(UmbraError(1, "could not read the page")))
        }
        if let status = tab.status { info["status"] = status }
        func number(_ group: String, _ key: String) -> Double { ((info[group] as? [String: Any])?[key] as? NSNumber)?.doubleValue ?? 0 }
        let title = (info["title"] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? "(no title)"
        let counts = ["links", "forms", "images", "iframes"].map { key -> String in
            let count = Int(number("counts", key))
            return "\(count) \(count == 1 ? String(key.dropLast()) : key)"
        }
        let url: String = info["url"] as? String ?? ""
        let status: String = tab.status.map { " · HTTP \($0)" } ?? ""
        let viewport: String = "viewport \(Int(number("viewport", "width")))×\(Int(number("viewport", "height"))) @\(number("viewport", "scale").formatted())x"
        let scrolled: String = "scrolled to \(Int(number("scroll", "x"))),\(Int(number("scroll", "y")))"
        let size: String = "of \(Int(number("document", "width")))×\(Int(number("document", "height")))"
        let state: String = "\(info["readyState"] as? String ?? "") · \(info["visibilityState"] as? String ?? "")"
        let lines: [String] = ["\(title) · \(url)\(status)", "\(viewport) · \(scrolled) \(size) · \(state)", counts.joined(separator: " · ")]
        info["text"] = lines.joined(separator: "\n")
        reply(.success(info))
    }
}
