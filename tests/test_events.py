"""Native event subscriptions through the public daemon protocol, localhost only."""
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


class EventTests(unittest.TestCase):
    def setUp(self):
        self.profile = base.new_profile()
        self.server = Server()
        self.server.page('/', '<title>Events</title><p>ready</p>')
        self.server.page('/next', '<title>Next</title><p>next</p>')
        self.server.route('/data')(lambda h: h.reply(body='private-response-body', headers=[('X-Private', 'private-header')]))
        self.server.route('/file')(lambda h: h.reply(body='download', headers=[('Content-Disposition', 'attachment; filename=private-name.txt')]))
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=24)
        result = base.umbra('runtime', profile=self.profile)
        self.assertEqual(result.code, 0, result.err)
        self.path = str(base.LOCKS / f'daemon-{self.profile}.sock')
        self.ok('open', tab='a', url=self.server.url('/'))
        self.ok('open', tab='b', url=self.server.url('/'))

    def tearDown(self):
        base.umbra('stop', profile=self.profile)
        self.pool.shutdown(wait=True)
        self.server.close()
        base.stop_daemons([self.profile])

    def call(self, command, **args):
        with socket.socket(socket.AF_UNIX) as conn:
            conn.settimeout(35); conn.connect(self.path)
            conn.sendall((json.dumps({'id': 1, 'command': command, 'args': args}) + '\n').encode())
            return json.loads(conn.makefile('rb').readline())

    def ok(self, command, **args):
        result = self.call(command, **args)
        self.assertTrue(result.get('ok'), result)
        return result['result']

    def pending(self, request):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if any(job['request'] == request for job in self.ok('tasks')['pending']):
                return
            time.sleep(.01)
        self.fail('event request did not become pending: ' + request)

    def wait_event(self, kind, request='watch', **owner):
        cursor = self.ok('events', target='a', **owner)['cursor']
        wait = self.pool.submit(self.call, 'events', target='a', since=cursor, kind=[kind], wait=5, request=request, **owner)
        self.pending(request)
        return wait

    def test_url_subscription_does_not_block_target_and_is_tab_scoped(self):
        waiting = self.wait_event('url')
        self.ok('open', tab='b', url=self.server.url('/next'))
        self.assertFalse(waiting.done())
        self.ok('open', tab='a', url=self.server.url('/next'))
        events = waiting.result()['result']['events']
        self.assertEqual([event['kind'] for event in events], ['url'])
        self.assertEqual(events[0]['url'], self.server.url('/next'))

    def test_same_document_url_change_is_observed(self):
        waiting = self.wait_event('url')
        self.ok('eval', tab='a', script='history.pushState({}, "", "/spa?step=2"); return true')
        self.assertEqual(waiting.result()['result']['events'][0]['url'], self.server.url('/spa?step=2'))
        waiting = self.wait_event('url', 'replace-watch')
        self.ok('eval', tab='a', script='history.replaceState({}, "", "/spa?step=3"); return true')
        self.assertEqual(waiting.result()['result']['events'][0]['url'], self.server.url('/spa?step=3'))

    def test_dialog_network_and_download_metadata(self):
        dialog = self.wait_event('dialog', 'dialog-watch')
        self.ok('eval', tab='a', script='alert("private-dialog-body"); return true')
        self.assertEqual(dialog.result()['result']['events'][0]['type'], 'alert')
        network = self.wait_event('network', 'network-watch')
        self.ok('eval', tab='a', script='await fetch("/data"); return true')
        self.assertTrue(network.result()['result']['events'])
        download = self.wait_event('download', 'download-watch')
        self.ok('eval', tab='a', script='const a=document.createElement("a"); a.href="/file"; a.click(); return true')
        self.assertEqual(download.result()['result']['events'][0]['state'], 'complete')
        retained = json.dumps(self.ok('events', target='a', since=0))
        for secret in ('private-dialog-body', 'private-response-body', 'private-header', 'private-name.txt'):
            self.assertNotIn(secret, retained)
        for event in self.ok('events', target='a', since=0)['events']:
            self.assertLessEqual(set(event), {'sequence', 'kind', 'time', 'url', 'status', 'method', 'state', 'type', 'failed', 'bytes'})

    def test_retention_is_bounded_and_expired_cursor_is_explicit(self):
        self.ok('events', target='a')
        self.ok('eval', tab='a', script='for(let i=0;i<270;i++) alert("not-retained"); return true')
        result = self.ok('events', target='a', since=0)
        self.assertEqual(len(result['events']), 256)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['cursor'], 270)
        self.assertEqual(result['events'][0]['sequence'], 15)
        current = self.ok('events', target='a', since=result['cursor'])
        self.assertEqual(current['events'], [])
        self.assertFalse(current['truncated'])

    def test_cancel_removes_waiters_and_enforces_owner(self):
        self.ok('lease', tab='a', agent='alice', task='events')
        self.assertFalse(self.call('events', target='a')['ok'])
        for index in range(20):
            request = 'cancel-watch-' + str(index)
            waiting = self.wait_event('dialog', request, agent='alice', task='events')
            self.assertFalse(self.call('cancel', id=request, agent='bob', task='events')['ok'])
            self.ok('cancel', id=request, agent='alice', task='events')
            self.assertFalse(waiting.result()['ok'])
        waiting = self.wait_event('dialog', 'survivor', agent='alice', task='events')
        self.ok('eval', tab='a', agent='alice', task='events', script='alert("ok"); return true')
        self.assertTrue(waiting.result()['ok'])
        self.assertEqual(self.ok('runtime')['coordinators'], 0)

    def test_duration_close_and_waiter_budget(self):
        before = time.monotonic()
        self.assertEqual(self.ok('events', target='a', wait=.1)['events'], [])
        self.assertGreaterEqual(time.monotonic() - before, .08)
        waits = []
        for index in range(16):
            request = 'budget-' + str(index)
            waits.append(self.pool.submit(self.call, 'events', target='a', wait=5, request=request))
            self.pending(request)
        self.assertFalse(self.call('events', target='a', wait=5)['ok'])
        self.ok('tab', action='close', name='a')
        self.assertTrue(all(not wait.result()['ok'] for wait in waits))
        for args in ({'wait': -1}, {'wait': 26}, {'since': -.1}, {'since': 1.5}, {'kind': ['body']}):
            self.assertFalse(self.call('events', target='b', **args)['ok'])


if __name__ == '__main__':
    unittest.main()
