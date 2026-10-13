// Umbra: the headless browser built for agents. Native WebKit on macOS, invisible, signed in.
//
// A per-profile daemon (`umbra serve`, started on demand, gone after 15 idle minutes) owns the
// tabs and the cookie store; `umbra <command>` and `umbra mcp` are thin clients of it, so a shell
// and an MCP client can drive the same live tabs. `read`, `fetch` and `download` also run
// without a daemon, in this process, for scheduled jobs.
//
// Exit codes: 0 ok, 1 Umbra itself failed, 2 no answer in time or a script threw, 3 signed out,
// 4 the page or download failed, 5 an HTTP error status, 64 usage, 78 a malformed sites file.
import AppKit

registry = coreCommands() + interactCommands() + captureCommands()

var args = Array(CommandLine.arguments.dropFirst().filter { !$0.hasPrefix("-psn_") })

// --profile must take effect before anything reads the environment.
if let index = args.firstIndex(of: "--profile"), index + 1 < args.count {
    setenv("UMBRA_PROFILE", args[index + 1], 1)
    args.removeSubrange(index...index + 1)
}
let json = args.contains("--json")
args.removeAll { $0 == "--json" }
let command = args.isEmpty ? "sign-in" : args.removeFirst()

let overview = """
    umbra: the headless browser built for agents (native WebKit, invisible, signed in)

    Session (a daemon keeps tabs alive between commands):
    \(registry.map { "  \($0.name.padding(toLength: 10, withPad: " ", startingAt: 0)) \($0.help.components(separatedBy: ". ").first!.trimmingCharacters(in: ["."]))" }.joined(separator: "\n"))

    Also:
      sign-in    One window to sign in to sites from ~/.config/umbra/sites.json, or a URL
      mcp        Serve every command as an MCP tool over stdio
      schema     Every command and its parameters, as JSON
      serve      Run the daemon in the foreground

    Options: --tab NAME, --timeout SECONDS, --json, --profile NAME (a separate store).
    `umbra help <command>` shows a command's parameters.
    """

func option(_ name: String) -> String? {
    guard let index = args.firstIndex(of: name), index + 1 < args.count else { return nil }
    defer { args.removeSubrange(index...index + 1) }
    return args[index + 1]
}

func seconds(_ name: String, default value: Double) -> Double {
    guard let text = option(name) else { return value }
    guard let number = Double(text), number >= 0 else { fail(64, "\(name) takes a number of seconds") }
    return number
}

func stdinText() -> String {
    isatty(STDIN_FILENO) != 0 ? "" : String(decoding: FileHandle.standardInput.readDataToEndOfFile(), as: UTF8.self)
}

/// Prints a daemon reply the way the CLI shows it: its text, or JSON with --json.
func show(_ result: Result<[String: Any], UmbraError>) -> Never {
    switch result {
    case .success(let value):
        if json || value["text"] == nil {
            var copy = value
            copy["text"] = nil
            let data = try! JSONSerialization.data(withJSONObject: jsonSafe(copy), options: [.prettyPrinted, .sortedKeys])
            print(String(decoding: data, as: UTF8.self))
        } else if let text = value["text"] as? String, !text.isEmpty {
            print(text)
        }
        exit(0)
    case .failure(let error):
        fail(error.code, error.message)
    }
}

switch command {
case "help", "-h", "--help":
    if let name = args.first, let found = lookup(name) {
        print(usageLine(found) + "\n\n" + found.help + "\n")
        for param in found.params + (found.usesTab ? globalParams : [globalParams[1]]) {
            print("  " + (param.positional ? param.name : "--\(param.name)").padding(toLength: 14, withPad: " ", startingAt: 0) + param.help)
        }
    } else {
        print(overview)
    }
case "version", "--version":
    print("umbra " + (Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "unknown"))
case "schema":
    let data = try! JSONSerialization.data(withJSONObject: schemaJSON(), options: [.prettyPrinted, .sortedKeys])
    print(String(decoding: data, as: UTF8.self))
case "serve":
    Daemon().run()
case "mcp":
    runMCP()

// One-shots keep their original contract (raw output, exit codes) whether or not a daemon runs.
case "read":
    let cookie = option("--cookie")
    let wait = seconds("--wait", default: 0)
    let until = option("--until")
    let timeout = seconds("--timeout", default: 90)
    guard args.count == 1, let url = URL(string: args[0]) else { fail(64, usageLine(lookup("read")!)) }
    let script = stdinText()
    if connectDaemon().map({ close($0) }) != nil {
        var values: [String: Any] = ["url": args[0], "script": script, "wait": wait, "timeout": timeout + wait]
        values["cookie"] = cookie
        values["until"] = until
        switch call("read", values, spawn: false) {
        case .success(let value): emit(value["output"]); exit(0)
        case .failure(let error): fail(error.code, error.message)
        }
    }
    headless(timeout: timeout + wait) { read(url, script: script, cookie: cookie, wait: wait, until: until) }
case "fetch":
    let from = option("--from")
    let timeout = seconds("--timeout", default: 90)
    guard args.count == 1, let url = URL(string: args[0]), url.host != nil,
          let page = URL(string: from ?? "/robots.txt", relativeTo: url)?.absoluteURL else { fail(64, usageLine(lookup("fetch")!)) }
    if connectDaemon().map({ close($0) }) != nil {
        var values: [String: Any] = ["url": args[0], "timeout": timeout]
        values["from"] = from
        switch call("fetch", values, spawn: false) {
        case .success(let value):
            FileHandle.standardOutput.write(Data((value["body"] as? String ?? "").utf8))
            let status = value["status"] as? Int ?? 0
            (200..<300).contains(status) ? exit(0) : fail(5, "HTTP \(status)")
        case .failure(let error): fail(error.code, error.message)
        }
    }
    headless(timeout: timeout) { fetch(url, from: page) }
case "download":
    let timeout = seconds("--timeout", default: 300)
    guard args.count == 2, let url = URL(string: args[0]) else { fail(64, usageLine(lookup("download")!)) }
    let file = URL(fileURLWithPath: args[1]).standardizedFileURL
    if connectDaemon().map({ close($0) }) != nil {
        switch call("download", ["url": args[0], "file": file.path, "timeout": timeout], spawn: false) {
        case .success: exit(0)
        case .failure(let error): fail(error.code, error.message)
        }
    }
    headless(timeout: timeout) { Download(url, to: file) }

case "sign-in":
    // A URL opens an ad hoc tab (no live check); a name picks a site from the sites file.
    let chosen = args.isEmpty ? sites : args.compactMap { arg -> Site? in
        if let url = URL(string: arg), ["http", "https"].contains(url.scheme ?? ""), let host = url.host {
            return Site(name: host, domain: host, login: arg)
        }
        guard let site = sites.first(where: { $0.name.caseInsensitiveCompare(arg) == .orderedSame }) else {
            fail(64, "no site named \(arg) in \(sitesFile.path); known: \(sites.map(\.name).joined(separator: ", "))")
        }
        return site
    }
    guard !chosen.isEmpty else {
        fail(64, "nothing to sign in to: name a URL (umbra sign-in https://example.com/login) or list sites in \(sitesFile.path)")
    }
    if connectDaemon().map({ close($0) }) != nil { _ = call("stop", [:], spawn: false); usleep(500_000) }  // it holds the store
    NSApplication.shared.setActivationPolicy(.accessory)  // a window, but no Dock icon and no Command-Tab entry
    takeLock()
    let delegate = SignIn(chosen)
    NSApplication.shared.delegate = delegate  // a weak reference; withExtendedLifetime keeps it alive
    withExtendedLifetime(delegate) { NSApplication.shared.run() }

case "status" where connectDaemon().map({ close($0) }) == nil:
    print("no daemon running for profile \(profile ?? "default")")
case "stop" where connectDaemon().map({ close($0) }) == nil:
    print("no daemon running for profile \(profile ?? "default")")
case "cookies" where connectDaemon().map({ close($0) }) == nil:
    NSApplication.shared.setActivationPolicy(.prohibited)
    takeLock()
    printCookies(args.first)
    NSApplication.shared.run()

default:
    guard let found = lookup(command) else { fail(64, "unknown command: \(command)\n\n\(overview)") }
    switch parseArguments(found, args) {
    case .failure(let error): fail(error.code, error.message)
    case .success(var values):
        if found.name == "eval", values["script"] == nil || values["script"] as? String == "-" { values["script"] = stdinText() }
        show(call(found.name, values))
    }
}
