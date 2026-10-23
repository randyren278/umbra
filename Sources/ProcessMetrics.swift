import Foundation
import WebKit
import Darwin
import ObjectiveC

private let cpuSecondsPerTick: Double? = {
    var timebase = mach_timebase_info_data_t()
    guard mach_timebase_info(&timebase) == KERN_SUCCESS, timebase.denom > 0 else { return nil }
    return Double(timebase.numer) / Double(timebase.denom) / 1_000_000_000
}()

private typealias ResponsiblePID = @convention(c) (pid_t) -> pid_t
private let responsiblePID: ResponsiblePID? = {
    guard let handle = dlopen(nil, RTLD_LAZY) else { return nil }
    defer { dlclose(handle) }
    guard let symbol = dlsym(handle, "responsibility_get_pid_responsible_for_pid") else { return nil }
    return unsafeBitCast(symbol, to: ResponsiblePID.self)
}()

private func tabProcess(_ tab: Tab) -> pid_t? {
    let selector = NSSelectorFromString("_webProcessIdentifier")
    guard tab.web.responds(to: selector), let method = class_getInstanceMethod(type(of: tab.web), selector) else { return nil }
    let type = method_copyReturnType(method)
    defer { free(type) }
    guard ["i", "I", "q", "Q"].contains(String(cString: type)) else { return nil }
    typealias Getter = @convention(c) (AnyObject, Selector) -> pid_t
    let pid = unsafeBitCast(method_getImplementation(method), to: Getter.self)(tab.web, selector)
    return pid > 0 ? pid : nil
}

private func processLabel(_ name: String) -> String {
    let text = name.replacingOccurrences(of: #"(?i)[a-z][a-z0-9+.-]*://\S+"#, with: "[url]", options: .regularExpression)
    return String(text.unicodeScalars.filter { !CharacterSet.controlCharacters.contains($0) }.prefix(80))
}

private final class ProcessSampler {
    weak var engine: Engine?
    var sampled = -Double.infinity
    var signature: [String] = []
    var rows: [[String: Any]] = []
    var coverage: [String: Any] = [:]
    let lifetime = 2.0

    func metrics(_ engine: Engine) -> [String: Any] {
        let now = ProcessInfo.processInfo.systemUptime
        let tabs = engine.tabs.map { ($0, tabProcess($0)) }
        let signature = tabs.map { "\($0.0.name):\($0.1 ?? 0)" }
        let alive = rows.allSatisfy { row in
            guard let pid = row["pid"] as? Int32 else { return false }
            return kill(pid, 0) == 0 || errno == EPERM
        }
        if self.engine !== engine || now - sampled >= lifetime || self.signature != signature || !alive {
            sample(tabs)
            self.engine = engine; self.signature = signature; sampled = now
        }
        let total = rows.reduce(UInt64(0)) { $0 + ($1["physicalFootprintBytes"] as? UInt64 ?? 0) }
        let cpu = cpuSecondsPerTick.map { _ in rows.reduce(0.0) { $0 + ($1["cpuSeconds"] as? Double ?? 0) } }
        return ["processes": rows, "tabs": tabs.map { tab, pid -> [String: Any] in
                    ["tab": processLabel(tab.name), "pid": pid as Any? ?? NSNull()]
                },
                "physicalFootprintBytes": total, "cpuSeconds": cpu as Any? ?? NSNull(), "processCount": rows.count,
                "sampleAgeSeconds": max(0, now - sampled), "cacheTTLSeconds": lifetime,
                "coverage": coverage,
                "scope": "Unique PIDs: this daemon, tracked tab WebContent, and WebKit helpers whose OS responsibility PID is this daemon. Shared process bytes are counted once, never divided among tabs.",
                "text": "\(rows.count) processes · \(total / 1048576) MiB physical footprint · best-effort browser coverage"]
    }

    private func sample(_ tabs: [(Tab, pid_t?)]) {
        let daemon = getpid()
        var kinds: [pid_t: String] = [daemon: "daemon"]
        var sources: [pid_t: String] = [daemon: "self"]
        var labels: [pid_t: [String]] = [:]
        for (tab, pid) in tabs {
            guard let pid else { continue }
            kinds[pid] = "WebContent"; sources[pid] = "tracked-tab"
            labels[pid, default: []].append(processLabel(tab.name))
        }
        let count = Int(proc_listallpids(nil, 0))
        let capacity = min(65536, max(256, count + 256))
        var pids = [pid_t](repeating: 0, count: capacity)
        let found = pids.withUnsafeMutableBytes { proc_listallpids($0.baseAddress, Int32($0.count)) }
        var gaps = ["OS responsibility can omit shared or differently attributed WebKit helpers; this is not an exact whole-browser total."]
        if cpuSecondsPerTick == nil { gaps.append("Native CPU time conversion is unavailable; process CPU counters are omitted.") }
        if responsiblePID == nil { gaps.append("OS responsibility attribution is unavailable; only the daemon and tracked tab WebContent are included.") }
        if found <= 0 || found >= capacity { gaps.append("The process census failed or reached its bounded capacity.") }
        if tabs.contains(where: { $0.1 == nil }) { gaps.append("One or more tabs have no available WebContent PID (unsupported API, unloaded or terminated process).") }
        if let responsiblePID, found > 0 {
            for pid in pids.prefix(min(Int(found), capacity)) where pid > 0 && kinds[pid] == nil {
                guard responsiblePID(pid) == daemon else { continue }
                var path = [CChar](repeating: 0, count: 4 * Int(MAXPATHLEN))
                let length = path.withUnsafeMutableBytes { proc_pidpath(pid, $0.baseAddress, UInt32($0.count)) }
                guard length > 0 else { continue }
                let executable = String(cString: path).split(separator: "/").last.map(String.init) ?? ""
                let kind: String
                switch executable {
                case "com.apple.WebKit.WebContent": kind = "WebContent"
                case "com.apple.WebKit.GPU": kind = "GPU"
                case "com.apple.WebKit.Networking": kind = "Networking"
                default: continue
                }
                guard responsiblePID(pid) == daemon else { continue }
                kinds[pid] = kind; sources[pid] = "os-responsibility"
            }
        }
        var missing = 0
        rows = kinds.keys.sorted().compactMap { pid in
            var usage = rusage_info_v2()
            let result = withUnsafeMutablePointer(to: &usage) { pointer in
                pointer.withMemoryRebound(to: rusage_info_t?.self, capacity: 1) { proc_pid_rusage(pid, RUSAGE_INFO_V2, $0) }
            }
            guard result == 0, usage.ri_proc_exit_abstime == 0 else { missing += 1; return nil }
            return ["pid": pid, "kind": kinds[pid]!, "attribution": sources[pid]!,
                    "tabs": labels[pid] ?? [], "sharedByTabs": (labels[pid]?.count ?? 0) > 1,
                    "physicalFootprintBytes": usage.ri_phys_footprint, "residentBytes": usage.ri_resident_size,
                    "cpuSeconds": cpuSecondsPerTick.map { (Double(usage.ri_user_time) + Double(usage.ri_system_time)) * $0 } as Any? ?? NSNull()]
        }
        if missing > 0 { gaps.append("\(missing) identified processes exited or could not be measured; their bytes are excluded.") }
        coverage = ["status": "partial", "webContentPIDAvailable": tabs.allSatisfy { $0.1 != nil },
                    "responsibilityAvailable": responsiblePID != nil, "censusLimit": 65536,
                    "measuredProcesses": rows.count, "unmeasuredProcesses": missing, "gaps": gaps]
    }
}

private let processSampler = ProcessSampler()

func processMetrics(_ engine: Engine) -> [String: Any] { processSampler.metrics(engine) }

func processCommands() -> [Command] {
    [Command(name: "processes", help: "Read best-effort native memory and cumulative CPU for this daemon and attributable WebKit processes. Bytes are counted once per PID, not allocated per tab; coverage gaps are explicit. Cached for at most two seconds.", usesTab: false) { _, engine, reply in
        reply(.success(processMetrics(engine)))
    }]
}
