import AppKit
import WebKit

private let frameChannel = "umbra-frame-" + UUID().uuidString.lowercased()
private let frameScript = #"""
(() => {
if (globalThis.__umbraFrame) return;
const token = Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2), links = new WeakMap();
const frames = root => [...root.querySelectorAll('iframe, frame'), ...[...root.querySelectorAll('*')].filter(e => e.shadowRoot).flatMap(e => frames(e.shadowRoot))];
window.addEventListener('message', event => {
  if (!event.isTrusted || event.data?.channel !== 'CHANNEL' || typeof event.data.token !== 'string') return;
  const el = frames(document).find(e => e.contentWindow === event.source);
  if (el) links.set(el, event.data.token);
});
globalThis.__umbraFrame = { token, hit: (child, x, y) => { const el=frames(document).find(e=>links.get(e)===child); let hit=document.elementFromPoint(x,y); while(hit?.shadowRoot) {const deep=hit.shadowRoot.elementFromPoint(x,y); if (!deep || deep===hit) break; hit=deep;} return !!el && hit===el; }, geometry: () => ({ token, url: location.href, title: document.title, width: innerWidth, height: innerHeight, children: frames(document).map(el => {
  const r = el.getBoundingClientRect(), s = getComputedStyle(el);
  let supported = true;
  for (let n = el; n; n = n.parentElement || n.getRootNode()?.host) { const c = getComputedStyle(n); if (c.transform !== 'none' || !['1', 'normal', ''].includes(c.zoom) || c.perspective !== 'none') supported = false; }
  return { token: links.get(el) || '', x: r.x + el.clientLeft + parseFloat(s.paddingLeft || 0), y: r.y + el.clientTop + parseFloat(s.paddingTop || 0), width: el.clientWidth - parseFloat(s.paddingLeft || 0) - parseFloat(s.paddingRight || 0), height: el.clientHeight - parseFloat(s.paddingTop || 0) - parseFloat(s.paddingBottom || 0), supported, visible: el.checkVisibility() && r.width > 0 && r.height > 0 };
}) }) };
window.webkit.messageHandlers['CHANNEL'].postMessage({token});
if (window !== window.parent) window.parent.postMessage({ channel: 'CHANNEL', token }, '*');
})();
"""#.replacingOccurrences(of: "CHANNEL", with: frameChannel)

private final class FrameDocument {
    let id: String, token: String, info: WKFrameInfo
    init(_ id: String, _ token: String, _ info: WKFrameInfo) { self.id = id; self.token = token; self.info = info }
    func run(_ tab: Tab, _ body: String, _ args: [String: Any] = [:], world: WKContentWorld = umbraWorld,
             _ done: @escaping (Result<Any?, UmbraError>) -> Void) {
        let operation = tab.operation
        var arguments = args
        var source = body
        if world == umbraWorld {
            arguments["__frameToken"] = token
            source = "if(globalThis.__umbraFrame?.token !== __frameToken) throw new Error('stale frame document; run frames again');\n" + body
        }
        tab.web.callAsyncJavaScript(source, arguments: arguments, in: info, in: world) { result in
            guard operation == tab.operation else { return }
            switch result {
            case .success(let value): done(.success(value))
            case .failure(let error): done(.failure(UmbraError(2, "frame \(self.id): \(exceptionText(error))")))
            }
        }
    }
    func umbra(_ tab: Tab, _ method: String, _ args: [String: Any], _ done: @escaping Reply) {
        let body = "const u=globalThis.__umbra; return u ? u[method](a) : 'missing'"
        var options = args, completed = false
        if method == "watch", let request = tab.operation {
            let id = UUID().uuidString, token = self.token, info = self.info
            options["_watch"] = id
            tab.engine.runtime.onFinish(request) {
                guard !completed else { return }
                tab.web.callAsyncJavaScript("if(globalThis.__umbraFrame?.token === token) globalThis.__umbra?.cancelWatch(id)", arguments: ["token": token, "id": id], in: info, in: umbraWorld) { _ in }
            }
        }
        func finish(_ result: Result<Any?, UmbraError>) {
            completed = true
            switch result {
            case .failure(let error): done(.failure(error))
            case .success(let raw):
                let value = raw as? [String: Any] ?? [:]
                if let error = value["error"] as? String { done(.failure(UmbraError(2, error))) }
                else { done(.success(value)) }
            }
        }
        run(tab, body, ["method": method, "a": options]) { result in
            guard case .success(let value) = result, value as? String == "missing" else { return finish(result) }
            self.run(tab, interactLibrary) { loaded in
                if case .failure(let error) = loaded { return done(.failure(error)) }
                self.run(tab, body, ["method": method, "a": options], finish)
            }
        }
    }
}

private final class FrameStore {
    var frames: [String: FrameDocument] = [:]
    var next = 0
}

private final class FrameBridge: NSObject, WKScriptMessageHandler {
    weak var engine: Engine?
    init(_ tab: Tab) { self.engine = tab.engine }
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let tab = engine?.tabs.first(where: { $0.web === message.webView }), let state = tab.extras["frames"] as? FrameStore, let body = message.body as? [String: Any],
              let token = body["token"] as? String, token.count <= 64 else { return }
        if state.frames.values.contains(where: { $0.token == token }) { return }
        if message.frameInfo.isMainFrame { state.frames.removeAll() }
        if state.frames.count >= 128, let oldest = state.frames.values.filter({ !$0.info.isMainFrame }).min(by: { Int($0.id.dropFirst()) ?? 0 < Int($1.id.dropFirst()) ?? 0 }) { state.frames[oldest.id] = nil }
        state.next += 1
        let frame = FrameDocument("f\(state.next)", token, message.frameInfo)
        state.frames[frame.id] = frame
    }
}

private func frameGeometry(_ tab: Tab, _ done: @escaping ([String: [String: Any]]) -> Void) {
    guard let state = tab.extras["frames"] as? FrameStore else { return done([:]) }
    let frames = Array(state.frames.values)
    guard !frames.isEmpty else { return done([:]) }
    var left = frames.count, result: [String: [String: Any]] = [:]
    for frame in frames {
        frame.run(tab, "return globalThis.__umbraFrame?.geometry()") { value in
            if case .success(let raw) = value, let geometry = raw as? [String: Any] { result[frame.id] = geometry }
            else { state.frames[frame.id] = nil }
            left -= 1
            if left == 0 { done(result) }
        }
    }
}

private func framePoint(_ frame: FrameDocument, _ tab: Tab, _ info: [String: Any], _ done: @escaping (Result<[String: Any], UmbraError>) -> Void) {
    frameGeometry(tab) { geometry in
        guard let state = tab.extras["frames"] as? FrameStore, geometry[frame.id] != nil else { return done(.failure(UmbraError(2, "frame is no longer attached; run frames again"))) }
        var token = frame.token, point = info, x = info["x"] as? Double ?? 0, y = info["y"] as? Double ?? 0
        var visited = Set<String>(), checks: [(FrameDocument, String, Double, Double)] = []
        while let child = state.frames.values.first(where: { $0.token == token }), !child.info.isMainFrame {
            guard visited.insert(token).inserted else { return done(.failure(UmbraError(2, "frame ancestry cycle"))) }
            var parent: (String, [String: Any])?
            for (id, g) in geometry {
                if let link = (g["children"] as? [[String: Any]])?.first(where: { $0["token"] as? String == token }) { parent = (id, link); break }
            }
            guard let (id, link) = parent, link["supported"] as? Bool == true, link["visible"] as? Bool == true,
                  let parentFrame = state.frames[id] else { return done(.failure(UmbraError(2, "frame geometry unavailable, hidden, transformed or zoomed; native input refused"))) }
            let width = link["width"] as? Double ?? 0, height = link["height"] as? Double ?? 0
            guard x >= 0, y >= 0, x < width, y < height else { return done(.failure(UmbraError(2, "target is outside its frame viewport"))) }
            x += link["x"] as? Double ?? 0; y += link["y"] as? Double ?? 0
            checks.append((parentFrame, token, x, y))
            token = parentFrame.token
        }
        guard state.frames.values.contains(where: { $0.token == token && $0.info.isMainFrame }) else { return done(.failure(UmbraError(2, "frame ancestor document is unavailable; run frames again"))) }
        guard x >= 0, y >= 0, x < tab.web.bounds.width / tab.web.pageZoom, y < tab.web.bounds.height / tab.web.pageZoom else { return done(.failure(UmbraError(2, "frame target is outside the top viewport; scroll the parent first"))) }
        point["x"] = x; point["y"] = y
        func validate(_ index: Int) {
            if index == checks.count { return done(.success(point)) }
            let (parent, child, x, y) = checks[index]
            parent.run(tab, "return globalThis.__umbraFrame?.hit(child,x,y)", ["child": child, "x": x, "y": y]) { result in
                guard case .success(let hit) = result, hit as? Bool == true else { return done(.failure(UmbraError(2, "frame target is covered in an ancestor document; native input refused"))) }
                validate(index + 1)
            }
        }
        validate(0)
    }
}

func frameCommands() -> [Command] {
    tabSetups.append { tab in
        tab.extras["frames"] = FrameStore()
        let scripts = tab.web.configuration.userContentController
        guard !scripts.userScripts.contains(where: { $0.source == frameScript }) else { return }
        scripts.add(FrameBridge(tab), contentWorld: umbraWorld, name: frameChannel)
        scripts.addUserScript(WKUserScript(source: frameScript, injectionTime: .atDocumentStart, forMainFrameOnly: false, in: umbraWorld))
    }
    return [
        Command(name: "frames", help: "List registered main and cross-origin frames by stable document id. IDs change on navigation; stale handles fail. Native frame evaluation reaches cross-origin DOM without disabling browser security.") { args, engine, reply in
            guard case .success(let tab) = engine.tab(args) else { return reply(.failure(UmbraError(64, "no tab"))) }
            settled(tab) { frameGeometry(tab) { geometry in
                let state = tab.extras["frames"] as? FrameStore
                let frames: [[String: Any]] = geometry.keys.sorted().map { id in
                    var result = geometry[id] ?? [:]
                    result["id"] = id; result["main"] = state?.frames[id]?.info.isMainFrame ?? false
                    result["token"] = nil; result["children"] = nil
                    return result
                }
                reply(.success(["frames": frames, "text": frames.map { "\($0["id"] ?? "")\($0["main"] as? Bool == true ? " (main)" : "") \($0["url"] ?? "")" }.joined(separator: "\n")]))
            } }
        },
        Command(name: "frame", help: "Run a supported operation in a registered frame: snapshot, observe, extract, get, watch, eval, click, fill, check, uncheck, select. Pass the operation's arguments as a JSON object. Native input uses ancestor frame coordinates, rejecting hidden/transformed/zoomed/offscreen frames; eval uses an async function body in the selected frame's page world (use return). No implicit current-frame selection or shared routing state.",
                params: [Param(name: "id", kind: .string, help: "Frame id from frames", required: true, positional: true),
                         Param(name: "operation", kind: .string, help: "Supported frame operation", required: true, positional: true),
                         Param(name: "args", kind: .string, help: "Operation arguments as a JSON object (at most 64 KiB)")]) { args, engine, reply in
            guard case .success(let tab) = engine.tab(args), let state = tab.extras["frames"] as? FrameStore,
                  let frame = state.frames[args.string("id") ?? ""] else { return reply(.failure(UmbraError(64, "unknown frame; run frames first"))) }
            let operation = args.string("operation") ?? "", source = args.string("args") ?? "{}"
            guard source.utf8.count <= 65536, let data = source.data(using: .utf8), let options = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return reply(.failure(UmbraError(64, "args must be a JSON object of at most 64 KiB"))) }
            frameOperation(frame, tab, operation, options, reply)
        },
        Command(name: "pointer", help: "Send trusted native pointer input at CSS viewport coordinates, including canvas content. No DOM semantics are inferred; the result reports coordinates and input delivery only.",
                params: [Param(name: "action", kind: .string, help: "click, move, down or up", required: true, positional: true),
                         Param(name: "x", kind: .number, help: "CSS viewport x", required: true), Param(name: "y", kind: .number, help: "CSS viewport y", required: true),
                         Param(name: "button", kind: .string, help: "left (coordinate input currently supports left button only)")]) { args, engine, reply in
            guard case .success(let tab) = engine.tab(args) else { return reply(.failure(UmbraError(64, "no tab"))) }
            let action = args.string("action") ?? "", button = args.string("button") ?? "left"
            guard ["click", "move", "down", "up"].contains(action), button == "left", let x = args.number("x"), let y = args.number("y"), x.isFinite, y.isFinite, x >= 0, y >= 0, x < tab.web.bounds.width / tab.web.pageZoom, y < tab.web.bounds.height / tab.web.pageZoom else { return reply(.failure(UmbraError(64, "valid pointer action/button and coordinates inside the viewport are required"))) }
            serial(tab, limit: 5, reply) { done in
                let point = tab.windowPoint(["x": x, "y": y])
                if action == "click" { tab.click(["x": x, "y": y], button: button) }
                else if action == "move" { tab.mouse(.mouseMoved, point) }
                else { tab.mouse(action == "down" ? (button == "left" ? .leftMouseDown : button == "right" ? .rightMouseDown : .otherMouseDown) : (button == "left" ? .leftMouseUp : button == "right" ? .rightMouseUp : .otherMouseUp), point) }
                tab.afterInput("Mouse") { done(.success(["action": action, "x": x, "y": y, "button": button, "text": "pointer \(action) at \(x),\(y)"])) }
            }
        },
    ]
}

private func frameOperation(_ frame: FrameDocument, _ tab: Tab, _ operation: String, _ options: [String: Any], _ reply: @escaping Reply) {
    let args = Args(values: options)
    if operation == "eval" {
        guard let script = args.string("script") else { return reply(.failure(UmbraError(64, "eval requires args.script"))) }
        return frame.run(tab, "return true") { checked in
            guard case .success = checked else { if case .failure(let error) = checked { reply(.failure(error)) }; return }
            frame.run(tab, script, world: .page) { result in
                switch result {
                case .failure(let error): reply(.failure(error))
                case .success(let value): reply(.success(["value": value.map(jsonSafe) ?? NSNull(), "text": rendered(value) ?? ""]))
                }
            }
        }
    }
    if ["snapshot", "observe"].contains(operation) {
        switch observationOptions(args) {
        case .failure(let error): reply(.failure(error))
        case .success(let options): return frame.umbra(tab, operation, options) { result in reply(result.map { var value = $0; value["snapshot"] = value["snapshot"] ?? value["text"]; return value }) }
        }
        return
    }
    if ["get", "extract", "watch"].contains(operation) {
        guard args.string("target") != nil else { return reply(.failure(UmbraError(64, "operation requires args.target"))) }
        var options = options
        if operation == "watch" {
            let duration = args.number("duration") ?? 10, state = args.string("state") ?? "visible"
            guard duration.isFinite, duration >= 0, duration <= 25, ["visible", "hidden", "attached", "detached", "text", "value"].contains(state), !["text", "value"].contains(state) || args.string("equals") != nil else { return reply(.failure(UmbraError(64, "invalid watch condition or duration"))) }
            options["duration"] = duration; options["state"] = state
        }
        if operation == "extract" {
            for (key, fallback, high) in [("limit", 100.0, 1000.0), ("text-max", 1000.0, 10000.0)] {
                let n = args.number(key) ?? fallback
                guard n.isFinite, n.rounded() == n, n >= 1, n <= high else { return reply(.failure(UmbraError(64, "invalid extraction budget"))) }
                options[key] = n
            }
        }
        return frame.umbra(tab, operation, options, reply)
    }
    guard ["click", "fill", "check", "uncheck", "select"].contains(operation), let target = args.string("target") else { return reply(.failure(UmbraError(64, "unsupported frame operation or missing args.target"))) }
    if operation == "fill", args.string("value") == nil { return reply(.failure(UmbraError(64, "fill requires args.value"))) }
    serial(tab, limit: 15, reply) { done in
        let done = formActionReply(tab, operation, done)
        frame.umbra(tab, "prepare", ["target": target, "action": operation, "arm": "click"]) { prepared in
            guard case .success(let info) = prepared else { return done(prepared) }
            guard info["status"] as? String == "ok" else { return done(.failure(UmbraError(2, info["message"] as? String ?? "frame target is not actionable"))) }
            let ref = info["ref"] as? String ?? ""
            if operation == "fill", info["kind"] as? String == "setter" {
                return frame.umbra(tab, "setValue", ["ref": ref, "value": args.string("value") ?? ""]) { result in done(result.map { $0.merging(["trusted": false, "method": "setter"]) { $1 } }) }
            }
            if operation == "select" { return frame.umbra(tab, "selectOptions", ["ref": ref, "values": args.strings("values")], done) }
            if ["check", "uncheck"].contains(operation), info["checked"] as? Bool == (operation == "check") { return done(.success(["ref": ref, "checked": operation == "check", "text": "already in requested state"])) }
            framePoint(frame, tab, info) { positioned in
                guard case .success(let point) = positioned else { if case .failure(let error) = positioned { done(.failure(error)) }; return }
                tab.click(point)
                tab.afterInput("Mouse") {
                    if operation == "fill" {
                        guard let value = args.string("value") else { return done(.failure(UmbraError(64, "fill requires args.value"))) }
                        return frame.umbra(tab, "fillStart", ["ref": ref, "clear": true]) { result in
                            guard case .success = result else { return done(result) }
                            if value.isEmpty { tab.press(parseKey("Backspace")!) } else { tab.web.insertText(value) }
                            tab.afterInput("Key") { frame.umbra(tab, "fillCheck", ["ref": ref]) { result in
                                done(result.map { var out = $0; if info["password"] as? Bool == true { out["value"] = "••••••" }; out["ref"] = ref; out["text"] = "filled frame field"; return out })
                            } }
                        }
                    }
                    frame.umbra(tab, "observed", [:]) { result in
                        if ["check", "uncheck"].contains(operation) {
                            return frame.umbra(tab, "stateOf", ["ref": ref]) { checked in
                                guard case .success(let state) = checked, state["checked"] as? Bool == (operation == "check") else { return done(.failure(UmbraError(2, "frame check state did not change"))) }
                                done(.success(state))
                            }
                        }
                        done(result.map { $0.merging(["ref": ref, "x": point["x"] ?? 0, "y": point["y"] ?? 0, "text": "clicked frame element"]) { $1 } })
                    }
                }
            }
        }
    }
}
