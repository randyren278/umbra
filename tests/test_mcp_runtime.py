"""MCP cancellation, typed IDs and daemon ownership through real stdio clients."""
import json
import queue
import socket
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_umbra as base
from server import Server


class MCPRuntimeTests(base.UmbraCase):
    def setUp(self):
        super().setUp()
        self.web = Server()
        self.web.page('/', '<title>MCP runtime</title><input id=name>')
        self.inbox = queue.Queue()
        self.proc = base.spawn('mcp', profile=self.profile, stdin=subprocess.PIPE)
        threading.Thread(target=self.read, daemon=True).start()
        self.write({'jsonrpc': '2.0', 'id': 'init', 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18'}})
        self.assertEqual(self.receive()['id'], 'init')
        self.assertExit(self.umbra('open', self.web.url('/'), '--tab', 'a'), 0)

    def tearDown(self):
        self.proc.stdin.close()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill(); self.proc.wait()
        self.umbra('stop')
        self.web.close()

    def read(self):
        for line in self.proc.stdout:
            self.inbox.put(json.loads(line))

    def write(self, value):
        self.proc.stdin.write((json.dumps(value) + '\n').encode()); self.proc.stdin.flush()

    def receive(self):
        return self.inbox.get(timeout=15)

    def call(self, id, name, **args):
        self.write({'jsonrpc': '2.0', 'id': id, 'method': 'tools/call', 'params': {'name': name, 'arguments': args}})

    def cancel(self, id):
        self.write({'jsonrpc': '2.0', 'method': 'notifications/cancelled', 'params': {'requestId': id}})

    def tasks(self):
        result = self.umbra('tasks', '--json')
        self.assertExit(result, 0)
        return json.loads(result.text)['pending']

    def pending(self, request):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if any(task['request'] == request for task in self.tasks()):
                return
            time.sleep(.01)
        self.fail('request did not become pending: ' + request)

    def value(self, script):
        result = self.umbra('eval', script, '--tab', 'a', '--json')
        self.assertExit(result, 0)
        return json.loads(result.text)['value']

    def test_numeric_and_string_ids_are_distinct_and_matching_owner_cancels(self):
        self.call(1, 'wait', tab='a', js='false', request='numeric', agent='alice', task='x', timeout=10)
        self.pending('numeric')
        self.call('1', 'eval', tab='a', script='window.survivor=7; return 7', request='string')
        self.pending('string')
        self.cancel(1)
        response = self.receive()
        self.assertEqual(response['id'], '1')
        self.assertEqual(response['result']['structuredContent']['value'], 7)
        self.assertFalse(any(task['request'] == 'numeric' for task in self.tasks()))
        self.assertEqual(self.value('return window.survivor'), 7)
        self.assertTrue(self.inbox.empty(), 'cancelled request must not emit a late result')

    def test_duplicate_active_id_is_rejected_without_replacing_original(self):
        self.call('same', 'wait', tab='a', ms=500, request='original')
        self.pending('original')
        self.call('same', 'eval', tab='a', script='window.duplicate=1; return 1')
        duplicate = self.receive()
        self.assertEqual(duplicate['error']['code'], -32600)
        self.assertIn('already active', duplicate['error']['message'])
        self.assertEqual(self.receive()['id'], 'same')
        self.assertTrue(self.value('return typeof window.duplicate === "undefined"'))

    def test_cancel_queued_mutation_never_runs_and_id_is_reusable(self):
        self.call('blocker', 'wait', tab='a', ms=700, request='blocker')
        self.pending('blocker')
        self.call('mutate', 'eval', tab='a', script='window.cancelled=1; return 1', request='mutation', agent='alice', task='x')
        self.pending('mutation')
        self.cancel('mutate')
        self.assertEqual(self.receive()['id'], 'blocker')
        self.assertTrue(self.value('return typeof window.cancelled === "undefined"'))
        self.call('mutate', 'eval', tab='a', script='return 9')
        self.assertEqual(self.receive()['result']['structuredContent']['value'], 9)
        self.assertTrue(self.inbox.empty())

    def test_immediate_cancel_prevents_dispatch_even_in_burst(self):
        self.call('blocker', 'wait', tab='a', ms=1000, request='blocker')
        self.pending('blocker')
        for index in range(30):
            id = 'burst-' + str(index)
            self.call(id, 'eval', tab='a', script='window.cancelledBurst=(window.cancelledBurst||0)+1; return true', request=id)
            self.cancel(id)
        self.assertEqual(self.receive()['id'], 'blocker')
        self.assertTrue(self.value('return typeof window.cancelledBurst === "undefined"'))
        self.assertTrue(self.inbox.empty())


class MCPWorkerBudgetTests(unittest.TestCase):
    def test_cancelled_calls_keep_worker_capacity_until_transport_returns(self):
        profile = base.new_profile()
        path = base.LOCKS / f'daemon-{profile}.sock'
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(str(path)); listener.listen(512); listener.settimeout(.1)
        held = queue.Queue()
        release = threading.Event()
        stopped = threading.Event()
        inbox = queue.Queue()

        def accept():
            while not stopped.is_set():
                try:
                    conn, _ = listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                data = b''
                while not data.endswith(b'\n'):
                    chunk = conn.recv(8192)
                    if not chunk:
                        break
                    data += chunk
                if release.is_set():
                    conn.sendall(b'{"ok":true,"result":{"value":7}}\n')
                    conn.close()
                else:
                    held.put(conn)

        thread = threading.Thread(target=accept, daemon=True)
        thread.start()
        process = base.spawn('mcp', profile=profile, stdin=subprocess.PIPE)

        def write(value):
            process.stdin.write((json.dumps(value) + '\n').encode()); process.stdin.flush()

        def read():
            for line in process.stdout:
                inbox.put(json.loads(line))

        threading.Thread(target=read, daemon=True).start()
        connections = []
        try:
            for index in range(256):
                id = 'held-' + str(index)
                write({'jsonrpc': '2.0', 'id': id, 'method': 'tools/call',
                       'params': {'name': 'eval', 'arguments': {'script': 'return 7'}}})
                write({'jsonrpc': '2.0', 'method': 'notifications/cancelled', 'params': {'requestId': id}})
            for _ in range(256):
                connections.append(held.get(timeout=15))
            write({'jsonrpc': '2.0', 'id': 'over-budget', 'method': 'tools/call',
                   'params': {'name': 'eval', 'arguments': {'script': 'return 7'}}})
            response = inbox.get(timeout=5)
            self.assertEqual(response['id'], 'over-budget')
            self.assertIn('too many active MCP calls', response['error']['message'])
            release.set()
            for conn in connections:
                conn.sendall(b'{"ok":true,"result":{}}\n'); conn.close()
            connections.clear()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                write({'jsonrpc': '2.0', 'id': 'recovered', 'method': 'tools/call',
                       'params': {'name': 'eval', 'arguments': {'script': 'return 7'}}})
                response = inbox.get(timeout=5)
                if 'result' in response:
                    self.assertFalse(response['result'].get('isError', False), response)
                    self.assertEqual(response['result']['structuredContent']['value'], 7)
                    break
                time.sleep(.01)
            else:
                self.fail('worker capacity was not released after transport completed')
            self.assertTrue(inbox.empty(), 'cancelled calls returned a late result')
        finally:
            for conn in connections:
                conn.close()
            while not held.empty():
                held.get_nowait().close()
            stopped.set(); listener.close(); thread.join(2)
            process.stdin.close()
            try:
                process.wait(5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()
            path.unlink(missing_ok=True)


if __name__ == '__main__':
    unittest.main()
