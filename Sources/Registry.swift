// Every daemon command, declared once: the CLI parser, `umbra schema`, `umbra help` and the MCP
// tool list are all built from this registry.
import Foundation

/// A failure with the process exit code it maps to (see the exit-code table in main.swift).
struct UmbraError: Error {
    let code: Int32
    let message: String
    let details: [String: Any]
    init(_ code: Int32, _ message: String, details: [String: Any] = [:]) {
        self.code = code; self.message = message; self.details = details.filter { $0.key != "code" && $0.key != "message" }
    }
    var json: [String: Any] { details.merging(["code": Int(code), "message": message]) { _, reserved in reserved } }
}

/// A command's answer: JSON-ready values. "text" is what the CLI prints when present.
typealias Reply = (Result<[String: Any], UmbraError>) -> Void

struct Param {
    enum Kind: String { case string, number, boolean, array }
    let name: String
    let kind: Kind
    let help: String
    var required = false
    var positional = false  // CLI: given in order without a flag; an array positional takes the rest
}

struct Command {
    let name: String
    let help: String
    var params: [Param] = []
    var aliases: [String] = []
    var usesTab = true    // accepts --tab and acts on that tab (the current one by default)
    var mcp = true        // offered as an MCP tool
    var timeout: Double = 30
    var deadlineError: ((Args) -> UmbraError)? = nil
    let run: (Args, Engine, @escaping Reply) -> Void
}

struct Args {
    let values: [String: Any]
    var operation: String? { string("_operation") }
    func string(_ name: String) -> String? { values[name].map { "\($0)" } }
    func number(_ name: String) -> Double? {
        switch values[name] {
        case let number as NSNumber: return number.doubleValue
        case let text as String: return Double(text)
        default: return nil
        }
    }
    func bool(_ name: String) -> Bool {
        switch values[name] {
        case let number as NSNumber: return number.boolValue
        case let text as String: return ["1", "true", "yes"].contains(text.lowercased())
        default: return false
        }
    }
    /// A file path argument, resolved against the caller's directory ("cwd", sent by the CLI and MCP).
    func path(_ name: String) -> URL? {
        guard let text = string(name) else { return nil }
        let expanded = (text as NSString).expandingTildeInPath
        if expanded.hasPrefix("/") { return URL(fileURLWithPath: expanded).standardizedFileURL }
        let base = string("cwd") ?? FileManager.default.currentDirectoryPath
        return URL(fileURLWithPath: expanded, relativeTo: URL(fileURLWithPath: base, isDirectory: true)).standardizedFileURL
    }
    func strings(_ name: String) -> [String] {
        switch values[name] {
        case let list as [Any]: return list.map { "\($0)" }
        case let text as String: return [text]
        default: return []
        }
    }
}

/// Filled at startup by main.swift from each lane's command list.
var registry: [Command] = []

func lookup(_ name: String) -> Command? {
    registry.first { $0.name == name || $0.aliases.contains(name) }
}

/// Global options every daemon command accepts on the command line.
let globalParams = [
    Param(name: "tab", kind: .string, help: "Tab to act on (default: the current tab)"),
    Param(name: "timeout", kind: .number, help: "Seconds before the command gives up"),
    Param(name: "agent", kind: .string, help: "Agent owning the task and any tab lease"),
    Param(name: "task", kind: .string, help: "Task owning the request and any tab lease"),
    Param(name: "request", kind: .string, help: "Caller-chosen request ID for status and cancellation"),
    Param(name: "deadline", kind: .number, help: "Absolute UNIX time after which the request must not start"),
]

func parameters(_ command: Command) -> [Param] {
    command.params + globalParams.filter { option in
        (option.name != "tab" || command.usesTab) && !command.params.contains(where: { $0.name == option.name })
    }
}

/// Turns `umbra <command> …` arguments into named values using the command's params.
func parseArguments(_ command: Command, _ argv: [String]) -> Result<[String: Any], UmbraError> {
    var values: [String: Any] = [:]
    let params = parameters(command)
    var positionals = command.params.filter(\.positional)
    var index = 0
    func convert(_ param: Param, _ text: String) -> Any? {
        switch param.kind {
        case .number: return Double(text).flatMap { $0.isFinite ? $0 : nil }  // JSON has no NaN or infinity
        case .boolean: return ["1", "true", "yes"].contains(text.lowercased())
        case .string, .array: return text
        }
    }
    while index < argv.count {
        let word = argv[index]
        if word.hasPrefix("--"), word.count > 2 {
            var name = String(word.dropFirst(2))
            var inline: String?
            if let equals = name.firstIndex(of: "=") {
                inline = String(name[name.index(after: equals)...])
                name = String(name[..<equals])
            }
            guard let param = params.first(where: { $0.name == name }) else {
                return .failure(UmbraError(64, "\(command.name) has no option --\(name)"))
            }
            let text: String
            if param.kind == .boolean, inline == nil {
                text = "true"
            } else if let inline {
                text = inline
            } else {
                index += 1
                guard index < argv.count else { return .failure(UmbraError(64, "--\(name) needs a value")) }
                text = argv[index]
            }
            guard let value = convert(param, text) else {
                return .failure(UmbraError(64, "--\(name) takes a \(param.kind.rawValue)"))
            }
            if param.kind == .array {
                values[name] = (values[name] as? [Any] ?? []) + [value]
            } else {
                values[name] = value
            }
        } else if let param = positionals.first {
            guard let value = convert(param, word) else {
                return .failure(UmbraError(64, "\(param.name) takes a \(param.kind.rawValue)"))
            }
            if param.kind == .array {
                values[param.name] = (values[param.name] as? [Any] ?? []) + [value]
            } else {
                values[param.name] = value
                positionals.removeFirst()
            }
        } else {
            return .failure(UmbraError(64, "unexpected argument: \(word)\n\n\(usageLine(command))"))
        }
        index += 1
    }
    if let missing = command.params.first(where: { $0.required && values[$0.name] == nil }) {
        return .failure(UmbraError(64, "\(command.name) needs \(missing.name)\n\n\(usageLine(command))"))
    }
    return .success(values)
}

func usageLine(_ command: Command) -> String {
    let parts = command.params.map { param -> String in
        let shown = param.positional ? "<\(param.name)\(param.kind == .array ? " …" : "")>"
            : "--\(param.name)" + (param.kind == .boolean ? "" : " <\(param.kind.rawValue)>")
        return param.required ? shown : "[\(shown)]"
    }
    return "usage: " + (["umbra", command.name] + parts).joined(separator: " ")
}

/// JSON Schema for a command's params, as MCP's tools/list and `umbra schema` present them.
func inputSchema(_ command: Command) -> [String: Any] {
    var properties: [String: Any] = [:]
    for param in parameters(command) {
        var schema: [String: Any] = ["description": param.help]
        switch param.kind {
        case .array: schema["type"] = "array"; schema["items"] = ["type": "string"]
        default: schema["type"] = param.kind.rawValue
        }
        properties[param.name] = schema
    }
    return ["type": "object", "properties": properties,
            "required": command.params.filter(\.required).map(\.name)]
}

func schemaJSON() -> [String: Any] {
    ["commands": registry.map { command -> [String: Any] in
        ["name": command.name, "aliases": command.aliases, "description": command.help,
         "usage": usageLine(command), "mcp": command.mcp, "inputSchema": inputSchema(command)]
    }]
}
