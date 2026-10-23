import Foundation
import CryptoKit
import Darwin

public struct UmbraError: Error, CustomStringConvertible {
    public let code: Any
    public let message: String
    public let details: [String: Any]
    public var description: String { message }
    public init(_ code: Any, _ message: String, details: [String: Any] = [:]) {
        self.code = code; self.message = message
        self.details = details.isEmpty ? ["code": code, "message": message] : details
    }
}

/// One serial command connection. Use another client for independent concurrent work.
public final class Umbra {
    public let profile: String
    public let socketPath: String
    public let binary: URL?
    public let maxReplyBytes: Int
    private var fd: Int32 = -1
    private var closed = false
    private var buffer = Data()
    private let serial = NSLock()
    private let state = NSLock()

    public static func profileSocket(_ profile: String) throws -> String {
        guard !profile.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, !profile.contains("\0") else {
            throw UmbraError(64, "an explicit nonempty profile is required")
        }
        let safe = profile.replacingOccurrences(of: "/", with: "_")
        let hash = SHA256.hash(data: Data(safe.utf8)).map { String(format: "%02x", $0) }.joined()
        let suffix = safe.count > 24 ? String(hash.prefix(8)) + "-" + String(hash.dropFirst(8).prefix(3)) : safe
        return FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Umbra/daemon-\(suffix).sock").path
    }

    public init(profile: String, binary: URL? = nil, socketPath: String? = nil, maxReplyBytes: Int = 16 * 1024 * 1024) throws {
        let derived = try Self.profileSocket(profile)
        guard maxReplyBytes > 0 else { throw UmbraError(64, "maxReplyBytes must be positive") }
        self.profile = profile; self.binary = binary; self.socketPath = socketPath ?? derived; self.maxReplyBytes = maxReplyBytes
    }

    @discardableResult public func start(deadline: Date = Date().addingTimeInterval(20)) throws -> Process? {
        guard let binary, binary.path.hasPrefix("/") else { throw UmbraError(64, "start requires an explicit absolute Umbra binary path") }
        do { _ = try call("status", deadline: deadline); return nil }
        catch let error as UmbraError { if error.code as? Int != 1 || closed { throw error } }
        let process = Process()
        process.executableURL = binary; process.arguments = ["serve"]
        process.environment = ProcessInfo.processInfo.environment.merging(["UMBRA_PROFILE": profile]) { _, value in value }
        process.standardInput = FileHandle.nullDevice; process.standardOutput = FileHandle.nullDevice; process.standardError = FileHandle.nullDevice
        try process.run()
        while deadline.timeIntervalSinceNow > 0 {
            do { _ = try call("status", deadline: deadline); return process }
            catch let error as UmbraError { if error.code as? Int != 1 { throw error } }
            Thread.sleep(forTimeInterval: min(0.02, max(0, deadline.timeIntervalSinceNow)))
        }
        throw UmbraError(2, "daemon startup deadline elapsed")
    }

    public func close() {
        state.lock(); closed = true; let current = fd; fd = -1; state.unlock()
        if current >= 0 { shutdown(current, SHUT_RDWR); Darwin.close(current) }
    }
    deinit { close() }

    private func disconnect(_ current: Int32) {
        state.lock(); let own = fd == current; if own { fd = -1 }; state.unlock()
        if own { shutdown(current, SHUT_RDWR); Darwin.close(current) }
        buffer.removeAll(keepingCapacity: false)
    }
    private func ready(_ current: Int32, _ events: Int16, _ deadline: Date) throws {
        while true {
            let remaining = deadline.timeIntervalSinceNow
            guard remaining > 0 else { throw UmbraError(2, "request deadline elapsed") }
            var descriptor = pollfd(fd: current, events: events, revents: 0)
            let count = poll(&descriptor, 1, Int32(min(remaining * 1000 + 1, Double(Int32.max))))
            if count < 0 && errno == EINTR { continue }
            if count == 0 { continue }
            guard count > 0, descriptor.revents & Int16(POLLERR | POLLNVAL) == 0 else { throw UmbraError(1, "socket is unavailable") }
            return
        }
    }
    private func connect(_ deadline: Date) throws -> Int32 {
        state.lock(); defer { state.unlock() }
        guard !closed else { throw UmbraError(1, "client is closed") }
        if fd >= 0 { return fd }
        let current = socket(AF_UNIX, SOCK_STREAM, 0)
        guard current >= 0 else { throw UmbraError(1, "could not create socket") }
        var noSignal: Int32 = 1
        setsockopt(current, SOL_SOCKET, SO_NOSIGPIPE, &noSignal, socklen_t(MemoryLayout<Int32>.size))
        _ = fcntl(current, F_SETFL, O_NONBLOCK)
        _ = fcntl(current, F_SETFD, FD_CLOEXEC)
        var address = sockaddr_un(); address.sun_family = sa_family_t(AF_UNIX)
        let bytes = Array(socketPath.utf8) + [0]
        guard bytes.count <= MemoryLayout.size(ofValue: address.sun_path) else { Darwin.close(current); throw UmbraError(64, "socket path is too long") }
        withUnsafeMutableBytes(of: &address.sun_path) { $0.copyBytes(from: bytes) }
        let result = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.connect(current, $0, socklen_t(MemoryLayout<sockaddr_un>.size)) }
        }
        do {
            if result != 0 {
                guard errno == EINPROGRESS else { throw UmbraError(1, "could not connect: \(String(cString: strerror(errno)))") }
                try ready(current, Int16(POLLOUT), deadline)
                var error: Int32 = 0; var size = socklen_t(MemoryLayout<Int32>.size)
                getsockopt(current, SOL_SOCKET, SO_ERROR, &error, &size)
                guard error == 0 else { throw UmbraError(1, "could not connect: \(String(cString: strerror(error)))") }
            }
            fd = current; return current
        } catch { Darwin.close(current); throw error }
    }

    /// Absolute Unix epoch deadline, including time waiting for this client's earlier call.
    public func call(_ command: String, _ args: [String: Any] = [:], deadline: Date = Date().addingTimeInterval(30), requestID: String = UUID().uuidString) throws -> [String: Any] {
        guard deadline.timeIntervalSince1970.isFinite else { throw UmbraError(64, "deadline must be finite") }
        guard serial.lock(before: deadline) else { throw UmbraError(2, "request deadline elapsed while queued") }
        defer { serial.unlock() }
        var values = args; values["deadline"] = deadline.timeIntervalSince1970
        if values["request"] == nil { values["request"] = requestID }
        var request = try JSONSerialization.data(withJSONObject: ["id": requestID, "command": command, "args": values]); request.append(10)
        let current = try connect(deadline)
        var framed = false
        do {
            var offset = 0
            while offset < request.count {
                try ready(current, Int16(POLLOUT), deadline)
                let count = request.withUnsafeBytes { Darwin.write(current, $0.baseAddress!.advanced(by: offset), request.count - offset) }
                if count < 0 && (errno == EINTR || errno == EAGAIN) { continue }
                guard count > 0 else { throw UmbraError(1, "could not write request") }
                offset += count
            }
            while !buffer.contains(10) {
                try ready(current, Int16(POLLIN), deadline)
                var chunk = [UInt8](repeating: 0, count: min(65536, maxReplyBytes + 1 - buffer.count))
                let count = Darwin.read(current, &chunk, chunk.count)
                if count < 0 && (errno == EINTR || errno == EAGAIN) { continue }
                guard count > 0 else { throw UmbraError(1, "daemon closed the connection") }
                buffer.append(contentsOf: chunk.prefix(count))
                guard buffer.count <= maxReplyBytes else { throw UmbraError(1, "reply exceeds maxReplyBytes") }
            }
            let newline = buffer.firstIndex(of: 10)!
            let line = Data(buffer[..<newline]); buffer.removeSubrange(...newline)
            guard let reply = try JSONSerialization.jsonObject(with: line) as? [String: Any], reply["id"] as? String == requestID,
                  let ok = reply["ok"] as? Bool else { throw UmbraError(1, "malformed or mismatched reply") }
            if !ok {
                guard let error = reply["error"] as? [String: Any], let message = error["message"] as? String,
                      error["code"] is Int || error["code"] is String else { throw UmbraError(1, "malformed error reply") }
                framed = true
                throw UmbraError(error["code"]!, message, details: error)
            }
            guard let result = reply["result"] as? [String: Any] else { throw UmbraError(1, "malformed result reply") }
            framed = true
            return result
        } catch { if !framed { disconnect(current) }; throw error }
    }

    /// Ask a provider for replacement args after a fresh observation. Never executes the step.
    public func proposeRepair(_ step: [String: Any], observeArgs: [String: Any] = [:], deadline: Date = Date().addingTimeInterval(30), callback: ([String: Any], [String: Any]) throws -> [String: Any]) throws -> [String: Any] {
        guard let command = step["command"] as? String, !command.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              step["args"] == nil || step["args"] is [String: Any], JSONSerialization.isValidJSONObject(step) else {
            throw UmbraError(64, "step requires a command and object args")
        }
        let observation = try call("observe", observeArgs, deadline: deadline)
        let replacement = try callback(observation, step)
        guard JSONSerialization.isValidJSONObject(replacement) else { throw UmbraError(64, "repair callback must return JSON args") }
        guard deadline.timeIntervalSinceNow > 0 else { throw UmbraError(2, "repair proposal deadline elapsed") }
        var proposal = step; proposal["args"] = replacement
        return ["step": proposal, "observation": observation, "dispatched": false]
    }

    /// Uses a separate control connection, so it can cancel a blocked call on this client.
    public func cancel(_ requestID: String, agent: String? = nil, task: String? = nil, deadline: Date = Date().addingTimeInterval(5)) throws -> [String: Any] {
        let control = try Umbra(profile: profile, socketPath: socketPath, maxReplyBytes: maxReplyBytes)
        defer { control.close() }
        var args: [String: Any] = ["id": requestID]
        if let agent { args["agent"] = agent }; if let task { args["task"] = task }
        return try control.call("cancel", args, deadline: deadline)
    }
}
