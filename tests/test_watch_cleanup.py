"""Cancelled DOM watches release their document budget through the public protocol."""
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


class WatchCleanupTests(unittest.TestCase):
    def setUp(self):
        self.profile = base.new_profile()
        self.server = Server()
        self.server.page('/', '<title>Watch cleanup</title><p id=ready>Ready</p>')
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        result = base.umbra('open', self.server.url('/'), '--tab', 'watch', profile=self.profile)
        self.assertEqual(result.code, 0, result.err)
        self.path = str(base.LOCKS / f'daemon-{self.profile}.sock')
        self.assertTrue(self.ok('watch', tab='watch', target='#ready', state='attached', duration=0)['matched'])

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

    def running(self, request):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if any(p['request'] == request and p['state'] == 'running' for p in self.ok('tasks')['pending']):
                # Give the event-driven watch time to install before cancelling its native call.
                time.sleep(.08)
                return
            time.sleep(.01)
        self.fail(f'{request} never ran')

    def test_cancelled_watches_release_capacity_before_expiry(self):
        started = time.monotonic()
        for index in range(16):
            request = f'watch-{index}'
            pending = self.pool.submit(self.call, 'watch', tab='watch', target='#missing',
                                       state='attached', duration=25, request=request)
            self.running(request)
            self.assertTrue(self.ok('cancel', id=request)['wasRunning'])
            cancelled = pending.result()
            self.assertFalse(cancelled['ok'], cancelled)
            self.assertIn('cancelled', cancelled['error']['message'])
        self.assertLess(time.monotonic() - started, 20, 'earlier watches must still be within their 25-second expiry')
        result = self.ok('watch', tab='watch', target='#ready', state='visible', duration=0)
        self.assertTrue(result['matched'])
        self.assertFalse(self.ok('tasks')['pending'])


    def test_cross_origin_frame_cancelled_watches_release_capacity(self):
        frame_server = Server()
        self.addCleanup(frame_server.close)
        frame_server.page('/frame', '<title>Frame watches</title><p id=ready>Frame ready</p>')
        self.server.page('/frames', f'<title>Frames</title><iframe src="{frame_server.url("/frame")}"></iframe>')
        self.ok('open', tab='watch', url=self.server.url('/frames'))
        deadline = time.monotonic() + 5
        frame = None
        while time.monotonic() < deadline:
            frame = next((f['id'] for f in self.ok('frames', tab='watch')['frames']
                          if f['url'] == frame_server.url('/frame')), None)
            if frame:
                break
            time.sleep(.01)
        self.assertIsNotNone(frame)
        matched = json.dumps({'target': '#ready', 'state': 'visible', 'duration': 0})
        self.assertTrue(self.ok('frame', tab='watch', id=frame, operation='watch', args=matched)['matched'])
        started = time.monotonic()
        for index in range(16):
            request = f'frame-watch-{index}'
            pending = self.pool.submit(self.call, 'frame', tab='watch', id=frame, operation='watch',
                                       args=json.dumps({'target': '#missing', 'state': 'attached', 'duration': 25}),
                                       request=request)
            self.running(request)
            self.assertTrue(self.ok('cancel', id=request)['wasRunning'])
            self.assertIn('cancelled', pending.result()['error']['message'])
        self.assertLess(time.monotonic() - started, 20)
        self.assertTrue(self.ok('frame', tab='watch', id=frame, operation='watch', args=matched)['matched'])

    def test_request_deadlines_release_watch_capacity(self):
        started = time.monotonic()
        for index in range(16):
            result = self.call('watch', tab='watch', target='#missing', state='attached',
                               duration=25, request=f'deadline-{index}', timeout=.12)
            self.assertFalse(result['ok'], result)
            self.assertIn('deadline', result['error']['message'])
        self.assertLess(time.monotonic() - started, 20)
        self.assertTrue(self.ok('watch', tab='watch', target='#ready', state='attached', duration=0)['matched'])

    def test_normal_matches_and_expiry_release_watch_capacity(self):
        for _ in range(20):
            self.assertTrue(self.ok('watch', tab='watch', target='#ready', state='attached', duration=0)['matched'])
            self.assertFalse(self.ok('watch', tab='watch', target='#missing', state='attached', duration=.001)['matched'])
        self.assertTrue(self.ok('watch', tab='watch', target='#ready', state='visible', duration=0)['matched'])


if __name__ == '__main__':
    unittest.main()
