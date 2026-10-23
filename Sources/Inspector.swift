import Foundation
import Network

private let inspectorHTML = #"""
<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Umbra · Runtime</title>
<style>
:root{color-scheme:dark;font:15px/1.5 -apple-system,BlinkMacSystemFont,sans-serif;background:#0d101b;color:#ecedfa}body{max-width:1120px;margin:40px auto;padding:0 24px}header{display:flex;align-items:center;justify-content:space-between;gap:20px}h1{font-size:32px;letter-spacing:-1px}p,small{color:#a8aec4}button,select{font:inherit;background:#222943;color:#f3f4ff;border:1px solid #47517a;border-radius:8px;padding:8px 12px;cursor:pointer}button:focus-visible,select:focus-visible{outline:2px solid #a8a6ff;outline-offset:3px}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin:28px 0}.stat,section{background:#151a2b;border:1px solid #2d3551;border-radius:12px;padding:20px}.stat strong{font-size:28px;display:block;color:#b6b2ff}section{margin:18px 0}h2{margin-top:0;font-size:18px}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}td,th{text-align:left;padding:10px 12px;border-bottom:1px solid #303750;overflow-wrap:anywhere}#processes{overflow-x:auto}#processes table{min-width:560px}#processes td:first-child,#processes td:nth-child(4),#processes td:nth-child(5){white-space:nowrap}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}img{max-width:100%;border-radius:8px}#error{color:#d4bfff;min-height:24px}@media(max-width:650px){body{margin:20px auto;padding:0 14px}.stats{grid-template-columns:repeat(2,1fr)}section{padding:12px}td,th{padding:8px 4px}header{align-items:start;flex-direction:column}}
</style>
<header><div><small>UMBRA / LOCAL RUNTIME</small><h1>Your agents, in view.</h1></div><button id=refresh>Refresh</button></header>
<p>Live queue, ownership and native process memory. Page capture starts only when requested. Process totals are best effort; shared helpers may be omitted.</p>
<div id=error role=status></div><div id=stats class=stats></div>
<section><h2>Tabs and ownership</h2><div id=tabs></div></section>
<section><h2>Process memory</h2><div id=processes role=region aria-label="Process memory table" tabindex=0></div><p id=coverage></p></section>
<section><h2>Tasks</h2><pre id=tasks></pre></section>
<section><h2>Recent commands</h2><pre id=history></pre></section>
<section><h2>Watch or take over</h2><select id=tab aria-label=Tab></select> <button id=capture>Capture frame</button> <label><input id=live type=checkbox> Live preview · 1 fps</label> <button id=handoff>Take over in Umbra</button><p>Takeover opens Umbra's window. Close it when finished.</p><img id=frame alt="Requested browser frame" hidden></section>
<script>
const token=location.hash.slice(1); history.replaceState(null,'',location.pathname); let state={};
const el=id=>document.getElementById(id); const fmt=x=>JSON.stringify(x,null,2);
async function api(path,body){const r=await fetch(path,{method:body?'POST':'GET',headers:{Authorization:'Bearer '+token,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined});const j=await r.json();if(!r.ok||j.error)throw Error(j.error||'Request failed');return j;}
function cell(row,text,tag='td'){const c=document.createElement(tag);c.textContent=String(text??'—');row.append(c);return c;}
async function refresh(){try{state=await api('/state');el('error').textContent='';el('stats').replaceChildren();for(const [label,value]of [['Active',state.active],['Queued',state.queued],['Tabs',state.tabs?.length],['Observed MiB',Math.round((state.processMetrics?.physicalFootprintBytes||0)/1048576)]]){const d=document.createElement('div');d.className='stat';const s=document.createElement('strong');s.textContent=value??'—';d.append(s,document.createTextNode(label));el('stats').append(d);}const table=document.createElement('table');const head=document.createElement('tr');for(const s of ['Tab','Agent','Task'])cell(head,s,'th');table.append(head);const selected=el('tab').value;el('tab').replaceChildren();for(const t of state.tabs||[]){const row=document.createElement('tr');const lease=(state.leases||[]).find(x=>x.tab===t.name)||{};cell(row,t.name);cell(row,lease.agent);cell(row,lease.task);table.append(row);const o=document.createElement('option');o.value=t.name;o.textContent=t.name;el('tab').append(o);}if([...el('tab').options].some(x=>x.value===selected))el('tab').value=selected;el('tabs').replaceChildren(table);const memory=document.createElement('table');const mh=document.createElement('tr');for(const k of ['PID','Process','Tabs','MiB','CPU seconds'])cell(mh,k,'th');memory.append(mh);for(const p of state.processMetrics?.processes||[]){const row=document.createElement('tr');for(const v of [p.pid,p.kind,(p.tabs||[]).join(', '),(p.physicalFootprintBytes/1048576).toFixed(1),typeof p.cpuSeconds==='number'?p.cpuSeconds.toFixed(2):'—'])cell(row,v);memory.append(row);}el('processes').replaceChildren(memory);el('coverage').textContent=state.processMetrics?.scope||'No sample';el('tasks').textContent=fmt({pending:state.pending,pressure:state.pressure,headroom:state.headroom,memory:state.memory});el('history').textContent=fmt(state.history);}catch(e){el('error').textContent=e.message;}}
function target(){const tab=el('tab').value;if(!tab)throw Error('Choose a tab');const lease=(state.leases||[]).find(x=>x.tab===tab)||{};return {tab,agent:lease.agent,task:lease.task};}
let capturing=false;async function capture(){if(capturing)return;capturing=true;try{const x=await api('/frame',target());el('frame').src='data:'+x.mime+';base64,'+x.data;el('frame').hidden=false;}catch(e){el('error').textContent=e.message;}finally{capturing=false;}}
el('refresh').onclick=refresh;el('capture').onclick=capture;setInterval(()=>{if(el('live').checked&&!document.hidden)capture();},1000);
el('handoff').onclick=async()=>{try{await api('/handoff',target());await refresh();}catch(e){el('error').textContent=e.message;}};
refresh();setInterval(()=>{if(!document.hidden)refresh();},2000);
</script>
"""#

private final class Inspector {
    let engine: Engine
    let listener: NWListener
    let token = UUID().uuidString
    var connections = 0
    var clients: [ObjectIdentifier: NWConnection] = [:]
    var timeouts: [ObjectIdentifier: DispatchSourceTimer] = [:]
    var started = false

    init(_ engine: Engine) throws {
        self.engine = engine
        let params = NWParameters.tcp
        params.requiredLocalEndpoint = .hostPort(host: .ipv4(.loopback), port: .any)
        listener = try NWListener(using: params)
    }

    func start(_ reply: @escaping Reply) {
        listener.stateUpdateHandler = { [weak self] state in
            guard let self else { return }
            if case .ready = state, !self.started {
                self.started = true
                let url = "http://127.0.0.1:\(self.listener.port!.rawValue)/#\(self.token)"
                reply(.success(["url": url, "text": url, "capture": "on demand", "loopback": true]))
            } else if case .failed = state, !self.started { reply(.failure(UmbraError(1, "cannot start local inspector"))) }
        }
        listener.newConnectionHandler = { [weak self] connection in
            guard let self else { connection.cancel(); return }
            guard self.connections < 16 else { connection.cancel(); return }
            self.connections += 1
            let id = ObjectIdentifier(connection)
            self.clients[id] = connection
            var closed = false
            connection.stateUpdateHandler = { [weak self, weak connection] state in
                if case .cancelled = state, !closed {
                    closed = true; self?.connections -= 1; self?.clients[id] = nil
                    let timeout = self?.timeouts.removeValue(forKey: id)
                    timeout?.setEventHandler {}; timeout?.cancel()
                    connection?.stateUpdateHandler = nil
                }
            }
            connection.start(queue: .main)
            self.receive(connection, Data())
            let timeout = DispatchSource.makeTimerSource(queue: .main)
            timeout.schedule(deadline: .now() + 65)
            timeout.setEventHandler { [weak connection] in connection?.cancel() }
            self.timeouts[id] = timeout; timeout.resume()
        }
        listener.start(queue: .main)
    }

    func stop() {
        listener.stateUpdateHandler = nil; listener.newConnectionHandler = nil
        listener.cancel()
        let old = clients; clients = [:]
        for timer in timeouts.values { timer.setEventHandler {}; timer.cancel() }
        timeouts = [:]
        for connection in old.values { connection.stateUpdateHandler = nil; connection.cancel() }
        connections = 0
    }

    func receive(_ connection: NWConnection, _ pending: Data) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 8192) { data, _, complete, error in
            var input = pending; input.append(data ?? Data())
            guard input.count <= 16384 else { return self.send(connection, code: 413, object: ["error": "request too large"]) }
            guard let text = String(data: input, encoding: .utf8), let boundary = text.range(of: "\r\n\r\n") else {
                if !complete, error == nil { return self.receive(connection, input) }; connection.cancel(); return
            }
            let header = String(text[..<boundary.lowerBound]), lines = header.components(separatedBy: "\r\n")
            let first = lines[0].split(separator: " ").map(String.init)
            guard first.count == 3 else { return self.send(connection, code: 400, object: ["error": "invalid request"]) }
            var headers: [String: String] = [:]
            for line in lines.dropFirst() { if let colon = line.firstIndex(of: ":") { headers[String(line[..<colon]).lowercased()] = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces) } }
            if first[0] == "GET", first[1] == "/" { return self.send(connection, code: 200, data: Data(inspectorHTML.utf8), mime: "text/html; charset=utf-8") }
            guard headers["authorization"] == "Bearer \(self.token)" else { return self.send(connection, code: 403, object: ["error": "inspector token required"]) }
            if first[0] == "GET", first[1] == "/state" {
                var state = self.engine.runtime.snapshot()
                state["tabs"] = self.engine.tabs.map { ["name": $0.name] }
                state["processMetrics"] = processMetrics(self.engine)
                return self.send(connection, code: 200, object: state)
            }
            guard first[0] == "POST", ["/frame", "/handoff"].contains(first[1]), let length = headers["content-length"].flatMap(Int.init), length >= 0, length <= 1024 else {
                return self.send(connection, code: 400, object: ["error": "invalid inspector action"])
            }
            let body = Data(text[boundary.upperBound...].utf8)
            guard body.count >= length else { if !complete, error == nil { return self.receive(connection, input) }; connection.cancel(); return }
            guard let args = try? JSONSerialization.jsonObject(with: body.prefix(length)) as? [String: Any], let tab = args["tab"] as? String else { return self.send(connection, code: 400, object: ["error": "tab required"]) }
            var values: [String: Any] = ["tab": tab, "timeout": 60]
            for key in ["agent", "task"] { if let value = args[key] as? String { values[key] = value } }
            values["message"] = first[1] == "/handoff" ? "Finish this step, then close the window" : nil
            self.engine.runtime.run(command: first[1] == "/frame" ? "screenshot" : "handoff", args: values) { result in
                switch result { case .success(let value): self.send(connection, code: 200, object: value)
                case .failure(let error): self.send(connection, code: 400, object: ["error": error.message]) }
            }
        }
    }

    func send(_ connection: NWConnection, code: Int, object: [String: Any]) {
        let data = (try? JSONSerialization.data(withJSONObject: jsonSafe(object), options: [.sortedKeys])) ?? Data("{}".utf8)
        send(connection, code: code, data: data, mime: "application/json")
    }

    func send(_ connection: NWConnection, code: Int, data: Data, mime: String) {
        let headers = "HTTP/1.1 \(code) Response\r\nContent-Type: \(mime)\r\nContent-Length: \(data.count)\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\nContent-Security-Policy: default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'\r\nConnection: close\r\n\r\n"
        connection.send(content: Data(headers.utf8) + data, completion: .contentProcessed { _ in connection.cancel() })
    }
}

private var inspector: Inspector?

func inspectorCommands() -> [Command] {
    [Command(name: "inspector", help: "Start an on-demand, token-protected loopback dashboard with queue/ownership metadata, requested screenshots and human takeover. --stop shuts its listener down. No background page capture.",
             params: [Param(name: "stop", kind: .boolean, help: "Stop the local dashboard")], usesTab: false) { args, engine, reply in
        if args.bool("stop") { inspector?.stop(); inspector = nil; return reply(.success(["text": "inspector stopped"])) }
        if let inspector, let port = inspector.listener.port {
            let url = "http://127.0.0.1:\(port.rawValue)/#\(inspector.token)"
            return reply(.success(["url": url, "text": url]))
        }
        do { let view = try Inspector(engine); inspector = view; view.start(reply) }
        catch { reply(.failure(UmbraError(1, "cannot create local inspector"))) }
    }]
}
