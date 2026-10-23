"""A reused public request label must not revive cancelled browser continuations."""
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


class RequestIdentityTests(unittest.TestCase):
    def setUp(self):
        self.profile = base.new_profile()
        self.server = Server()
        self.server.page('/', '<title>Request identity</title><script>window.clicked=0</script>')
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)
        result = base.umbra('open', self.server.url('/'), '--tab', 'identity', profile=self.profile)
        self.assertEqual(result.code, 0, result.err)
        self.path = str(base.LOCKS / f'daemon-{self.profile}.sock')
        self.ok('snapshot', tab='identity')

    def tearDown(self):
        base.umbra('stop', profile=self.profile)
        self.pool.shutdown(wait=True)
        self.server.close()
        base.stop_daemons([self.profile])

    def call(self, command, **args):
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(15)
            connection.connect(self.path)
            connection.sendall((json.dumps({'id': 1, 'command': command, 'args': args}) + '\n').encode())
            response = connection.makefile('rb').readline()
            self.assertTrue(response, 'daemon closed without a reply')
            return json.loads(response)

    def ok(self, command, **args):
        result = self.call(command, **args)
        self.assertTrue(result.get('ok'), result)
        return result['result']

    def running(self, request):
        until = time.monotonic() + 5
        while time.monotonic() < until:
            if any(item['request'] == request and item['state'] == 'running' for item in self.ok('tasks')['pending']):
                time.sleep(.25)
                return
            time.sleep(.005)
        self.fail(f'{request} did not start')

    def arm_late_button(self):
        self.ok('eval', tab='identity', script="""
setTimeout(()=>{const button=document.createElement('button');button.id='late';
button.textContent='Save';button.onclick=()=>window.clicked++;document.body.append(button)},900);
return true;
""")

    def test_reused_request_does_not_revive_a_cancelled_click(self):
        self.arm_late_button()
        cancelled = self.pool.submit(self.call, 'click', tab='identity', target='#late', request='repeat', timeout=4)
        self.running('repeat')
        self.ok('cancel', id='repeat')
        replacement = self.pool.submit(self.call, 'wait', tab='identity', ms=1500, request='repeat')
        self.running('repeat')
        result = cancelled.result(timeout=5)
        self.assertFalse(result['ok'], result)
        self.assertIn('cancelled', result['error']['message'])
        result = replacement.result(timeout=5)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['result']['request'], 'repeat')
        self.assertEqual(self.ok('eval', tab='identity', script='return window.clicked')['value'], 0,
                         'the cancelled request clicked after its public label was reused')

    def test_caller_operation_value_cannot_choose_the_private_identity(self):
        self.arm_late_button()
        cancelled = self.pool.submit(self.call, 'click', tab='identity', target='#late',
                                     request='cancelled', timeout=4, _operation='forced-operation')
        self.running('cancelled')
        self.ok('cancel', id='cancelled')
        replacement = self.pool.submit(self.call, 'wait', tab='identity', ms=2000,
                                       request='replacement', _operation='forced-operation')
        self.running('replacement')
        self.assertFalse(cancelled.result(timeout=5)['ok'])
        result = replacement.result(timeout=5)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['result']['request'], 'replacement')
        self.assertNotIn('_operation', json.dumps(result))
        self.assertNotIn('forced-operation', json.dumps(self.ok('tasks')))
        self.assertEqual(self.ok('eval', tab='identity', script='return window.clicked')['value'], 0)

    def test_reused_request_cannot_adopt_a_cancelled_sign_in_check(self):
        def slow_probe(h):
            time.sleep(1.5)
            h.reply(401, '{}', 'application/json')
        self.server.route('/slow-probe')(slow_probe)
        self.server.page('/login', '<title>Log in</title><input type=password>')
        sites = base.write_sites([{'name': 'Slow', 'domain': '127.0.0.1',
                                   'login': self.server.url('/login'), 'probe': '/slow-probe'}])
        cancelled = self.pool.submit(self.call, 'sign-in', sites=['Slow'], sitesFile=str(sites),
                                     request='repeat', agent='alice', task='auth', timeout=6)
        self.running('repeat')
        self.ok('cancel', id='repeat', agent='alice', task='auth')
        replacement = self.pool.submit(self.call, 'wait', tab='identity', ms=2000,
                                       request='repeat', agent='alice', task='auth')
        self.assertFalse(cancelled.result(timeout=5)['ok'])
        result = replacement.result(timeout=5)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.server.requests('/login'), [], 'an old auth check adopted the replacement request')
        self.assertEqual(result['result']['request'], 'repeat')

    def test_parent_reuse_preserves_matching_owner_and_child_cancellation(self):
        self.ok('open', tab='child', url=self.server.url('/'))
        for _ in range(2):
            parent = self.pool.submit(self.call, 'wait', tab='identity', ms=2000,
                                      request='parent', agent='alice', task='work')
            self.running('parent')
            child = self.pool.submit(self.call, 'wait', tab='child', ms=2000,
                                     request='child', _parent='parent', agent='alice', task='work')
            self.running('child')
            denied = self.call('eval', tab='child', script='return 1', _parent='parent',
                               agent='bob', task='work')
            self.assertFalse(denied['ok'], denied)
            denied = self.call('cancel', id='parent', agent='bob', task='work')
            self.assertFalse(denied['ok'], denied)
            self.ok('cancel', id='parent', agent='alice', task='work')
            self.assertFalse(parent.result(timeout=5)['ok'])
            self.assertFalse(child.result(timeout=5)['ok'])
            self.assertEqual(self.ok('tasks')['pending'], [])
        self.assertNotIn('_operation', json.dumps(self.ok('tasks')))
        self.assertNotIn('_parentOperation', json.dumps(self.ok('tasks')))


if __name__ == '__main__':
    unittest.main()
