import Foundation
import WebKit

private let observationParams = [
    Param(name: "interactive", kind: .boolean, help: "Only controls and headings"),
    Param(name: "scope", kind: .string, help: "Limit observation to a ref, text=… or CSS selector"),
    Param(name: "max", kind: .number, help: "Outline character budget, 1–100000 (default 25000)"),
    Param(name: "depth", kind: .number, help: "Maximum outline nesting depth, 0–100 (default 100)"),
]

func observationOptions(_ args: Args) -> Result<[String: Any], UmbraError> {
    var out: [String: Any] = ["interactive": args.bool("interactive")]
    for (key, fallback, low, high) in [("max", 25000.0, 1.0, 100000.0), ("depth", 100.0, 0.0, 100.0)] {
        let n = args.number(key) ?? fallback
        guard n.isFinite, n.rounded() == n, n >= low, n <= high else {
            return .failure(UmbraError(64, "\(key) must be an integer from \(Int(low)) to \(Int(high))"))
        }
        out[key] = n
    }
    out["_redact"] = args.bool("_redact")
    out["scope"] = args.string("scope")
    out["since"] = args.string("since")
    return .success(out)
}

func observeCommands() -> [Command] {
    func run(_ method: String, _ args: [String: Any], _ engine: Engine, _ original: Args, _ reply: @escaping Reply) {
        switch engine.tab(original) {
        case .failure(let error): reply(.failure(error))
        case .success(let tab):
            settled(tab) {
                var options = args, completed = false
                if method == "watch", let request = tab.operation {
                    let id = UUID().uuidString
                    options["_watch"] = id
                    engine.runtime.onFinish(request) {
                        guard !completed else { return }
                        tab.web.callAsyncJavaScript("globalThis.__umbra?.cancelWatch(id)", arguments: ["id": id], in: nil, in: umbraWorld) { _ in }
                    }
                }
                tab.umbra(method, options) { result in
                    completed = true
                    switch result {
                    case .failure(let error): reply(.failure(error))
                    case .success(let value):
                        if let error = value["error"] as? String { reply(.failure(UmbraError(2, error))) }
                        else { reply(.success(value)) }
                    }
                }
            }
        }
    }
    return [
        Command(name: "observe", help: "Observe the page, or return a line replacement patch against --since baseline. Baselines are document-local, at most 8 and 512 KiB of serialized UTF-16 content per tab, including metadata. Missing/evicted/navigation baselines return full content and metadata. Apply patch.start and patch.delete to the requested baseline's snapshot lines, then insert patch.lines. Delta metadata (refs, url, title, truncated, depthLimited, doc) inherits from that baseline when omitted. changed reports outline changes only. Use identical scope/budgets for deltas.",
                params: observationParams + [Param(name: "since", kind: .string, help: "Baseline token returned by an earlier observe")]) { args, engine, reply in
            switch observationOptions(args) {
            case .failure(let error): reply(.failure(error))
            case .success(let options): run("observe", options, engine, args, reply)
            }
        },
        Command(name: "extract", help: "Deterministically extract table rows/cells, typed form fields, or list items with source refs. Password values are omitted. JSON Schema subset: type, properties, required, additionalProperties, items, bounds, enum and const. Unsupported constraints fail explicitly.",
                params: [targetParam(), Param(name: "kind", kind: .string, help: "table, form or list (inferred from root)"),
                         Param(name: "limit", kind: .number, help: "Maximum rows/fields/items, 1–1000 (default 100)"),
                         Param(name: "text-max", kind: .number, help: "Maximum characters per value, 1–10000 (default 1000)"),
                         Param(name: "schema", kind: .string, help: "JSON Schema object to validate extracted data")]) { args, engine, reply in
            var options: [String: Any] = ["target": args.string("target") ?? ""]
            options["kind"] = args.string("kind")
            for (key, fallback, high) in [("limit", 100.0, 1000.0), ("text-max", 1000.0, 10000.0)] {
                let value = args.number(key) ?? fallback
                guard value.isFinite, value.rounded() == value, value >= 1, value <= high else { return reply(.failure(UmbraError(64, "\(key) must be an integer from 1 to \(Int(high))"))) }
                options[key] = value
            }
            if let schema = args.string("schema") {
                guard schema.utf8.count <= 65536, let data = schema.data(using: .utf8),
                      let value = try? JSONSerialization.jsonObject(with: data, options: .fragmentsAllowed) else { return reply(.failure(UmbraError(64, "schema must be valid JSON of at most 65536 bytes"))) }
                options["schema"] = value
            }
            run("extract", options, engine, args, reply)
        },
        Command(name: "watch", help: "Wait for a DOM condition using mutation and input/change/load events; no polling or retained log. Returns matched=false on expiry. Watches clean up after completion, at most 16 per document. Programmatic property changes without DOM events cannot be observed; use input/change events.",
                params: [targetParam(), Param(name: "state", kind: .string, help: "visible, hidden, attached, detached, text or value"),
                         Param(name: "equals", kind: .string, help: "Exact expected text/value for those states"),
                         Param(name: "duration", kind: .number, help: "Maximum watch seconds, 0–25 (default 10)")]) { args, engine, reply in
            let state = args.string("state") ?? "visible", duration = args.number("duration") ?? 10
            guard ["visible", "hidden", "attached", "detached", "text", "value"].contains(state), duration.isFinite, duration >= 0, duration <= 25 else { return reply(.failure(UmbraError(64, "state must be visible/hidden/attached/detached/text/value and duration must be 0–25 seconds"))) }
            guard !["text", "value"].contains(state) || args.string("equals") != nil else { return reply(.failure(UmbraError(64, "text and value watches require --equals"))) }
            var options: [String: Any] = ["target": args.string("target") ?? "", "state": state, "duration": duration]
            options["equals"] = args.string("equals")
            run("watch", options, engine, args, reply)
        },
        Command(name: "capabilities", help: "Report browser observation capabilities and explicit limitations.", usesTab: false) { _, _, reply in
            reply(.success(["text": "Native WebKit; open shadow roots and same-origin frames; cross-origin frame-scoped DOM and trusted coordinate pointer input; canvas semantics unavailable", "engine": "WebKit", "shadowRoots": "open", "sameOriginFrames": true, "crossOriginDOM": true, "frameScopedCommands": true, "coordinatePointer": true, "closedShadowDOM": false, "canvasSemantics": false, "nativeTrustedInput": true, "deltaBaselines": ["count": 8, "bytes": 524288], "watch": ["eventDriven": true, "maxSeconds": 25, "maxPerDocument": 16]]))
        },
    ]
}
