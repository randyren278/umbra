// Keep signed in: a launch agent runs `umbra keep-signed-in` every ten minutes, and each run visits
// the sites marked keepSignedIn, so a site that signs you out when idle never sees you idle.
import AppKit
import WebKit

let agentLabel = "com.randyren.umbra.keep-signed-in" + (profile.map { "." + $0.replacingOccurrences(of: "/", with: "_") } ?? "")
let agentFile = (environment["UMBRA_AGENTS"].map { URL(fileURLWithPath: $0) }
                 ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/LaunchAgents"))
    .appendingPathComponent(agentLabel + ".plist")

/// Runs launchctl and waits for it without turning the run loop, so no other request runs meanwhile.
@discardableResult
func launchctl(_ arguments: String...) -> Bool {
    var files: posix_spawn_file_actions_t?
    posix_spawn_file_actions_init(&files)
    posix_spawn_file_actions_addopen(&files, 1, "/dev/null", O_WRONLY, 0)
    posix_spawn_file_actions_addopen(&files, 2, "/dev/null", O_WRONLY, 0)
    let argv: [UnsafeMutablePointer<CChar>?] = (["launchctl"] + arguments).map { strdup($0) } + [nil]
    defer {
        argv.forEach { free($0) }
        posix_spawn_file_actions_destroy(&files)
    }
    var pid: pid_t = 0
    guard posix_spawn(&pid, "/bin/launchctl", &files, nil, argv, environ) == 0 else { return false }
    var status: Int32 = 0
    while waitpid(pid, &status, 0) < 0 {
        if errno != EINTR { return false }
    }
    return status == 0
}

/// Installs the launch agent while any site in the file is kept signed in, and removes it once none
/// is. It is rewritten each time, so it always runs this copy of Umbra with this sites file.
func syncAgent(_ file: URL) throws {
    let kept = try readSites(file).contains { $0.keepSignedIn == true }
    // A job whose run is starting is taken down after bootout returns: wait until launchd lets it go.
    let job = "gui/\(getuid())/\(agentLabel)"
    launchctl("bootout", job)
    for _ in 0..<50 where launchctl("print", job) { usleep(100_000) }
    guard kept else {
        try? FileManager.default.removeItem(at: agentFile)
        return
    }
    var variables = ["UMBRA_SITES": file.path]
    variables["UMBRA_PROFILE"] = profile
    let log = supportDir.appendingPathComponent(profileFile("keep-signed-in", "log")).path
    let agent: [String: Any] = [
        "Label": agentLabel,
        "ProgramArguments": [Bundle.main.executableURL?.resolvingSymlinksInPath().path ?? CommandLine.arguments[0], "keep-signed-in"],
        "EnvironmentVariables": variables,
        // Every ten minutes by the clock: unlike StartInterval, a visit missed while the Mac slept runs on wake.
        "StartCalendarInterval": stride(from: 0, to: 60, by: 10).map { ["Minute": $0] },
        "RunAtLoad": true,
        "StandardOutPath": log,
        "StandardErrorPath": log,
    ]
    do {
        try FileManager.default.createDirectory(at: agentFile.deletingLastPathComponent(), withIntermediateDirectories: true)
        try PropertyListSerialization.data(fromPropertyList: agent, format: .xml, options: 0).write(to: agentFile, options: .atomic)
    } catch {
        throw UmbraError(1, "could not write \(agentFile.path): \(error.localizedDescription)")
    }
    guard launchctl("bootstrap", "gui/\(getuid())", agentFile.path) else {
        try? FileManager.default.removeItem(at: agentFile)  // or it would load at the next login anyway
        throw UmbraError(1, "launchctl could not load \(agentFile.path)")
    }
}

/// Marks sites from the file as kept signed in, or not, and brings the launch agent in line.
func keep(_ names: [String], _ on: Bool, file: URL) -> Result<[Site], UmbraError> {
    if let link = names.first(where: { ["http", "https"].contains(URL(string: $0)?.scheme ?? "") }) {
        return .failure(UmbraError(64, "\(link) is a URL: keep-signed-in takes site names from \(file.path) "
                                   + "(`umbra sign-in <login URL> --save NAME` adds one)"))
    }
    switch resolveSites(names, file: file) {
    case .failure(let error): return .failure(error)
    case .success(let named):
        let before = ((try? readSites(file)) ?? []).filter { site in named.contains { $0.name == site.name } }  // in file order, once each
        var chosen = before
        do {
            for index in chosen.indices {
                chosen[index].keepSignedIn = on
                try saveSite(chosen[index], to: file)
            }
            try syncAgent(file)
        } catch {
            // The file goes back as it was, so it never claims sites are kept that nothing visits.
            for var site in before {
                site.keepSignedIn = site.keepSignedIn ?? false
                try? saveSite(site, to: file)
            }
            try? syncAgent(file)
            return .failure(error as? UmbraError ?? UmbraError(1, "could not save \(file.path): \(error)"))
        }
        return .success(chosen)
    }
}

/// A request to the site, so it counts you as active, that also says whether you're still signed
/// in: the probe, or the login page loaded for a site with neither probe nor cookie. A site with only
/// a cookie is visited at its root, then its cookie decides.
func visit(_ site: Site, done: @escaping (Signed) -> Void) {
    guard site.probeScript == nil, site.probeURL == nil, site.cookie != nil, let root = site.base.flatMap({ URL(string: "/", relativeTo: $0)?.absoluteURL }) else {
        return Check.run(site, done: done)
    }
    Check.visit(root) { Check.run(site, done: done) }
}

/// Visits each site at once, keeps the cookies they set, and answers with a line per site.
func visitAll(_ chosen: [Site], reply: @escaping Reply) {
    let at = ISO8601DateFormatter().string(from: Date())
    var found: [String: Signed] = [:]
    let group = DispatchGroup()
    for site in chosen {
        group.enter()
        visit(site) { found[site.name] = $0; group.leave() }
    }
    group.notify(queue: .main) {
        persist {
            let states = chosen.map { site -> (Site, String) in
                (site, found[site.name] == .yes ? "signedIn" : found[site.name] == .no ? "signedOut" : "unknown")
            }
            let text = states.map { site, state in
                "\(at) \(site.name): " + ["signedIn": "signed in",
                                          "signedOut": "signed out; `umbra sign-in \(site.name)` signs in again"][state, default: "couldn't check (\(Check.why[site.name] ?? "the site didn't answer as expected"))"]
            }.joined(separator: "\n")
            if states.contains(where: { $0.1 == "signedOut" }) { return reply(.failure(UmbraError(3, text))) }
            if states.contains(where: { $0.1 == "unknown" }) { return reply(.failure(UmbraError(4, text))) }
            reply(.success(["at": at, "sites": states.map { ["site": $0.0.name, "state": $0.1] }, "text": text]))
        }
    }
}

/// The command, in the daemon or in a one-shot run: `site` names mark (or with --off unmark) those
/// sites; with none, every marked site is visited.
func keepSignedIn(_ args: Args, reply: @escaping Reply) {
    let names = args.strings("sites")
    let file = args.string("sitesFile").map { URL(fileURLWithPath: $0) } ?? sitesFile
    requestSitesFiles.insert(file)  // its kept sites' cookies are kept too
    if args.bool("off") {
        if names.isEmpty { return reply(.failure(UmbraError(64, "--off needs the sites to stop keeping signed in"))) }
        return reply(keep(names, false, file: file).map { chosen in
            ["sites": chosen.map(\.name), "text": chosen.map { "\($0.name): no longer kept signed in" }.joined(separator: "\n")]
        })
    }
    if !names.isEmpty {
        switch keep(names, true, file: file) {
        case .failure(let error): return reply(.failure(error))
        case .success(let chosen): return visitAll(chosen, reply: reply)
        }
    }
    let kept: [Site]
    do { kept = try readSites(file).filter { $0.keepSignedIn == true } } catch {
        return reply(.failure(UmbraError(78, "could not read \(file.path): \(error)")))
    }
    if kept.isEmpty { return reply(.success(["sites": [], "text": "no site is kept signed in; `umbra keep-signed-in <site>` turns it on"])) }
    visitAll(kept, reply: reply)
}

func keepSignedInCommands() -> [Command] {
    [
        Command(name: "keep-signed-in", help: "Keep sites from signing you out for being idle: Umbra visits each site marked keepSignedIn in the sites file every ten minutes, from a launch agent that runs this command. With site names, marks them (installing the agent) and visits them now; with --off, unmarks them (the agent goes with the last one). With no names, visits every marked site now. Prints a line per site; exits 3 if one is signed out, 4 if one couldn't be checked.",
                params: [Param(name: "sites", kind: .array, help: "Site names from the sites file", positional: true),
                         Param(name: "off", kind: .boolean, help: "Stop keeping these sites signed in")],
                usesTab: false, mcp: false, timeout: 60) { args, _, reply in
            keepSignedIn(args, reply: reply)
        },
    ]
}
