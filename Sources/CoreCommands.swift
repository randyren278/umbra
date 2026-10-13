// Session basics: navigation, tabs, scripts, waiting, handoff and the daemon itself.
import AppKit
import WebKit

/// Accepts what a person or an agent would type: "example.com", "localhost:3000", a file path.
func normalizeURL(_ text: String) -> URL? {
    let text = text.trimmingCharacters(in: .whitespaces)
    if text.hasPrefix("/") || text.hasPrefix("~") {
        return URL(fileURLWithPath: (text as NSString).expandingTildeInPath)
    }
    if text.contains("://") || ["data:", "about:", "blob:"].contains(where: text.hasPrefix) { return URL(string: text) }
    let local = ["localhost", "127.", "0.0.0.0", "[::1]"].contains(where: text.hasPrefix)
    return URL(string: (local ? "http://" : "https://") + text)
}

func pageInfo(_ tab: Tab, title: String? = nil, url: String? = nil) -> [String: Any] {
    var info = tab.summary
    if let title { info["title"] = title }
    if let url { info["url"] = url }
    if let status = tab.status { info["status"] = status }
    let shownTitle = (info["title"] as? String ?? "").isEmpty ? "(no title)" : info["title"] as! String
    info["text"] = "\(shownTitle) · \(info["url"] as? String ?? "about:blank")" + (tab.status.map { " · HTTP \($0)" } ?? "")
    return info
}

/// Replies with the page as the document itself reports it: WebKit's title and URL properties
/// can lag a just-finished load.
func report(_ tab: Tab, _ reply: @escaping Reply) {
    tab.run("return [document.title, location.href]") { result in
        let pair = (try? result.get()) as? [String]
        reply(.success(pageInfo(tab, title: pair?.first, url: pair?.last)))
    }
}

/// Finishes a navigation-style command: optionally waits for `until`, then reports the page.
func settle(_ tab: Tab, _ args: Args, _ reply: @escaping Reply) -> (Result<Void, UmbraError>) -> Void {
    { result in
        if case .failure(let error) = result { return reply(.failure(error)) }
        guard let until = args.string("until") else { return report(tab, reply) }
        tab.wait(until: until, timeout: args.number("timeout") ?? 30) { waited in
            switch waited {
            case .success: report(tab, reply)
            case .failure(let error): reply(.failure(error))
            }
        }
    }
}

let visibleJS = "(el => { if (!el) return false; const r = el.getBoundingClientRect(), s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' })"

let idleJS = """
let last = -1, quiet = 0;
for (let i = 0; i < 600; i++) {
  const count = performance.getEntriesByType('resource').length;
  if (count === last && document.readyState === 'complete') { if (++quiet >= 5) return true; } else { quiet = 0; last = count; }
  await new Promise(r => setTimeout(r, 100));
}
return false
"""

/// A tab for one-shot work, closed when the command answers, its own timeout included, so a
/// slow page never leaves a scratch tab behind.
func scratch(_ engine: Engine, _ kind: String, _ args: Args, timeout fallback: Double,
             _ reply: @escaping Reply) -> (Tab, Reply) {
    let tab = engine.open(name: "\(kind)-\(UUID().uuidString.prefix(6))", select: false)
    if engine.current === tab { engine.current = nil }  // never the tab a session command lands on
    var answered = false
    let finish: Reply = { result in
        guard !answered else { return }
        answered = true
        engine.close(tab)
        reply(result)
    }
    let timeout = args.number("timeout") ?? fallback
    DispatchQueue.main.asyncAfter(deadline: .now() + timeout) { finish(.failure(UmbraError(2, "no answer in \(duration(timeout))"))) }
    return (tab, finish)
}

func coreCommands() -> [Command] {
    [
        Command(name: "open", help: "Load a URL in a tab (the current one, or --tab NAME, created if new) and wait for it to load.",
                params: [Param(name: "url", kind: .string, help: "URL, host name or file path", required: true, positional: true),
                         Param(name: "new", kind: .boolean, help: "Open in a new tab"),
                         Param(name: "until", kind: .string, help: "JavaScript condition to wait for after load, across redirects")],
                aliases: ["goto", "navigate"], timeout: 60) { args, engine, reply in
            guard let url = normalizeURL(args.string("url") ?? "") else { return reply(.failure(UmbraError(64, "not a URL: \(args.string("url") ?? "")"))) }
            let tab: Tab
            if args.bool("new") { tab = engine.open(name: args.string("tab")) } else {
                switch engine.tab(args, create: true) { case .success(let found): tab = found; case .failure(let error): return reply(.failure(error)) }
            }
            engine.current = tab
            tab.status = nil
            if url.isFileURL {
                tab.loading.append(settle(tab, args, reply))
                tab.web.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent())
            } else {
                tab.load(url, done: settle(tab, args, reply))
            }
        },
        Command(name: "back", help: "Go back in the tab's history.", timeout: 60) { args, engine, reply in
            guard case .success(let tab) = engine.tab(args) else { return reply(.failure(UmbraError(64, "no open tab"))) }
            tab.go({ $0.goBack() }, done: settle(tab, args, reply))
        },
        Command(name: "forward", help: "Go forward in the tab's history.", timeout: 60) { args, engine, reply in
            guard case .success(let tab) = engine.tab(args) else { return reply(.failure(UmbraError(64, "no open tab"))) }
            tab.go({ $0.goForward() }, done: settle(tab, args, reply))
        },
        Command(name: "reload", help: "Reload the page.", timeout: 60) { args, engine, reply in
            guard case .success(let tab) = engine.tab(args) else { return reply(.failure(UmbraError(64, "no open tab"))) }
            tab.go({ $0.reload() }, done: settle(tab, args, reply))
        },
        Command(name: "tabs", help: "List open tabs; * marks the current one.", usesTab: false) { _, engine, reply in
            let list = engine.tabs.map { tab -> [String: Any] in tab.summary.merging(["current": tab === engine.current]) { $1 } }
            let text = engine.tabs.map { ($0 === engine.current ? "* " : "  ") + "\($0.name)\t\($0.web.title ?? "")\t\($0.web.url?.absoluteString ?? "about:blank")" }
            reply(.success(["tabs": list, "text": text.isEmpty ? "no tabs" : text.joined(separator: "\n")]))
        },
        Command(name: "tab", help: "Make a tab current, open a new one, or close one.",
                params: [Param(name: "action", kind: .string, help: "select, new or close", required: true, positional: true),
                         Param(name: "name", kind: .string, help: "Tab name", positional: true)],
                usesTab: false) { args, engine, reply in
            let name = args.string("name")
            switch args.string("action") {
            case "new":
                if let name, engine.tabs.contains(where: { $0.name == name }) { return reply(.failure(UmbraError(64, "a tab named \(name) is open"))) }
                reply(.success(pageInfo(engine.open(name: name))))
            case "select":
                guard let tab = engine.tabs.first(where: { $0.name == name }) else { return reply(.failure(UmbraError(64, "no tab named \(name ?? "")"))) }
                engine.current = tab
                reply(.success(pageInfo(tab)))
            case "close":
                if let name, !engine.tabs.contains(where: { $0.name == name }) { return reply(.failure(UmbraError(64, "no tab named \(name)"))) }
                guard let tab = name.flatMap({ name in engine.tabs.first { $0.name == name } }) ?? engine.current else {
                    return reply(.failure(UmbraError(64, "no tab to close")))
                }
                engine.close(tab)
                reply(.success(["closed": tab.name, "text": "closed \(tab.name)"]))
            default:
                reply(.failure(UmbraError(64, "tab takes select, new or close")))
            }
        },
        Command(name: "eval", help: "Run JavaScript in the page as the body of an async function; prints what it returns. Sees the page's own globals.",
                params: [Param(name: "script", kind: .string, help: "JavaScript (use `return`); `-` or omitted reads stdin on the CLI", positional: true)],
                timeout: 60) { args, engine, reply in
            guard case .success(let tab) = engine.tab(args) else { return reply(.failure(UmbraError(64, "no open tab; start with `umbra open <url>`"))) }
            tab.run(args.string("script") ?? "return document.title") { result in
                switch result {
                case .success(let value): reply(.success(["value": value.map(jsonSafe) ?? NSNull(), "text": rendered(value) ?? ""]))
                case .failure(let error): reply(.failure(error))
                }
            }
        },
        Command(name: "wait", help: "Wait until a selector is visible, text appears, the URL matches, a JavaScript condition holds, an element is gone, or the network is idle.",
                params: [Param(name: "selector", kind: .string, help: "CSS selector that must be visible"),
                         Param(name: "text", kind: .string, help: "Text that must appear on the page"),
                         Param(name: "url", kind: .string, help: "Substring of the URL, or /regex/"),
                         Param(name: "js", kind: .string, help: "JavaScript condition"),
                         Param(name: "gone", kind: .string, help: "CSS selector that must disappear"),
                         Param(name: "idle", kind: .boolean, help: "No new network requests for 500 ms"),
                         Param(name: "ms", kind: .number, help: "Just wait this many milliseconds")],
                timeout: 120) { args, engine, reply in
            guard case .success(let tab) = engine.tab(args) else { return reply(.failure(UmbraError(64, "no open tab"))) }
            let timeout = args.number("timeout") ?? 30
            let done: (Result<Void, UmbraError>) -> Void = { result in
                switch result { case .success: report(tab, reply); case .failure(let error): reply(.failure(error)) }
            }
            if let ms = args.number("ms") {
                return DispatchQueue.main.asyncAfter(deadline: .now() + ms / 1000) { done(.success(())) }
            }
            if args.bool("idle") {
                return tab.run(idleJS) { result in
                    if case .success(let value) = result, (value as? Bool) == true { return done(.success(())) }
                    done(.failure(UmbraError(2, "the network never went idle")))
                }
            }
            var conditions: [String] = []
            var values: [String: Any] = [:]
            if let selector = args.string("selector") { conditions.append("\(visibleJS)(document.querySelector(selector))"); values["selector"] = selector }
            if let text = args.string("text") { conditions.append("(document.body && document.body.innerText.includes(text))"); values["text"] = text }
            if let url = args.string("url") {
                if url.count > 2, url.hasPrefix("/"), url.hasSuffix("/") {
                    conditions.append("new RegExp(urlPattern).test(location.href)"); values["urlPattern"] = String(url.dropFirst().dropLast())
                } else { conditions.append("location.href.includes(urlPart)"); values["urlPart"] = url }
            }
            if let gone = args.string("gone") { conditions.append("!\(visibleJS)(document.querySelector(gone))"); values["gone"] = gone }
            if let js = args.string("js") { conditions.append("(\(js))") }
            guard !conditions.isEmpty else { return reply(.failure(UmbraError(64, "wait needs --selector, --text, --url, --js, --gone, --idle or --ms"))) }
            tab.wait(until: conditions.joined(separator: " && "), values, timeout: timeout, done: done)
        },
        Command(name: "downloads", help: "Files the page downloaded in this tab, with where they were saved.") { args, engine, reply in
            guard case .success(let tab) = engine.tab(args) else { return reply(.failure(UmbraError(64, "no open tab"))) }
            let text = tab.downloads.map { "\($0["state"] ?? "")\t\($0["path"] ?? $0["error"] ?? "")" }
            reply(.success(["downloads": tab.downloads, "text": text.isEmpty ? "no downloads" : text.joined(separator: "\n")]))
        },
        Command(name: "handoff", help: "Show the tab to the person so they can do a step Umbra can't (2FA, CAPTCHA, consent); returns when they close the window or --until holds, and hides it again.",
                params: [Param(name: "message", kind: .string, help: "What the person should do", positional: true),
                         Param(name: "until", kind: .string, help: "JavaScript condition that ends the handoff by itself")],
                timeout: 900) { args, engine, reply in
            guard case .success(let tab) = engine.tab(args) else { return reply(.failure(UmbraError(64, "no open tab"))) }
            let timeout = args.number("timeout") ?? 900
            let begin = {
                tab.handoff = { report(tab, reply) }
                tab.reveal(args.string("message") ?? "Finish this step, then close the window")
                // Whatever happens, the window hides again: on --until, on close, or at the timeout.
                DispatchQueue.main.asyncAfter(deadline: .now() + timeout) {
                    guard tab.handoff != nil else { return }
                    tab.handoff = { reply(.failure(UmbraError(2, "handoff timed out after \(duration(timeout))"))) }
                    tab.endHandoff()
                }
                guard let until = args.string("until") else { return }
                tab.wait(until: until, timeout: timeout) { result in
                    guard tab.handoff != nil, case .success = result else { return }
                    tab.endHandoff()
                }
            }
            // Nothing to hand off when the condition already holds: no window flashes up.
            guard let until = args.string("until") else { return begin() }
            tab.run("return Boolean(\(until))") { result in
                if case .success(let value) = result, (value as? Bool) == true { return report(tab, reply) }
                begin()
            }
        },
        Command(name: "cookies", help: "Cookie names, domains and expiry in this profile's store. Never values.",
                params: [Param(name: "domain", kind: .string, help: "Only cookies within this domain", positional: true)],
                usesTab: false) { args, _, reply in
            store.httpCookieStore.getAllCookies { cookies in
                let domain = args.string("domain")
                let list = cookies.filter { domain.map($0.within) ?? true }.sorted { ($0.bareDomain, $0.name) < ($1.bareDomain, $1.name) }
                    .map { ["name": $0.name, "domain": $0.domain, "expires": $0.expiresDate.map { ISO8601DateFormatter().string(from: $0) } ?? "session"] }
                reply(.success(["cookies": list, "text": list.map { "\($0["name"]!)\t\($0["domain"]!)\t\($0["expires"]!)" }.joined(separator: "\n")]))
            }
        },
        Command(name: "status", help: "The daemon's profile, uptime and tabs.", usesTab: false) { _, engine, reply in
            let uptime = Int(Date().timeIntervalSince(startedAt))
            reply(.success(["pid": Int(getpid()), "profile": profile ?? "default", "uptime": uptime, "idleLimit": Int(idleLimit),
                            "tabs": engine.tabs.map(\.summary),
                            "text": "umbra daemon \(getpid()) · profile \(profile ?? "default") · up \(uptime) s · \(engine.tabs.count) tab(s)"]))
        },
        Command(name: "stop", help: "Close every tab and stop this profile's daemon.", usesTab: false, mcp: false) { _, _, reply in
            unlink(socketPath)  // from here on, a new command starts a fresh daemon instead of reaching this one
            reply(.success(["text": "stopped"]))
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) { keepSessions { exit(0) } }
        },
        // The one-shot commands, served by a running daemon so scripts never wait on its lock.
        Command(name: "read", help: "Load a page in a scratch tab, run a script (or return its HTML), and close it.",
                params: [Param(name: "url", kind: .string, help: "Page URL", required: true, positional: true),
                         Param(name: "script", kind: .string, help: "JavaScript (async function body)"),
                         Param(name: "cookie", kind: .string, help: "Fail with exit 3 unless this cookie is set for the page"),
                         Param(name: "wait", kind: .number, help: "Seconds to let the page settle"),
                         Param(name: "until", kind: .string, help: "JavaScript condition to wait for, across redirects")],
                usesTab: false, timeout: 90) { args, engine, reply in
            guard let url = normalizeURL(args.string("url") ?? "") else { return reply(.failure(UmbraError(64, "not a URL"))) }
            let (tab, finish) = scratch(engine, "read", args, timeout: 90, reply)
            tab.load(url) { loaded in
                if case .failure(let error) = loaded { return finish(.failure(error)) }
                DispatchQueue.main.asyncAfter(deadline: .now() + (args.number("wait") ?? 0)) {
                    let proceed = {
                        store.httpCookieStore.getAllCookies { cookies in
                            let host = tab.web.url?.host ?? ""
                            if let cookie = args.string("cookie"), !cookies.contains(where: { $0.name == cookie && $0.sent(to: host) }) {
                                return finish(.failure(UmbraError(3, "signed out of \(host)")))
                            }
                            let script = args.string("script") ?? ""
                            tab.run(script.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "return document.documentElement.outerHTML" : script) { result in
                                switch result {
                                case .success(let value): finish(.success(["output": rendered(value) as Any? ?? NSNull()]))
                                case .failure(let error): finish(.failure(UmbraError(2, error.message.replacingOccurrences(of: "script failed", with: "the script failed"))))
                                }
                            }
                        }
                    }
                    guard let until = args.string("until") else { return proceed() }
                    tab.wait(until: until, timeout: args.number("timeout") ?? 90) { waited in
                        if case .failure(let error) = waited { return finish(.failure(error)) }
                        proceed()
                    }
                }
            }
        },
        Command(name: "fetch", help: "GET a URL with the browser's session, from a page on the same site; returns status and body.",
                params: [Param(name: "url", kind: .string, help: "URL to fetch", required: true, positional: true),
                         Param(name: "from", kind: .string, help: "Same-site page to fetch from (default: /robots.txt)")],
                usesTab: false, timeout: 90) { args, engine, reply in
            guard let url = URL(string: args.string("url") ?? ""), url.host != nil,
                  let page = URL(string: args.string("from") ?? "/robots.txt", relativeTo: url)?.absoluteURL else {
                return reply(.failure(UmbraError(64, "fetch needs an absolute URL")))
            }
            let (tab, finish) = scratch(engine, "fetch", args, timeout: 90, reply)
            tab.load(page) { loaded in
                if case .failure(let error) = loaded { return finish(.failure(error)) }
                tab.run(fetchScript, ["url": url.absoluteString]) { result in
                    guard case .success(let value) = result, let pair = value as? [Any],
                          let status = pair.first as? Int, let body = pair.last as? String else {
                        if case .failure(let error) = result { return finish(.failure(UmbraError(4, "request failed: \(error.message)"))) }
                        return finish(.failure(UmbraError(4, "request failed")))
                    }
                    finish(.success(["status": status, "body": body, "text": body]))
                }
            }
        },
        Command(name: "download", help: "Download a URL with the browser's session to a file, replacing it once complete.",
                params: [Param(name: "url", kind: .string, help: "URL to download", required: true, positional: true),
                         Param(name: "file", kind: .string, help: "Where to save it", required: true, positional: true)],
                usesTab: false, timeout: 300) { args, _, reply in
            guard let url = URL(string: args.string("url") ?? "") else { return reply(.failure(UmbraError(64, "not a URL"))) }
            let file = args.path("file")!
            var job: Download?
            job = Download(url, to: file) { code, message in
                _ = job
                job = nil
                code == 0 ? reply(.success(["path": file.path, "text": file.path])) : reply(.failure(UmbraError(code, message)))
            }
            let timeout = args.number("timeout") ?? 300
            DispatchQueue.main.asyncAfter(deadline: .now() + timeout) { [weak job] in job?.cancel("no answer in \(duration(timeout))") }
        },
    ]
}
