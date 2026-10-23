"""Native capture cancellation and all-tab blocking through disposable localhost sessions."""
import base64
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


class CaptureCancellationTests(unittest.TestCase):
    def setUp(self):
        self.profile = base.new_profile()
        self.server = Server()
        self.server.page('/', '<title>Capture cancellation</title><div style="height:14000px;background:linear-gradient(red,blue)">capture</div>')
        self.server.page('/tall', '<title>Capture fallback</title><div style="height:24000px;background:linear-gradient(red,blue)">capture</div>')
        self.server.page('/small', '<title>Capture success</title><p>ready</p>')
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=3)
        result = base.umbra('open', self.server.url('/'), '--tab', 'capture', profile=self.profile)
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

    def running(self, request):
        until = time.monotonic() + 5
        while time.monotonic() < until:
            if any(job['request'] == request and job['state'] == 'running'
                   for job in self.ok('tasks')['pending']):
                return
            time.sleep(.005)
        self.fail(f'{request} never ran')

    def cancel_capture(self, command='screenshot', **args):
        file = base.TMP / f'{self.profile}-{command}.out'
        for attempt in range(4):
            file.write_bytes(b'SENTINEL')
            request = f'capture-{attempt}'
            future = self.pool.submit(self.call, command, tab='capture', path=str(file), request=request, **args)
            self.running(request)
            time.sleep(.1 if command == 'screenshot' else .02)
            cancelled = self.call('cancel', id=request)
            if not cancelled.get('ok'):
                self.assertTrue(future.result().get('ok'), cancelled)
                continue
            self.assertTrue(cancelled['result']['wasRunning'], cancelled)
            response = future.result()
            self.assertFalse(response['ok'], response)
            self.assertEqual(response['error']['code'], 2)
            self.assertTrue(file.read_bytes() == b'SENTINEL', 'capture wrote before cancellation acknowledgement')
            return file, request
        self.fail('native capture completed before every attempted running cancellation')

    def unchanged_after_callbacks(self, file):
        for _ in range(2):
            time.sleep(1)
            self.assertTrue(file.read_bytes() == b'SENTINEL', 'cancelled native capture wrote after acknowledgement')

    def test_cancelled_native_screenshot_keeps_existing_destination(self):
        file, request = self.cancel_capture(full=True)
        # Reusing the public request ID must not reactivate the earlier native callback.
        replacement = self.pool.submit(self.call, 'wait', tab='capture', ms=1800, request=request)
        self.unchanged_after_callbacks(file)
        self.assertTrue(replacement.result()['ok'])

    def test_cancelled_fallback_cannot_restore_over_replacement_viewport(self):
        self.ok('open', tab='capture', url=self.server.url('/tall'))
        file, _ = self.cancel_capture(full=True)
        restored = self.ok('viewport', tab='capture')
        self.assertEqual((restored['width'], restored['height']), (1280, 900))
        self.ok('viewport', tab='capture', width=600, height=400)
        self.unchanged_after_callbacks(file)
        final = self.ok('viewport', tab='capture')
        self.assertEqual((final['width'], final['height']), (600, 400))

    def test_screenshot_deadline_cannot_write_late(self):
        file = base.TMP / f'{self.profile}-deadline.png'
        file.write_bytes(b'SENTINEL')
        result = self.call('screenshot', tab='capture', path=str(file), full=True, timeout=.1)
        self.assertFalse(result['ok'], result)
        self.assertIn('deadline', result['error']['message'])
        self.unchanged_after_callbacks(file)

    def test_pdf_deadline_cannot_write_late(self):
        file = base.TMP / f'{self.profile}-deadline.pdf'
        file.write_bytes(b'SENTINEL')
        result = self.call('pdf', tab='capture', path=str(file), timeout=.001)
        self.assertFalse(result['ok'], result)
        self.assertIn('deadline', result['error']['message'])
        self.unchanged_after_callbacks(file)

    def test_block_compilation_deadline_cannot_apply_late(self):
        result = self.call('block', tab='capture', types=['images', 'fonts', 'media', 'stylesheets', 'scripts', 'trackers'],
                           all=True, timeout=.001)
        self.assertFalse(result['ok'], result)
        self.assertIn('deadline', result['error']['message'])
        time.sleep(.5)
        self.assertEqual(self.ok('block', tab='capture')['blocked'], [])
        self.ok('open', tab='future', url=self.server.url('/small'))
        self.assertEqual(self.ok('block', tab='future')['blocked'], [])

    def test_normal_native_capture_and_block_success(self):
        self.ok('open', tab='capture', url=self.server.url('/small'))
        for command, magic in [('screenshot', b'\x89PNG'), ('pdf', b'%PDF')]:
            file = base.TMP / f'{self.profile}-{command}'
            result = self.ok(command, tab='capture', path=str(file))
            self.assertTrue(file.read_bytes().startswith(magic))
            self.assertEqual(result['bytes'], file.stat().st_size)
        self.ok('open', tab='other', url=self.server.url('/small'))
        self.ok('lease', tab='other', agent='alice', task='capture')
        denied = self.call('block', tab='capture', types=['images'], all=True)
        self.assertFalse(denied['ok'], denied)
        self.assertEqual(self.ok('block', tab='capture')['blocked'], [])
        self.assertEqual(self.ok('block', tab='other', agent='alice', task='capture')['blocked'], [])
        self.ok('block', tab='capture', types=['images'])
        self.assertFalse(self.call('block', tab='capture', none=True, all=True)['ok'])
        self.assertEqual(self.ok('block', tab='capture')['blocked'], ['images'])
        self.ok('lease', tab='capture', agent='alice', task='capture')
        self.ok('block', tab='capture', types=['images'], all=True, agent='alice', task='capture')
        self.assertEqual(self.ok('block', tab='other', agent='alice', task='capture')['blocked'], ['images'])
        self.ok('open', tab='future', url=self.server.url('/small'))
        self.assertEqual(self.ok('block', tab='future')['blocked'], ['images'])
        self.ok('block', tab='capture', none=True, all=True, agent='alice', task='capture')
        self.assertEqual(self.ok('block', tab='future')['blocked'], [])

    def test_native_capture_variants_keep_success_and_restore_state(self):
        self.ok('open', tab='capture', url=self.server.url('/small'))
        inline = self.ok('screenshot', tab='capture', target='p', format='jpeg', quality=.7)
        self.assertEqual(inline['mime'], 'image/jpeg')
        self.assertTrue(base64.b64decode(inline['data']).startswith(b'\xff\xd8'))
        self.ok('viewport', tab='capture', width=150, height=300, scale=.5)
        self.ok('open', tab='capture', url=self.server.url('/tall'))
        self.ok('eval', tab='capture', script='scrollTo(0,500); return true')
        file = base.TMP / f'{self.profile}-full.png'
        capture = self.ok('screenshot', tab='capture', path=str(file), full=True)
        self.assertTrue(capture['cut'])
        self.assertEqual((capture['width'], capture['height']), (150, 16384))
        viewport = self.ok('viewport', tab='capture')
        self.assertEqual((viewport['width'], viewport['height']), (150, 300))
        self.assertEqual(self.ok('eval', tab='capture', script='return scrollY')['value'], 500)

    def test_all_blocking_rejects_other_busy_tabs_without_mutating(self):
        self.ok('open', tab='other', url=self.server.url('/small'))
        self.ok('block', tab='capture', types=['images'], all=True)
        future = self.pool.submit(self.call, 'wait', tab='other', ms=1000, request='other-busy')
        self.running('other-busy')
        for mutation in [{'types': ['fonts']}, {'none': True}]:
            denied = self.call('block', tab='capture', all=True, **mutation)
            self.assertFalse(denied['ok'], denied)
            self.assertIn('busy', denied['error']['message'])
        self.assertEqual(self.ok('block', tab='capture', all=True)['blocked'], ['images'])
        self.assertTrue(future.result()['ok'])
        self.assertEqual(self.ok('block', tab='other')['blocked'], ['images'])


if __name__ == '__main__':
    unittest.main()
