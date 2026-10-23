"""SDK transport contracts against a deterministic local Unix-socket daemon fixture."""
import importlib.util
import json
import hashlib
import http.server
import os
import uuid
from unittest.mock import patch
from pathlib import Path
import shutil
import socketserver
import subprocess
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('umbra_sdk', ROOT / 'sdk/python/umbra.py')
sdk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sdk)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.server.connections += 1
        connection = self.server.connections
        for line in self.rfile:
            request = json.loads(line)
            name, args, rid = request['command'], request['args'], request['id']
            self.server.commands.append(name)
            reply = {'id': rid, 'ok': True, 'result': {'connection': connection, 'args': args}}
            if name == 'error':
                reply = {'id': rid, 'ok': False, 'error': {'code': 78, 'message': 'fixture failure', 'extra': 'retained'}}
            elif name == 'large':
                reply['result']['body'] = 'x' * 4096
            elif name == 'wrong':
                reply['id'] = 'another-request'
            elif name == 'sleep':
                time.sleep(args.get('seconds', .2))
            elif name == 'block':
                event = threading.Event()
                self.server.pending[args.get('request', rid)] = event
                event.wait(2)
                self.server.pending.pop(args.get('request', rid), None)
                reply = {'id': rid, 'ok': False, 'error': {'code': 2, 'message': 'cancelled'}}
            elif name == 'cancel':
                self.server.cancel_targets.append(args['id'])
                event = self.server.pending.get(args['id'])
                if event:
                    event.set()
                reply['result']['cancelled'] = bool(event)
            try:
                encoded = (json.dumps(reply) + '\n').encode()
                # Deliberately fragment replies across reads.
                self.wfile.write(encoded[:7]); self.wfile.flush()
                self.wfile.write(encoded[7:]); self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


class SDKTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='umbra-sdk-', dir='/tmp')
        cls.path = str(Path(cls.temp.name) / 'daemon.sock')
        cls.server = Server(cls.path, Handler)
        cls.server.connections, cls.server.pending, cls.server.commands = 0, {}, []
        cls.server.cancel_targets = []
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(); cls.temp.cleanup()

    def client(self, **options):
        client = sdk.Umbra('sdk-fixture', socket_path=self.path, **options)
        self.addCleanup(client.close)
        return client

    def test_python_persistence_errors_and_close(self):
        client = self.client()
        first = client.call('echo', {'n': 1})
        self.assertEqual(first['connection'], client.call('echo')['connection'])
        self.assertGreater(first['args']['deadline'], time.time())
        self.assertEqual(client.call('echo', request_id='python-id')['args']['request'], 'python-id')
        with self.assertRaises(sdk.UmbraError) as error:
            client.call('error')
        self.assertEqual(error.exception.code, 78)
        self.assertEqual(error.exception.details['extra'], 'retained')
        self.assertEqual(first['connection'], client.call('echo')['connection'])
        client.close()
        with self.assertRaisesRegex(sdk.UmbraError, 'closed'):
            client.call('echo')
        self.assertIn('connection', self.client().call('echo'))

    def test_python_limits_ids_and_deadlines(self):
        client = self.client(max_reply_bytes=512)
        with self.assertRaisesRegex(sdk.UmbraError, 'exceeds'):
            client.call('large')
        self.assertIn('connection', client.call('echo'))
        with self.assertRaisesRegex(sdk.UmbraError, 'mismatched'):
            client.call('wrong')
        started = time.monotonic()
        with self.assertRaises(sdk.UmbraError) as error:
            client.call('sleep', deadline=time.time() + .05)
        self.assertEqual(error.exception.code, 2)
        self.assertLess(time.monotonic() - started, .3)
        self.assertIn('connection', client.call('echo'))

    def test_python_control_connection_cancels_busy_client(self):
        client, errors = self.client(), []
        def block():
            try:
                client.call('block', request_id='python-block')
            except sdk.UmbraError as error:
                errors.append(error)
        worker = threading.Thread(target=block)
        worker.start()
        end = time.time() + 2
        while 'python-block' not in self.server.pending and time.time() < end:
            time.sleep(.005)
        self.assertTrue(client.cancel('python-block')['cancelled'])
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors[0].code, 2)

    def test_python_repair_is_observe_only(self):
        client = self.client()
        start = len(self.server.commands)
        calls = []
        def provider(observation, step):
            calls.append((observation, step))
            return {"target": "#new"}
        proposal = client.propose_repair({"command": "click", "args": {"target": "#old"}}, provider)
        self.assertFalse(proposal['dispatched'])
        self.assertEqual(proposal['step']['args'], {"target": "#new"})
        self.assertEqual(self.server.commands[start:], ['observe'])
        self.assertIn('connection', calls[0][0])
        with self.assertRaises(ValueError):
            client.propose_repair({"command": "click"}, lambda *_: [])
        self.assertNotIn('click', self.server.commands[start:])

    def test_profiles_are_explicit_and_long_names_match(self):
        for invalid in ('', ' ', '\0'):
            with self.assertRaises(ValueError):
                sdk.Umbra(invalid)
        self.assertEqual(sdk.profile_socket('a' * 30).name, 'daemon-3a54fc0c-bc0.sock')
        with self.assertRaises(ValueError):
            self.client().start()
        with patch.object(subprocess, 'Popen') as spawn:
            self.assertIsNone(self.client(binary='/unused/absolute/umbra').start())
            spawn.assert_not_called()

    @unittest.skipUnless(shutil.which('node'), 'Node required')
    def test_typescript_transport(self):
        source = '''
import assert from 'node:assert/strict';
import { Umbra, UmbraError } from SDK;
const options = { socketPath: SOCKET };
const client = new Umbra('sdk-fixture', options);
const existing = new Umbra('sdk-fixture', { ...options, binary: '/unused/absolute/umbra' });
assert.equal(await existing.start(), undefined); existing.close();
const first = await client.call('echo');
assert.equal((await client.call('echo', {}, {requestId:'ts-id'})).args.request, 'ts-id');
const proposal = await client.proposeRepair({ command: 'click', args: { target: '#old' } }, (observation, step) => { assert.ok(observation.connection); assert.equal(step.command, 'click'); return { target: '#new' }; });
assert.equal(proposal.dispatched, false); assert.equal(proposal.step.args.target, '#new');
await assert.rejects(client.proposeRepair({ command: 'click' }, () => []), /args object/);
assert.equal(first.connection, (await client.call('echo')).connection);
await assert.rejects(client.call('error'), e => e.code === 78 && e.details.extra === 'retained');
assert.equal(first.connection, (await client.call('echo')).connection);
const small = new Umbra('sdk-fixture', { ...options, maxReplyBytes: 512 });
await assert.rejects(small.call('large'), /exceeds/);
assert.ok((await small.call('echo')).connection);
await assert.rejects(small.call('wrong'), /mismatched/);
await assert.rejects(small.call('sleep', {}, { deadline: Date.now()/1000 + .04 }), e => e.code === 2);
const busy = client.call('block', {}, { requestId: 'ts-block' });
const observedBusy = assert.rejects(busy, e => e.code === 2);
await new Promise(r => setTimeout(r, 30));
assert.equal((await client.cancel('ts-block')).cancelled, true);
await observedBusy;
const slow = client.call('sleep', { seconds: .2 });
const start = Date.now();
await assert.rejects(client.call('echo', {}, { deadline: Date.now()/1000 + .03 }), e => e.code === 2);
assert.ok(Date.now()-start < 150);
await slow;
const controller = new AbortController();
const aborting = client.call('block', {}, { requestId: 'ts-abort', signal: controller.signal });
const observedAbort = assert.rejects(aborting, e => e.code === 'cancelled');
setTimeout(() => controller.abort(), 25);
await observedAbort;
client.close(); small.close();
await assert.rejects(client.call('echo'), /closed/);
console.log('PASS TypeScript persistent calls, errors, bounds, IDs, deadlines, cancellation and AbortSignal');
'''.replace('from SDK', 'from ' + json.dumps((ROOT / 'sdk/typescript/umbra.ts').as_uri())).replace('SOCKET', json.dumps(self.path))
        path = Path(self.temp.name) / 'test.mts'
        path.write_text(source)
        result = subprocess.run(['node', '--experimental-strip-types', str(path)], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required')
    def test_typescript_abort_and_timeout_cancel_overridden_runtime_id(self):
        source = """
import assert from 'node:assert/strict';
import { Umbra } from SDK;
for (const mode of ['abort', 'deadline']) {
  const client = new Umbra('sdk-fixture', { socketPath: SOCKET });
  const controller = new AbortController();
  const pending = client.call('block', {request:'ts-runtime-'+mode}, {
    requestId:'ts-wire-'+mode, signal:controller.signal,
    deadline:Date.now()/1000 + (mode === 'deadline' ? .2 : 2),
  });
  const observed = assert.rejects(pending, e => e.code === (mode === 'abort' ? 'cancelled' : 2));
  if (mode === 'abort') setTimeout(() => controller.abort(), 40);
  await observed;
  await new Promise(r => setTimeout(r, 100));
  client.close();
}
""".replace('from SDK', 'from ' + json.dumps((ROOT / 'sdk/typescript/umbra.ts').as_uri())).replace('SOCKET', json.dumps(self.path))
        path = Path(self.temp.name) / 'runtime-id.mts'
        path.write_text(source)
        result = subprocess.run(['node', '--experimental-strip-types', str(path)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for mode in ('abort', 'deadline'):
            self.assertIn('ts-runtime-' + mode, self.server.cancel_targets)
            self.assertNotIn('ts-wire-' + mode, self.server.cancel_targets)
            self.assertNotIn('ts-runtime-' + mode, self.server.pending)

    @unittest.skipUnless(shutil.which('swiftc'), 'Swift required')
    def test_swift_transport(self):
        source = '''
import Foundation
let path = SOCKET
let client = try Umbra(profile: "sdk-fixture", socketPath: path)
let existing = try Umbra(profile: "sdk-fixture", binary: URL(fileURLWithPath: "/unused/absolute/umbra"), socketPath: path)
let spawned = try existing.start(); precondition(spawned == nil); existing.close()
let first = try client.call("echo")
let idCheck = try client.call("echo", requestID: "swift-id")
precondition((idCheck["args"] as! [String: Any])["request"] as? String == "swift-id")
let proposal = try client.proposeRepair(["command": "click", "args": ["target": "#old"]]) { observation, step in
 precondition(observation["connection"] != nil); precondition(step["command"] as? String == "click")
 return ["target": "#new"]
}
precondition(proposal["dispatched"] as? Bool == false)
precondition((proposal["step"] as! [String: Any])["command"] as? String == "click")
let second = try client.call("echo")
precondition(first["connection"] as! Int == second["connection"] as! Int)
do { _ = try client.call("error"); fatalError("missing error") }
catch let error as UmbraError { precondition(error.code as? Int == 78); precondition(error.details["extra"] as? String == "retained") }
let small = try Umbra(profile: "sdk-fixture", socketPath: path, maxReplyBytes: 512)
do { _ = try small.call("large"); fatalError("missing limit") }
catch let error as UmbraError { precondition(error.message.contains("exceeds")) }
_ = try small.call("echo")
do { _ = try small.call("wrong"); fatalError("missing mismatch") }
catch let error as UmbraError { precondition(error.message.contains("mismatched")) }
let start = Date()
do { _ = try small.call("sleep", deadline: Date().addingTimeInterval(0.04)); fatalError("missing deadline") }
catch let error as UmbraError { precondition(error.code as? Int == 2) }
precondition(Date().timeIntervalSince(start) < 0.3)
let done = DispatchSemaphore(value: 0)
DispatchQueue.global().async {
 do { _ = try client.call("block", requestID: "swift-block"); fatalError("missing cancellation") }
 catch let error as UmbraError { precondition(error.code as? Int == 2) }
 catch { fatalError("unexpected error") }
 done.signal()
}
Thread.sleep(forTimeInterval: 0.05)
let cancelled = try client.cancel("swift-block")
precondition(cancelled["cancelled"] as? Bool == true)
precondition(done.wait(timeout: .now() + 2) == .success)
client.close(); small.close()
do { _ = try client.call("echo"); fatalError("missing closed error") }
catch let error as UmbraError { precondition(error.message.contains("closed")) }
print("PASS Swift persistent calls, errors, bounds, IDs, deadlines and cancellation")
'''.replace('SOCKET', json.dumps(self.path))
        main = Path(self.temp.name) / 'main.swift'
        main.write_text(source)
        binary = Path(self.temp.name) / 'swift-test'
        built = subprocess.run(['swiftc', '-swift-version', '5', str(ROOT / 'sdk/swift/Umbra.swift'), str(main), '-o', str(binary)], capture_output=True, text=True, timeout=60)
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


@unittest.skipUnless(os.environ.get('UMBRA_SDK_BINARY'), 'set UMBRA_SDK_BINARY to test the built runtime')
class SDKLiveTests(unittest.TestCase):
    def test_three_clients_share_real_local_page(self):
        binary = str(Path(os.environ['UMBRA_SDK_BINARY']).resolve())
        profile = 'sdk-' + uuid.uuid4().hex[:12]
        class Page(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = b'<title>SDK local fixture</title><h1>SDK shared page</h1><button onclick="window.repairClicks=(window.repairClicks||0)+1">Continue</button>'
                self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
            def log_message(self, *_):
                pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Page)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        process = None
        with tempfile.TemporaryDirectory(prefix='umbra-sdk-live-', dir='/tmp') as directory:
            env = {**os.environ, 'UMBRA_PROFILE': profile, 'UMBRA_IDLE': '60', 'UMBRA_UNSEEN': '1', 'UMBRA_AGENTS': directory}
            client = sdk.Umbra(profile, binary=binary)
            try:
                with patch.dict(os.environ, env):
                    process = client.start()
                status = client.call('status')
                self.assertEqual(status['profile'], profile)
                client.call('open', {'url': f'http://127.0.0.1:{server.server_port}/', 'tab': 'shared'})
                self.assertEqual(client.call('eval', {'script': 'window.sdkCount = 1; return window.sdkCount'})['value'], 1)
                self.assertIn('Continue', client.call('snapshot', {'interactive': True})['text'])
                if os.environ.get('UMBRA_SDK_RUNTIME') == '1':
                    waiting = sdk.Umbra(profile)
                    errors = []
                    def wait_owned():
                        try:
                            waiting.call('wait', {'js': 'false', 'agent': 'python', 'task': 'owned', 'timeout': 5}, request_id='python-owned')
                        except sdk.UmbraError as error:
                            errors.append(error)
                    worker = threading.Thread(target=wait_owned); worker.start()
                    end = time.time() + 2
                    while time.time() < end:
                        if any(row['request'] == 'python-owned' for row in client.call('tasks')['pending']):
                            break
                        time.sleep(.01)
                    self.assertEqual(client.cancel('python-owned', agent='python', task='owned')['cancelled'], 'python-owned')
                    worker.join(2); waiting.close()
                    self.assertFalse(worker.is_alive()); self.assertEqual(errors[0].code, 2)
                    proposal = client.propose_repair({'command': 'click', 'args': {'target': '#old'}}, lambda observation, step: {'target': 'button'})
                    self.assertFalse(proposal['dispatched']); self.assertIn('Continue', json.dumps(proposal['observation']))
                ts = Path(directory) / 'live.mts'
                ts.write_text("import { Umbra } from " + json.dumps((ROOT / 'sdk/typescript/umbra.ts').as_uri()) + ";\n" +
                    "const b = new Umbra(process.env.UMBRA_PROFILE!, {binary: process.env.UMBRA_SDK_BINARY}); await b.start();\n" +
                    "try { const r = await b.call('eval', {script:'return ++window.sdkCount'}); if(r.value !== 2) throw Error('shared state'); console.log('PASS TypeScript real WebKit'); } finally {b.close();}\n")
                if os.environ.get('UMBRA_SDK_RUNTIME') == '1':
                    source = ts.read_text().replace("console.log('PASS TypeScript real WebKit');", """
const waiting = new Umbra(process.env.UMBRA_PROFILE!);
const wait = waiting.call('wait', {js:'false',agent:'ts',task:'owned',timeout:5,request:'ts-owned-runtime'}, {requestId:'ts-owned-wire'});
let waitCode; const observedWait = wait.catch(e=>{waitCode=e.code;});
for(let i=0;i<100;i++){const tasks=await b.call('tasks');if(tasks.pending.some(t=>t.request==='ts-owned-runtime'))break;await new Promise(r=>setTimeout(r,10));}
const cancelled=await b.cancel('ts-owned-runtime',{agent:'ts',task:'owned'});if(cancelled.cancelled!=='ts-owned-runtime')throw Error('cancel ID');
await observedWait;waiting.close();if(waitCode!==2)throw Error('cancel code');
const proposal=await b.proposeRepair({command:'click',args:{target:'#old'}},(observation,step)=>{if(!JSON.stringify(observation).includes('Continue'))throw Error('fresh observation');return {target:'button'};});
if(proposal.dispatched!==false)throw Error('proposal dispatched');
console.log('PASS TypeScript real WebKit cancellation and repair');
""")
                    ts.write_text(source)
                result = subprocess.run(['node', '--experimental-strip-types', str(ts)], env=env, text=True, capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                main = Path(directory) / 'main.swift'
                main.write_text('import Foundation\nlet b = try Umbra(profile: ProcessInfo.processInfo.environment["UMBRA_PROFILE"]!, binary: URL(fileURLWithPath: ProcessInfo.processInfo.environment["UMBRA_SDK_BINARY"]!))\ntry b.start()\ndefer { b.close() }\nlet r = try b.call("eval", ["script": "return ++window.sdkCount"])\nprecondition(r["value"] as? Int == 3)\nprint("PASS Swift real WebKit")\n')
                if os.environ.get('UMBRA_SDK_RUNTIME') == '1':
                    source = main.read_text().replace('print("PASS Swift real WebKit")', """
let waiting = try Umbra(profile: ProcessInfo.processInfo.environment["UMBRA_PROFILE"]!)
let done = DispatchSemaphore(value:0)
DispatchQueue.global().async {
 do { _ = try waiting.call("wait", ["js":"false","agent":"swift","task":"owned","timeout":5], requestID:"swift-owned"); fatalError("not cancelled") }
 catch let error as UmbraError { precondition(error.code as? Int == 2) }
 catch { fatalError("unexpected wait error") }
 done.signal()
}
for _ in 0..<100 {
 let tasks = try b.call("tasks")
 if (tasks["pending"] as! [[String:Any]]).contains(where:{$0["request"] as? String == "swift-owned"}) { break }
 Thread.sleep(forTimeInterval:0.01)
}
let cancelled = try b.cancel("swift-owned", agent:"swift", task:"owned")
precondition(cancelled["cancelled"] as? String == "swift-owned")
precondition(done.wait(timeout:.now()+2) == .success);waiting.close()
let proposal = try b.proposeRepair(["command":"click","args":["target":"#old"]]) { observation, step in
 precondition(String(describing:observation).contains("Continue"));return ["target":"button"]
}
precondition(proposal["dispatched"] as? Bool == false)
print("PASS Swift real WebKit cancellation and repair")
""")
                    main.write_text(source)
                swift_binary = Path(directory) / 'live-swift'
                result = subprocess.run(['swiftc', '-swift-version', '5', str(ROOT / 'sdk/swift/Umbra.swift'), str(main), '-o', str(swift_binary)], capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                result = subprocess.run([str(swift_binary)], env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(client.call('eval', {'script': 'return window.sdkCount'})['value'], 3)
                self.assertEqual(client.call('eval', {'script': 'return window.repairClicks || 0'})['value'], 0)
                client.close()
                with sdk.Umbra(profile) as other:
                    self.assertEqual(other.call('status')['pid'], status['pid'])
            finally:
                client.close()
                subprocess.run([binary, 'stop'], env=env, capture_output=True, timeout=15)
                if process:
                    process.wait(timeout=15)
                server.shutdown(); server.server_close(); thread.join()
                digest = hashlib.sha256(('umbra profile ' + profile).encode()).hexdigest()[:32]
                store = '-'.join([digest[:8], digest[8:12], digest[12:16], digest[16:20], digest[20:]])
                shutil.rmtree(Path.home() / 'Library/WebKit/com.randyren.umbra/WebsiteDataStore' / store, ignore_errors=True)
                support = Path.home() / 'Library/Application Support/Umbra'
                for path in support.glob('*-' + profile + '.*'):
                    path.unlink(missing_ok=True)
                shutil.rmtree(Path.home() / 'Downloads/Umbra' / profile, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
