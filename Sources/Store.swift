// The cookie store, profiles, sites and locks every Umbra mode shares.
import AppKit
import CryptoKit
import WebKit

/// Umbra's own messages. A one-shot run points the process's stderr at /dev/null, because WebKit
/// prints internal chatter there ("sandbox_extension_issue_file failed…") that callers would
/// mistake for errors.
let messages = FileHandle(fileDescriptor: dup(STDERR_FILENO), closeOnDealloc: true)

func silenceWebKit() {
    _ = messages
    let null = open("/dev/null", O_WRONLY)
    dup2(null, STDERR_FILENO)
    close(null)
}

func say(_ message: String) {
    messages.write(Data((message + "\n").utf8))
}

func fail(_ code: Int32, _ message: String) -> Never {
    say(message)
    exit(code)
}

/// "3 seconds", "0.5 seconds".
func duration(_ seconds: Double) -> String {
    String(format: "%g second%@", seconds, seconds == 1 ? "" : "s")
}

let environment = ProcessInfo.processInfo.environment
let profile = environment["UMBRA_PROFILE"].flatMap { $0.isEmpty ? nil : $0 }

func uuid(named name: String) -> UUID {
    let hex = Array(SHA256.hash(data: Data(name.utf8)).map { String(format: "%02X", $0) }.joined())
    return UUID(uuidString: [0..<8, 8..<12, 12..<16, 16..<20, 20..<32].map { String(hex[$0]) }.joined(separator: "-"))!
}

// The store is keyed by this identifier and the bundle identifier in Info.plist, which is
// embedded in the binary: changing either signs every site out. The default identifier came
// from FIRST's helper so its store could move here intact.
let store = WKWebsiteDataStore(forIdentifier: profile.map { uuid(named: "umbra profile " + $0) }
                               ?? UUID(uuidString: "6F1C7A52-3D0B-4E8F-9A61-F1A57C0DE001")!)

struct Site: Decodable {
    let name, domain, login: String
    var cookie: String? = nil     // signed in while this cookie is set (HttpOnly ones included)
    var probe: String? = nil      // or while this same-origin path answers 2xx
    var keepSession: Bool? = nil  // persist session-only cookies, which WebKit drops when a run ends
    var keepSignedIn: Bool? = nil // visited every ten minutes so the session never idles out; implies keepSession
}

let sitesFile = environment["UMBRA_SITES"].map { URL(fileURLWithPath: $0) }
    ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".config/umbra/sites.json")

/// A sites file as it is now; no file means no sites.
func readSites(_ file: URL = sitesFile) throws -> [Site] {
    guard let data = try? Data(contentsOf: file) else { return [] }
    return try JSONDecoder().decode([Site].self, from: data)
}

/// The sites file when this process started: a malformed one stops every command (exit 78).
let sites: [Site] = {
    do { return try readSites() } catch { fail(78, "could not read \(sitesFile.path): \(error)") }
}()

extension HTTPCookie {
    var bareDomain: String { domain.trimmingCharacters(in: ["."]) }
    func sent(to host: String) -> Bool { host == bareDomain || host.hasSuffix("." + bareDomain) }
    func within(_ domain: String) -> Bool { bareDomain == domain || bareDomain.hasSuffix("." + domain) }
}

let safariVersion = Bundle(path: "/Applications/Safari.app")?.infoDictionary?["CFBundleShortVersionString"] as? String ?? "26.0"

func configuration() -> WKWebViewConfiguration {
    let config = WKWebViewConfiguration()
    config.websiteDataStore = store
    // WebKit writes the rest of Safari's user agent around this, so sites see Safari.
    config.applicationNameForUserAgent = "Version/\(safariVersion) Safari/605.1.15"
    return config
}

/// Sites files that sign-in requests named besides this process's own (a client may use another).
var requestSitesFiles: Set<URL> = []

func keepSessions(then done: @escaping () -> Void) {
    // A daemon outlives edits to the sites file, and sign-ins may come from another file.
    let kept = ((try? readSites()) ?? sites) + requestSitesFiles.flatMap { (try? readSites($0)) ?? [] }
    store.httpCookieStore.getAllCookies { cookies in
        let lasting = cookies.filter { cookie in
            cookie.isSessionOnly && kept.contains { ($0.keepSession == true || $0.keepSignedIn == true) && cookie.within($0.domain) }
        }.compactMap { cookie -> HTTPCookie? in
            var properties = cookie.properties ?? [:]
            properties[.discard] = nil
            properties[.expires] = Date(timeIntervalSinceNow: 30 * 86400)
            return HTTPCookie(properties: properties)
        }
        let group = DispatchGroup()
        for cookie in lasting {
            group.enter()
            store.httpCookieStore.setCookie(cookie) { group.leave() }
        }
        group.notify(queue: .main, execute: done)
    }
}

/// keepSessions, then has WebKit write every cookie to disk before the process exits. The network
/// process otherwise writes them on its own schedule, and a quick exit on a busy machine loses
/// them: a sign-in made during the run would be gone on the next one.
func persist(then done: @escaping () -> Void) {
    keepSessions {
        let flush = NSSelectorFromString("_flushCookiesToDiskWithCompletionHandler:")
        let cookies = store.httpCookieStore
        guard cookies.responds(to: flush) else { return done() }
        typealias Flush = @convention(c) (AnyObject, Selector, @escaping @convention(block) () -> Void) -> Void
        unsafeBitCast(cookies.method(for: flush), to: Flush.self)(cookies, flush) { DispatchQueue.main.async(execute: done) }
    }
}

let supportDir: URL = {
    let dir = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Umbra")
    try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    return dir
}()

/// A file name for this profile's lock, socket or log; long profile names are hashed so a
/// socket path stays under the 104-byte limit.
func profileFile(_ kind: String, _ ext: String) -> String {
    guard let profile else { return "\(kind).\(ext)" }
    let safe = profile.replacingOccurrences(of: "/", with: "_")
    if kind == "daemon", ext == "sock", safe.count > 24 {
        return "daemon-" + uuid(named: safe).uuidString.prefix(12).lowercased() + ".sock"
    }
    return "\(kind)-\(safe).\(ext)"
}

private var held: [String: Int32] = [:]

/// Opens a lock file and takes it; nil when `wait` is false and someone else holds it. The
/// descriptor stays open, so the lock lasts until the process exits; taking it again is a no-op.
@discardableResult
func takeLock(_ kind: String = "store", wait: Bool = true) -> Int32? {
    if let fd = held[kind] { return fd }
    let fd = open(supportDir.appendingPathComponent(profileFile(kind, "lock")).path, O_RDWR | O_CREAT, 0o600)
    if fd < 0 { fail(1, "could not open Umbra's \(kind) lock") }
    if flock(fd, wait ? LOCK_EX : LOCK_EX | LOCK_NB) == 0 {
        held[kind] = fd
        return fd
    }
    close(fd)
    if wait { fail(1, "could not lock Umbra's cookie store") }
    return nil
}

func exceptionText(_ error: Error) -> String {
    let info = (error as NSError).userInfo
    return info["WKJavaScriptExceptionMessage"] as? String ?? error.localizedDescription
}
