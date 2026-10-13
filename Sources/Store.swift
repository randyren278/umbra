// The cookie store, profiles, sites and locks every Umbra mode shares.
import AppKit
import CryptoKit
import WebKit

func fail(_ code: Int32, _ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(code)
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
}

let sitesFile = environment["UMBRA_SITES"].map { URL(fileURLWithPath: $0) }
    ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".config/umbra/sites.json")

let sites: [Site] = {
    guard let data = try? Data(contentsOf: sitesFile) else { return [] }
    do { return try JSONDecoder().decode([Site].self, from: data) }
    catch { fail(78, "could not read \(sitesFile.path): \(error)") }
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

func keepSessions(then done: @escaping () -> Void) {
    store.httpCookieStore.getAllCookies { cookies in
        let lasting = cookies.filter { cookie in
            cookie.isSessionOnly && sites.contains { $0.keepSession == true && cookie.within($0.domain) }
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

/// Opens a lock file and takes it; nil when `wait` is false and someone else holds it. The
/// descriptor stays open, so the lock lasts until the process exits.
@discardableResult
func takeLock(_ kind: String = "store", wait: Bool = true) -> Int32? {
    let fd = open(supportDir.appendingPathComponent(profileFile(kind, "lock")).path, O_RDWR | O_CREAT, 0o600)
    if fd < 0 { fail(1, "could not open Umbra's \(kind) lock") }
    if flock(fd, wait ? LOCK_EX : LOCK_EX | LOCK_NB) == 0 { return fd }
    close(fd)
    if wait { fail(1, "could not lock Umbra's cookie store") }
    return nil
}

func exceptionText(_ error: Error) -> String {
    let info = (error as NSError).userInfo
    return info["WKJavaScriptExceptionMessage"] as? String ?? error.localizedDescription
}
