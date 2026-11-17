// Declared, origin-bound JSON routes. No browser is created until an explicit fallback.
import Foundation
import WebKit

private let apiLimit = 4 * 1024 * 1024

private func apiObject(_ text: String?, _ label: String) throws -> [String: Any] {
    guard let text else { return [:] }
    guard text.utf8.count <= 1024 * 1024, let data = text.data(using: .utf8),
          let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
        throw UmbraError(64, "\(label) must be a JSON object of at most 1 MiB")
    }
    return object
}

private func apiOrigin(_ url: URL) -> String? {
    guard let part = URLComponents(url: url, resolvingAgainstBaseURL: false),
          let scheme = part.scheme?.lowercased(), ["http", "https"].contains(scheme),
          let host = part.host?.lowercased(), !host.isEmpty, part.user == nil, part.password == nil else { return nil }
    return "\(scheme)://\(host):\(part.port ?? (scheme == "https" ? 443 : 80))"
}

private struct APIRoute {
    let method: String
    let path: String
    let defaults: [String: Any]
    let capture: [String: String]
    let redact: [String]
    let versionField: String?
    let idempotencyField: String?
    let authentication: Bool
    var mutating: Bool { !["GET", "HEAD"].contains(method) }
}

private struct APIContract {
    let origin: URL
    let routes: [String: APIRoute]
    let browserPaths: Set<String>

    init(_ file: URL) throws {
        guard let handle = try? FileHandle(forReadingFrom: file) else { throw UmbraError(64, "could not read API contract") }; defer { try? handle.close() }
        guard let data = try? handle.read(upToCount: 256 * 1024 + 1), data.count <= 256 * 1024,
              let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              Set(root.keys).isSubset(of: ["origin", "routes", "browserPaths"]),
              let raw = root["origin"] as? String, let origin = URL(string: raw), apiOrigin(origin) != nil,
              let parts = URLComponents(url: origin, resolvingAgainstBaseURL: false),
              ["", "/"].contains(parts.path), parts.query == nil, parts.fragment == nil,
              let declared = root["routes"] as? [String: [String: Any]], !declared.isEmpty, declared.count <= 128 else {
            throw UmbraError(64, "invalid API contract (origin, routes, optional browserPaths; at most 256 KiB)")
        }
        self.origin = origin
        var routes: [String: APIRoute] = [:]
        for (name, value) in declared {
            guard !name.isEmpty, name.count <= 128,
                  Set(value.keys).isSubset(of: ["method", "path", "defaults", "capture", "redact", "versionField", "idempotencyField", "authentication"]),
                  let method = value["method"] as? String, ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"].contains(method),
                  let path = value["path"] as? String, path.hasPrefix("/"), !path.hasPrefix("//"), path.count <= 4096,
                  let url = URL(string: path, relativeTo: origin)?.absoluteURL, apiOrigin(url) == apiOrigin(origin),
                  URLComponents(url: url, resolvingAgainstBaseURL: false)?.fragment == nil,
                  value["defaults"] == nil || value["defaults"] is [String: Any],
                  value["capture"] == nil || value["capture"] is [String: String],
                  value["redact"] == nil || value["redact"] is [String],
                  value["authentication"] == nil || value["authentication"] is Bool,
                  ["versionField", "idempotencyField"].allSatisfy({ value[$0] == nil || value[$0] is String }) else {
                throw UmbraError(64, "invalid API route \(name)")
            }
            let capture = value["capture"] as? [String: String] ?? [:]
            let redact = value["redact"] as? [String] ?? []
            guard capture.count <= 64, redact.count <= 64, capture.allSatisfy({ !$0.key.isEmpty && $0.key.count <= 128 && !$0.value.isEmpty && $0.value.count <= 256 }),
                  redact.allSatisfy({ !$0.isEmpty && $0.count <= 256 }) else { throw UmbraError(64, "API capture/redaction budget exceeded") }
            routes[name] = APIRoute(method: method, path: path, defaults: value["defaults"] as? [String: Any] ?? [:],
                                    capture: capture, redact: redact, versionField: value["versionField"] as? String,
                                    idempotencyField: value["idempotencyField"] as? String, authentication: value["authentication"] as? Bool ?? false)
        }
        self.routes = routes
        let browser = root["browserPaths"] as? [String] ?? []
        guard root["browserPaths"] == nil || root["browserPaths"] is [String], browser.count <= 64,
              browser.allSatisfy({ $0.hasPrefix("/") && !$0.hasPrefix("//") && $0.count <= 2048 && !$0.contains("?") && !$0.contains("#") }) else {
            throw UmbraError(64, "browserPaths must be exact same-origin paths")
        }
        browserPaths = Set(browser)
    }
}

private final class APITransfer {
    let route: APIRoute
    let routeName: String
    let operation: String?
    let id: String
    let reply: Reply
    let active: () -> Bool
    var data = Data()
    var response: HTTPURLResponse?
    var task: URLSessionDataTask?
    var finished = false
    init(_ route: APIRoute, _ routeName: String, _ operation: String?, _ id: String, _ active: @escaping () -> Bool, _ reply: @escaping Reply) {
        self.route = route; self.routeName = routeName; self.operation = operation; self.id = id; self.active = active; self.reply = reply
    }
}

private final class APISession: NSObject, URLSessionDataDelegate, URLSessionTaskDelegate {
    let name: String
    let contract: APIContract
    let agent: String
    let ownerTask: String
    var tokens: [String: Any] = [:]
    var cookies: [HTTPCookie] = []
    private var responseSecrets: [String] = []
    var needsAuth = false
    var authEpoch = 0
    var closed = false
    var browserStore: WKWebsiteDataStore?
    private var http: URLSession?
    private var transfers: [Int: APITransfer] = [:]

    init(_ name: String, _ contract: APIContract, _ args: Args) {
        self.name = name; self.contract = contract
        agent = args.string("agent") ?? ""; ownerTask = args.string("task") ?? ""
    }
    func ownerError(_ args: Args) -> UmbraError? {
        agent == (args.string("agent") ?? "") && ownerTask == (args.string("task") ?? "") ? nil :
            UmbraError(64, "API session is owned by another agent/task")
    }
    var summary: [String: Any] {
        ["name": name, "origin": contract.origin.absoluteString, "routes": contract.routes.keys.sorted(),
         "backend": "api", "authEpoch": authEpoch, "needsAuth": needsAuth, "browserCreated": browserStore != nil,
         "agent": agent, "task": ownerTask]
    }
    func close() {
        closed = true; http?.invalidateAndCancel(); http = nil; tokens = [:]; cookies = []; browserStore = nil
    }
    private func client() -> URLSession {
        if let http { return http }
        let config = URLSessionConfiguration.ephemeral
        config.httpCookieStorage = nil; config.httpShouldSetCookies = false; config.urlCredentialStorage = nil; config.urlCache = nil
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        let value = URLSession(configuration: config, delegate: self, delegateQueue: .main)
        http = value; return value
    }
    private func eligible(_ cookie: HTTPCookie) -> Bool {
        guard let host = contract.origin.host else { return false }
        return cookie.sent(to: host) && (cookie.expiresDate.map { $0 > Date() } ?? true)
    }
    private func receivedCookies(_ response: HTTPURLResponse) -> [HTTPCookie] {
        guard let url = response.url else { return [] }
        var headers: [String: String] = [:]
        for (key, value) in response.allHeaderFields { headers[String(describing: key)] = String(describing: value) }
        return HTTPCookie.cookies(withResponseHeaderFields: headers, for: url)
    }
    private func cookieHeader(_ url: URL) -> String? {
        cookies.removeAll { !eligible($0) }
        let outgoing = cookies.filter { cookie in
            let path = url.path.isEmpty ? "/" : url.path
            let match = path == cookie.path || (path.hasPrefix(cookie.path) && (cookie.path.hasSuffix("/") || path.dropFirst(cookie.path.count).first == "/"))
            return match && (!cookie.isSecure || url.scheme == "https")
        }
        return HTTPCookie.requestHeaderFields(with: outgoing)["Cookie"]
    }
    private var cookieBudgetOK: Bool { cookies.count <= 256 && cookies.reduce(0, { $0 + $1.name.utf8.count + $1.value.utf8.count }) <= 128 * 1024 }
    private func merge(_ found: [HTTPCookie]) {
        for cookie in found where cookie.sent(to: contract.origin.host ?? "") {
            cookies.removeAll { $0.name == cookie.name && $0.domain == cookie.domain && $0.path == cookie.path }
            if eligible(cookie) { cookies.append(cookie) }
        }
        cookies.removeAll { !eligible($0) }
    }
    func sync(_ done: @escaping () -> Void) {
        guard let browserStore else { return done() }
        browserStore.httpCookieStore.getAllCookies { [weak self] found in
            guard let self, !self.closed else { return }
            self.cookies = found.filter(self.eligible); done()
        }
    }
    func publish(_ done: @escaping () -> Void) {
        guard let browserStore else { return done() }
        browserStore.httpCookieStore.getAllCookies { [weak self] old in
            guard let self, !self.closed else { return }
            let group = DispatchGroup()
            for cookie in old where cookie.sent(to: self.contract.origin.host ?? "") && !self.cookies.contains(where: { $0.name == cookie.name && $0.domain == cookie.domain && $0.path == cookie.path }) {
                group.enter(); browserStore.httpCookieStore.delete(cookie) { group.leave() }
            }
            for cookie in self.cookies {
                group.enter(); browserStore.httpCookieStore.setCookie(cookie) { group.leave() }
            }
            group.notify(queue: .main, execute: done)
        }
    }
    private func resolve(_ value: Any) throws -> Any {
        if let value = value as? String, value.hasPrefix("$token:") {
            guard let token = tokens[String(value.dropFirst(7))] else { throw UmbraError(64, "API route requires a captured token; refresh its declared read/auth route") }
            return token
        }
        if let value = value as? [String: Any] { return try value.mapValues(resolve) }
        if let value = value as? [Any] { return try value.map(resolve) }
        return value
    }
    private func value(_ path: String, in root: Any) -> Any? {
        path.split(separator: ".").reduce(Optional(root)) { current, key in (current as? [String: Any])?[String(key)] }
    }
    private var privateStrings: [String] {
        Array(Set(responseSecrets + cookies.map(\.value) + tokens.values.compactMap { $0 as? String })).filter { !$0.isEmpty }
    }
    private func containsPrivate(_ text: String, _ secrets: [String]? = nil) -> Bool {
        (secrets ?? privateStrings).contains { text == $0 || ($0.count >= 8 && text.contains($0)) }
    }
    private func publicRequestID(_ id: String) -> Any { containsPrivate(id) ? NSNull() : id as Any }
    private func scrub(_ value: Any, keys: Set<String>, secretValues: [String]? = nil) throws -> Any {
        let secrets = secretValues ?? privateStrings
        let conflict = UmbraError(5, "API response conflicts with private session values; use the browser or refine the contract", details: ["kind": "redaction_conflict", "needsBrowser": true])
        if let object = value as? [String: Any] {
            var safe: [String: Any] = [:]
            for (key, value) in object where !keys.contains(key) && !["cookie", "set-cookie"].contains(key.lowercased()) {
                guard !containsPrivate(key, secrets) else { throw conflict }
                safe[key] = try scrub(value, keys: keys, secretValues: secrets)
            }
            return safe
        }
        if let array = value as? [Any] { return try array.map { try scrub($0, keys: keys, secretValues: secrets) } }
        if let text = value as? String, containsPrivate(text, secrets) { throw conflict }
        return value
    }
    func prepareBrowser(existing: Bool, _ done: @escaping () -> Void) {
        if existing { sync { self.publish(done) } } else { publish(done) }
    }
    func browserPage(_ tab: Tab) -> Result<[String: Any], UmbraError> {
        do {
            let page = try scrub(pageInfo(tab), keys: []) as? [String: Any] ?? [:]
            return .success(page.merging(["backend": "browser", "apiSession": name]) { _, new in new })
        } catch let error as UmbraError { return .failure(error) }
        catch { return .failure(UmbraError(5, "could not represent browser fallback")) }
    }
    func call(_ routeName: String, _ args: Args, _ engine: Engine, _ reply: @escaping Reply) {
        guard let route = contract.routes[routeName] else { return reply(.failure(UmbraError(64, "API route is not declared", details: ["kind": "needs_browser"]))) }
        let operation = args.operation
        let active = { [weak self, weak engine] in
            guard let self, !self.closed, let engine else { return false }
            return operation.map(engine.runtime.isActive) ?? true
        }
        sync { [weak self, weak engine] in
            guard let self, let engine, active() else { return }
            do {
                var body = try apiObject(args.string("data"), "data")
                for (key, value) in route.defaults { body[key] = try self.resolve(value) }
                if let field = route.versionField, body[field] == nil {
                    guard let version = self.tokens[field] else { throw UmbraError(64, "API mutation requires \(field) from an observation") }
                    body[field] = version
                }
                let id = (route.idempotencyField.flatMap { body[$0] as? String }) ?? args.string("request") ?? UUID().uuidString.lowercased()
                guard !id.isEmpty, id.utf8.count <= 256 else { throw UmbraError(64, "API request identifier is invalid") }
                if let field = route.idempotencyField { body[field] = id }
                var components = URLComponents(url: URL(string: route.path, relativeTo: self.contract.origin)!.absoluteURL, resolvingAgainstBaseURL: false)!
                let query = try apiObject(args.string("query"), "query")
                guard query.count <= 128 else { throw UmbraError(64, "too many API query fields") }
                var items = components.queryItems ?? []
                let fixed = Set(items.map(\.name))
                for (key, item) in query.sorted(by: { $0.key < $1.key }) {
                    guard !fixed.contains(key), item is String || item is NSNumber || item is NSNull else { throw UmbraError(64, "API query fields must be scalar and cannot replace declared query fields") }
                    items.append(URLQueryItem(name: key, value: item is NSNull ? "" : String(describing: item)))
                }
                if !route.mutating && !body.isEmpty { throw UmbraError(64, "GET/HEAD API routes accept query, not data/default body") }
                if !items.isEmpty { components.queryItems = items }
                guard let url = components.url, apiOrigin(url) == apiOrigin(self.contract.origin) else { throw UmbraError(64, "API URL escaped its declared origin") }
                var request = URLRequest(url: url)
                request.httpMethod = route.method; request.httpShouldHandleCookies = false; request.setValue("application/json", forHTTPHeaderField: "Accept")
                request.timeoutInterval = min(30, max(0.1, args.number("timeout") ?? 30))
                if let deadline = args.number("deadline") { request.timeoutInterval = min(request.timeoutInterval, max(0.1, deadline - Date().timeIntervalSince1970)) }
                request.setValue(self.cookieHeader(url), forHTTPHeaderField: "Cookie")
                if route.mutating {
                    let bytes = try JSONSerialization.data(withJSONObject: body, options: [.sortedKeys])
                    guard bytes.count <= 1024 * 1024 else { throw UmbraError(64, "API request body exceeds 1 MiB") }
                    request.httpBody = bytes; request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                }
                let transfer = APITransfer(route, routeName, operation, id, active, reply)
                let task = self.client().dataTask(with: request); transfer.task = task; self.transfers[task.taskIdentifier] = transfer
                if let operation {
                    engine.runtime.failureContext(operation) { [weak transfer] in guard let transfer, !transfer.finished, route.mutating else { return [:] }; return ["kind": "unknown_outcome", "outcome": "unknown", "requestId": self.publicRequestID(id), "limitation": "A dispatched mutation may have committed; reconcile or reuse a server-supported idempotency key before retrying."] }
                    engine.runtime.onCancel(operation) { task.cancel() }
                    engine.runtime.onFinish(operation) { [weak self, weak transfer] in
                        guard let self, let transfer, !transfer.finished else { return }
                        transfer.finished = true; self.transfers[task.taskIdentifier] = nil; task.cancel()
                    }
                }
                task.resume()
            } catch let error as UmbraError { reply(.failure(error)) }
            catch { reply(.failure(UmbraError(64, "could not construct declared API request"))) }
        }
    }
    private func finish(_ task: URLSessionTask, _ result: Result<[String: Any], UmbraError>) {
        guard let transfer = transfers.removeValue(forKey: task.taskIdentifier), !transfer.finished else { return }
        transfer.finished = true
        guard transfer.active() else { return }
        var answer = result
        if transfer.route.mutating, case .failure(let error) = result {
            let status = error.details["status"] as? Int ?? transfer.response?.statusCode
            let rejected = status.map { [400, 401, 403, 404, 405, 409, 410, 411, 412, 413, 414, 415, 416, 417, 422, 428, 429, 431].contains($0) } ?? false
            var details = error.details
            details["requestId"] = publicRequestID(transfer.id); details["outcome"] = rejected ? "rejected" : "unknown"
            if !rejected {
                details["causeKind"] = details["kind"] ?? "invalid_response"
                details["kind"] = "unknown_outcome"
            }
            if let status { details["status"] = status }
            answer = .failure(UmbraError(error.code, error.message, details: details))
        }
        transfer.reply(answer)
    }
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse, completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        guard let transfer = transfers[dataTask.taskIdentifier], let response = response as? HTTPURLResponse,
              response.url.flatMap(apiOrigin) == apiOrigin(contract.origin) else {
            completionHandler(.cancel); finish(dataTask, .failure(UmbraError(5, "API response is not same-origin HTTP"))); return
        }
        transfer.response = response
        if response.statusCode == 401 { if !needsAuth { authEpoch += 1 }; needsAuth = true; tokens = [:] }
        guard response.expectedContentLength <= Int64(apiLimit) else {
            completionHandler(.cancel); finish(dataTask, .failure(UmbraError(5, "API response exceeds 4 MiB"))); return
        }
        completionHandler(.allow)
    }
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        guard let transfer = transfers[dataTask.taskIdentifier] else { return }
        guard transfer.data.count + data.count <= apiLimit else {
            dataTask.cancel(); finish(dataTask, .failure(UmbraError(5, "API response exceeds 4 MiB"))); return
        }
        transfer.data.append(data)
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        guard let transfer = transfers[task.taskIdentifier], !transfer.route.mutating,
              ["GET", "HEAD"].contains(request.httpMethod ?? ""), request.url.flatMap(apiOrigin) == apiOrigin(contract.origin) else {
            completionHandler(nil); return
        }
        merge(receivedCookies(response))
        guard cookieBudgetOK, let url = request.url else { completionHandler(nil); finish(task, .failure(UmbraError(5, "API cookie budget exceeded"))); return }
        var redirected = request
        redirected.httpShouldHandleCookies = false
        redirected.setValue(cookieHeader(url), forHTTPHeaderField: "Cookie")
        completionHandler(redirected)
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, didReceive challenge: URLAuthenticationChallenge, completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        if challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust { completionHandler(.performDefaultHandling, nil) }
        else { completionHandler(.cancelAuthenticationChallenge, nil) }
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        guard let transfer = transfers[task.taskIdentifier], !transfer.finished else { return }
        if error != nil {
            let fields: [String: Any] = transfer.route.mutating ? ["kind": "unknown_outcome", "outcome": "unknown", "requestId": transfer.id] : ["kind": "transport_error"]
            return finish(task, .failure(UmbraError(2, "API transport failed; no automatic retry was made", details: fields)))
        }
        guard let response = transfer.response else { return finish(task, .failure(UmbraError(5, "API response was not HTTP"))) }
        let received = receivedCookies(response)
        responseSecrets = cookies.map(\.value) + received.map(\.value) + tokens.values.compactMap { $0 as? String }
        defer { responseSecrets = [] }
        merge(received)
        guard cookieBudgetOK else { cookies = []; return finish(task, .failure(UmbraError(5, "API cookie budget exceeded"))) }
        let status = response.statusCode
        if status == 401 { if !needsAuth { authEpoch += 1 }; needsAuth = true; tokens = [:] }
        let route = transfer.route
        let answer: Result<[String: Any], UmbraError>
        if !(200..<300).contains(status) {
            let kind = status == 401 ? "needs_auth" : status == 409 || status == 412 ? "conflict" : status == 403 ? "refused" : "http_error"
            answer = .failure(UmbraError(status == 401 ? 3 : 5, "API request returned HTTP \(status)", details: ["status": status, "kind": kind, "requestId": transfer.id]))
        } else if let object = transfer.data.isEmpty ? NSNull() : try? JSONSerialization.jsonObject(with: transfer.data, options: [.fragmentsAllowed]) {
            var invalid = false
            var capturedTokens: [String: Any] = [:]
            for (name, path) in route.capture {
                guard let captured = value(path, in: object), captured is String || captured is NSNumber || captured is NSNull,
                      ((captured as? String)?.utf8.count ?? 0) <= 4096 else { invalid = true; break }
                capturedTokens[name] = captured
            }
            let updatedTokens = tokens.merging(capturedTokens) { _, new in new }
            if updatedTokens.count > 64 || updatedTokens.values.reduce(0, { $0 + (($1 as? String)?.utf8.count ?? 16) }) > 65536 { invalid = true }
            if invalid { answer = .failure(UmbraError(5, "API response did not satisfy declared scalar captures", details: ["kind": "invalid_response"])) }
            else {
                tokens = updatedTokens
                responseSecrets += capturedTokens.values.compactMap { $0 as? String }
                if route.authentication { needsAuth = false; authEpoch += 1 }
                let keys = Set(route.redact.map { $0.split(separator: ".").last.map(String.init) ?? $0 } + route.capture.values.map { $0.split(separator: ".").last.map(String.init) ?? $0 })
                do {
                let safe = try scrub(object, keys: keys)
                var fields: [String: Any] = ["backend": "api", "name": name, "route": transfer.routeName, "origin": contract.origin.absoluteString,
                                              "status": status, "data": safe, "authEpoch": authEpoch,
                                              "outcome": route.mutating ? "acknowledged" : "observed", "requestId": publicRequestID(transfer.id)]
                if let revision = value("revision", in: object), revision is String || revision is NSNumber, !((revision as? String).map({ containsPrivate($0) }) ?? false) { fields["version"] = revision }
                fields["text"] = (try? String(data: JSONSerialization.data(withJSONObject: safe, options: [.sortedKeys, .fragmentsAllowed]), encoding: .utf8)) ?? "API HTTP \(status)"
                answer = .success(fields)
                } catch let error as UmbraError { answer = .failure(error) }
                catch { answer = .failure(UmbraError(5, "could not represent API response")) }
            }
        } else { answer = .failure(UmbraError(5, "API response was not JSON", details: ["kind": "invalid_response"])) }
        publish { [weak self, weak task] in guard let self, let task else { return }; self.finish(task, answer) }
    }
}

private final class APIBank {
    weak var engine: Engine?
    var sessions: [String: APISession] = [:]
    init(_ engine: Engine) { self.engine = engine }
    deinit { sessions.values.forEach { $0.close() } }
}
private var apiBanks: [ObjectIdentifier: APIBank] = [:]
private func apiBank(_ engine: Engine) -> APIBank {
    apiBanks = apiBanks.filter { $0.value.engine != nil }
    let key = ObjectIdentifier(engine)
    if let bank = apiBanks[key] { return bank }
    let bank = APIBank(engine); apiBanks[key] = bank; return bank
}

func apiCommands() -> [Command] {
    [Command(name: "api", help: "Use an explicitly declared same-origin JSON API session without a browser. open NAME --contract FILE; read NAME ROUTE (GET/HEAD only); call NAME ROUTE [--data JSON] [--query JSON]; browser NAME --url URL [--tab NAME] [--render]; list; close NAME. Cookies and captured tokens stay private; mutations are never automatically replayed. Browser fallback uses its own ephemeral store.",
             params: [Param(name: "action", kind: .string, help: "open, read, call, browser, list or close", required: true, positional: true),
                      Param(name: "name", kind: .string, help: "Logical API session", positional: true),
                      Param(name: "route", kind: .string, help: "Declared route name for call", positional: true),
                      Param(name: "contract", kind: .string, help: "JSON contract file for open"),
                      Param(name: "data", kind: .string, help: "JSON request object for a mutation"),
                      Param(name: "query", kind: .string, help: "Scalar JSON query object"),
                      Param(name: "url", kind: .string, help: "Declared same-origin browser fallback URL"),
                      Param(name: "tab", kind: .string, help: "Browser fallback tab name"),
                      Param(name: "render", kind: .boolean, help: "Render the browser fallback immediately instead of keeping it nonvisual")], usesTab: false) { args, engine, reply in
        let bank = apiBank(engine)
        let action = args.string("action") ?? ""
        if action == "list" {
            let rows = bank.sessions.values.filter { $0.ownerError(args) == nil }.sorted { $0.name < $1.name }.map(\.summary)
            return reply(.success(["sessions": rows, "text": rows.map { "\($0["name"]!) · \($0["origin"]!) · api" }.joined(separator: "\n")]))
        }
        guard let name = args.string("name"), !name.isEmpty, name.count <= 128 else { return reply(.failure(UmbraError(64, "api needs a session name of at most 128 characters"))) }
        if action == "open" {
            guard bank.sessions[name] == nil else { return reply(.failure(UmbraError(64, "API session already exists"))) }
            guard bank.sessions.count < 32, let file = args.path("contract") else { return reply(.failure(UmbraError(64, "api open needs --contract FILE (at most 32 sessions)"))) }
            do {
                let session = APISession(name, try APIContract(file), args); bank.sessions[name] = session
                return reply(.success(session.summary.merging(["text": "API session \(name) opened; no browser created"]) { _, new in new }))
            } catch let error as UmbraError { return reply(.failure(error)) }
            catch { return reply(.failure(UmbraError(64, "could not open API contract"))) }
        }
        guard let session = bank.sessions[name] else { return reply(.failure(UmbraError(64, "no API session named \(name)"))) }
        if let error = session.ownerError(args) { return reply(.failure(error)) }
        switch action {
        case "call", "read":
            guard let route = args.string("route") else { return reply(.failure(UmbraError(64, "api call needs a declared route"))) }
            if action == "read" {
                guard let declared = session.contract.routes[route], !declared.mutating else { return reply(.failure(UmbraError(64, "api read requires a declared GET or HEAD route", details: ["kind": "unsupported_read"]))) }
                guard args.values["data"] == nil else { return reply(.failure(UmbraError(64, "api read does not accept request data"))) }
                guard declared.method != "HEAD" || declared.capture.isEmpty else { return reply(.failure(UmbraError(64, "HEAD read cannot capture response fields"))) }
            }
            session.call(route, args, engine, reply)
        case "close":
            session.close(); bank.sessions[name] = nil
            for tab in engine.tabs where tab.extras["apiSession"] as? String == name {
                tab.extras["apiSession"] = nil
            }
            reply(.success(["closed": name, "text": "closed API session \(name); browser tabs remain open"]))
        case "browser":
            guard !session.needsAuth else { return reply(.failure(UmbraError(3, "API session requires authentication", details: ["kind": "needs_auth"]))) }
            guard let raw = args.string("url"), let url = URL(string: raw, relativeTo: session.contract.origin)?.absoluteURL,
                  apiOrigin(url) == apiOrigin(session.contract.origin), session.contract.browserPaths.contains(url.path),
                  URLComponents(url: url, resolvingAgainstBaseURL: false)?.fragment == nil else {
                return reply(.failure(UmbraError(64, "browser URL must match a declared same-origin browserPaths entry")))
            }
            let hadBrowser = session.browserStore != nil
            let tabName = args.string("tab") ?? "api-\(name)"
            let tab: Tab
            if let existing = engine.tabs.first(where: { $0.name == tabName }) {
                guard existing.extras["apiSession"] as? String == name else { return reply(.failure(UmbraError(64, "browser tab belongs to another session"))) }
                if let error = engine.runtime.ownerError(existing, args) { return reply(.failure(error)) }
                guard existing.handoff == nil else { return reply(.failure(UmbraError(1, "browser tab is under human control"))) }
                tab = existing
            } else {
                if let error = engine.runtime.admissionError(newTab: true) { return reply(.failure(error)) }
                if session.browserStore == nil { session.browserStore = .nonPersistent() }
                tab = engine.open(name: tabName, config: configuration(dataStore: session.browserStore!), select: false, nonvisual: !args.bool("render"))
                tab.extras["apiSession"] = name; tab.extras["apiAgent"] = session.agent; tab.extras["apiTask"] = session.ownerTask
            }
            let operation = args.operation
            let active = { operation.map(engine.runtime.isActive) ?? true }
            tab.busy += 1; tab.operation = operation
            if let operation {
                engine.runtime.onCancel(operation) { [weak tab] in tab?.cancelOperation() }
                engine.runtime.onFinish(operation) { [weak tab] in
                    guard let tab else { return }; tab.busy = max(0, tab.busy - 1); tab.operation = nil; tab.lastUsed = Date()
                }
            }
            session.prepareBrowser(existing: hadBrowser) {
                guard active() else { return }
                let load = {
                    if tab.web.url == url, !tab.web.isLoading { return reply(session.browserPage(tab)) }
                    tab.load(url) { result in
                        guard active() else { return }
                        switch result {
                        case .success: reply(session.browserPage(tab))
                        case .failure(let error): reply(.failure(error))
                        }
                    }
                }
                if args.bool("render") { tab.wake(render: true, then: load) } else { load() }
            }
        default: reply(.failure(UmbraError(64, "api action must be open, read, call, browser, list or close")))
        }
    }]
}
