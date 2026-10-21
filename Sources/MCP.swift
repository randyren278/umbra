// `umbra mcp`: a stdio MCP server whose tools are the registry's commands, run by the daemon.
// JSON-RPC 2.0, one message per line. Each tools/call goes to the profile's daemon through
// `call()` (which starts it), so MCP clients and the CLI drive the same live tabs.
import Foundation

let mcpVersions = ["2025-06-18", "2025-03-26", "2024-11-05"]  // newest first

let mcpInstructions = """
    Umbra is a real WebKit browser on this Mac: invisible, signed in to the person's sites, and its \
    tabs persist between calls (the `umbra` CLI drives the same tabs). Work in a loop: `open` a URL, \
    `snapshot` to see the page as a tree with element refs (e1, e2…), then act on refs with `click`, \
    `fill`, `type`, `press` or `select`. Refs go stale when the page changes: snapshot again after \
    navigating or after any action that changes the page. `wait` for a selector, text, URL, JavaScript \
    condition or network idle instead of sleeping. For 2FA, a CAPTCHA or a consent screen, call `handoff` \
    with a short message; the person finishes it in a window and the call returns. Give your work its \
    own `tab` name and close it with `tab` when you are done: each open tab holds a web process, and one \
    unused for 15 minutes closes by itself. Use `screenshot` \
    only when visual layout matters; `text` and `markdown` are cheaper for reading. For a JSON API on \
    a signed-in site, `fetch` the URL directly: one request with the browser's cookies, no tab needed.
    """

/// Tools that only look, unless they write a file where the caller says (a `path` or `file`
/// param), which may replace one. Every tool may reach any site.
let readOnlyTools: Set = ["snapshot", "text", "markdown", "screenshot", "tabs", "status", "cookies", "links",
                          "html", "info", "console", "network", "downloads", "get"]

let errorHints: [Int32: String] = [
    1: "Umbra itself failed; the next call starts it again.",
    2: "A script threw, or nothing happened in time: pass a larger timeout, or wait for a specific condition.",
    3: "Call `sign-in` with the site's name: the person signs in in Umbra's window and the call returns once they have. Then open the page again.",
    4: "Check the URL, and that the site is up.",
]

func mcpTools() -> [[String: Any]] {
    registry.filter(\.mcp).map { command in
        var schema = inputSchema(command)
        schema["additionalProperties"] = false
        if (schema["required"] as? [String])?.isEmpty == true { schema["required"] = nil }  // draft-04 forbids []
        let writes = command.params.contains { ["path", "file"].contains($0.name) }
        return ["name": command.name, "description": command.help, "inputSchema": schema,
                "annotations": ["readOnlyHint": readOnlyTools.contains(command.name) && !writes, "destructiveHint": writes,
                                "openWorldHint": true]]
    }
}

/// Arguments the daemon would misread: unknown names (a model guessing) or a missing required one.
/// `cwd` is the caller's directory, a convention rather than a parameter.
func argumentProblem(_ command: Command, _ arguments: [String: Any]) -> UmbraError? {
    let known = (inputSchema(command)["properties"] as? [String: Any] ?? [:]).keys.sorted()
    if let unknown = arguments.keys.sorted().first(where: { !known.contains($0) && $0 != "cwd" }) {
        return UmbraError(64, "\(command.name) has no parameter \(unknown); it takes \(known.joined(separator: ", "))")
    }
    if let missing = command.params.first(where: { $0.required && arguments[$0.name] == nil }) {
        return UmbraError(64, "\(command.name) needs \(missing.name)")
    }
    return nil
}

/// A daemon reply as a tools/call result: its text (or JSON), a screenshot as an image, and the
/// other fields as structured content, minus any copy of the text (html, markdown, fetch's body)
/// so a big page isn't sent twice. Failures are tool errors the model can read and act on.
func toolResult(_ result: Result<[String: Any], UmbraError>) -> [String: Any] {
    switch result {
    case .failure(let error):
        let text = [error.message, errorHints[error.code]].compactMap { $0 }.joined(separator: "\n")
        return ["content": [["type": "text", "text": text]], "isError": true]
    case .success(let value):
        var text = value["text"] as? String ?? ""
        let fields = value.filter { $0.key != "text" && $0.key != "data" && (text.isEmpty || $0.value as? String != text) }
        if text.isEmpty {
            let data = try? JSONSerialization.data(withJSONObject: jsonSafe(fields), options: [.sortedKeys, .withoutEscapingSlashes])
            text = data.map { String(decoding: $0, as: UTF8.self) } ?? ""
        }
        var content: [[String: Any]] = [["type": "text", "text": text]]
        if let data = value["data"] as? String, let mime = value["mime"] as? String, mime.hasPrefix("image/") {
            content.append(["type": "image", "data": data, "mimeType": mime])
        }
        var answer: [String: Any] = ["content": content]
        if !fields.isEmpty { answer["structuredContent"] = fields }
        return answer
    }
}

func rpcResult(_ id: Any, _ result: [String: Any]) -> [String: Any] { ["jsonrpc": "2.0", "id": id, "result": result] }

func rpcError(_ id: Any, _ code: Int, _ message: String) -> [String: Any] {
    ["jsonrpc": "2.0", "id": id, "error": ["code": code, "message": message]]
}

/// JSON-RPC ids and MCP progress tokens are strings or numbers; MCP also rules out null.
func validID(_ id: Any?) -> Bool {
    if id is String { return true }
    guard let number = id as? NSNumber else { return false }
    return CFGetTypeID(number) != CFBooleanGetTypeID()
}

func runMCP() -> Never {
    if args == ["--print-config"] { printMCPConfig() }
    guard args.isEmpty else { fail(64, "usage: umbra mcp [--print-config]") }
    signal(SIGPIPE, SIG_IGN)  // a client that hung up must not take a call in flight down with it
    // Daemons this server starts are its children: reap each one that exits (idle, stop, sign-in).
    let reaper = DispatchSource.makeSignalSource(signal: SIGCHLD, queue: .global())
    reaper.setEventHandler { while waitpid(-1, nil, WNOHANG) > 0 {} }
    reaper.resume()
    let output = NSLock()
    var running = Set<String>()  // tools/call ids not yet answered or cancelled
    let calls = DispatchGroup()
    func send(_ message: [String: Any]) {
        output.lock()
        writeAll(STDOUT_FILENO, line(message))
        output.unlock()
    }
    while let text = readLine() {
        if text.allSatisfy(\.isWhitespace) { continue }
        guard let parsed = try? JSONSerialization.jsonObject(with: Data(text.utf8), options: .fragmentsAllowed) else {
            send(rpcError(NSNull(), -32700, "Parse error"))
            continue
        }
        let message = parsed as? [String: Any] ?? [:]
        let id = message["id"]
        let echo: Any = validID(id) ? id! : NSNull()
        guard message["jsonrpc"] as? String == "2.0", id == nil || validID(id), let method = message["method"] as? String else {
            // A response to a request this server never sends is ignored, like a notification.
            if message["method"] == nil, message["result"] != nil || message["error"] != nil { continue }
            send(rpcError(echo, -32600, "Invalid Request"))
            continue
        }
        let params = message["params"] as? [String: Any] ?? [:]
        guard let id else {
            if method == "notifications/cancelled", let request = params["requestId"] {
                output.lock()
                running.remove("\(request)")
                output.unlock()
            }
            continue
        }
        switch method {
        case "initialize":
            let asked = params["protocolVersion"] as? String ?? ""
            let version = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "unknown"
            send(rpcResult(id, ["protocolVersion": mcpVersions.contains(asked) ? asked : mcpVersions[0],
                                "capabilities": ["tools": ["listChanged": false]],
                                "serverInfo": ["name": "umbra", "title": "Umbra", "version": version],
                                "instructions": mcpInstructions]))
        case "ping":
            send(rpcResult(id, [:]))
        case "tools/list":
            send(rpcResult(id, ["tools": mcpTools()]))
        case "tools/call":
            let name = params["name"] as? String ?? ""
            guard let command = registry.first(where: { $0.mcp && $0.name == name }) else {
                send(rpcError(id, -32602, name.isEmpty ? "tools/call needs a tool name" : "Unknown tool: \(name)"))
                continue
            }
            guard let arguments = (params["arguments"] ?? [String: Any]()) as? [String: Any] else {
                send(rpcError(id, -32602, "arguments must be an object"))
                continue
            }
            let key = "\(id)"
            output.lock()
            running.insert(key)
            output.unlock()
            // A client that asked for progress hears from a long call (open, wait, handoff) every 5 s,
            // so an SDK's request timeout doesn't give up on it.
            let heartbeat: DispatchSourceTimer? = {
                guard let token = (params["_meta"] as? [String: Any])?["progressToken"], validID(token) else { return nil }
                let started = Date()
                let timer = DispatchSource.makeTimerSource(queue: .global())
                timer.schedule(deadline: .now() + 5, repeating: 5)
                timer.setEventHandler {
                    let seconds = Int(Date().timeIntervalSince(started).rounded())
                    output.lock()
                    if running.contains(key) {
                        writeAll(STDOUT_FILENO, line(["jsonrpc": "2.0", "method": "notifications/progress", "params":
                            ["progressToken": token, "progress": seconds, "message": "\(name): \(seconds) s"]]))
                    }
                    output.unlock()
                }
                timer.resume()
                return timer
            }()
            calls.enter()
            Thread.detachNewThread {
                var values = arguments.filter { !($0.value is NSNull) }  // models send null for "not given"
                values["cwd"] = values["cwd"] ?? FileManager.default.currentDirectoryPath  // relative paths mean the client's directory
                let problem = argumentProblem(command, values)
                values["sitesFile"] = sitesFile.path  // set after the check, so a model can't choose the file
                let result: Result<[String: Any], UmbraError> = problem.map { .failure($0) } ?? call(name, values)
                heartbeat?.cancel()
                output.lock()
                if running.remove(key) != nil { writeAll(STDOUT_FILENO, line(rpcResult(id, toolResult(result)))) }
                output.unlock()
                calls.leave()
            }
        default:
            send(rpcError(id, -32601, "Method not found: \(method)"))
        }
    }
    withExtendedLifetime(reaper) { calls.wait() }  // stdin closed: answer what is in flight, then go
    exit(0)
}

/// `umbra mcp --print-config`: setup snippets for Claude Code, Codex, Claude Desktop and Cursor.
func printMCPConfig() -> Never {
    let path = Bundle.main.executableURL?.resolvingSymlinksInPath().path ?? CommandLine.arguments[0]
    func quoted(_ word: String) -> String {
        word.allSatisfy({ $0.isLetter || $0.isNumber || "/._-+:@%=,".contains($0) }) ? word
            : "'" + word.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }
    var server: [String: Any] = ["command": path, "args": ["mcp"]]
    server["env"] = profile.map { ["UMBRA_PROFILE": $0] }
    let servers = ["mcpServers": ["umbra": server]]
    let block = String(decoding: try! JSONSerialization.data(withJSONObject: servers, options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]),
                       as: UTF8.self)
    print("""
        Claude Code (add --scope user to use it in every project):
          claude mcp add umbra\(profile.map { " -e " + quoted("UMBRA_PROFILE=" + $0) } ?? "") -- \(quoted(path)) mcp

        Codex (every project):
          codex mcp add umbra\(profile.map { " --env " + quoted("UMBRA_PROFILE=" + $0) } ?? "") -- \(quoted(path)) mcp

        Claude Desktop (~/Library/Application Support/Claude/claude_desktop_config.json):
        \(block)

        Cursor (~/.cursor/mcp.json):
        \(block)
        """)
    exit(0)
}
