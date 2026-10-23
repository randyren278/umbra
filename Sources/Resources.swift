import Foundation
import WebKit

private func applyResources(_ kinds: [String], _ tab: Tab, active: @escaping () -> Bool, _ done: @escaping (Result<Void, UmbraError>) -> Void) {
    compile(kinds) { result in
        if case .failure = result { return done(result) }
        guard active() else { return done(.failure(UmbraError(2, "resource request cancelled"))) }
        let state = observed(tab)
        for kind in state.blocked { ruleLists[kind].map(tab.web.configuration.userContentController.remove) }
        state.blocked = Set(kinds)
        for kind in kinds { ruleLists[kind].map(tab.web.configuration.userContentController.add) }
        done(.success(()))
    }
}

private final class HTTPRead: NSObject, URLSessionDataDelegate {
    let limit: Int
    let reply: Reply
    var data = Data()
    var response: HTTPURLResponse?
    var session: URLSession!
    var task: URLSessionDataTask?
    var finished = false

    init(_ limit: Int, reply: @escaping Reply) { self.limit = limit; self.reply = reply }

    func start(_ url: URL, timeout: Double) {
        let config = URLSessionConfiguration.ephemeral
        config.httpCookieStorage = nil; config.httpShouldSetCookies = false
        config.urlCache = nil; config.timeoutIntervalForRequest = timeout; config.timeoutIntervalForResource = timeout
        session = URLSession(configuration: config, delegate: self, delegateQueue: .main)
        task = session.dataTask(with: url); task?.resume()
    }

    func finish(_ result: Result<[String: Any], UmbraError>) {
        guard !finished else { return }
        finished = true; task?.cancel(); session.invalidateAndCancel(); reply(result)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        self.response = response as? HTTPURLResponse
        if response.expectedContentLength > limit { completionHandler(.cancel); finish(.failure(UmbraError(4, "HTTP body exceeds byte budget"))) }
        else { completionHandler(.allow) }
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        guard self.data.count + data.count <= limit else { return finish(.failure(UmbraError(4, "HTTP body exceeds byte budget"))) }
        self.data.append(data)
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        if let error { return finish(.failure(UmbraError((error as NSError).code == NSURLErrorTimedOut ? 2 : 4, "HTTP request failed"))) }
        guard let response else { return finish(.failure(UmbraError(4, "HTTP request has no response"))) }
        guard (200..<300).contains(response.statusCode) else { return finish(.failure(UmbraError(5, "HTTP \(response.statusCode)"))) }
        guard let text = String(data: data, encoding: .utf8) else { return finish(.failure(UmbraError(4, "HTTP body is not UTF-8 text"))) }
        var value: [String: Any] = ["body": text, "bytes": data.count, "status": response.statusCode,
                                    "engine": "HTTP", "authenticated": false, "javascript": false, "text": text]
        if let json = try? JSONSerialization.jsonObject(with: data, options: .fragmentsAllowed) { value["json"] = json }
        finish(.success(value))
    }
}

func resourceCommands() -> [Command] {
    [
        Command(name: "resources", help: "Set this tab's resource preset: lean blocks images/fonts/media/trackers; balanced blocks trackers; visual blocks nothing. Optional --reload --until tests the restricted page and restores previous rules/reloads on failure. Never blocks scripts or stylesheets in a preset.",
                params: [Param(name: "preset", kind: .string, help: "lean, balanced or visual", required: true, positional: true),
                         Param(name: "reload", kind: .boolean, help: "Reload after applying the preset"),
                         Param(name: "until", kind: .string, help: "JS readiness condition; if unmet, restore previous rules and reload")], timeout: 60) { args, engine, reply in
            guard let tab = pick(args, engine, reply), let preset = args.string("preset") else { return }
            let presets = ["lean": ["images", "fonts", "media", "trackers"], "balanced": ["trackers"], "visual": []]
            guard let kinds = presets[preset] else { return reply(.failure(UmbraError(64, "preset must be lean, balanced or visual"))) }
            guard args.string("until") == nil || args.bool("reload") else { return reply(.failure(UmbraError(64, "until needs reload"))) }
            let previous = Array(observed(tab).blocked)
            let active = { args.operation.map(engine.runtime.isActive) ?? false }
            applyResources(kinds, tab, active: active) { result in
                if case .failure(let error) = result { return reply(.failure(error)) }
                guard let request = args.operation, engine.runtime.isActive(request) else { return }
                func done(_ fallback: Bool) {
                    reply(.success(["preset": fallback ? "previous" : preset, "blocked": Array(observed(tab).blocked).sorted(), "fallback": fallback,
                                    "text": fallback ? "restored previous resource rules and reloaded" : "resource preset \(preset)" ]))
                }
                guard args.bool("reload") else { return done(false) }
                tab.go({ $0.reload() }) { loaded in
                    if case .failure(let error) = loaded { return reply(.failure(error)) }
                    guard let until = args.string("until") else { return done(false) }
                    tab.wait(until: until, timeout: min(args.number("timeout") ?? 5, 5)) { ready in
                        if case .success = ready { return done(false) }
                        guard engine.runtime.isActive(request) else { return }
                        applyResources(previous, tab, active: active) { restored in
                            if case .failure(let error) = restored { return reply(.failure(error)) }
                            tab.go({ $0.reload() }) { loaded in
                                if case .failure(let error) = loaded { return reply(.failure(error)) }
                                tab.wait(until: until, timeout: min(args.number("timeout") ?? 5, 5)) { checked in
                                    if case .failure(let error) = checked { return reply(.failure(error)) }
                                    done(true)
                                }
                            }
                        }
                    }
                }
            }
        },
        Command(name: "http", help: "Read public UTF-8 HTML/text/JSON through bounded ephemeral HTTP without creating a WebKit tab. No JavaScript or login cookies. Use fetch or open when the page needs Umbra's authenticated browser.",
                params: [Param(name: "url", kind: .string, help: "Public HTTP(S) URL", required: true, positional: true),
                         Param(name: "max", kind: .number, help: "Maximum response bytes, 1...4194304 (default 1048576)")], usesTab: false, timeout: 30) { args, engine, reply in
            guard let url = URL(string: args.string("url") ?? ""), ["http", "https"].contains(url.scheme), url.host != nil,
                  url.user == nil, url.password == nil else { return reply(.failure(UmbraError(64, "http needs an HTTP(S) URL without embedded credentials"))) }
            let max = args.number("max") ?? 1048576
            guard max.isFinite, max.rounded() == max, max >= 1, max <= 4194304 else { return reply(.failure(UmbraError(64, "max must be 1...4194304 bytes"))) }
            let read = HTTPRead(Int(max), reply: reply)
            read.start(url, timeout: args.number("timeout") ?? 30)
            if let request = args.operation { engine.runtime.onCancel(request) { read.finish(.failure(UmbraError(2, "HTTP request cancelled"))) } }
        },
    ]
}
