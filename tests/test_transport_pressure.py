"""A temporarily full UNIX listener must not be replaced by an auto-spawned daemon."""
import errno
import json
import socket
import sys
import threading
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_umbra as base
from server import Server


class TransportPressureTests(unittest.TestCase):
    def test_authoritative_failure_can_arrive_during_delivery_grace(self):
        profile = base.new_profile()
        path = base.LOCKS / f'daemon-{profile}.sock'
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(str(path)); listener.listen(1); listener.settimeout(2)
        process = timer = None
        try:
            process = base.spawn('eval', 'return 7', '--json', '--timeout', '.12', profile=profile)
            conn, request = self.receive(listener)
            conn.sendall(b'{"ok":false,"error":')
            def finish():
                try:
                    conn.sendall(b'{"code":2,"message":"fixture execution deadline settled"}}\n')
                except OSError:
                    pass
                finally:
                    conn.close()
            timer = threading.Timer(max(0, request['args']['deadline'] - time.time() + .04), finish)
            timer.start()
            out, err = process.communicate(timeout=2)
            self.assertEqual(process.returncode, 2, (out, err))
            self.assertEqual(json.loads(err)['error']['message'], 'fixture execution deadline settled')
        finally:
            if timer:
                timer.join(1)
            listener.close()
            if process and process.poll() is None:
                process.kill(); process.wait()
            base.stop_daemons([profile]); path.unlink(missing_ok=True)

    def test_success_newline_after_execution_deadline_is_rejected(self):
        for partial in (False, True):
            with self.subTest(partial=partial):
                profile = base.new_profile()
                path = base.LOCKS / f'daemon-{profile}.sock'
                listener = socket.socket(socket.AF_UNIX)
                listener.bind(str(path)); listener.listen(1); listener.settimeout(2)
                process = timer = None
                try:
                    process = base.spawn('eval', 'return 7', '--json', '--timeout', '.12', profile=profile)
                    conn, request = self.receive(listener)
                    if partial:
                        conn.sendall(b'{"ok":true,"result":{"value":7}}')
                    def finish():
                        try:
                            conn.sendall(b'\n' if partial else b'{"ok":true,"result":{"value":7}}\n')
                        except OSError:
                            pass
                        finally:
                            conn.close()
                    timer = threading.Timer(max(0, request['args']['deadline'] - time.time() + .04), finish)
                    timer.start()
                    out, err = process.communicate(timeout=2)
                    self.assertEqual(process.returncode, 2, (out, err))
                    self.assertIn('deadline', json.loads(err)['error']['message'])
                finally:
                    if timer:
                        timer.join(1)
                    listener.close()
                    if process and process.poll() is None:
                        process.kill(); process.wait()
                    base.stop_daemons([profile]); path.unlink(missing_ok=True)

    def test_unfinished_reply_is_bounded_by_fixed_delivery_grace(self):
        profile = base.new_profile()
        path = base.LOCKS / f'daemon-{profile}.sock'
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(str(path)); listener.listen(1); listener.settimeout(2)
        process = conn = None
        try:
            process = base.spawn('eval', 'return 7', '--json', '--timeout', '.12', profile=profile)
            conn, request = self.receive(listener)
            conn.sendall(b'{"ok":false,"error":')
            out, err = process.communicate(timeout=2)
            returned = time.time() - request['args']['deadline']
            self.assertEqual(process.returncode, 2, (out, err))
            self.assertGreaterEqual(returned, .09)
            self.assertLess(returned, .2)
            self.assertIn('deadline', json.loads(err)['error']['message'])
        finally:
            if conn:
                conn.close()
            listener.close()
            if process and process.poll() is None:
                process.kill(); process.wait()
            base.stop_daemons([profile]); path.unlink(missing_ok=True)

    def delayed_reply(self, conn, delay):
        conn.sendall(b'{"ok":true,"result":')
        def finish():
            try:
                conn.sendall(b'{"value":7}}\n')
            except OSError:
                pass
            finally:
                conn.close()
        timer = threading.Timer(delay, finish)
        timer.start()
        return timer

    def test_connected_reply_wait_keeps_timeout_and_absolute_deadline(self):
        for option in ('--timeout', '--deadline'):
            with self.subTest(option=option):
                profile = base.new_profile()
                path = base.LOCKS / f'daemon-{profile}.sock'
                listener = socket.socket(socket.AF_UNIX)
                listener.bind(str(path)); listener.listen(1); listener.settimeout(2)
                process = timer = None
                try:
                    value = '.12' if option == '--timeout' else str(time.time() + .12)
                    started = time.monotonic()
                    process = base.spawn('eval', 'return 7', '--json', option, value, profile=profile)
                    conn, request = self.receive(listener)
                    self.assertEqual(request['command'], 'eval')
                    timer = self.delayed_reply(conn, .5)
                    out, err = process.communicate(timeout=2)
                    self.assertEqual(process.returncode, 2, (out, err))
                    self.assertIn('deadline', json.loads(err)['error']['message'])
                    self.assertLess(time.monotonic() - started, .35)
                finally:
                    if timer:
                        timer.join(1)
                    listener.close()
                    if process and process.poll() is None:
                        process.kill(); process.wait()
                    base.stop_daemons([profile]); path.unlink(missing_ok=True)

    def test_mcp_reply_budget_covers_readiness_and_actual_dispatch(self):
        for stage in ('runtime', 'eval'):
            with self.subTest(stage=stage):
                profile = base.new_profile()
                path = base.LOCKS / f'daemon-{profile}.sock'
                listener = socket.socket(socket.AF_UNIX)
                listener.bind(str(path)); listener.listen(1); listener.settimeout(2)
                process = timer = None
                try:
                    message = {'jsonrpc': '2.0', 'id': 'held-reply', 'method': 'tools/call',
                               'params': {'name': 'eval', 'arguments': {'script': 'return 7', 'timeout': .12}}}
                    started = time.monotonic()
                    process = base.spawn('mcp', profile=profile, input=json.dumps(message) + '\n')
                    process.stdin = None
                    conn, request = self.receive(listener)
                    self.assertEqual(request['command'], 'runtime')
                    if stage == 'eval':
                        conn.sendall(b'{"ok":true,"result":{}}\n'); conn.close()
                        conn, request = self.receive(listener)
                    self.assertEqual(request['command'], stage)
                    timer = self.delayed_reply(conn, .5)
                    out, err = process.communicate(timeout=2)
                    self.assertEqual(process.returncode, 0, (out, err))
                    value = json.loads(out)['result']
                    self.assertTrue(value.get('isError', False), value)
                    self.assertIn('deadline', value['content'][0]['text'])
                    self.assertLess(time.monotonic() - started, .35)
                finally:
                    if timer:
                        timer.join(1)
                    listener.close()
                    if process and process.poll() is None:
                        process.kill(); process.wait()
                    base.stop_daemons([profile]); path.unlink(missing_ok=True)

    def test_partial_replies_complete_with_budget_or_no_explicit_budget(self):
        for options, delay in [(('--timeout', '1'), .05), ((), .5)]:
            with self.subTest(options=options):
                profile = base.new_profile()
                path = base.LOCKS / f'daemon-{profile}.sock'
                listener = socket.socket(socket.AF_UNIX)
                listener.bind(str(path)); listener.listen(1); listener.settimeout(2)
                process = timer = None
                try:
                    process = base.spawn('eval', 'return 7', '--json', *options, profile=profile)
                    conn, _ = self.receive(listener)
                    timer = self.delayed_reply(conn, delay)
                    out, err = process.communicate(timeout=2)
                    self.assertEqual(process.returncode, 0, (out, err))
                    self.assertEqual(json.loads(out)['value'], 7)
                finally:
                    if timer:
                        timer.join(1)
                    listener.close()
                    if process and process.poll() is None:
                        process.kill(); process.wait()
                    base.stop_daemons([profile]); path.unlink(missing_ok=True)

    def receive(self, listener):
        while True:
            conn, _ = listener.accept()
            conn.settimeout(2)
            data = b''
            while not data.endswith(b'\n'):
                chunk = conn.recv(8192)
                if not chunk:
                    break
                data += chunk
            if data:
                return conn, json.loads(data)
            conn.close()

    def test_one_shot_probes_preserve_daemon_routing_and_budget(self):
        web = Server()
        web.page('/source', '<p>one-shot fallback</p>')
        try:
            for command in ('read', 'fetch'):
                with self.subTest(command=command):
                    profile = base.new_profile()
                    path = base.LOCKS / f'daemon-{profile}.sock'
                    listener = socket.socket(socket.AF_UNIX)
                    listener.bind(str(path)); listener.listen(1); listener.settimeout(2)
                    parked = socket.socket(socket.AF_UNIX)
                    parked.connect(str(path))
                    process = None
                    try:
                        process = base.spawn(command, web.url('/source'), '--timeout', '2', profile=profile,
                                             input="return 'one-shot fallback';" if command == 'read' else None)
                        if process.stdin:
                            process.stdin = None
                        time.sleep(.2)
                        first, _ = listener.accept(); first.close()
                        conn, request = self.receive(listener)
                        with conn:
                            self.assertEqual(request['command'], command)
                            self.assertLess(request['args']['deadline'] - time.time(), 1.9)
                            result = {'output': 'daemon route'} if command == 'read' else {'status': 200, 'body': 'daemon route'}
                            conn.sendall((json.dumps({'ok': True, 'result': result}) + '\n').encode())
                        out, err = process.communicate(timeout=3)
                        self.assertEqual(process.returncode, 0, (out, err))
                        self.assertEqual(out.strip(), b'daemon route')
                        self.assertEqual(web.log, [])
                        self.assertNotIn(profile, base.daemons().values())
                    finally:
                        parked.close(); listener.close()
                        if process and process.poll() is None:
                            process.kill(); process.wait()
                        base.stop_daemons([profile]); path.unlink(missing_ok=True)
        finally:
            web.close()

    def test_one_shot_probe_expiry_does_not_fall_back_after_budget(self):
        profile = base.new_profile()
        path = base.LOCKS / f'daemon-{profile}.sock'
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(str(path)); listener.listen(1)
        parked = socket.socket(socket.AF_UNIX)
        parked.connect(str(path))
        try:
            started = time.monotonic()
            result = base.umbra('fetch', 'http://127.0.0.1:1/unreached', '--timeout', '.08', profile=profile, timeout=2)
            self.assertEqual(result.code, 2, (result.text, result.err))
            self.assertLess(time.monotonic() - started, .7)
            self.assertNotIn(profile, base.daemons().values())
        finally:
            parked.close(); listener.close(); path.unlink(missing_ok=True)

    def test_mcp_readiness_and_dispatch_share_one_absolute_budget(self):
        profile = base.new_profile()
        path = base.LOCKS / f'daemon-{profile}.sock'
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(str(path)); listener.listen(1); listener.settimeout(2)
        parked = socket.socket(socket.AF_UNIX)
        parked.connect(str(path))
        process = None
        try:
            message = {'jsonrpc': '2.0', 'id': 'budget', 'method': 'tools/call',
                       'params': {'name': 'eval', 'arguments': {'script': 'return 7', 'timeout': 2}}}
            process = base.spawn('mcp', profile=profile, input=json.dumps(message) + '\n')
            process.stdin = None
            time.sleep(.2)
            first, _ = listener.accept(); first.close()
            conn, ready = self.receive(listener)
            with conn:
                self.assertEqual(ready['command'], 'runtime')
                self.assertLess(ready['args']['deadline'] - time.time(), 1.9)
                conn.sendall(b'{"ok":true,"result":{}}\n')
            conn, actual = self.receive(listener)
            with conn:
                self.assertEqual(actual['command'], 'eval')
                self.assertEqual(actual['args']['deadline'], ready['args']['deadline'])
                conn.sendall(b'{"ok":true,"result":{"value":7}}\n')
            out, err = process.communicate(timeout=3)
            self.assertEqual(process.returncode, 0, (out, err))
            self.assertEqual(json.loads(out)['result']['structuredContent']['value'], 7)
            self.assertNotIn(profile, base.daemons().values())
        finally:
            parked.close(); listener.close()
            if process and process.poll() is None:
                process.kill(); process.wait()
            base.stop_daemons([profile]); path.unlink(missing_ok=True)

    def test_invalid_mcp_timeout_keeps_usage_error(self):
        for timeout in (0, -1, 86401):
            with self.subTest(timeout=timeout):
                profile = base.new_profile()
                message = {'jsonrpc': '2.0', 'id': 'invalid', 'method': 'tools/call',
                           'params': {'name': 'eval', 'arguments': {'script': 'return 7', 'timeout': timeout, 'deadline': time.time() - 1}}}
                result = base.umbra('mcp', profile=profile, input=json.dumps(message) + '\n')
                self.assertEqual(result.code, 0, result.err)
                value = json.loads(result.text)['result']
                self.assertTrue(value['isError'])
                self.assertIn('timeout must be', value['content'][0]['text'])

    def test_retry_respects_supplied_timeout_and_absolute_deadline(self):
        for option in ('--timeout', '--deadline'):
            with self.subTest(option=option):
                profile = base.new_profile()
                path = base.LOCKS / f'daemon-{profile}.sock'
                listener = socket.socket(socket.AF_UNIX)
                listener.bind(str(path)); listener.listen(1)
                inode = path.stat().st_ino
                parked = socket.socket(socket.AF_UNIX)
                parked.connect(str(path))
                process = None
                try:
                    value = '.08' if option == '--timeout' else str(time.time() + .08)
                    started = time.monotonic()
                    process = base.spawn('eval', 'return 7', '--json', option, value, profile=profile)
                    out, err = process.communicate(timeout=2)
                    self.assertEqual(process.returncode, 2, (out, err))
                    self.assertIn('deadline', json.loads(err)['error']['message'])
                    self.assertLess(time.monotonic() - started, .7)
                    self.assertEqual(path.stat().st_ino, inode)
                    self.assertNotIn(profile, base.daemons().values())
                finally:
                    parked.close(); listener.close()
                    if process and process.poll() is None:
                        process.kill(); process.wait()
                    base.stop_daemons([profile])
                    path.unlink(missing_ok=True)

    def test_expired_deadline_does_not_start_an_absent_daemon(self):
        profile = base.new_profile()
        result = base.umbra('eval', 'return 7', '--json', '--deadline', str(time.time() - 1), profile=profile)
        self.assertEqual(result.code, 2, (result.text, result.err))
        self.assertIn('deadline', json.loads(result.err)['error']['message'])
        self.assertNotIn(profile, base.daemons().values())
        self.assertFalse((base.LOCKS / f'daemon-{profile}.sock').exists())

    def test_full_listener_backlog_is_retried_without_starting_a_daemon(self):
        profile = base.new_profile()
        path = base.LOCKS / f'daemon-{profile}.sock'
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(str(path)); listener.listen(1); listener.settimeout(2)
        inode = path.stat().st_ino
        parked = socket.socket(socket.AF_UNIX)
        parked.connect(str(path))
        process = None
        try:
            with socket.socket(socket.AF_UNIX) as probe:
                with self.assertRaises(OSError) as refused:
                    probe.connect(str(path))
                self.assertEqual(refused.exception.errno, errno.ECONNREFUSED)
            process = base.spawn('eval', 'return 7', '--json', profile=profile)
            time.sleep(.2)
            first, _ = listener.accept()
            first.close()
            try:
                conn, _ = listener.accept()
            except socket.timeout:
                conn = None
            if conn:
                with conn:
                    conn.settimeout(2)
                    data = b''
                    while not data.endswith(b'\n'):
                        chunk = conn.recv(8192)
                        if not chunk:
                            break
                        data += chunk
                    self.assertEqual(json.loads(data)['command'], 'eval')
                    conn.sendall(b'{"ok":true,"result":{"value":7}}\n')
            out, err = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, (out, err))
            self.assertEqual(json.loads(out)['value'], 7)
            self.assertEqual(path.stat().st_ino, inode)
            self.assertNotIn(profile, base.daemons().values())
        finally:
            parked.close(); listener.close()
            if process and process.poll() is None:
                process.kill(); process.wait()
            base.stop_daemons([profile])
            path.unlink(missing_ok=True)


if __name__ == '__main__':
    unittest.main()
