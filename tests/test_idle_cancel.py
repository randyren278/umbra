"""Idle waits release document timers on cancellation and request deadlines."""
import concurrent.futures
import json
import socket
import sys
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_umbra as base
from server import Server


class IdleCancelTests(unittest.TestCase):
    def setUp(self):
        self.profile = base.new_profile()
        self.server = Server()
        self.server.page('/', '''<!doctype html><title>Idle cancellation</title><p>Ready</p>
<script>
window.idleQueries=0;
const entries=performance.getEntriesByType.bind(performance);
performance.getEntriesByType=function(kind){window.idleQueries++;return entries(kind)};
let request=0;
window.poll=setInterval(()=>fetch('/tick?i='+request++).catch(()=>{}),30);
</script>''')
        self.server.route('/tick')(lambda h: h.reply(200, 'tick'))
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        result = base.umbra('open', self.server.url('/'), '--tab', 'idle', profile=self.profile)
        self.assertEqual(result.code, 0, result.err)
        self.path = str(base.LOCKS / f'daemon-{self.profile}.sock')

    def tearDown(self):
        base.umbra('stop', profile=self.profile)
        self.pool.shutdown(wait=True)
        self.server.close()
        base.stop_daemons([self.profile])

    def call(self, command, **args):
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(35)
            connection.connect(self.path)
            connection.sendall((json.dumps({'id': 1, 'command': command, 'args': args}) + '\n').encode())
            data = connection.makefile('rb').readline()
            self.assertTrue(data, 'daemon closed without reply')
            return json.loads(data)

    def ok(self, command, **args):
        result = self.call(command, **args)
        self.assertTrue(result.get('ok'), result)
        return result['result']

    def evaluate(self, script):
        return self.ok('eval', tab='idle', script=script)['value']

    def running(self, request, pending):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if pending.done():
                self.fail(f'{request} finished before cancellation: {pending.result()}')
            if any(p['request'] == request and p['state'] == 'running' for p in self.ok('tasks')['pending']):
                time.sleep(.08)
                return
            time.sleep(.01)
        self.fail(f'{request} never ran')

    def cancel_idle(self, request):
        pending = self.pool.submit(self.call, 'wait', tab='idle', idle=True, timeout=25, request=request)
        self.running(request, pending)
        self.assertTrue(self.ok('cancel', id=request)['wasRunning'])
        result = pending.result()
        self.assertFalse(result['ok'], result)
        self.assertIn('cancelled', result['error']['message'])

    def idle(self):
        self.evaluate('clearInterval(window.poll)')
        started = time.monotonic()
        self.ok('wait', tab='idle', idle=True, timeout=2)
        self.assertLess(time.monotonic() - started, 1.5)

    def test_cancelled_idle_stops_page_world_polling(self):
        self.cancel_idle('counter-proof')
        before = self.evaluate('return window.idleQueries')
        time.sleep(.45)
        after = self.evaluate('return window.idleQueries')
        self.assertEqual(after, before, f'idle resource queries continued after cancellation: {before} -> {after}')

    def test_cancelled_idle_waits_release_capacity_before_expiry(self):
        started = time.monotonic()
        for index in range(20):
            self.cancel_idle(f'cancel-{index}')
        self.assertLess(time.monotonic() - started, 20)
        self.idle()

    def test_deadlined_idle_waits_release_capacity_before_expiry(self):
        started = time.monotonic()
        for index in range(20):
            result = self.call('wait', tab='idle', idle=True, timeout=25,
                               deadline=time.time() + .12, request=f'deadline-{index}')
            self.assertFalse(result['ok'], result)
            self.assertIn('deadline', result['error']['message'])
        self.assertLess(time.monotonic() - started, 20)
        self.idle()

    def test_page_overrides_cannot_break_internal_idle(self):
        self.evaluate('''clearInterval(window.poll);
performance.getEntriesByType=()=>{throw Error('page performance override')};
window.setTimeout=()=>{throw Error('page timer override')};
window.Map=()=>{throw Error('page Map override')};
window.Promise=()=>{throw Error('page Promise override')};''')
        self.ok('wait', tab='idle', idle=True, timeout=2)
        self.assertEqual(self.evaluate('return window.idleQueries'), 0)

    def test_normal_idle_completions_release_capacity(self):
        self.evaluate('clearInterval(window.poll)')
        for _ in range(20):
            self.ok('wait', tab='idle', idle=True, timeout=2)
        self.assertFalse(self.ok('tasks')['pending'])


if __name__ == '__main__':
    unittest.main()
