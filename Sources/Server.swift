// The per-profile daemon that owns WebKit, and the client the CLI and MCP use to reach it.
// Wire format: one JSON object per line. Request {id, command, args}; reply {id, ok, result}
// or {id, ok: false, error: {code, message}}.
import AppKit
import Darwin

let socketPath = supportDir.appendingPathComponent(profileFile("daemon", "sock")).path
let daemonLog = supportDir.appendingPathComponent(profileFile("daemon", "log")).path
let idleLimit = environment["UMBRA_IDLE"].flatMap(Double.init) ?? 900
let startedAt = Date()

/// JSON-safe copy of a value that came from WebKit (dates, data and other objects included).
func jsonSafe(_ value: Any) -> Any {
    switch value {
    case let dictionary as [String: Any]: return dictionary.mapValues(jsonSafe)
    case let list as [Any]: return list.map(jsonSafe)
    case let date as Date: return ISO8601DateFormatter().string(from: date)
    case let data as Data: return data.base64EncodedString()
    case is NSNull, is String, is NSNumber: return value
    default: return String(describing: value)
    }
}

func line(_ object: [String: Any]) -> Data {
    var data = (try? JSONSerialization.data(withJSONObject: jsonSafe(object), options: [.withoutEscapingSlashes])) ?? Data("{}".utf8)
    data.append(10)
    return data
}

func writeAll(_ fd: Int32, _ data: Data) {
    data.withUnsafeBytes { raw in
        var offset = 0
        while offset < raw.count {
            let written = write(fd, raw.baseAddress! + offset, raw.count - offset)
            if written <= 0 { return }
            offset += written
        }
    }
}

func socketAddress() -> sockaddr_un {
    var address = sockaddr_un()
    address.sun_family = sa_family_t(AF_UNIX)
    withUnsafeMutableBytes(of: &address.sun_path) { raw in
        let bytes = Array(socketPath.utf8.prefix(raw.count - 1))
        raw.copyBytes(from: bytes)
    }
    return address
}

func withAddress<T>(_ body: (UnsafePointer<sockaddr>, socklen_t) -> T) -> T {
    var address = socketAddress()
    return withUnsafePointer(to: &address) {
        $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { body($0, socklen_t(MemoryLayout<sockaddr_un>.size)) }
    }
}

// MARK: Daemon

final class Daemon {
    let engine = Engine()
    var lastUsed = Date()
    var inFlight = 0

    func run() -> Never {
        // A client that hangs up mid-reply must not take every tab down with it.
        signal(SIGPIPE, SIG_IGN)
        // One daemon per profile: a second one bows out before touching the socket, after
        // giving a stopping predecessor a moment to let go.
        var owned = takeLock("daemon", wait: false)
        for _ in 0..<40 where owned == nil {
            usleep(50_000)
            owned = takeLock("daemon", wait: false)
        }
        guard owned != nil else { exit(0) }
        _ = startedAt
        // Prohibited: no Dock icon, no menu bar, and the app can never become active, so it never
        // takes focus from the person. Tab windows still order in, so pages still see themselves as
        // visible. Only a handoff switches to accessory, for as long as the person needs the window.
        NSApplication.shared.setActivationPolicy(.prohibited)
        unlink(socketPath)
        let listener = socket(AF_UNIX, SOCK_STREAM, 0)
        guard listener >= 0, withAddress({ bind(listener, $0, $1) }) == 0, listen(listener, 32) == 0 else {
            fail(1, "could not listen on \(socketPath): \(String(cString: strerror(errno)))")
        }
        chmod(socketPath, 0o600)
        takeLock()  // the store: waits for a one-shot run already under way
        NotificationCenter.default.addObserver(forName: NSApplication.didBecomeActiveNotification, object: nil, queue: .main) { _ in
            if !self.engine.tabs.contains(where: { $0.handoff != nil }) { NSApp.deactivate() }
        }
        Thread.detachNewThread {
            while true {
                let client = accept(listener, nil, nil)
                if client >= 0 { self.serve(client) }
            }
        }
        Timer.scheduledTimer(withTimeInterval: 15, repeats: true) { _ in
            let handingOff = self.engine.tabs.contains { $0.handoff != nil }
            if self.inFlight == 0, !handingOff, Date().timeIntervalSince(self.lastUsed) > idleLimit { self.shutdown() }
        }
        signal(SIGTERM, SIG_IGN)
        let terminate = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
        terminate.setEventHandler { self.shutdown() }
        terminate.resume()
        withExtendedLifetime(terminate) { NSApplication.shared.run() }
        exit(0)
    }

    func shutdown() {
        unlink(socketPath)
        keepSessions { exit(0) }
        DispatchQueue.main.asyncAfter(deadline: .now() + 5) { exit(0) }
    }

    /// One thread per connection; its requests run in order, on the main thread.
    func serve(_ fd: Int32) {
        Thread.detachNewThread {
            var buffer = Data()
            var chunk = [UInt8](repeating: 0, count: 65536)
            while true {
                while let newline = buffer.firstIndex(of: 10) {
                    let request = buffer[buffer.startIndex..<newline]
                    buffer.removeSubrange(buffer.startIndex...newline)
                    let done = DispatchSemaphore(value: 0)
                    var reply = Data()
                    DispatchQueue.main.async {
                        self.handle(Data(request)) { reply = line($0); done.signal() }
                    }
                    done.wait()
                    writeAll(fd, reply)
                }
                let count = read(fd, &chunk, chunk.count)
                if count <= 0 { break }
                buffer.append(chunk, count: count)
            }
            close(fd)
        }
    }

    func handle(_ request: Data, reply: @escaping ([String: Any]) -> Void) {
        lastUsed = Date()
        guard let object = try? JSONSerialization.jsonObject(with: request) as? [String: Any],
              let name = object["command"] as? String else {
            return reply(["ok": false, "error": ["code": 64, "message": "malformed request"]])
        }
        let id = object["id"] ?? NSNull()
        guard let command = lookup(name) else {
            return reply(["id": id, "ok": false, "error": ["code": 64, "message": "unknown command: \(name)"]])
        }
        let args = Args(values: object["args"] as? [String: Any] ?? [:])
        let timeout = args.number("timeout") ?? command.timeout
        var answered = false
        inFlight += 1
        func finish(_ result: Result<[String: Any], UmbraError>) {
            guard !answered else { return }
            answered = true
            inFlight -= 1
            lastUsed = Date()
            switch result {
            case .success(let value): reply(["id": id, "ok": true, "result": value])
            case .failure(let error): reply(["id": id, "ok": false, "error": ["code": Int(error.code), "message": error.message]])
            }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + timeout + 0.5) {
            finish(.failure(UmbraError(2, "\(name) gave no answer in \(duration(timeout))")))
        }
        command.run(args, engine, finish)
    }
}

/// An accessory app shows no menu bar, but Command-V still routes through the main menu: during
/// a handoff, the person must be able to paste a password.
func installEditMenu() {
    let edit = NSMenu(title: "Edit")
    edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
    edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
    edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
    edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
    edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
    edit.addItem(withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
    let item = NSMenuItem()
    item.submenu = edit
    NSApp.mainMenu = NSMenu()
    NSApp.mainMenu?.addItem(item)
}

// MARK: Client

func connectDaemon() -> Int32? {
    let fd = socket(AF_UNIX, SOCK_STREAM, 0)
    guard fd >= 0 else { return nil }
    if withAddress({ connect(fd, $0, $1) }) == 0 { return fd }
    close(fd)
    return nil
}

/// Starts `umbra serve` in its own session, so it outlives this process and the terminal.
func spawnDaemon() {
    let executable = Bundle.main.executableURL?.resolvingSymlinksInPath().path ?? CommandLine.arguments[0]
    var attributes: posix_spawnattr_t?
    posix_spawnattr_init(&attributes)
    posix_spawnattr_setflags(&attributes, Int16(POSIX_SPAWN_SETSID))
    var files: posix_spawn_file_actions_t?
    posix_spawn_file_actions_init(&files)
    posix_spawn_file_actions_addopen(&files, 0, "/dev/null", O_RDONLY, 0)
    posix_spawn_file_actions_addopen(&files, 1, daemonLog, O_WRONLY | O_CREAT | O_APPEND, 0o600)
    posix_spawn_file_actions_adddup2(&files, 1, 2)
    let argv: [UnsafeMutablePointer<CChar>?] = [strdup(executable), strdup("serve"), nil]
    var pid: pid_t = 0
    posix_spawn(&pid, executable, &files, &attributes, argv, environ)
    argv.forEach { free($0) }
    posix_spawn_file_actions_destroy(&files)
    posix_spawnattr_destroy(&attributes)
}

/// Sends one command to this profile's daemon, starting it first when `spawn` is set.
func call(_ command: String, _ args: [String: Any], spawn: Bool = true) -> Result<[String: Any], UmbraError> {
    var fd = connectDaemon()
    if fd == nil, spawn {
        spawnDaemon()
        let deadline = Date().addingTimeInterval(20)
        while fd == nil, Date() < deadline {
            usleep(20_000)
            fd = connectDaemon()
        }
    }
    guard let fd else { return .failure(UmbraError(1, "could not reach Umbra's daemon; see \(daemonLog)")) }
    defer { close(fd) }
    writeAll(fd, line(["id": 1, "command": command, "args": args]))
    var buffer = Data()
    var chunk = [UInt8](repeating: 0, count: 65536)
    while true {  // a reply can be megabytes (screenshots): look for the newline in new bytes only
        let count = read(fd, &chunk, chunk.count)
        if count <= 0 { return .failure(UmbraError(1, "Umbra's daemon closed the connection; see \(daemonLog)")) }
        buffer.append(chunk, count: count)
        if chunk[..<count].contains(10) { break }
    }
    guard let reply = try? JSONSerialization.jsonObject(with: buffer) as? [String: Any] else {
        return .failure(UmbraError(1, "unreadable reply from Umbra's daemon"))
    }
    if reply["ok"] as? Bool == true { return .success(reply["result"] as? [String: Any] ?? [:]) }
    let error = reply["error"] as? [String: Any]
    return .failure(UmbraError(Int32(error?["code"] as? Int ?? 1), error?["message"] as? String ?? "failed"))
}
