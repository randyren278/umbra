"""Conformance tests for `umbra mcp`, the stdio MCP server: the initialize handshake and version
negotiation, tools/list schemas, tools/call into the daemon against a 127.0.0.1 page, concurrent
calls, malformed input, shutdown at EOF and --print-config. Standard library only.

Every server runs under a throwaway profile named mcp-<random>, never the default store. When the
run ends, pass or fail, their daemon is stopped and their store and files are deleted.

    python3 tests/mcp_conformance.py [-v] [Class.test_name ...]
    UMBRA=/path/to/umbra python3 tests/mcp_conformance.py
"""
import atexit
import base64
import hashlib
import http.server
import itertools
import json
import os
import plistlib
import queue
import re
import secrets
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
UMBRA = os.environ.get('UMBRA') or str(REPO / 'build/Umbra.app/Contents/MacOS/umbra')
STORES = Path.home() / 'Library/WebKit/com.randyren.umbra/WebsiteDataStore'
SUPPORT = Path.home() / 'Library/Application Support/Umbra'
DOWNLOADS = Path.home() / 'Downloads/Umbra'
REAL_STORE = '6f1c7a52-3d0b-4e8f-9a61-f1a57c0de001'
PROFILE = 'mcp-' + secrets.token_hex(4)  # gets a daemon
COLD = PROFILE + '-cold'                 # never does: only requests the server answers itself
NO_SITES = Path('/nonexistent/umbra-sites.json')
VERSIONS = ['2025-06-18', '2025-03-26', '2024-11-05']
CORE = {'open', 'back', 'forward', 'reload', 'tabs', 'tab', 'eval', 'wait', 'status', 'handoff', 'cookies',
        'downloads', 'fetch'}
READ_ONLY = {'snapshot', 'text', 'markdown', 'screenshot', 'tabs', 'status', 'cookies', 'links', 'html', 'info',
             'console', 'network', 'downloads', 'get', 'observe', 'extract', 'watch', 'capabilities', 'frames', 'events', 'tasks', 'processes'}
TYPES = {'string', 'number', 'integer', 'boolean', 'array', 'object', 'null'}
PAGE = '<!doctype html><title>Umbra MCP</title><h1>Hello from MCP</h1>'

processes = []


def store_dir(name):
    """Where WebKit keeps UMBRA_PROFILE=name's store (Store.swift's uuid(named:), lowercased)."""
    h = hashlib.sha256(f'umbra profile {name}'.encode()).hexdigest()[:32]
    return STORES / '-'.join((h[:8], h[8:12], h[12:16], h[16:20], h[20:]))


def environment(profile):
    assert profile.startswith('mcp-') and store_dir(profile).name != REAL_STORE, 'never the default store'
    return dict(os.environ, UMBRA_PROFILE=profile, UMBRA_IDLE='60', UMBRA_SITES=str(NO_SITES))


def umbra(*args, profile=PROFILE, timeout=60):
    """Runs the umbra CLI to completion."""
    return subprocess.run([UMBRA, *args], env=environment(profile), capture_output=True, text=True,
                          timeout=timeout)


def socket_path(profile):
    return SUPPORT / f'daemon-{profile}.sock'


def daemon_gone(profile, pid, timeout=15):
    """Waits for a stopped daemon to remove its socket and exit."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            if not socket_path(profile).exists():
                return True
        time.sleep(0.05)
    return False


def daemons():
    """Pids of every `umbra serve` for this run's profiles, reachable or not: ps shows the environment
    of this user's processes, and a daemon carries its profile there."""
    listing = subprocess.run(['ps', 'axeww', '-o', 'pid=,command='], capture_output=True, text=True).stdout
    profiles = '|'.join(re.escape(name) for name in (PROFILE, COLD))
    pattern = re.compile(rf'/umbra serve\s.*\sUMBRA_PROFILE=({profiles})(\s|$)')
    return [int(line.split(None, 1)[0]) for line in listing.splitlines() if pattern.search(line)]


cleaned = False


def cleanup():
    """Stops every daemon the run started, then deletes its profiles' stores and files. Returns what
    is left (nothing, normally)."""
    global cleaned
    if cleaned:
        return []
    for proc in processes:  # first, so no server starts another daemon
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    # `stop` reaches the daemon serving the socket. Others started in the same burst poll the lock for
    # up to 2 s, and one takes over once the first stops: stop again until none is left, then make any
    # straggler go. Files go last: deleting a lock file under a live daemon lets another lock a fresh
    # copy and run beside it, out of reach of `stop`.
    deadline = time.monotonic() + 5
    while daemons() and time.monotonic() < deadline:
        for name in (PROFILE, COLD):
            try:
                umbra('stop', profile=name, timeout=15)
            except subprocess.TimeoutExpired:
                pass
        time.sleep(0.2)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in daemons():
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 6
        while daemons() and time.monotonic() < deadline:
            time.sleep(0.1)
    alive = daemons()
    left = []
    # A WebKit helper can outlive its daemon by a moment, so check the stores stay gone.
    for _ in range(5 if not alive else 0):
        for name in (PROFILE, COLD):
            shutil.rmtree(store_dir(name), ignore_errors=True)
            for path in SUPPORT.glob(f'*-{name}.*'):
                path.unlink(missing_ok=True)
            shutil.rmtree(DOWNLOADS / name, ignore_errors=True)
        time.sleep(0.3)
        left = [str(path) for name in (PROFILE, COLD)
                for path in (store_dir(name), *SUPPORT.glob(f'*-{name}.*'), DOWNLOADS / name) if path.exists()]
        if not left:
            break
    left = [f'daemon {pid}' for pid in alive] + left
    cleaned = not left
    print(f'cleanup: left behind {", ".join(left)}' if left else
          f'cleanup: every daemon exited; removed the stores and files of {PROFILE} and {COLD}', file=sys.stderr)
    return left


def tearDownModule():
    left = cleanup()
    assert not left, f'cleanup left {left}'
    assert not daemons(), 'a daemon for the suite\'s profiles is still alive'


def interrupted(signum, frame):
    cleanup()
    os._exit(128 + signum)


atexit.register(cleanup)  # if the run stops early
for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
    signal.signal(sig, interrupted)


class Page(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        body = PAGE.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


web = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Page)
web.daemon_threads = True
threading.Thread(target=web.serve_forever, daemon=True).start()
BASE = f'http://127.0.0.1:{web.server_address[1]}'


def closed_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


class Server:
    """One `umbra mcp` process. A thread queues every message it writes to stdout; any line that is
    not a JSON-RPC 2.0 message lands in `junk`, which must stay empty."""

    def __init__(self, profile=PROFILE, argv=None, cwd=None):
        self.proc = subprocess.Popen(argv or [UMBRA, 'mcp'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, env=environment(profile), cwd=cwd)
        processes.append(self.proc)
        self.inbox = queue.Queue()
        self.junk = []
        self.stderr = []
        self.ids = itertools.count(1)
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=lambda: self.stderr.extend(self.proc.stderr), daemon=True).start()

    def _read(self):
        for raw in self.proc.stdout:
            try:
                message = json.loads(raw)
                assert isinstance(message, dict) and message.get('jsonrpc') == '2.0'
            except (ValueError, AssertionError):
                self.junk.append(raw)
                continue
            self.inbox.put(message)
        self.inbox.put(None)  # stdout closed

    def write(self, message):
        self.proc.stdin.write(message if isinstance(message, bytes) else (json.dumps(message) + '\n').encode())
        self.proc.stdin.flush()

    def send(self, method, params=None):
        id = next(self.ids)
        self.write({'jsonrpc': '2.0', 'id': id, 'method': method, **({} if params is None else {'params': params})})
        return id

    def notify(self, method, params=None):
        self.write({'jsonrpc': '2.0', 'method': method, **({} if params is None else {'params': params})})

    def receive(self, timeout=30):
        return self.inbox.get(timeout=timeout)

    def request(self, method, params=None, timeout=30):
        """Sends a request and returns the next message, which must be its answer."""
        id = self.send(method, params)
        message = self.receive(timeout)
        assert message is not None and message.get('id') == id, f'expected the answer to {id}, got {message}'
        return message

    def initialize(self, version=VERSIONS[0]):
        result = self.request('initialize', {'protocolVersion': version, 'capabilities': {},
                                             'clientInfo': {'name': 'mcp_conformance', 'version': '1'}})['result']
        self.notify('notifications/initialized')
        return result

    def call(self, name, arguments=None, timeout=90):
        message = self.request('tools/call', {'name': name, 'arguments': arguments or {}}, timeout)
        assert 'result' in message, message
        return message['result']

    def close(self, timeout=10):
        self.proc.stdin.close()
        return self.proc.wait(timeout)


def text(result):
    assert result['content'][0]['type'] == 'text', result
    return result['content'][0]['text']


def json_blocks(output):
    """Every top-level JSON object in a text, in order."""
    decoder, blocks, index = json.JSONDecoder(), [], 0
    while (start := output.find('\n{', index)) >= 0:
        block, index = decoder.raw_decode(output, start + 1)
        blocks.append(block)
    return blocks


class Handshake(unittest.TestCase):
    def test_echoes_each_supported_version(self):
        for version in VERSIONS:
            with self.subTest(version=version):
                server = Server(COLD)
                self.assertEqual(server.initialize(version)['protocolVersion'], version)
                self.assertEqual(server.close(), 0)

    def test_answers_any_other_version_with_the_latest(self):
        for version in ('1999-01-01', '2026-01-01', 7):
            with self.subTest(version=version):
                server = Server(COLD)
                self.assertEqual(server.initialize(version)['protocolVersion'], VERSIONS[0])
                self.assertEqual(server.close(), 0)

    def test_server_info_capabilities_and_instructions(self):
        server = Server(COLD)
        result = server.initialize()
        with open(Path(UMBRA).resolve().parent.parent / 'Info.plist', 'rb') as file:
            version = plistlib.load(file)['CFBundleShortVersionString']
        self.assertEqual(result['serverInfo'], {'name': 'umbra', 'title': 'Umbra', 'version': version})
        self.assertEqual(result['capabilities'], {'tools': {'listChanged': False}})
        self.assertIsInstance(result['instructions'], str)  # content: Tools.test_instructions_teach_the_loop…
        self.assertEqual(server.request('ping')['result'], {})
        self.assertEqual(server.close(), 0)
        self.assertEqual(server.junk, [])

    def test_initialize_is_fast_and_starts_no_daemon(self):
        times = []
        for _ in range(7):
            start = time.monotonic()
            server = Server(COLD)
            server.send('initialize', {'protocolVersion': VERSIONS[0], 'capabilities': {},
                                       'clientInfo': {'name': 'mcp_conformance', 'version': '1'}})
            self.assertIn('result', server.receive())
            times.append(time.monotonic() - start)
            self.assertTrue(server.request('tools/list')['result']['tools'])
            self.assertEqual(server.close(), 0)
        print(f'\ninitialize, from spawn to answer: best {min(times) * 1000:.1f} ms, '
              f'median {statistics.median(times) * 1000:.1f} ms of {len(times)}', file=sys.stderr)
        self.assertLess(min(times), 0.05)
        self.assertFalse(socket_path(COLD).exists(), 'initialize and tools/list must not start the daemon')


class Tools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server = Server(COLD)
        cls.instructions = server.initialize()['instructions']
        cls.tools = server.request('tools/list')['result']['tools']
        server.close()
        cls.commands = json.loads(umbra('schema', profile=COLD).stdout)['commands']

    def test_tools_are_the_registry_commands_offered_to_mcp(self):
        names = [tool['name'] for tool in self.tools]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(set(names), {command['name'] for command in self.commands if command['mcp']})
        self.assertLessEqual(CORE, set(names))
        self.assertNotIn('stop', names)
        helps = {command['name']: command['description'] for command in self.commands}
        for tool in self.tools:
            self.assertEqual(tool['description'], helps[tool['name']])

    def test_every_tool_has_a_valid_input_schema(self):
        for tool in self.tools:
            with self.subTest(tool=tool['name']):
                self.assertRegex(tool['name'], r'^[A-Za-z0-9_-]{1,64}$')
                self.assertTrue(tool['description'].strip())
                schema = tool['inputSchema']
                self.assertLessEqual(set(schema), {'type', 'properties', 'required', 'additionalProperties'})
                self.assertEqual(schema['type'], 'object')
                self.assertIs(schema['additionalProperties'], False)
                self.assertIsInstance(schema['properties'], dict)
                self.assertNotIn('cwd', schema['properties'], 'cwd is a convention, not a parameter')
                for name, prop in schema['properties'].items():
                    self.assertIn(prop['type'], TYPES, name)
                    self.assertTrue(prop['description'].strip(), name)
                    if prop['type'] == 'array':
                        self.assertIn(prop['items']['type'], TYPES, name)
                if 'required' in schema:
                    required = schema['required']
                    self.assertTrue(required, 'required, when present, is a non-empty list')
                    self.assertEqual(len(required), len(set(required)))
                    self.assertLessEqual(set(required), set(schema['properties']))

    def test_annotations(self):
        for tool in self.tools:
            with self.subTest(tool=tool['name']):
                writes = bool({'path', 'file'} & set(tool['inputSchema']['properties']))  # may replace a file
                self.assertEqual(tool['annotations'], {'readOnlyHint': tool['name'] in READ_ONLY and not writes,
                                                       'destructiveHint': writes, 'openWorldHint': True})
        hints = {tool['name']: tool['annotations'] for tool in self.tools}
        for name in {'download', 'screenshot', 'pdf'} & set(hints):
            self.assertIs(hints[name]['destructiveHint'], True, name)
        for name in {'snapshot', 'text', 'tabs', 'upload'} & set(hints):  # upload reads its files
            self.assertIs(hints[name]['destructiveHint'], False, name)

    def test_instructions_teach_the_loop_with_real_tools(self):
        words = self.instructions
        self.assertLess(len(words), 1200, 'concise')
        self.assertRegex(words, r'persist between calls')
        # open, then snapshot for refs, then act on the refs; snapshot again after changes
        order = [words.index(part) for part in ('`open`', '`snapshot`', 'refs', '`click`', 'snapshot again')]
        self.assertEqual(order, sorted(order))
        for part in ('`wait`', 'instead of sleeping', '`handoff`', '2FA', 'CAPTCHA', '`screenshot`', '`fetch`'):
            self.assertIn(part, words)
        # Every tool the instructions name is one this server offers.
        named = set(re.findall(r'`([a-z-]+)`', words)) - {'umbra'}
        self.assertLessEqual(named, {tool['name'] for tool in self.tools})


class Calls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = Server(PROFILE)
        cls.server.initialize()
        cls.tools = {tool['name'] for tool in cls.server.request('tools/list')['result']['tools']}

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def tearDown(self):
        self.assertEqual(self.server.junk, [])

    def open_page(self):
        result = self.server.call('open', {'url': BASE + '/page'})
        self.assertFalse(result.get('isError'), result)
        return result

    def test_open_then_eval_share_the_tab_with_the_cli(self):
        result = self.open_page()
        self.assertEqual(len(result['content']), 1)
        self.assertIn('Umbra MCP', text(result))
        self.assertIn(BASE + '/page', text(result))
        fields = result['structuredContent']
        self.assertEqual((fields['title'], fields['url'], fields['status']), ('Umbra MCP', BASE + '/page', 200))
        self.assertNotIn('text', fields)
        result = self.server.call('eval', {'script': 'return document.querySelector("h1").textContent'})
        self.assertEqual(result, {'content': [{'type': 'text', 'text': 'Hello from MCP'}]})  # value == text
        result = self.server.call('eval', {'script': 'return {h1: document.querySelector("h1").textContent}'})
        self.assertEqual(result, {'content': [{'type': 'text', 'text': '{"h1":"Hello from MCP"}'}],
                                  'structuredContent': {'value': {'h1': 'Hello from MCP'}}})
        # A shell sees the page the MCP client opened, and the client sees what the shell changed.
        self.assertEqual(umbra('eval', 'return location.href').stdout, BASE + '/page\n')
        self.assertEqual(umbra('eval', 'document.title = "set from the CLI"').returncode, 0)
        self.assertEqual(text(self.server.call('eval', {'script': 'return document.title'})), 'set from the CLI')

    def test_a_result_without_text_is_shown_as_json(self):
        self.open_page()
        result = self.server.call('eval', {'script': 'return null'})
        self.assertEqual(result, {'content': [{'type': 'text', 'text': '{"value":null}'}],
                                  'structuredContent': {'value': None}})

    def test_structured_content_does_not_repeat_the_text(self):
        result = self.server.call('fetch', {'url': BASE + '/api'})  # the reply's body is also its text
        self.assertEqual(result, {'content': [{'type': 'text', 'text': PAGE}], 'structuredContent': {'status': 200}})

    def test_relative_paths_resolve_in_the_clients_directory(self):
        self.server.call('status')  # the daemon runs elsewhere, so its own directory can't stand in
        with tempfile.TemporaryDirectory() as here, tempfile.TemporaryDirectory() as there:
            server = Server(PROFILE, cwd=here)  # MCP clients start the server in the project directory
            server.initialize()
            result = server.call('download', {'url': BASE + '/file', 'file': 'got.html'})
            self.assertFalse(result.get('isError'), result)
            self.assertEqual((Path(here) / 'got.html').read_text(), PAGE)
            result = server.call('download', {'url': BASE + '/file', 'file': 'got.html', 'cwd': there})
            self.assertFalse(result.get('isError'), result)  # a client's own cwd wins
            self.assertEqual((Path(there) / 'got.html').read_text(), PAGE)
            self.assertEqual(server.close(), 0)
            self.assertEqual(server.junk, [])

    def test_an_unreachable_page_is_a_tool_error(self):
        result = self.server.call('open', {'url': f'http://127.0.0.1:{closed_port()}/'})
        self.assertIs(result['isError'], True)
        self.assertIn('Check the URL', text(result))
        self.assertNotIn('structuredContent', result)

    def test_a_script_error_is_a_tool_error(self):
        self.open_page()
        result = self.server.call('eval', {'script': 'throw new Error("boom")'})
        self.assertIs(result['isError'], True)
        self.assertIn('boom', text(result))

    def test_signed_out_suggests_sign_in(self):
        result = self.server.call('read', {'url': BASE + '/page', 'cookie': 'session'})
        self.assertIs(result['isError'], True)
        self.assertIn('signed out of 127.0.0.1', text(result))
        self.assertIn('Call `sign-in`', text(result))

    def test_argument_mistakes_are_tool_errors(self):
        result = self.server.call('open', {})
        self.assertIs(result['isError'], True)
        self.assertIn('open needs url', text(result))
        result = self.server.call('eval', {'script': 'return 1', 'selector': 'h1'})
        self.assertIs(result['isError'], True)
        self.assertIn('eval has no parameter selector; it takes agent, deadline, request, script, tab, task, timeout', text(result))
        # The server sends its own sites file with each call; a model can't name one.
        result = self.server.call('status', {'sitesFile': '/tmp/elsewhere.json'})
        self.assertIs(result['isError'], True)
        self.assertIn('status has no parameter sitesFile', text(result))
        self.assertFalse(self.server.call('status', {}).get('isError'))

    def test_null_arguments_count_as_not_given(self):
        self.open_page()
        result = self.server.call('eval', {'script': 'return 1 + 1', 'tab': None, 'timeout': None})
        self.assertEqual(text(result), '2')

    def test_unknown_tools_and_bad_params_are_invalid_params(self):
        for params in ({'name': 'teleport', 'arguments': {}}, {'name': 'stop', 'arguments': {}}, {'arguments': {}},
                       {'name': 'eval', 'arguments': 'return 1'}, {'name': 'eval', 'arguments': ['return 1']}):
            with self.subTest(params=params):
                self.assertEqual(self.server.request('tools/call', params)['error']['code'], -32602)

    def test_calls_run_concurrently(self):
        for index in range(5):
            self.server.call('open', {'url': BASE + '/page', 'tab': f'parallel-{index}'})
        delays = [1000, 800, 600, 400, 200]  # ms; the last call sent should answer first
        start = time.monotonic()
        calls = {self.server.send('tools/call', {'name': 'eval', 'arguments': {
            'tab': f'parallel-{index}', 'script': f'await new Promise(r => setTimeout(r, {ms})); return {index}'}}): index
            for index, ms in enumerate(delays)}
        ping = self.server.send('ping')
        answers = [self.server.receive() for _ in range(len(calls) + 1)]
        elapsed = time.monotonic() - start
        self.assertEqual(sorted(answer['id'] for answer in answers), sorted([*calls, ping]))
        self.assertEqual(answers[0], {'jsonrpc': '2.0', 'id': ping, 'result': {}}, 'ping waits for no call')
        for answer in answers[1:]:
            self.assertEqual(text(answer['result']), str(calls[answer['id']]))
        self.assertEqual([calls[answer['id']] for answer in answers[1:]], [4, 3, 2, 1, 0])
        self.assertLess(elapsed, 2, 'the calls overlapped instead of running one after another')

    def test_long_calls_send_progress_when_the_client_asks(self):
        for name in ('long', 'short', 'silent'):
            self.server.call('open', {'url': BASE + '/page', 'tab': 'progress-' + name})

        def slow(ms, value, token=None):
            params = {'name': 'eval', 'arguments': {'tab': 'progress-' + value, 'script': f'await new Promise(r => setTimeout(r, {ms})); return "{value}"'}}
            if token is not None:
                params['_meta'] = {'progressToken': token}
            return self.server.send('tools/call', params)

        long, short, silent = slow(11000, 'long', 'beat'), slow(6500, 'short', 7), slow(6500, 'silent')
        messages = []
        while not any(message.get('id') == long for message in messages):
            messages.append(self.server.receive())
        answered = {message['id']: index for index, message in enumerate(messages) if 'id' in message}
        self.assertEqual(set(answered), {long, short, silent})
        for id, value in ((long, 'long'), (short, 'short'), (silent, 'silent')):
            self.assertEqual(text(messages[answered[id]]['result']), value)

        def beats(token):
            return [index for index, message in enumerate(messages) if message.get('method') == 'notifications/progress'
                    and message['params']['progressToken'] == token]

        progress = [message for message in messages if message.get('method') == 'notifications/progress']
        self.assertEqual({message['params']['progressToken'] for message in progress}, {'beat', 7}, 'only when asked')
        values = [messages[index]['params']['progress'] for index in beats('beat')]
        self.assertGreaterEqual(len(values), 2)  # every 5 s: at 5 and 10 s of 11
        self.assertEqual(values, sorted(set(values)), 'progress only increases')
        self.assertTrue(beats(7))
        self.assertLess(max(beats(7)), answered[short], 'nothing after the answer')
        self.assertTrue(all(message['params']['message'].startswith('eval') for message in progress))

    def test_survives_a_daemon_restart(self):
        first = self.server.call('status')['structuredContent']
        self.assertEqual(first['profile'], PROFILE)
        self.assertEqual(umbra('stop').returncode, 0)
        self.assertTrue(daemon_gone(PROFILE, first['pid']))
        # Calls that arrive together while no daemon runs start exactly one.
        ids = [self.server.send('tools/call', {'name': 'status', 'arguments': {}}) for _ in range(4)]
        answers = [self.server.receive() for _ in ids]
        self.assertEqual(sorted(answer['id'] for answer in answers), ids)
        pids = {answer['result']['structuredContent']['pid'] for answer in answers}
        self.assertEqual(len(pids), 1, answers)
        self.assertNotIn(first['pid'], pids)
        self.open_page()

    def test_a_screenshot_is_an_image(self):
        if 'screenshot' not in self.tools:
            self.skipTest('no screenshot tool in this build yet')
        self.open_page()
        result = self.server.call('screenshot')
        self.assertFalse(result.get('isError'), result)
        images = [item for item in result['content'] if item['type'] == 'image']
        self.assertEqual(len(images), 1, result['content'])
        data = base64.b64decode(images[0]['data'], validate=True)
        magic = {'image/png': b'\x89PNG', 'image/jpeg': b'\xff\xd8\xff'}
        self.assertTrue(data.startswith(magic[images[0]['mimeType']]))
        self.assertNotIn('data', result.get('structuredContent', {}))


class Malformed(unittest.TestCase):
    def setUp(self):
        self.server = Server(COLD)
        self.server.initialize()

    def tearDown(self):
        self.assertEqual(self.server.request('ping')['result'], {}, 'the server survives')
        self.assertEqual(self.server.close(), 0)
        self.assertEqual(self.server.junk, [])

    def test_unparseable_json_is_a_parse_error(self):
        for raw in (b'{"jsonrpc": "2.0", "id": 1, "method": "ping"', b'not json', b'{"a": }', b'{"a": 1} x'):
            with self.subTest(raw=raw):
                self.server.write(raw + b'\n')
                self.assertEqual(self.server.receive(), {'jsonrpc': '2.0', 'id': None,
                                                         'error': {'code': -32700, 'message': 'Parse error'}})

    def test_invalid_requests(self):
        for raw, id in ((b'[]', None), (b'"ping"', None), (b'{"jsonrpc": "2.0", "id": 5}', 5),
                        (b'{"jsonrpc": "1.0", "id": 6, "method": "ping"}', 6), (b'{"id": 7, "method": "ping"}', 7),
                        (b'{"jsonrpc": "2.0", "id": 8, "method": 3}', 8),
                        (b'{"jsonrpc": "2.0", "id": true, "method": "ping"}', None),
                        (b'{"jsonrpc": "2.0", "id": null, "method": "ping"}', None),
                        (b'{"jsonrpc": "2.0", "id": {}, "method": "ping"}', None)):
            with self.subTest(raw=raw):
                self.server.write(raw + b'\n')
                answer = self.server.receive()
                self.assertEqual(answer['error']['code'], -32600)
                self.assertEqual(answer['id'], id)

    def test_unknown_method(self):
        self.server.write({'jsonrpc': '2.0', 'id': 'abc', 'method': 'resources/list'})
        answer = self.server.receive()
        self.assertEqual((answer['id'], answer['error']['code']), ('abc', -32601))

    def test_notifications_and_responses_get_no_answer(self):
        self.server.notify('notifications/cancelled', {'requestId': 99})
        self.server.notify('notifications/progress', {'progressToken': 1, 'progress': 1})
        self.server.notify('no/such/notification')
        self.server.write({'jsonrpc': '2.0', 'id': 42, 'result': {}})
        self.server.write({'jsonrpc': '2.0', 'id': 43, 'error': {'code': -1, 'message': 'no'}})
        self.server.write(b'\n   \n')
        self.assertEqual(self.server.request('ping')['result'], {})  # the next message is ping's answer

    def test_requests_work_before_initialize(self):
        server = Server(COLD)
        self.assertEqual(server.request('ping')['result'], {})
        self.assertTrue(server.request('tools/list')['result']['tools'])
        self.assertEqual(server.close(), 0)


class Shutdown(unittest.TestCase):
    def test_eof_exits_cleanly(self):
        server = Server(COLD)
        server.initialize()
        start = time.monotonic()
        self.assertEqual(server.close(), 0)
        self.assertLess(time.monotonic() - start, 1)
        self.assertIsNone(server.receive(5))
        self.assertEqual(server.junk, [])

    def test_eof_answers_the_calls_in_flight_first(self):
        server = Server(PROFILE)
        server.initialize()
        server.call('open', {'url': BASE + '/page'})
        id = server.send('tools/call', {'name': 'eval', 'arguments': {
            'script': 'await new Promise(r => setTimeout(r, 300)); return "late"'}})
        server.proc.stdin.close()
        answer = server.receive()
        self.assertEqual((answer['id'], text(answer['result'])), (id, 'late'))
        self.assertEqual(server.proc.wait(10), 0)

    def test_a_cancelled_call_gets_no_answer(self):
        server = Server(PROFILE)
        server.initialize()
        server.call('open', {'url': BASE + '/page'})
        id = server.send('tools/call', {'name': 'eval', 'arguments': {
            'script': 'await new Promise(r => setTimeout(r, 300)); return "late"'}})
        server.notify('notifications/cancelled', {'requestId': id, 'reason': 'test'})
        server.proc.stdin.close()
        self.assertEqual(server.proc.wait(10), 0)
        self.assertIsNone(server.receive(5), 'nothing after the cancel')


class PrintConfig(unittest.TestCase):
    def test_snippets_name_the_resolved_executable(self):
        result = umbra('mcp', '--print-config')
        self.assertEqual(result.returncode, 0, result.stderr)
        blocks = json_blocks(result.stdout)
        self.assertEqual(len(blocks), 2)  # Claude Desktop, Cursor
        server = blocks[0]['mcpServers']['umbra']
        self.assertTrue(os.path.isabs(server['command']))
        self.assertEqual(os.path.realpath(server['command']), os.path.realpath(UMBRA))
        self.assertEqual(server['args'], ['mcp'])
        self.assertEqual(server['env'], {'UMBRA_PROFILE': PROFILE})
        self.assertEqual(blocks[1], blocks[0])
        self.assertIn(f"claude mcp add umbra -e UMBRA_PROFILE={PROFILE} -- {server['command']} mcp\n", result.stdout)
        self.assertIn(f"codex mcp add umbra --env UMBRA_PROFILE={PROFILE} -- {server['command']} mcp\n", result.stdout)
        # The snippet starts a working server on its profile.
        started = Server(server['env']['UMBRA_PROFILE'], argv=[server['command'], *server['args']])
        self.assertEqual(started.initialize()['serverInfo']['name'], 'umbra')
        self.assertEqual(started.call('status')['structuredContent']['profile'], PROFILE)
        self.assertEqual(started.close(), 0)

    def test_other_options_are_a_usage_error(self):
        result = umbra('mcp', '--bogus', profile=COLD)
        self.assertEqual(result.returncode, 64)
        self.assertIn('usage: umbra mcp [--print-config]', result.stderr)


if __name__ == '__main__':
    sys.dont_write_bytecode = True
    unittest.main()
