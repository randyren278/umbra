import Foundation

private final class Recording {
    weak var tab: Tab?
    let tabName: String
    let directory: URL
    let engine: Engine
    let owner: [String: Any]
    let maxBytes: Int
    let started = Date()
    var timer: Timer?
    var deadline: Timer?
    var frames: [[String: Any]] = []
    var bytes = 0
    var pending = false
    var ended = false
    var reason = "recording"

    init(_ tab: Tab, _ directory: URL, _ engine: Engine, _ args: Args, maxBytes: Int) {
        self.tab = tab; self.tabName = tab.name; self.directory = directory; self.engine = engine; self.maxBytes = maxBytes
        owner = args.values.filter { ["agent", "task"].contains($0.key) }
    }

    func start(fps: Double, seconds: Double) {
        tab?.busy += 1
        timer = Timer.scheduledTimer(withTimeInterval: 1 / fps, repeats: true) { [weak self] _ in self?.capture() }
        deadline = Timer.scheduledTimer(withTimeInterval: seconds, repeats: false) { [weak self] _ in self?.stop("duration reached") }
        capture()
    }

    func capture() {
        guard !ended, !pending else { return }
        guard let tab, engine.tabs.contains(where: { $0 === tab }) else { return stop("tab closed") }
        guard frames.count < 600 else { return stop("frame limit reached") }
        pending = true
        let file = directory.appendingPathComponent(String(format: "%06d.png", frames.count))
        var args = owner
        args["tab"] = tab.name; args["path"] = file.path; args["timeout"] = 5
        engine.runtime.run(command: "screenshot", args: args) { result in
            self.pending = false
            if case .failure = result { return self.stop("capture failed") }
            let count = ((try? FileManager.default.attributesOfItem(atPath: file.path)[.size]) as? NSNumber)?.intValue ?? 0
            guard !self.ended, self.bytes + count <= self.maxBytes else {
                try? FileManager.default.removeItem(at: file)
                if !self.ended { self.stop("byte limit reached") }; return
            }
            self.bytes += count
            self.frames.append(["file": file.lastPathComponent, "seconds": Date().timeIntervalSince(self.started), "bytes": count])
            self.manifest()
        }
    }

    func manifest() {
        let value: [String: Any] = ["format": "png-sequence", "frames": frames, "bytes": bytes, "reason": reason, "finished": ended]
        if let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) {
            try? data.write(to: directory.appendingPathComponent("recording.json"), options: .atomic)
        }
    }

    func stop(_ reason: String) {
        guard !ended else { return }
        ended = true; self.reason = reason
        timer?.invalidate(); timer = nil
        deadline?.invalidate(); deadline = nil
        if let tab { tab.busy = max(0, tab.busy - 1) }
        manifest()
    }

    var status: [String: Any] { ["tab": tabName, "path": directory.path, "frames": frames.count, "bytes": bytes, "finished": ended, "reason": reason] }
}

private var recordings: [String: Recording] = [:]

func recordingCommands() -> [Command] {
    [
        Command(name: "record", help: "Explicitly start/stop a bounded PNG frame recording of this tab. Disabled until start; at most 600 frames, 10 minutes and 64 MiB. A JSON manifest records times; no video encoder or background capture service is required.",
                params: [Param(name: "action", kind: .string, help: "start, stop or status", required: true, positional: true),
                         Param(name: "path", kind: .string, help: "New directory for frame files (start)"),
                         Param(name: "fps", kind: .number, help: "Capture rate, 0.1...5 (default 1)"),
                         Param(name: "seconds", kind: .number, help: "Duration, 1...600 seconds (default 60)"),
                         Param(name: "max", kind: .number, help: "Maximum frame bytes, 1024...67108864 (default 16777216)")]) { args, engine, reply in
            guard let tab = pick(args, engine, reply) else { return }
            let key = tab.name
            switch args.string("action") {
            case "start":
                guard recordings[key]?.ended != false else { return reply(.failure(UmbraError(64, "this tab is already recording"))) }
                guard let directory = args.path("path"), !FileManager.default.fileExists(atPath: directory.path) else { return reply(.failure(UmbraError(64, "record start needs a new directory path"))) }
                let fps = args.number("fps") ?? 1, seconds = args.number("seconds") ?? 60, max = args.number("max") ?? 16777216
                guard fps.isFinite, fps >= 0.1, fps <= 5, seconds.isFinite, seconds >= 1, seconds <= 600,
                      max.isFinite, max.rounded() == max, max >= 1024, max <= 67108864 else { return reply(.failure(UmbraError(64, "recording rate, duration or byte budget is out of range"))) }
                do { try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700]) }
                catch { return reply(.failure(UmbraError(1, "cannot create recording directory"))) }
                for key in recordings.keys where recordings[key]?.ended == true { recordings[key] = nil }
                let recording = Recording(tab, directory, engine, args, maxBytes: Int(max))
                recordings[key] = recording; recording.start(fps: fps, seconds: seconds)
                reply(.success(recording.status.merging(["text": "recording started in \(directory.path)"]) { _, new in new }))
            case "stop", "status":
                guard let recording = recordings[key] else { return reply(.success(["recording": false, "text": "not recording"])) }
                if args.string("action") == "stop" { recording.stop("stopped") }
                reply(.success(recording.status.merging(["text": recording.ended ? "recording finished" : "recording"]) { _, new in new }))
            default: reply(.failure(UmbraError(64, "record action must be start, stop or status")))
            }
        },
        Command(name: "diagnose", help: "Write a bounded diagnostic JSON bundle (mode 0600, at most 512 KiB). Default: metadata and failure codes only. Explicit --observations --target <tab> with matching agent/task adds a bounded page outline with field values and URLs redacted. Never includes cookies, headers, screenshots or command arguments.",
                params: [Param(name: "path", kind: .string, help: "Diagnostic JSON output file", required: true, positional: true),
                         Param(name: "target", kind: .string, help: "Named tab for explicit page observation"),
                         Param(name: "observations", kind: .boolean, help: "Include redacted page content; requires target, agent and task"),
                         Param(name: "max", kind: .number, help: "Observation byte budget, 1...65536 (default 16384)")], usesTab: false) { args, engine, reply in
            guard let path = args.path("path") else { return reply(.failure(UmbraError(64, "diagnose needs a path"))) }
            let max = args.number("max") ?? 16384
            guard max.isFinite, max.rounded() == max, max >= 1, max <= 65536 else { return reply(.failure(UmbraError(64, "max must be an integer from 1 to 65536"))) }
            var value = engine.runtime.snapshot()
            value["profile"] = nil
            value["bundleVersion"] = 2
            value["captured"] = Date().timeIntervalSince1970
            value["content"] = args.bool("observations") ? "redacted observation" : "metadata only"
            value["failures"] = (value["history"] as? [[String: Any]] ?? []).filter { $0["errorCode"] != nil }
            value["excludes"] = ["cookies", "headers", "URLs", "screenshots", "command arguments", "field values"]
            func write() {
                guard let data = try? JSONSerialization.data(withJSONObject: jsonSafe(value), options: [.sortedKeys]), data.count <= 524288 else {
                    return reply(.failure(UmbraError(4, "diagnostic bundle exceeds 512 KiB")))
                }
                let temporary = path.deletingLastPathComponent().appendingPathComponent(".umbra-diagnose-" + UUID().uuidString)
                defer { try? FileManager.default.removeItem(at: temporary) }
                guard FileManager.default.createFile(atPath: temporary.path, contents: data, attributes: [.posixPermissions: 0o600]),
                      rename(temporary.path, path.path) == 0 else { return reply(.failure(UmbraError(1, "cannot write diagnostic bundle"))) }
                reply(.success(["path": path.path, "bytes": data.count, "text": "diagnostic bundle \(path.path) · \(data.count) bytes"]))
            }
            guard args.bool("observations") else { return write() }
            guard let target = args.string("target"), !target.isEmpty,
                  let agent = args.string("agent"), !agent.isEmpty, let task = args.string("task"), !task.isEmpty else {
                return reply(.failure(UmbraError(64, "observations require an explicit target tab and matching agent/task")))
            }
            var options: [String: Any] = ["tab": target, "agent": agent, "task": task, "_redact": true, "max": max]
            options["_parent"] = args.string("request")
            options["_parentOperation"] = args.operation
            options["deadline"] = args.values["deadline"]
            engine.runtime.run(command: "observe", args: options) { result in
                switch result {
                case .failure(let error):
                    if error.code == 64 { return reply(.failure(error)) }
                    value["observationFailure"] = ["code": Int(error.code), "stage": "observe"]
                case .success(let observed):
                    let text = observed["text"] as? String ?? ""
                    var bytes = Data(text.utf8).prefix(Int(max))
                    while String(data: bytes, encoding: .utf8) == nil, !bytes.isEmpty { bytes = bytes.dropLast() }
                    value["observation"] = ["text": String(decoding: bytes, as: UTF8.self), "refs": observed["refs"] ?? 0,
                                            "redacted": true, "truncated": observed["truncated"] as? Bool == true || text.utf8.count > Int(max)]
                }
                write()
            }
        },
    ]
}
