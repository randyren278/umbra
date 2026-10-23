import Foundation
import CryptoKit

private let executionLimit = 64
private let definitionLimit = 262144

private struct Step {
    let command: String
    let args: [String: Any]
    let before: [String: Any]?
    let after: [String: Any]?
    let repeatable: Bool
}

private func steps(_ value: Any) throws -> [Step] {
    guard let rows = value as? [[String: Any]], !rows.isEmpty, rows.count <= executionLimit else {
        throw UmbraError(64, "steps must contain 1...\(executionLimit) command objects")
    }
    let forbidden = Set(["batch", "workflow", "stop", "lease", "cancel", "runtime", "tasks", "sign-in", "keep-signed-in", "inspector", "record"])
    return try rows.map { row in
        guard let name = row["command"] as? String, let command = lookup(name), !forbidden.contains(command.name),
              row["args"] == nil || row["args"] is [String: Any] else {
            throw UmbraError(64, "each step needs a supported non-coordinator command and an args object")
        }
        let args = row["args"] as? [String: Any] ?? [:]
        let params = parameters(command)
        for (key, value) in args {
            guard let param = params.first(where: { $0.name == key }) else { throw UmbraError(64, "\(name) has no parameter \(key)") }
            switch param.kind {
            case .string: guard value is String else { throw UmbraError(64, "\(key) must be a string") }
            case .number: guard let n = value as? NSNumber, CFGetTypeID(n) != CFBooleanGetTypeID(), n.doubleValue.isFinite else { throw UmbraError(64, "\(key) must be a number") }
            case .boolean: guard let n = value as? NSNumber, CFGetTypeID(n) == CFBooleanGetTypeID() else { throw UmbraError(64, "\(key) must be a boolean") }
            case .array: guard value is [String] else { throw UmbraError(64, "\(key) must be an array of strings") }
            }
        }
        if let missing = command.params.first(where: { $0.required && args[$0.name] == nil }) { throw UmbraError(64, "\(name) needs \(missing.name)") }
        for key in ["before", "after"] where row[key] != nil {
            guard let condition = row[key] as? [String: Any], !condition.isEmpty,
                  Set(condition.keys).isSubset(of: ["js", "url", "text", "selector", "gone"]), condition.values.allSatisfy({ $0 is String }) else {
                throw UmbraError(64, "\(key) must contain string wait conditions: js, url, text, selector or gone")
            }
        }
        return Step(command: command.name, args: args, before: row["before"] as? [String: Any],
                    after: row["after"] as? [String: Any], repeatable: row["repeatable"] as? Bool ?? false)
    }
}

private func parseJSON(_ text: String) throws -> Any {
    guard text.utf8.count <= definitionLimit, let data = text.data(using: .utf8) else { throw UmbraError(64, "definition exceeds 256 KiB") }
    do { return try JSONSerialization.jsonObject(with: data) }
    catch { throw UmbraError(64, "invalid JSON definition") }
}

private func bindings(_ object: Any, _ variables: [String: Any]) throws -> Any {
    if let text = object as? String {
        if text.hasPrefix("${"), text.hasSuffix("}"), text.filter({ $0 == "$" }).count == 1,
           let value = variables[String(text.dropFirst(2).dropLast())] { return value }
        var result = text
        for (key, value) in variables {
            guard value is String || value is NSNumber else { throw UmbraError(64, "variables must be strings, numbers or booleans") }
            result = result.replacingOccurrences(of: "${\(key)}", with: String(describing: value))
        }
        if result.contains("${") { throw UmbraError(64, "workflow has an unbound variable") }
        return result
    }
    if let rows = object as? [Any] { return try rows.map { try bindings($0, variables) } }
    if let dict = object as? [String: Any] { return try dict.mapValues { try bindings($0, variables) } }
    return object
}

private final class Execution {
    let engine: Engine
    let args: Args
    let plan: [Step]
    let reply: Reply
    let checkpoint: URL?
    let digest: String
    var index = 0
    var phase = "ready"
    var receipts: [[String: Any]] = []
    var resultBytes = 0
    var dispatched = false
    var activeIndex = 0
    var stage = "ready"
    var lock: Int32 = -1

    init(_ engine: Engine, _ args: Args, _ plan: [Step], checkpoint: URL? = nil, digest: String = "", reply: @escaping Reply) {
        self.engine = engine; self.args = args; self.plan = plan; self.checkpoint = checkpoint; self.digest = digest; self.reply = reply
        if let request = args.operation { engine.runtime.failureContext(request) { self.failureDetails() } }
    }

    deinit { if lock >= 0 { flock(lock, LOCK_UN); close(lock) } }

    func acquire() throws {
        guard let checkpoint else { return }
        lock = open(checkpoint.path + ".lock", O_CREAT | O_RDWR | O_CLOEXEC, 0o600)
        guard lock >= 0, flock(lock, LOCK_EX | LOCK_NB) == 0 else { throw UmbraError(1, "checkpoint is already in use or cannot be locked") }
    }

    func write(_ phase: String) throws {
        self.phase = phase
        guard let checkpoint else { return }
        let value: [String: Any] = ["version": 1, "digest": digest, "index": index, "phase": phase,
                                   "steps": plan.count, "updated": Date().timeIntervalSince1970]
        do {
            try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]).write(to: checkpoint, options: [.atomic])
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: checkpoint.path)
        } catch { throw UmbraError(1, "cannot write workflow checkpoint") }
    }

    func context(_ input: [String: Any], tab: Any? = nil) -> [String: Any] {
        var values = input
        for name in ["agent", "task", "deadline"] { if let value = args.values[name] { values[name] = value } }
        values["request"] = nil
        values["_parent"] = args.string("request")
        values["_parentOperation"] = args.operation
        if values["tab"] == nil { values["tab"] = tab ?? args.values["tab"] }
        if let timeout = args.number("timeout") { values["timeout"] = min((values["timeout"] as? Double) ?? timeout, timeout) }
        return values
    }

    func condition(_ condition: [String: Any]?, tab: Any?, _ done: @escaping (Result<Void, UmbraError>) -> Void) {
        guard let condition else { return done(.success(())) }
        var values = context(condition, tab: tab)
        values["timeout"] = min((values["timeout"] as? Double) ?? 5, 5)
        engine.runtime.run(command: "wait", args: values) { result in done(result.map { _ in () }) }
    }

    func failureDetails(at stage: String? = nil) -> [String: Any] {
        let step = min(activeIndex + 1, plan.count)
        var details: [String: Any] = ["completed": false, "count": plan.count, "results": receipts,
                                    "failedStep": ["step": step, "command": plan[max(0, step - 1)].command,
                                                   "stage": stage ?? self.stage, "dispatched": dispatched, "phase": phase]]
        if let checkpoint { details["checkpoint"] = checkpoint.path }
        return details
    }

    func fail(_ error: UmbraError, at stage: String) {
        let step = min(activeIndex + 1, plan.count)
        reply(.failure(UmbraError(error.code, "step \(step)/\(plan.count) \(stage): \(error.message)" + (checkpoint == nil ? "" : "; checkpoint \(phase)"), details: failureDetails(at: stage))))
    }

    func start() {
        guard let parent = args.operation, engine.runtime.isActive(parent) else { return }
        guard index < plan.count else {
            do { try write("complete") } catch let error as UmbraError { return fail(error, at: "checkpoint") } catch { return }
            return reply(.success(["completed": true, "count": plan.count, "results": receipts,
                                   "checkpoint": checkpoint?.path ?? NSNull(), "text": "completed \(plan.count) steps"]))
        }
        let step = plan[index]
        activeIndex = index
        dispatched = false; stage = "precondition"
        condition(step.before, tab: step.args["tab"]) { before in
            if case .failure(let error) = before { return self.fail(error, at: "precondition") }
            self.stage = "checkpoint"
            do { try self.write("pending") } catch let error as UmbraError { return self.fail(error, at: "checkpoint") } catch { return }
            self.dispatched = true; self.stage = "command"
            self.engine.runtime.run(command: step.command, args: self.context(step.args)) { result in
                switch result {
                case .failure(let error): self.fail(error, at: "command")
                case .success(let value):
                    self.stage = "postcondition"
                    self.condition(step.after, tab: step.args["tab"]) { after in
                        if case .failure(let error) = after { return self.fail(error, at: "postcondition") }
                        let receipt: [String: Any] = ["step": self.index + 1, "command": step.command, "ok": true, "result": value]
                        let bytes = (try? JSONSerialization.data(withJSONObject: jsonSafe(receipt)).count) ?? 4194305
                        guard self.resultBytes + bytes <= 4177920 else {
                            self.receipts.append(["step": self.index + 1, "command": step.command, "ok": true, "resultOmitted": "batch output exceeds 4 MiB"])
                            self.index += 1; self.stage = "checkpoint"
                            do { try self.write("ready") } catch let error as UmbraError { return self.fail(error, at: "checkpoint") } catch { return }
                            return self.fail(UmbraError(4, "command completed but output exceeds 4 MiB; use file paths or smaller observation budgets"), at: "output")
                        }
                        self.resultBytes += bytes; self.receipts.append(receipt)
                        self.index += 1; self.stage = "checkpoint"
                        do { try self.write("ready") } catch let error as UmbraError { return self.fail(error, at: "checkpoint") } catch { return }
                        self.start()
                    }
                }
            }
        }
    }
}

func executionCommands() -> [Command] {
    [
        Command(name: "batch", help: "Execute 1...64 JSON command steps in order, stop on failure, and return bounded action receipts. Steps may include before/after wait conditions. No rollback or automatic retry.",
                params: [Param(name: "steps", kind: .string, help: "JSON array of {command,args,before?,after?}", required: true, positional: true)],
                usesTab: false, timeout: 120) { args, engine, reply in
            do { Execution(engine, args, try steps(parseJSON(args.string("steps") ?? "")), reply: reply).start() }
            catch let error as UmbraError { reply(.failure(error)) } catch { reply(.failure(UmbraError(64, "invalid batch"))) }
        },
        Command(name: "workflow", help: "Run a parameterized JSON workflow with an atomic, secret-free checkpoint. Resume never repeats an uncertain side effect unless marked repeatable or explicitly resolved. Variables use ${name} placeholders.",
                params: [Param(name: "file", kind: .string, help: "JSON workflow file with steps array", required: true, positional: true),
                         Param(name: "vars", kind: .string, help: "JSON variable object; values are not written to checkpoints"),
                         Param(name: "checkpoint", kind: .string, help: "Atomic checkpoint file (mode 0600)", required: true),
                         Param(name: "resume", kind: .boolean, help: "Resume an existing matching checkpoint"),
                         Param(name: "resolve", kind: .string, help: "Resolve pending step: verified (check postcondition) or retry (explicitly repeat)")],
                usesTab: false, timeout: 300) { args, engine, reply in
            do {
                guard let file = args.path("file"), let checkpoint = args.path("checkpoint") else { throw UmbraError(64, "workflow needs file and checkpoint") }
                let size = (try FileManager.default.attributesOfItem(atPath: file.path)[.size] as? NSNumber)?.intValue ?? Int.max
                guard size <= definitionLimit else { throw UmbraError(64, "workflow exceeds 256 KiB") }
                let definition = try Data(contentsOf: file)
                guard let object = try JSONSerialization.jsonObject(with: definition) as? [String: Any], let raw = object["steps"] else { throw UmbraError(64, "workflow needs steps") }
                let variables = try parseJSON(args.string("vars") ?? "{}")
                guard let variables = variables as? [String: Any] else { throw UmbraError(64, "vars must be a JSON object") }
                let resolved = try bindings(raw, variables)
                let plan = try steps(resolved)
                let canonical = try JSONSerialization.data(withJSONObject: resolved, options: [.sortedKeys])
                let digest = SHA256.hash(data: canonical).map { String(format: "%02x", $0) }.joined()
                let run = Execution(engine, args, plan, checkpoint: checkpoint, digest: digest, reply: reply)
                try run.acquire()
                if args.bool("resume") {
                    let size = (try FileManager.default.attributesOfItem(atPath: checkpoint.path)[.size] as? NSNumber)?.intValue ?? Int.max
                    guard size <= 4096, let state = try JSONSerialization.jsonObject(with: Data(contentsOf: checkpoint)) as? [String: Any],
                          state["digest"] as? String == digest, let index = state["index"] as? Int, index >= 0, index <= plan.count,
                          let phase = state["phase"] as? String, ["pending", "ready", "complete"].contains(phase),
                          (phase != "complete" || index == plan.count), (phase != "pending" || index < plan.count) else {
                        throw UmbraError(64, "checkpoint does not match this workflow and variables")
                    }
                    run.index = index; run.activeIndex = min(index, plan.count - 1); run.phase = phase
                    run.dispatched = phase == "pending"; run.stage = phase == "pending" ? "resolution" : "ready"
                    if phase == "pending", !plan[index].repeatable {
                        if args.string("resolve") == "verified" {
                            guard plan[index].after != nil else { throw UmbraError(64, "verified resolution needs a postcondition") }
                            return run.condition(plan[index].after, tab: plan[index].args["tab"]) { result in
                                if case .failure(let error) = result { return run.fail(error, at: "resolution") }
                                run.index += 1
                                do { try run.write("ready"); run.start() }
                                catch let error as UmbraError { run.fail(error, at: "checkpoint") } catch { }
                            }
                        }
                        guard args.string("resolve") == "retry" else { throw UmbraError(4, "pending step has an uncertain outcome; verify its effect, then use --resolve verified or explicitly --resolve retry") }
                    }
                } else {
                    guard !FileManager.default.fileExists(atPath: checkpoint.path) else { throw UmbraError(64, "checkpoint exists; use --resume or a new path") }
                    try run.write("ready")
                }
                run.start()
            } catch let error as UmbraError { reply(.failure(error)) }
            catch { reply(.failure(UmbraError(64, "cannot read workflow or checkpoint JSON"))) }
        },
    ]
}
