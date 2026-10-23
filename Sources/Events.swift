import Foundation

private final class EventLog {
    var sequence = 0
    var entries: [[String: Any]] = []
    var waiters: [String: (Set<String>, Int, Reply, DispatchSourceTimer)] = [:]

    func emit(_ kind: String, _ fields: [String: Any]) {
        sequence += 1
        var entry: [String: Any] = ["sequence": sequence, "kind": kind, "time": Date().timeIntervalSince1970]
        for (key, value) in fields where ["url", "status", "method", "state", "type", "failed", "bytes"].contains(key) {
            entry[key] = (value as? String).map { String($0.prefix(2048)) } ?? value
        }
        entries.append(entry)
        if entries.count > 256 { entries.removeFirst(entries.count - 256) }
        for (id, waiter) in Array(waiters) where waiter.0.isEmpty || waiter.0.contains(kind) { finish(id, since: waiter.1, kinds: waiter.0) }
    }

    func result(since: Int, kinds: Set<String>) -> [String: Any] {
        let selected = entries.filter { ($0["sequence"] as? Int ?? 0) > since && (kinds.isEmpty || kinds.contains($0["kind"] as? String ?? "")) }
        return ["events": selected, "cursor": sequence, "truncated": since < (entries.first?["sequence"] as? Int ?? 1) - 1,
                "text": "\(selected.count) events · cursor \(sequence)"]
    }

    func finish(_ id: String, since: Int, kinds: Set<String>) {
        guard let waiter = waiters.removeValue(forKey: id) else { return }
        waiter.3.setEventHandler {}; waiter.3.cancel()
        waiter.2(.success(result(since: since, kinds: kinds)))
    }

    func cancel(_ id: String) {
        guard let waiter = waiters.removeValue(forKey: id) else { return }
        waiter.3.setEventHandler {}; waiter.3.cancel()
    }

    func close() {
        let old = waiters; waiters = [:]
        for waiter in old.values { waiter.3.setEventHandler {}; waiter.3.cancel(); waiter.2(.failure(UmbraError(4, "event target tab closed"))) }
    }
}

func emitEvent(_ tab: Tab, _ kind: String, _ fields: [String: Any] = [:]) {
    (tab.extras["events"] as? EventLog)?.emit(kind, fields)
}

func closeEvents(_ tab: Tab) { (tab.extras["events"] as? EventLog)?.close() }

func eventCommands() -> [Command] {
    [Command(name: "events", help: "Subscribe to bounded native URL/dialog/download/network events for a named tab without blocking its action queue. Logging starts on first use, holds 256 metadata events, and never stores response bodies or cookie/header values. --wait returns on a matching event or at its duration.",
             params: [Param(name: "target", kind: .string, help: "Named tab to observe", required: true, positional: true),
                      Param(name: "since", kind: .number, help: "Last cursor (default: current cursor)"),
                      Param(name: "kind", kind: .array, help: "url, dialog, download or network (default all)"),
                      Param(name: "wait", kind: .number, help: "Event-driven wait seconds, 0...25 (default 0)")], usesTab: false) { args, engine, reply in
        guard let name = args.string("target"), let tab = engine.tabs.first(where: { $0.name == name }) else { return reply(.failure(UmbraError(64, "no tab named event target"))) }
        if let error = engine.runtime.ownerError(tab, args) { return reply(.failure(error)) }
        let kinds = Set(args.strings("kind"))
        guard kinds.isSubset(of: ["url", "dialog", "download", "network"]) else { return reply(.failure(UmbraError(64, "unknown event kind"))) }
        let seconds = args.number("wait") ?? 0, since = args.number("since")
        guard seconds.isFinite, seconds >= 0, seconds <= 25,
              since == nil || (since!.isFinite && since! >= 0 && since!.rounded() == since! && since! <= Double(Int.max / 2)) else { return reply(.failure(UmbraError(64, "invalid event cursor or wait duration"))) }
        let log: EventLog
        if let existing = tab.extras["events"] as? EventLog { log = existing }
        else { log = EventLog(); tab.extras["events"] = log }
        let cursor = since.map(Int.init) ?? log.sequence
        let result = log.result(since: cursor, kinds: kinds)
        guard seconds > 0, (result["events"] as? [Any])?.isEmpty == true else { return reply(.success(result)) }
        guard log.waiters.count < 16, let request = args.operation else { return reply(.failure(UmbraError(1, "event waiter budget exhausted"))) }
        let timer = DispatchSource.makeTimerSource(queue: .main)
        timer.schedule(deadline: .now() + seconds)
        timer.setEventHandler { [weak log] in log?.finish(request, since: cursor, kinds: kinds) }
        log.waiters[request] = (kinds, cursor, reply, timer)
        timer.resume()
        engine.runtime.onCancel(request) { log.cancel(request) }
    }]
}
