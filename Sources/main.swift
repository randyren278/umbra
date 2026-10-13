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

var args = Array(CommandLine.arguments.dropFirst().filter { !$0.hasPrefix("-psn_") })

// --profile must take effect before anything reads the environment: `environment`, and with it
// the profile, store and socket, are fixed on first use.
if let index = args.firstIndex(of: "--profile"), index + 1 < args.count {
    setenv("UMBRA_PROFILE", args[index + 1], 1)
    args.removeSubrange(index...index + 1)
}

registry = coreCommands() + interactCommands() + captureCommands() + signInCommands()
_ = sites  // a malformed sites file stops every command (exit 78), before anything else runs
let json = args.contains("--json")
args.removeAll { $0 == "--json" }
// Global options may come before the command too: `umbra --tab work snapshot`.
var leading: [String] = []
while args.count >= 2, ["--tab", "--timeout"].contains(args[0]) {
    leading += args.prefix(2)
    args.removeFirst(2)
}
let command = args.isEmpty ? "sign-in" : args.removeFirst()
args += leading

let oneShots = """
    usage: umbra read <url> [--cookie NAME] [--wait SECONDS] [--until JS] [--timeout SECONDS]  < script.js
           umbra fetch <url> [--from PAGE] [--timeout SECONDS]
           umbra download <url> <file> [--timeout SECONDS]
           umbra sign-in [site or URL ...] [--save NAME]
           umbra cookies [domain]
           umbra --version
           umbra <command> [args]  (session commands below; `umbra help <command>` for details)
    """

let overview = """
    \(oneShots)

    umbra: the headless browser built for agents (native WebKit, invisible, signed in)

    Session (a daemon keeps tabs alive between commands):
    \(registry.map { "  \($0.name.padding(toLength: 10, withPad: " ", startingAt: 0)) \($0.help.components(separatedBy: ". ").first!.trimmingCharacters(in: ["."]))" }.joined(separator: "\n"))

    Also:
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
    guard let number = Double(text), number >= 0 else { fail(64, "\(name) takes a number of seconds\n\n\(oneShots)") }
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
            let data = try! JSONSerialization.data(withJSONObject: jsonSafe(copy), options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes])
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
    let special = [
        "sign-in": "usage: umbra sign-in [site or URL ...] [--save NAME] [--timeout SECONDS]\n\nShows the person Umbra's sign-in window; opening Umbra.app does the same. With no sites it lists every site in ~/.config/umbra/sites.json (or $UMBRA_SITES: [{name, domain, login, cookie | probe, keepSession}]) and whether each is signed in, and returns at once. With site names or login URLs, it opens the login page of each one still signed out and returns once all are signed in (exit 0), or exits 3 if the window is closed first, 2 after --timeout (default 900). A signed-in site is never loaded: its page closes the moment the sign-in lands, so the window can't be used to browse. A URL has no check: the person clicks Done. An unknown site name exits 64. With --save NAME (refused, exit 64, if the name or domain is already listed), a login URL becomes a site in the sites file (as the window's Add Account does): Umbra learns the cookie its sign-in sets, so from then on it is checked like the others and agents can use it. The daemon shows the window, so other commands keep working meanwhile.",
        "mcp": "usage: umbra mcp [--print-config]\n\nServes every command as an MCP tool over stdio (JSON-RPC), sharing live tabs with the CLI through this profile's daemon. --print-config prints setup snippets for Claude Code, Codex, Claude Desktop and Cursor.",
        "schema": "usage: umbra schema\n\nEvery daemon command with its description, usage line and JSON Schema for its parameters, as JSON.",
        "serve": "usage: umbra serve\n\nRuns this profile's daemon in the foreground. Normally it starts by itself on the first session command and exits after UMBRA_IDLE seconds (default 900) without commands.",
    ]
    if let name = args.first, let text = special[name] {
        print(text)
    } else if let name = args.first, let found = lookup(name) {
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
    // The daemon shows the window: it owns the store, so agents keep working while a person signs
    // in. Opening Umbra.app runs this with no arguments.
    let timeout = seconds("--timeout", default: 900)
    let save = option("--save")
    if case .failure(let error) = resolveSites(args) { fail(error.code, error.message) }
    var values: [String: Any] = ["sites": args, "timeout": timeout, "sitesFile": sitesFile.path]
    values["save"] = save
    show(call("sign-in", values))

case "status" where connectDaemon().map({ close($0) }) == nil:
    print("no daemon running for profile \(profile ?? "default")")
case "stop" where connectDaemon().map({ close($0) }) == nil:
    print("no daemon running for profile \(profile ?? "default")")
case "cookies" where connectDaemon().map({ close($0) }) == nil:
    NSApplication.shared.setActivationPolicy(.prohibited)
    takeLock()
    silenceWebKit()
    // A store with no web view yet may not have read its cookies from disk (seen on a fresh CI
    // runner): load a blank page first, then list.
    let warm = Page(URL(string: "about:blank")!) { _ in printCookies(args.first) }
    withExtendedLifetime(warm) { NSApplication.shared.run() }

default:
    guard let found = lookup(command) else { fail(64, "\(oneShots)\n\nunknown command: \(command)") }
    switch parseArguments(found, args) {
    case .failure(let error): fail(error.code, error.message)
    case .success(var values):
        if found.name == "eval", values["script"] == nil || values["script"] as? String == "-" { values["script"] = stdinText() }
        values["cwd"] = FileManager.default.currentDirectoryPath  // relative paths mean the caller's directory
        values["sitesFile"] = sitesFile.path  // and the caller's sites file, which may not be the daemon's
        show(call(found.name, values))
    }
}
