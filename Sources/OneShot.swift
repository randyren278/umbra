// One-shot read, fetch and download in this process, for scripts and scheduled jobs when no
// daemon is running: no window, no Dock icon, and the process exits when the job is done.
import AppKit
import WebKit

var leaving = false

/// Ends a headless run once session cookies are kept.
func leave(_ code: Int32, _ message: String = "") {
    guard !leaving else { return }
    leaving = true
    if !message.isEmpty { say(message) }
    keepSessions { exit(code) }
    DispatchQueue.main.asyncAfter(deadline: .now() + 5) { exit(code) }
}

final class Page: NSObject, WKNavigationDelegate {
    let web = WKWebView(frame: NSRect(x: 0, y: 0, width: 1280, height: 900), configuration: configuration())
    let wait: Double
    let until: String?
    let loaded: (WKWebView) -> Void
    var started = false

    init(_ url: URL, wait: Double = 0, until: String? = nil, loaded: @escaping (WKWebView) -> Void) {
        self.wait = wait
        self.until = until
        self.loaded = loaded
        super.init()
        web.navigationDelegate = self
        web.load(URLRequest(url: url))
    }

    // didFinish fires again on redirects and single-page apps: only the first one counts.
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard !started else { return }
        started = true
        DispatchQueue.main.asyncAfter(deadline: .now() + wait) { self.settle() }
    }

    // --until: re-checked in whatever document is current, so redirects and auto-submitted
    // forms can land first; an error mid-navigation counts as not yet.
    func settle() {
        guard let until else { return loaded(web) }
        web.evaluateJavaScript("Boolean(\(until))") { value, _ in
            if value as? Bool == true { return self.loaded(self.web) }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { self.settle() }
        }
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        failed(error)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        failed(error)
    }

    // A script redirect cancels the navigation it replaces; only a failure before the page loads counts.
    func failed(_ error: Error) {
        guard !started, (error as NSError).code != NSURLErrorCancelled else { return }
        leave(4, "page failed to load: \(error.localizedDescription)")
    }
}

/// The text a script result prints as: strings raw, booleans as true/false, JSON otherwise.
func rendered(_ value: Any?) -> String? {
    guard let value, !(value as AnyObject is NSNull) else { return nil }  // undefined arrives as a boxed nil
    switch value {
    case let string as String: return string
    case let bool as NSNumber where CFGetTypeID(bool) == CFBooleanGetTypeID(): return bool.boolValue ? "true" : "false"
    case let object where JSONSerialization.isValidJSONObject(object):
        return String(data: try! JSONSerialization.data(withJSONObject: object, options: [.withoutEscapingSlashes, .sortedKeys]), encoding: .utf8)!
    default: return String(describing: value)
    }
}

func emit(_ value: Any?) {
    guard let text = rendered(value) else { return }
    FileHandle.standardOutput.write(Data((text.hasSuffix("\n") ? text : text + "\n").utf8))
}

func read(_ url: URL, script: String, cookie: String?, wait: Double, until: String?) -> Page {
    Page(url, wait: wait, until: until) { web in
        store.httpCookieStore.getAllCookies { cookies in
            let host = web.url?.host ?? ""
            if let cookie, !cookies.contains(where: { $0.name == cookie && $0.sent(to: host) }) {
                return leave(3, "signed out of \(host)")
            }
            let body = script.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                ? "return document.documentElement.outerHTML" : script
            web.callAsyncJavaScript(body, arguments: [:], in: nil, in: .page) { result in
                switch result {
                case .success(let value): emit(value); leave(0)
                case .failure(let error): leave(2, "the script failed: \(exceptionText(error))")
                }
            }
        }
    }
}

let fetchScript = "const response = await fetch(url, {credentials: 'include'}); return [response.status, await response.text()]"

func fetch(_ url: URL, from page: URL) -> Page {
    Page(page) { web in
        web.callAsyncJavaScript(fetchScript, arguments: ["url": url.absoluteString], in: nil, in: .page) { result in
            guard case .success(let value) = result, let pair = value as? [Any],
                  let status = pair.first as? Int, let body = pair.last as? String else {
                if case .failure(let error) = result { return leave(4, "request failed: \(exceptionText(error))") }
                return leave(4, "request failed")
            }
            FileHandle.standardOutput.write(Data(body.utf8))
            (200..<300).contains(status) ? leave(0) : leave(5, "HTTP \(status)")
        }
    }
}

// A top-level navigation that becomes a download, as a click in Safari would: WebKit only
// attaches a site's cookies when the site is the request's first party, which a bare
// startDownload(using:) lacks.
final class Download: NSObject, WKNavigationDelegate, WKDownloadDelegate {
    let web = WKWebView(frame: .zero, configuration: configuration())
    let file: URL
    let part: URL
    let finished: (Int32, String) -> Void
    var transfer: WKDownload?
    var done = false

    init(_ url: URL, to file: URL, finished: @escaping (Int32, String) -> Void = { leave($0, $1) }) {
        self.file = file
        self.finished = finished
        part = file.deletingLastPathComponent().appendingPathComponent("." + file.lastPathComponent + ".umbra-part")
        super.init()
        var isDirectory: ObjCBool = false
        let folder = file.deletingLastPathComponent().path
        guard FileManager.default.fileExists(atPath: folder, isDirectory: &isDirectory), isDirectory.boolValue else {
            DispatchQueue.main.async { self.end(4, "no such directory: \(folder)") }
            return
        }
        web.navigationDelegate = self
        web.load(URLRequest(url: url))
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        if let http = navigationResponse.response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            decisionHandler(.cancel)
            return end(5, "HTTP \(http.statusCode)")
        }
        decisionHandler(.download)
    }

    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        transfer = download
        download.delegate = self
    }

    /// Answers once; whatever WebKit reports afterwards is ignored.
    func end(_ code: Int32, _ message: String) {
        guard !done else { return }
        done = true
        finished(code, message)
    }

    /// Stops the transfer for good: a timed-out download must never land later.
    func cancel(_ message: String) {
        guard !done else { return }
        transfer?.cancel { _ in }
        web.stopLoading()
        try? FileManager.default.removeItem(at: part)
        end(2, message)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        // Handing the response to a download interrupts the navigation (WebKit error 102): expected.
        let error = error as NSError
        guard !(error.domain == "WebKitErrorDomain" && error.code == 102), error.code != NSURLErrorCancelled else { return }
        end(4, "download failed: \(error.localizedDescription)")
    }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String,
                  completionHandler: @escaping (URL?) -> Void) {
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            completionHandler(nil)
            return end(5, "HTTP \(http.statusCode)")
        }
        if done { return completionHandler(nil) }
        try? FileManager.default.removeItem(at: part)
        completionHandler(part)
    }

    func downloadDidFinish(_ download: WKDownload) {
        guard !done else { try? FileManager.default.removeItem(at: part); return }
        guard rename(part.path, file.path) == 0 else {
            try? FileManager.default.removeItem(at: part)
            return end(4, "could not move the download to \(file.path)")
        }
        end(0, "")
    }

    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        try? FileManager.default.removeItem(at: part)
        end(4, "download failed: \(error.localizedDescription)")
    }
}

/// Runs a one-shot command in this process: no window, no Dock icon, never takes focus.
func headless(timeout: Double, _ start: () -> AnyObject) -> Never {
    NSApplication.shared.setActivationPolicy(.prohibited)
    takeLock()
    silenceWebKit()
    let job = start()
    DispatchQueue.main.asyncAfter(deadline: .now() + timeout) {
        if let download = job as? Download { return download.cancel("no answer in \(duration(timeout))") }
        leave(2, "no answer in \(duration(timeout))")
    }
    withExtendedLifetime(job) { NSApplication.shared.run() }
    exit(0)
}
