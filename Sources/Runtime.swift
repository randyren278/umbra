// Main-thread ownership, admission and per-tab scheduling for the shared browser.
import Foundation
import Dispatch
import Darwin

private final class MemoryHeadroom {
    let reserve = UInt64(runtimeLimit("UMBRA_MIN_AVAILABLE_MB", 512, 0...16384)) * 1048576
    let job = UInt64(runtimeLimit("UMBRA_JOB_MB", 128, 1...4096)) * 1048576
    private let host = mach_host_self()
    private var checked = Date.distantPast
    private var sample: UInt64?

    deinit { mach_port_deallocate(mach_task_self_, host) }

    var available: UInt64? {
        guard Date().timeIntervalSince(checked) >= 1 else { return sample }
        checked = Date()
        var stats = vm_statistics64_data_t()
        var count = mach_msg_type_number_t(MemoryLayout<vm_statistics64_data_t>.stride / MemoryLayout<integer_t>.stride)
        let status = withUnsafeMutablePointer(to: &stats) { pointer in
            pointer.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
                host_statistics64(host, host_flavor_t(HOST_VM_INFO64), $0, &count)
            }
        }
        sample = status == KERN_SUCCESS ? (UInt64(stats.free_count) + UInt64(stats.inactive_count)) * UInt64(vm_page_size) : nil
        return sample
    }
}

final class Runtime {
    struct Lease {
        let agent: String
        let task: String
        var expires: Date
        var json: [String: Any] { ["agent": agent, "task": task, "expires": expires.timeIntervalSince1970] }
    }
    final class Job {
        let id: String
        let operation: String
        let command: Command
        let args: Args
        let tab: Tab?
        let lane: String
        let accepted = Date()
        let deadline: Date
        let reply: Reply
        let bytes: Int
        var started: Date?
        var scratchTabs: [Tab] = []
        var onCancel: [() -> Void] = []
        var onFinish: [() -> Void] = []
        var failureDetails: (() -> [String: Any])?
        var timer: DispatchSourceTimer?
        var finished = false
        var coordinator: Bool { ["batch", "workflow", "replay", "events", "diagnose"].contains(command.name) }
        init(_ id: String, _ operation: String, _ command: Command, _ args: Args, _ tab: Tab?, _ lane: String, _ deadline: Date, _ reply: @escaping Reply) {
            self.id = id; self.operation = operation; self.command = command; self.args = args; self.tab = tab
            self.lane = lane; self.deadline = deadline; self.reply = reply
            self.bytes = (try? JSONSerialization.data(withJSONObject: args.values).count) ?? 0
        }
        var json: [String: Any] {
            ["request": id, "command": command.name, "tab": tab?.name as Any? ?? NSNull(),
             "agent": args.string("agent") ?? "", "task": args.string("task") ?? "",
             "state": started == nil ? "queued" : "running", "accepted": accepted.timeIntervalSince1970,
             "deadline": deadline.timeIntervalSince1970]
        }
    }
    unowned let engine: Engine
    let pendingLimit = runtimeLimit("UMBRA_PENDING", 256, 1...4096)
    let queueByteLimit = runtimeLimit("UMBRA_QUEUE_BYTES", 32 * 1024 * 1024, 1024...268435456)
    let activeLimit = runtimeLimit("UMBRA_ACTIVE", 32, 1...256)
    let tabLimit = runtimeLimit("UMBRA_TABS", 128, 1...1024)
    var jobs: [Job] = []
    var leases: [String: Lease] = [:]
    private var cancellations: [String: (agent: String?, task: String?, until: Date)] = [:]
    var history: [[String: Any]] = []
    var completed = 0, rejected = 0, cancelled = 0, timedOut = 0, peakQueued = 0, peakActive = 0
    var connectionCount: (() -> Int)?
    var pressure = "normal"
    private let headroom = MemoryHeadroom()
    var effectiveActiveLimit: Int {
        let limit = pressure == "critical" ? 0 : pressure == "warning" ? max(1, activeLimit / 2) : activeLimit
        guard limit > 0, let available = headroom.available else { return limit }
        guard available > headroom.reserve else { return 0 }
        return min(limit, max(1, Int((available - headroom.reserve) / headroom.job)))
    }
    var reclaimed = 0
    private var draining = false
    private var finishing = 0
    private var admissionTimer: DispatchSourceTimer?
    private var pressureSource: DispatchSourceMemoryPressure?

    init(_ engine: Engine) {
        self.engine = engine
        let source = DispatchSource.makeMemoryPressureSource(eventMask: [.normal, .warning, .critical], queue: .main)
        source.setEventHandler { [weak self] in
            guard let self, let source = self.pressureSource else { return }
            let event = source.data
            self.pressure = event.contains(.critical) ? "critical" : event.contains(.warning) ? "warning" : "normal"
            self.reclaimed += self.engine.reclaim(critical: self.pressure == "critical", pressured: self.pressure != "normal")
            self.drain()
        }
        source.resume()
        pressureSource = source
    }

    func lease(_ tab: Tab) -> Lease? {
        guard let lease = leases[tab.name] else { return nil }
        if lease.expires <= Date(), tab.busy == 0, !jobs.contains(where: { $0.tab === tab }) { leases.removeValue(forKey: tab.name); return nil }
        return lease
    }

    func ownerError(_ tab: Tab, _ args: Args) -> UmbraError? {
        guard let owner = lease(tab), owner.agent != args.string("agent") || owner.task != (args.string("task") ?? "") else { return nil }
        return UmbraError(64, "tab \(tab.name) is leased by another agent/task; provide its matching --agent and --task")
    }

    func submit(_ command: Command, _ input: Args, _ reply: @escaping Reply) {
        if let parentID = input.string("_parent") {
            guard let parent = jobs.first(where: { $0.id == parentID && !$0.finished && $0.deadline > Date() }),
                  input.string("_parentOperation").map({ $0 == parent.operation }) ?? true,
                  parent.args.string("agent") == input.string("agent"), parent.args.string("task") == input.string("task") else {
                return reply(.failure(UmbraError(2, "parent request is no longer active or has different ownership")))
            }
        }
        let controls = ["runtime", "tasks", "cancel", "stop", "status", "processes"]
        if controls.contains(command.name) { return command.run(input, engine, reply) }
        let id = input.string("request") ?? UUID().uuidString.lowercased()
        cancellations = cancellations.filter { $0.value.until > Date() }
        if let cancellation = cancellations[id], cancellation.agent == input.string("agent"), cancellation.task == input.string("task") {
            cancellations[id] = nil
            return reply(.failure(UmbraError(2, "request cancelled before dispatch")))
        }
        guard !id.isEmpty, id.count <= 256, !jobs.contains(where: { $0.id == id }) else { return reply(.failure(UmbraError(64, "request must be a unique nonempty identifier of at most 256 characters"))) }
        guard ["agent", "task", "tab", "name"].allSatisfy({ (input.string($0)?.count ?? 0) <= 256 }) else { return reply(.failure(UmbraError(64, "agent, task and tab labels must be at most 256 characters"))) }
        let bytes = (try? JSONSerialization.data(withJSONObject: input.values).count) ?? 0
        guard jobs.reduce(0, { $0 + $1.bytes }) + bytes + 512 <= queueByteLimit else { rejected += 1; return reply(.failure(UmbraError(1, "runtime argument byte budget exhausted; retry after an active task completes"))) }
        guard jobs.count < pendingLimit else { rejected += 1; return reply(.failure(UmbraError(1, "runtime queue full (\(pendingLimit)); retry after an active task completes"))) }
        let seconds = input.number("timeout") ?? command.timeout
        guard seconds.isFinite, seconds > 0, seconds <= 86400 else { return reply(.failure(UmbraError(64, "timeout must be between 0 and 86400 seconds"))) }
        let deadlineNumber = input.number("deadline") ?? Date().addingTimeInterval(seconds).timeIntervalSince1970
        guard deadlineNumber.isFinite else { return reply(.failure(UmbraError(64, "deadline must be finite UNIX seconds"))) }
        var deadline = min(Date().addingTimeInterval(seconds), Date(timeIntervalSince1970: deadlineNumber))
        if let parentID = input.string("_parent") {
            guard let parent = jobs.first(where: { $0.id == parentID && !$0.finished }),
                  input.string("_parentOperation").map({ $0 == parent.operation }) ?? true,
                  parent.args.string("agent") == input.string("agent"), parent.args.string("task") == input.string("task") else {
                return reply(.failure(UmbraError(2, "parent request is no longer active or has different ownership")))
            }
            deadline = min(deadline, parent.deadline)
        }
        guard deadline > Date() else { return reply(.failure(UmbraError(2, "request deadline has already elapsed"))) }
        var values = input.values
        let operation = UUID().uuidString.lowercased()
        values["_operation"] = operation
        values["_parentOperation"] = input.string("_parent").flatMap { id in jobs.first(where: { $0.id == id && !$0.finished })?.operation }
        if command.name == "open", normalizeURL(input.string("url") ?? "") == nil {
            return reply(.failure(UmbraError(64, "not a URL: \(input.string("url") ?? "")")))
        }
        var tab: Tab?
        if command.usesTab {
            if command.name == "open", input.bool("new"), let name = input.string("tab"), engine.tabs.contains(where: { $0.name == name }) {
                return reply(.failure(UmbraError(64, "a tab named \(name) is open")))
            }
            if command.name == "open", (input.bool("new") || (try? engine.tab(input).get()) == nil) {
                if let error = admissionError(newTab: true) { rejected += 1; return reply(.failure(error)) }
                tab = engine.open(name: input.string("tab"), select: false)
                values["new"] = false
            } else {
                switch engine.tab(input) { case .success(let found): tab = found; case .failure(let error): return reply(.failure(error)) }
            }
        } else if command.name == "tab", ["close", "select"].contains(input.string("action") ?? "") {
            tab = input.string("name").flatMap { name in engine.tabs.first { $0.name == name } } ?? engine.current
            if values["name"] == nil { values["name"] = tab?.name }
        } else if command.name == "tab", input.string("action") == "new", engine.tabs.count >= tabLimit || pressure == "critical" {
            rejected += 1; return reply(.failure(UmbraError(1, "new tab admission paused: tab limit or critical memory pressure")))
        }
        if !command.usesTab, input.bool("all"), let protected = engine.tabs.first(where: { ownerError($0, input) != nil }) {
            return reply(.failure(UmbraError(64, "tab \(protected.name) is leased; --all requires ownership of every tab")))
        }
        if let tab {
            if let error = ownerError(tab, input) { return reply(.failure(error)) }
            values["tab"] = tab.name
        }
        values["request"] = id
        let job = Job(id, operation, command, Args(values: values), tab, tab.map { "tab:\($0.name)" } ?? (["batch", "workflow", "replay"].contains(command.name) ? "coordinator:\(id)" : "global:\(id)"), deadline, reply)
        jobs.append(job)
        peakQueued = max(peakQueued, jobs.filter { $0.started == nil }.count)
        let timer = DispatchSource.makeTimerSource(queue: .main)
        job.timer = timer
        timer.schedule(deadline: .now() + max(0, deadline.timeIntervalSinceNow))
        timer.setEventHandler { [weak self, weak job] in
            guard let self, let job, !job.finished else { return }
            self.timedOut += 1
            self.finish(job, .failure(command.deadlineError?(job.args) ?? UmbraError(2, "\(command.name) exceeded its request deadline")), interrupt: true)
        }
        timer.resume()
        drain()
    }

    func drain() {
        guard !draining, finishing == 0 else { return }
        draining = true
        defer { draining = false }
        if jobs.contains(where: { $0.started == nil }), effectiveActiveLimit == 0 {
            if admissionTimer == nil {
                let timer = DispatchSource.makeTimerSource(queue: .main)
                timer.schedule(deadline: .now() + 1, repeating: 1)
                timer.setEventHandler { [weak self] in self?.drain() }
                admissionTimer = timer; timer.resume()
            }
        } else { admissionTimer?.setEventHandler {}; admissionTimer?.cancel(); admissionTimer = nil }
        var blocked = Set(jobs.filter { $0.started != nil }.map(\.lane))
        var active = jobs.filter { $0.started != nil && !$0.coordinator }.count
        for job in Array(jobs) where !job.finished && job.started == nil {
            if job.deadline <= Date() {
                timedOut += 1
                finish(job, .failure(UmbraError(2, "request deadline elapsed in queue")), interrupt: true)
                continue
            }
            let cleanup = (job.command.name == "tab" && job.args.string("action") == "close")
                || (job.command.name == "lease" && job.args.bool("release"))
                || (job.command.name == "record" && job.args.string("action") == "stop")
                || (job.command.name == "inspector" && job.args.bool("stop"))
            if active >= effectiveActiveLimit && !job.coordinator && !cleanup { continue }
            if blocked.contains(job.lane) { continue }
            blocked.insert(job.lane)
            if let tab = job.tab {
                guard engine.tabs.contains(where: { $0 === tab }) else { finish(job, .failure(UmbraError(64, "target tab closed before request started"))); continue }
                if let error = ownerError(tab, job.args) { finish(job, .failure(error)); continue }
                guard tab.handoff == nil || job.command.name == "handoff" else { finish(job, .failure(UmbraError(1, "tab is under human control"))); continue }
                tab.busy += 1
                tab.operation = job.operation
            }
            job.started = Date(); if !job.coordinator { active += 1 }
            peakActive = max(peakActive, active)
            let execute = { [weak self, weak job] in
                guard let self, let job, !job.finished else { return }
                job.command.run(job.args, self.engine) { [weak self, weak job] result in
                    guard let self, let job else { return }; self.finish(job, result)
                }
            }
            if let tab = job.tab { tab.wake(then: execute) } else { execute() }
        }
        // Synchronous commands may have released a slot while drain was traversing the snapshot.
        let occupied = Set(jobs.filter { $0.started != nil }.map(\.lane))
        if jobs.contains(where: { $0.started == nil && !occupied.contains($0.lane) }), jobs.filter({ $0.started != nil && !$0.coordinator }).count < effectiveActiveLimit {
            DispatchQueue.main.async { [weak self] in self?.drain() }
        }
    }

    func finish(_ job: Job, _ result: Result<[String: Any], UmbraError>, interrupt: Bool = false) {
        guard !job.finished else { return }
        var answer = result
        if case .failure(let error) = result, let details = job.failureDetails?() {
            answer = .failure(UmbraError(error.code, error.message, details: details.merging(error.details) { _, actual in actual }))
        }
        job.failureDetails = nil
        job.timer?.setEventHandler {}; job.timer?.cancel(); job.timer = nil
        finishing += 1
        defer { finishing -= 1; if finishing == 0 { drain() } }
        job.finished = true
        jobs.removeAll { $0 === job }
        let finished = job.onFinish; job.onFinish = []
        finished.forEach { $0() }
        if interrupt {
            let cleanup = job.onCancel
            job.onCancel = []
            cleanup.forEach { $0() }
            for tab in job.scratchTabs where engine.tabs.contains(where: { $0 === tab }) {
                tab.cancelOperation()
                engine.close(tab)
            }
            for child in Array(jobs) where child.args.string("_parent") == job.id && child.args.string("_parentOperation") == job.operation {
                finish(child, .failure(UmbraError(2, "parent request interrupted")), interrupt: true)
            }
        }
        if job.started != nil, let tab = job.tab {
            if interrupt { tab.cancelOperation() }
            tab.busy = max(0, tab.busy - 1)
            tab.operation = nil
            tab.lastUsed = Date()
        }
        completed += 1
        var record = job.json
        record["state"] = interrupt ? "interrupted" : "completed"
        record["elapsedMs"] = Date().timeIntervalSince(job.accepted) * 1000
        record["queueMs"] = (job.started ?? Date()).timeIntervalSince(job.accepted) * 1000
        if case .failure(let error) = result { record["errorCode"] = Int(error.code) }
        history.append(record)
        if history.count > 128 { history.removeFirst(history.count - 128) }
        job.reply(answer.map { job.command.name == "keep-signed-in" ? $0 : $0.merging(["request": job.id]) { old, _ in old } })
    }

    func cancel(_ id: String, args: Args) -> Result<[String: Any], UmbraError> {
        guard let job = jobs.first(where: { $0.id == id }) else {
            if args.bool("_pending"), !id.isEmpty, id.count <= 256 {
                cancellations = cancellations.filter { $0.value.until > Date() }
                guard cancellations.count < 256 else { return .failure(UmbraError(1, "pending cancellation budget exhausted")) }
                cancellations[id] = (args.string("agent"), args.string("task"), Date().addingTimeInterval(120))
                return .success(["cancelled": id, "beforeDispatch": true, "text": "cancelled before dispatch"])
            }
            return .failure(UmbraError(64, "no pending request \(id)"))
        }
        guard job.args.string("agent") == args.string("agent"), job.args.string("task") == args.string("task") else { return .failure(UmbraError(64, "cancellation requires the request's matching agent/task")) }
        cancelled += 1
        let running = job.started != nil
        finish(job, .failure(UmbraError(2, "request \(id) cancelled")), interrupt: true)
        return .success(["cancelled": id, "wasRunning": running, "text": "cancelled \(id)", "limitation": "Cancellation stops queued work and browser wait continuations. JavaScript or side effects already dispatched cannot be rolled back."])
    }

    func admissionError(newTab: Bool) -> UmbraError? {
        if newTab, engine.tabs.count >= tabLimit || pressure == "critical" { return UmbraError(1, "new tab admission paused: tab limit or critical memory pressure") }
        if newTab, let available = headroom.available, available <= headroom.reserve { return UmbraError(1, "new tab admission paused: estimated system memory headroom below reserve") }
        return nil
    }

    func onCancel(_ operation: String, _ action: @escaping () -> Void) {
        guard let job = jobs.first(where: { $0.operation == operation && !$0.finished }) else { return action() }
        job.onCancel.append(action)
    }

    func onFinish(_ operation: String, _ action: @escaping () -> Void) {
        guard let job = jobs.first(where: { $0.operation == operation && !$0.finished }) else { return action() }
        job.onFinish.append(action)
    }

    func failureContext(_ operation: String, _ context: @escaping () -> [String: Any]) {
        jobs.first(where: { $0.operation == operation && !$0.finished })?.failureDetails = context
    }

    var memoryStatus: [String: Any] {
        var info = task_vm_info_data_t()
        var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<integer_t>.size)
        let result = withUnsafeMutablePointer(to: &info) { pointer in
            pointer.withMemoryRebound(to: integer_t.self, capacity: Int(count)) { task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &count) }
        }
        guard result == KERN_SUCCESS else { return ["available": false, "scope": "daemon only; excludes WebKit helpers"] }
        return ["available": true, "physicalFootprintBytes": info.phys_footprint, "residentBytes": info.resident_size,
                "scope": "daemon only; excludes WebKit helpers"]
    }

    @discardableResult
    func trackScratch(_ tab: Tab, operation: String?) -> Bool {
        guard let operation, let job = jobs.first(where: { $0.operation == operation && !$0.finished }) else { return false }
        tab.operation = operation
        job.scratchTabs.append(tab)
        return true
    }

    func isActive(_ operation: String) -> Bool { jobs.contains { $0.operation == operation && !$0.finished } }

    func run(command name: String, args: [String: Any], reply: @escaping Reply) {
        guard let command = lookup(name) else { return reply(.failure(UmbraError(64, "unknown command: \(name)"))) }
        submit(command, Args(values: args), reply)
    }

    func snapshot() -> [String: Any] {
        status.merging(["pending": jobs.map(\.json), "history": history,
                        "leases": engine.tabs.compactMap { tab -> [String: Any]? in
                            guard let owner = lease(tab) else { return nil }
                            return owner.json.merging(["tab": tab.name]) { _, new in new }
                        }]) { _, new in new }
    }

    var status: [String: Any] {
        let active = jobs.filter { $0.started != nil && !$0.coordinator }.count
        let coordinators = jobs.filter { $0.started != nil && $0.coordinator }.count
        let queued = jobs.filter { $0.started == nil }.count
        return ["active": active, "coordinators": coordinators, "connections": connectionCount?() ?? 0, "effectiveActiveLimit": effectiveActiveLimit, "queued": queued, "completed": completed,
                "cancelled": cancelled, "timedOut": timedOut, "rejected": rejected,
                "peakActive": peakActive, "peakQueued": peakQueued, "pressure": pressure,
                "reclaimedTabs": reclaimed, "tabs": engine.tabs.count, "memory": memoryStatus,
                "headroom": ["estimatedAvailableBytes": headroom.available as Any? ?? NSNull(), "reserveBytes": headroom.reserve,
                             "perActiveJobEstimateBytes": headroom.job, "scope": "system free (includes speculative) + inactive pages; estimate, not a process memory budget"],
                "argumentBytes": jobs.reduce(0, { $0 + $1.bytes }),
                "limits": ["pending": pendingLimit, "active": activeLimit, "tabs": tabLimit, "argumentBytes": queueByteLimit,
                           "connections": runtimeLimit("UMBRA_CONNECTIONS", 64, 2...1024), "requestBytes": runtimeLimit("UMBRA_REQUEST_BYTES", 8 * 1024 * 1024, 1024...67108864)],
                "text": "\(active) active · \(queued) queued · \(completed) completed · memory \(pressure)"]
    }
}

func runtimeLimit(_ key: String, _ fallback: Int, _ range: ClosedRange<Int>) -> Int {
    guard let text = environment[key], let value = Int(text), range.contains(value) else { return fallback }
    return value
}

func runtimeCommands() -> [Command] {
    [
        Command(name: "runtime", help: "Inspect bounded scheduler, memory pressure, admission and queue counters.", params: [Param(name: "reclaim", kind: .boolean, help: "Reclaim clean abandoned tabs using critical-pressure policy")], usesTab: false) { args, engine, reply in
            if args.bool("reclaim") { engine.runtime.reclaimed += engine.reclaim(critical: true) }
            reply(.success(engine.runtime.snapshot()))
        },
        Command(name: "tasks", help: "List pending requests and the last 128 completion records (no command arguments).", usesTab: false) { _, engine, reply in
            reply(.success(["pending": engine.runtime.jobs.map(\.json), "history": engine.runtime.history, "text": "\(engine.runtime.jobs.count) pending requests"]))
        },
        Command(name: "cancel", help: "Cancel a request by its --request identifier; requires matching agent/task.", params: [Param(name: "id", kind: .string, help: "Request identifier", required: true, positional: true)], usesTab: false) { args, engine, reply in
            reply(engine.runtime.cancel(args.string("id") ?? "", args: args))
        },
        Command(name: "lease", help: "Acquire or renew exclusive agent/task ownership of a tab; release with --release.", params: [Param(name: "seconds", kind: .number, help: "Lease duration, 1–86400 seconds (default 300)"), Param(name: "release", kind: .boolean, help: "Release this agent/task's lease")]) { args, engine, reply in
            guard case .success(let tab) = engine.tab(args) else { return reply(.failure(UmbraError(64, "no target tab"))) }
            guard let agent = args.string("agent"), !agent.isEmpty, agent.count <= 256, (args.string("task") ?? "").count <= 256 else { return reply(.failure(UmbraError(64, "lease requires --agent (at most 256 characters)"))) }
            if let error = engine.runtime.ownerError(tab, args) { return reply(.failure(error)) }
            if args.bool("release") {
                engine.runtime.leases.removeValue(forKey: tab.name)
                return reply(.success(["released": tab.name, "text": "released \(tab.name)"]))
            }
            let seconds = args.number("seconds") ?? 300
            guard seconds.isFinite, seconds >= 1, seconds <= 86400 else { return reply(.failure(UmbraError(64, "seconds must be 1–86400"))) }
            let owner = Runtime.Lease(agent: agent, task: args.string("task") ?? "", expires: Date().addingTimeInterval(seconds))
            engine.runtime.leases[tab.name] = owner
            reply(.success(["tab": tab.name, "lease": owner.json, "text": "leased \(tab.name) to \(agent)"]))
        }
    ]
}
