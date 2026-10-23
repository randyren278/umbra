// What a tab's pages say and fetch: console messages, uncaught errors and network requests,
// recorded from document start; and content blocking. (Capture lane.)
import AppKit
import WebKit

/// Page-world instrumentation, injected into every frame at document start: it wraps console,
/// fetch and XMLHttpRequest (each still does what the page expects), watches resource timing and
/// load errors, and posts what it sees to the daemon.
let observeJS = #"""
(() => {
  const handler = window.webkit?.messageHandlers?.umbraObserve;
  if (!handler || location.protocol === 'about:') return;
  const post = body => { try { handler.postMessage(body); } catch {} };
  post({kind: 'doc', url: location.href, mime: document.contentType, t: Date.now()});
  if (window === window.top) {
    const changed = () => post({kind: 'url', url: location.href, t: Date.now()});
    addEventListener('popstate', changed); addEventListener('hashchange', changed);
    for (const method of ['pushState', 'replaceState']) {
      const original = history[method];
      history[method] = function (...args) { const result = original.apply(this, args); changed(); return result; };
    }
  }

  const say = v => {
    if (typeof v === 'string') return v;
    if (v instanceof Error) return `${v.name}: ${v.message}`;
    if (v instanceof Node) return '<' + v.nodeName.toLowerCase() + (v.id ? '#' + v.id : '') + '>';
    try { const s = JSON.stringify(v); if (s !== undefined) return s; } catch {}
    return String(v);
  };
  const format = args => {
    const rest = [...args];
    let head = typeof rest[0] === 'string' ? rest.shift() : null;
    if (head !== null) head = head.replace(/%[sdifoOc%]/g, m => m === '%%' ? '%' : !rest.length ? m
      : m === '%c' ? (rest.shift(), '') : m === '%d' || m === '%i' ? String(parseInt(rest.shift()))
      : m === '%f' ? String(parseFloat(rest.shift())) : say(rest.shift()));
    return (head === null ? [] : [head]).concat(rest.map(say)).join(' ').slice(0, 4000);
  };
  // The caller's file and line: the first stack frame with one that is not this script's.
  const where = () => {
    for (const frame of (new Error().stack || '').split('\n')) {
      const m = /@(.+):(\d+):\d+$/.exec(frame);
      if (m && !m[1].startsWith('user-script:')) return {url: m[1], line: +m[2]};
    }
    return {};
  };
  for (const level of ['debug', 'log', 'info', 'warn', 'error']) {
    const original = console[level];
    if (typeof original !== 'function') continue;
    console[level] = function (...args) {
      post({kind: 'console', level, text: format(args), t: Date.now(), ...where()});
      return original.apply(this, args);
    };
  }
  const assert = console.assert;
  console.assert = function (ok, ...args) {
    if (!ok) post({kind: 'console', level: 'error', text: 'Assertion failed' + (args.length ? ': ' + format(args) : ''), t: Date.now(), ...where()});
    return assert.apply(this, arguments);
  };
  addEventListener('error', e => {
    if (e instanceof ErrorEvent) {
      post({kind: 'console', level: 'error', source: 'exception', text: e.message || say(e.error), url: e.filename, line: e.lineno, t: Date.now()});
    } else if (e.target instanceof Element) {
      const url = e.target.currentSrc || e.target.src || e.target.href;
      if (url) post({kind: 'net', via: 'error', type: e.target.localName, url: String(url), failed: true, error: 'failed to load', t: Date.now()});
    }
  }, true);
  addEventListener('unhandledrejection', e => {
    post({kind: 'console', level: 'error', source: 'rejection', text: 'Unhandled rejection: ' + say(e.reason), t: Date.now()});
  });

  const absolute = u => { try { return new URL(u, location.href).href; } catch { return String(u); } };
  const fetch0 = window.fetch;
  if (fetch0) window.fetch = function (input, init) {
    const request = input instanceof Request ? input : null;
    const method = String(init?.method || request?.method || 'GET').toUpperCase();
    const url = absolute(request ? request.url : input), t0 = performance.now();
    const done = fields => post({kind: 'net', via: 'fetch', type: 'fetch', method, url, duration: Math.round(performance.now() - t0), t: Date.now(), ...fields});
    post({kind: 'start'});
    return fetch0.apply(this, arguments).then(response => {
      const length = response.headers.get('content-length');
      done({status: response.status, mime: (response.headers.get('content-type') || '').split(';')[0], ...(length ? {size: +length} : {})});
      return response;
    }, error => {
      done({failed: true, error: String(error?.message ?? error)});
      throw error;
    });
  };
  const XHR = XMLHttpRequest.prototype, open0 = XHR.open, send0 = XHR.send, requests = new WeakMap();
  XHR.open = function (method, url) {
    requests.set(this, {method: String(method).toUpperCase(), url: absolute(url)});
    return open0.apply(this, arguments);
  };
  XHR.send = function () {
    const request = requests.get(this);
    if (request) {
      const t0 = performance.now();
      let error = 'request failed';
      post({kind: 'start'});
      for (const type of ['error', 'abort', 'timeout']) this.addEventListener(type, () => { error = type === 'error' ? 'network error' : type; });
      this.addEventListener('loadend', () => {
        const length = this.getResponseHeader('content-length');
        post({kind: 'net', via: 'xhr', type: 'xhr', ...request, duration: Math.round(performance.now() - t0), t: Date.now(),
              ...(this.status ? {status: this.status, mime: (this.getResponseHeader('content-type') || '').split(';')[0]} : {failed: true, error}),
              ...(length ? {size: +length} : {})});
      }, {once: true});
    }
    return send0.apply(this, arguments);
  };
  try {
    new PerformanceObserver(list => post({kind: 'timing', entries: list.getEntries().map(e => ({
      via: 'timing', type: e.initiatorType, url: e.name, duration: Math.round(e.duration), t: Math.round(performance.timeOrigin + e.startTime),
      ...((e.encodedBodySize || e.transferSize) ? {size: e.encodedBodySize || e.transferSize} : {}),
      ...(e.responseStatus ? {status: e.responseStatus} : {}),
    }))})).observe({type: 'resource', buffered: true});
  } catch {}
})();
"""#

/// What the capture lane keeps for each tab.
final class Observed {
    var console: [[String: Any]] = []  // the current page's last 500 messages
    var network: [[String: Any]] = []  // the current page's last 1000 requests
    var inflight = 0                   // fetch and XHR requests under way in the current page
    var blocked: Set<String> = []

    func keep(_ entry: [String: Any], in list: inout [[String: Any]], limit: Int) {
        list.append(entry)
        if list.count > limit { list.removeFirst(list.count - limit) }
    }

    /// Adds a request, folding together what different sources say about the same one: resource
    /// timing gives a fetch or XHR its size, and a load error marks a timed resource failed.
    func file(_ entry: [String: Any]) {
        let via = entry["via"] as? String, url = entry["url"] as? String
        func last(_ test: ([String: Any]) -> Bool) -> Int? {
            network.lastIndex { $0["url"] as? String == url && test($0) }
        }
        if via == "timing", ["fetch", "xmlhttprequest"].contains(entry["type"] as? String) {
            if let index = last({ ["fetch", "xhr"].contains($0["via"] as? String) && $0["size"] == nil }) {
                network[index]["size"] = entry["size"]
            }
            return
        }
        if via == "timing", let index = last({ $0["via"] as? String == "error" && $0["duration"] == nil }) {
            for key in ["duration", "size", "status"] { network[index][key] = entry[key] }
            return
        }
        if via == "error", let index = last({ $0["via"] as? String == "timing" && $0["failed"] == nil }) {
            network[index]["failed"] = true
            network[index]["error"] = entry["error"]
            return
        }
        keep(entry, in: &network, limit: 1000)
    }
}

func observed(_ tab: Tab) -> Observed {
    if let state = tab.extras["capture"] as? Observed { return state }
    let state = Observed()
    tab.extras["capture"] = state
    return state
}

/// In-flight fetch and XHR requests in the tab's current page: zero means the page's own scripts
/// are not waiting on the network (`wait --idle` can use this).
func inflightRequests(_ tab: Tab) -> Int {
    observed(tab).inflight
}

let stamp: ISO8601DateFormatter = {
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return formatter
}()

/// Receives what the instrumentation posts and files it under the tab it came from.
final class Observer: NSObject, WKScriptMessageHandler {
    static let shared = Observer()
    let tabs = NSMapTable<WKWebView, Tab>.weakToWeakObjects()

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let web = message.webView, let tab = tabs.object(forKey: web),
              let body = message.body as? [String: Any], let kind = body["kind"] as? String else { return }
        let state = observed(tab)
        let main = message.frameInfo.isMainFrame
        func entry(_ fields: [String: Any]) -> [String: Any] {
            var entry = fields
            entry["kind"] = nil
            if let t = entry.removeValue(forKey: "t") as? NSNumber {
                entry["time"] = stamp.string(from: Date(timeIntervalSince1970: t.doubleValue / 1000))
            }
            if !main { entry["frame"] = message.frameInfo.request.url?.absoluteString ?? "" }
            return entry
        }
        switch kind {
        case "url":
            if main { emitEvent(tab, "url", ["url": body["url"] ?? ""]) }
        case "console":
            state.keep(entry(body), in: &state.console, limit: 500)
        case "start":
            state.inflight += 1
        case "net":
            if ["fetch", "xhr"].contains(body["via"] as? String) { state.inflight = max(0, state.inflight - 1) }
            state.file(entry(body))
            emitEvent(tab, "network", entry(body))
        case "doc":
            var document = entry(body)
            document["type"] = "document"
            document["via"] = "document"
            if main {  // a new page: the logs start over, as in a browser's developer tools
                state.console = []
                state.network = []
                state.inflight = 0
                document["status"] = tab.status
            }
            state.keep(document, in: &state.network, limit: 1000)
        case "timing":
            for timing in body["entries"] as? [[String: Any]] ?? [] { state.file(entry(timing)) }
        default:
            break
        }
    }
}

/// Runs for every new tab before its first load. A pop-up shares its opener's content controller,
/// which then already has the script; the handler is replaced rather than added twice, which
/// would raise an exception and take the daemon down.
func instrument(_ tab: Tab) {
    Observer.shared.tabs.setObject(tab, forKey: tab.web)
    let controller = tab.web.configuration.userContentController
    if !controller.userScripts.contains(where: { $0.source == observeJS }) {
        controller.addUserScript(WKUserScript(source: observeJS, injectionTime: .atDocumentStart, forMainFrameOnly: false))
    }
    controller.removeScriptMessageHandler(forName: "umbraObserve", contentWorld: .page)
    controller.add(Observer.shared, contentWorld: .page, name: "umbraObserve")
    for kind in blockedEverywhere.sorted() {
        guard let list = ruleLists[kind] else { continue }
        controller.add(list)
        observed(tab).blocked.insert(kind)
    }
}

// MARK: Console and network

let levels = ["debug": 0, "log": 1, "info": 1, "warn": 2, "error": 3]

let consoleCommand: Command = Command(
    name: "console",
    help: "What the page logged: console.log/info/warn/error/debug calls, failed console.assert, uncaught exceptions and unhandled promise rejections, each with level, text, time and source URL and line, since the current page started loading (up to the last 500; iframes included). --level keeps that level and more severe ones (warn gives warnings and errors). --clear empties the log after returning it, so the next call shows only what is new.",
    params: [Param(name: "level", kind: .string, help: "Minimum level: debug, log, info, warn or error"),
             Param(name: "clear", kind: .boolean, help: "Empty the log after returning it")]
) { args, engine, reply in
    guard let tab = pick(args, engine, reply) else { return }
    let level = args.string("level")?.lowercased()
    guard level == nil || levels[level!] != nil else { return reply(.failure(UmbraError(64, "--level takes debug, log, info, warn or error"))) }
    let state = observed(tab)
    let entries = state.console.filter { levels[$0["level"] as? String ?? ""] ?? 1 >= levels[level ?? "debug"]! }
    if args.bool("clear") { state.console = [] }
    let lines = entries.map { entry -> String in
        let place = (entry["url"] as? String).map { " (\($0)" + ((entry["line"] as? NSNumber).map { ":\($0)" } ?? "") + ")" } ?? ""
        return "[\(entry["level"] ?? "log")] \(entry["text"] ?? "")" + place
    }
    reply(.success(["entries": entries, "count": entries.count,
                    "text": lines.isEmpty ? "no console messages" : lines.joined(separator: "\n")]))
}

let networkCommand: Command = Command(
    name: "network",
    help: "The tab's requests: page documents (with HTTP status and MIME type), fetch and XMLHttpRequest calls (method, status, duration, size) and every resource from the browser's resource timing (images, scripts, stylesheets…), plus resources that failed to load, since the current page started loading (up to the last 1000; iframes included). `inflight` counts fetch/XHR requests still running. --failed keeps errors and HTTP 4xx/5xx; --filter keeps URLs containing a substring; --clear empties the log after returning it.",
    params: [Param(name: "filter", kind: .string, help: "Substring the URL must contain"),
             Param(name: "failed", kind: .boolean, help: "Only failed requests and HTTP errors"),
             Param(name: "clear", kind: .boolean, help: "Empty the log after returning it")]
) { args, engine, reply in
    guard let tab = pick(args, engine, reply) else { return }
    let state = observed(tab)
    let filter = args.string("filter")?.lowercased()
    let entries = state.network.filter { entry in
        let status = (entry["status"] as? NSNumber)?.intValue ?? 0
        if args.bool("failed"), entry["failed"] == nil, status < 400 { return false }
        return filter.map { (entry["url"] as? String ?? "").lowercased().contains($0) } ?? true
    }.map { $0.filter { $0.key != "via" } }
    if args.bool("clear") { state.network = [] }
    let lines = entries.map { entry -> String in
        let status = (entry["status"] as? NSNumber).map { "\($0)" } ?? (entry["failed"] != nil ? "ERR" : "-")
        let duration = (entry["duration"] as? NSNumber).map { "\($0) ms" } ?? ""
        let size = (entry["size"] as? NSNumber).map { byteSize($0.intValue) } ?? ""
        let error = (entry["error"] as? String).map { "  (\($0))" } ?? ""
        return [status, entry["method"] as? String ?? "GET", entry["type"] as? String ?? "", duration, size, entry["url"] as? String ?? ""]
            .filter { !$0.isEmpty }.joined(separator: " ") + error
    }
    reply(.success(["entries": entries, "count": entries.count, "inflight": state.inflight,
                    "text": (lines.isEmpty ? "no requests" : lines.joined(separator: "\n")) + (state.inflight > 0 ? "\n\(state.inflight) in flight" : "")]))
}

// MARK: Blocking

/// Common analytics, advertising and session-recording hosts (and a host plus path prefix).
let trackers = [
    "google-analytics.com", "googletagmanager.com", "googletagservices.com", "doubleclick.net", "googlesyndication.com",
    "googleadservices.com", "adservice.google.com", "connect.facebook.net", "facebook.com/tr", "hotjar.com", "hotjar.io",
    "segment.com", "segment.io", "mixpanel.com", "mxpnl.com", "amplitude.com", "heapanalytics.com", "fullstory.com",
    "clarity.ms", "mouseflow.com", "crazyegg.com", "scorecardresearch.com", "quantserve.com", "chartbeat.com",
    "chartbeat.net", "criteo.com", "criteo.net", "taboola.com", "outbrain.com", "adnxs.com", "amazon-adsystem.com",
    "ads-twitter.com", "analytics.twitter.com", "bat.bing.com", "analytics.tiktok.com", "snap.licdn.com",
    "px.ads.linkedin.com", "adroll.com", "pubmatic.com", "rubiconproject.com", "openx.net", "moatads.com",
]

let resourceTypes = ["images": "image", "fonts": "font", "media": "media", "stylesheets": "style-sheet", "scripts": "script"]
let blockKinds = ["images", "fonts", "media", "stylesheets", "scripts", "trackers"]

var ruleLists: [String: WKContentRuleList] = [:]  // compiled once per daemon
var blockedEverywhere: Set<String> = []          // what `block --all` set, for tabs opened later

func rules(_ kind: String) -> String {
    let block = ["type": "block"]
    let list: [[String: Any]]
    if let type = resourceTypes[kind] {
        list = [["trigger": ["url-filter": ".*", "resource-type": [type]], "action": block]]
    } else {
        list = trackers.map { entry in
            let host = String(entry.prefix { $0 != "/" }), path = String(entry.dropFirst(host.count))
            let pattern = "^[^:]+://+([^:/]+\\.)?" + NSRegularExpression.escapedPattern(for: host)
                + (path.isEmpty ? "[:/]" : NSRegularExpression.escapedPattern(for: path))
            return ["trigger": ["url-filter": pattern], "action": block]
        }
    }
    return String(decoding: try! JSONSerialization.data(withJSONObject: list), as: UTF8.self)
}

func compile(_ kinds: [String], done: @escaping (Result<Void, UmbraError>) -> Void) {
    let group = DispatchGroup()
    var failure: UmbraError?
    for kind in kinds where ruleLists[kind] == nil {
        group.enter()
        WKContentRuleListStore.default().compileContentRuleList(forIdentifier: "umbra-block-\(kind)", encodedContentRuleList: rules(kind)) { list, error in
            if let list { ruleLists[kind] = list } else { failure = UmbraError(1, "could not compile the \(kind) rules: \(error?.localizedDescription ?? "")") }
            group.leave()
        }
    }
    group.notify(queue: .main) { done(failure.map { .failure($0) } ?? .success(())) }
}

let blockCommand: Command = Command(
    name: "block",
    help: "Stop the tab from loading kinds of content, to make pages faster and quieter: images, fonts, media, stylesheets, scripts, and trackers (common analytics and ad hosts such as Google Analytics, Tag Manager, DoubleClick, the Facebook pixel, Hotjar, Segment, Mixpanel and Amplitude). Adds to what is already blocked; --none stops all blocking. --all applies to every open tab and to tabs opened later. Takes effect from the next load (reload to apply it to the current page). With no arguments, reports what is blocked.",
    params: [Param(name: "types", kind: .array, help: "images, fonts, media, stylesheets, scripts, trackers (spaces or commas)", positional: true),
             Param(name: "none", kind: .boolean, help: "Stop blocking everything"),
             Param(name: "all", kind: .boolean, help: "Every open tab and tabs opened later")]
) { args, engine, reply in
    let kinds = args.strings("types").flatMap { $0.lowercased().split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) } }.filter { !$0.isEmpty }
    if let unknown = kinds.first(where: { !blockKinds.contains($0) }) {
        return reply(.failure(UmbraError(64, "cannot block \(unknown); choose from \(blockKinds.joined(separator: ", "))")))
    }
    var tabs: [Tab]
    if args.bool("all") { tabs = engine.tabs } else {
        guard let tab = pick(args, engine, reply) else { return }
        tabs = [tab]
    }
    func ownershipError() -> UmbraError? {
        if args.bool("all") { tabs = engine.tabs }
        if let error = tabs.compactMap({ engine.runtime.ownerError($0, args) }).first { return error }
        if args.bool("all"), !kinds.isEmpty || args.bool("none"), let busy = tabs.first(where: { $0.busy > 0 && $0.operation != args.operation }) {
            return UmbraError(1, "tab \(busy.name) is busy; retry --all after its command completes")
        }
        return nil
    }
    if let error = ownershipError() { return reply(.failure(error)) }
    func report() {
        let blocked = args.bool("all") ? blockedEverywhere : observed(tabs[0]).blocked
        let list = blockKinds.filter(blocked.contains)
        let whom = args.bool("all") ? "every tab" : tabs[0].name
        let note = (kinds.isEmpty && !args.bool("none")) ? "" : " (reload to apply to the current page)"
        reply(.success(["blocked": list, "tabs": tabs.map(\.name),
                        "text": list.isEmpty ? "blocking nothing in \(whom)" + note : "blocking \(list.joined(separator: ", ")) in \(whom)" + note]))
    }
    if args.bool("none") {
        for tab in tabs {
            let state = observed(tab)
            for kind in state.blocked { ruleLists[kind].map(tab.web.configuration.userContentController.remove) }
            state.blocked = []
        }
        if args.bool("all") { blockedEverywhere = [] }
        return report()
    }
    if kinds.isEmpty { return report() }
    let operation = args.operation
    var cancelled = false
    if let operation { engine.runtime.onCancel(operation) { cancelled = true } }
    compile(kinds) { result in
        guard !cancelled, operation.map(engine.runtime.isActive) ?? true else { return }
        if let error = ownershipError() { return reply(.failure(error)) }
        if case .failure(let error) = result { return reply(.failure(error)) }
        for tab in tabs {
            for kind in kinds where !observed(tab).blocked.contains(kind) {
                tab.web.configuration.userContentController.add(ruleLists[kind]!)
                observed(tab).blocked.insert(kind)
            }
        }
        if args.bool("all") { blockedEverywhere.formUnion(kinds) }
        report()
    }
}
