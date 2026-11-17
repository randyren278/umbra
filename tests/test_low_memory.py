"""Opt-in low-memory sessions through the public CLI, daemon protocol and MCP.

Only synthetic localhost accounts and throwaway profiles are used. The lead runs this
suite after building; no GUI permission or real browsing state is required.
"""
import json
import queue
import socket
import subprocess
import sys
import threading
import time
import unittest
import urllib.request
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_umbra as base
from hybrid_site import make_server, contract


class LowMemoryTests(base.UmbraCase):
    def setUp(self):
        super().setUp()
        self.web = make_server()
        threading.Thread(target=self.web.serve_forever, daemon=True).start()
        self.origin = f'http://127.0.0.1:{self.web.server_address[1]}'
        self.contract = self.tmp / 'contract.json'
        self.contract.write_text(json.dumps(contract(self.origin)))
        self.assertExit(self.umbra('tabs'), 0)
        self.path = base.LOCKS / f'daemon-{self.profile}.sock'
        self.counter = 0

    def tearDown(self):
        self.umbra('stop')
        self.web.shutdown()
        self.web.server_close()

    def rpc(self, command, expected=True, sensitive=False, **args):
        self.counter += 1
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(20)
            connection.connect(str(self.path))
            connection.sendall(json.dumps(dict(id=self.counter, command=command, args=args)).encode()+b'\n')
            stream = connection.makefile('rb')
            response = json.loads(stream.readline())
            stream.close()
        self.assertEqual(response.get('ok'), expected, 'sensitive response did not match expected outcome' if sensitive else response)
        return response['result'] if expected else response['error']

    def value(self, tab, script, **owner):
        return self.rpc('eval', tab=tab, script=script, **owner)['value']

    def wait(self, tab, predicate='window.ready === true', timeout=10, **owner):
        end = time.monotonic()+timeout
        while time.monotonic() < end:
            try:
                if self.value(tab, 'return '+predicate, **owner):
                    return
            except (AssertionError, OSError):
                pass
            time.sleep(.025)
        self.fail('fixture readiness did not hold: '+predicate)

    def admin(self, action='truth'):
        request = urllib.request.Request(self.origin+'/admin/'+action,
                                        data=None if action == 'truth' else b'{}',
                                        headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)

    def api(self, action, name='work', route=None, expected=True, **args):
        if route is not None:
            args['route'] = route
        return self.rpc('api', expected=expected, action=action, name=name, **args)

    def open_api(self, name='work', **owner):
        self.api('open', name, contract=str(self.contract), **owner)
        return self.login_api(name, **owner)

    def login_api(self, name='work', **owner):
        self.api('call', name, 'handshake', **owner)
        reply = self.api('call', name, 'login', data=json.dumps(dict(username='agent', password='fixture-password')), **owner)
        self.assertTrue(reply['data']['authenticated'])
        self.assertNotIn('csrf', reply['data'])
        return reply

    def observe(self, view='inbox', name='work', query=None):
        return self.api('call', name, view, query=json.dumps(query or {}))['data']

    def test_default_is_unchanged_and_nonvisual_outline_promotes_without_reload(self):
        normal = self.rpc('open', url=self.origin+'/login', tab='normal')
        self.assertTrue(normal['rendered'])
        self.assertFalse(normal['nonvisual'])
        bare = self.rpc('open', url=self.origin+'/login', tab='bare', nonvisual=True)
        self.assertTrue(bare['nonvisual'])
        self.assertFalse(bare['windowCreated'])
        self.assertEqual(self.value('bare', 'return document.visibilityState'), 'hidden')
        before = self.value('bare', 'window.marker="same-document";document.querySelector("#username").value="draft";return location.href')
        outline = self.rpc('snapshot', tab='bare', interactive=True)
        self.assertIn('Sign in', outline['text'])
        self.assertIn('username', outline['text'].lower())
        self.assertFalse(next(x for x in self.rpc('tabs')['tabs'] if x['tab']=='bare')['windowCreated'])
        self.rpc('render', tab='bare')
        self.assertEqual(self.value('bare', 'return {marker:window.marker,draft:document.querySelector("#username").value,url:location.href,visible:document.visibilityState}'),
                         dict(marker='same-document', draft='draft', url=before, visible='visible'))

    def test_native_actions_and_capture_promote_only_their_target(self):
        self.rpc('open', url=self.origin+'/login', tab='input', nonvisual=True)
        self.rpc('open', url=self.origin+'/login', tab='untouched', nonvisual=True)
        self.value('input', 'document.querySelector("#username").addEventListener("input",e=>window.trusted=e.isTrusted);return true')
        self.rpc('fill', tab='input', target='#username', value='agent')
        self.assertTrue(self.value('input', 'return window.trusted'))
        tabs = {x['tab']:x for x in self.rpc('tabs')['tabs']}
        self.assertTrue(tabs['input']['rendered'])
        self.assertFalse(tabs['untouched']['windowCreated'])
        self.rpc('screenshot', tab='untouched', path=str(self.tmp/'capture.png'))
        self.assertGreater((self.tmp/'capture.png').stat().st_size, 1000)
        self.assertTrue(next(x for x in self.rpc('tabs')['tabs'] if x['tab']=='untouched')['rendered'])

    def test_private_api_and_browser_do_not_initialize_the_ordinary_profile_store(self):
        self.assertFalse(self.rpc('status')['profileStoreUsed'])
        self.open_api()
        self.assertFalse(self.rpc('status')['profileStoreUsed'])
        self.api('browser', url=self.origin+'/app/js-only', tab='private')
        self.wait('private')
        self.rpc('render', tab='private')
        self.assertFalse(self.rpc('status')['profileStoreUsed'])
        self.rpc('open', url=self.origin+'/login', tab='ordinary')
        self.assertTrue(self.rpc('status')['profileStoreUsed'])

    def test_api_tasks_freshness_conflicts_retries_and_expiry(self):
        self.open_api()
        state = self.observe(query=dict(q='urgent', unread=1))
        expected = [x['id'] for x in self.admin()['items'] if x['unread'] and 'urgent' in x['subject'].lower()]
        self.assertEqual([x['id'] for x in state['items']], expected[:50])
        self.assertEqual(state['count'], len(expected))
        self.assertNotIn('csrf', state)
        body = dict(id=expected[0], revision=state['revision'], requestId='retry-same')
        first = self.api('call', route='mark', data=json.dumps(body))
        second = self.api('call', route='mark', data=json.dumps(body))
        self.assertEqual(first['data']['revision'], second['data']['revision'])
        self.assertTrue(second['data']['replay'])
        self.assertEqual(len(self.admin()['actions']), 1)
        mismatch = self.api('call', route='mark', data=json.dumps(dict(body, id=expected[1])), expected=False)
        self.assertEqual(mismatch['status'], 409)
        stale = self.api('call', route='mark', data=json.dumps(dict(body, requestId='stale')), expected=False)
        self.assertEqual(stale['status'], 409)
        denied = self.api('call', route='badcsrf', data=json.dumps(dict(id=expected[1], revision=first['data']['revision'], requestId='invalid-csrf')), expected=False)
        self.assertEqual(denied['status'], 403)
        self.assertEqual(len(self.admin()['actions']), 1)
        self.admin('bump')
        current = self.observe(query=dict(q='urgent', unread=1))
        self.assertGreater(current['revision'], state['revision'])
        self.assertEqual(current['items'][0]['id'], 9001)
        self.admin('expire')
        expired = self.api('call', route='inbox', expected=False)
        self.assertEqual(expired['status'], 401)
        self.assertEqual(expired['kind'], 'needs_auth')

    def test_declared_defaults_origin_and_session_isolation(self):
        self.open_api('one')
        self.api('open', 'two', contract=str(self.contract))
        self.assertEqual(self.api('call', 'two', 'inbox', expected=False)['status'], 401)
        self.login_api('two')
        # Locked declarations win over caller body values: this must mark, not overwrite preferences.
        state = self.observe(name='one', query=dict(q='urgent', unread=1))
        self.api('call', 'one', 'mark', data=json.dumps(dict(id=state['items'][0]['id'], revision=state['revision'], requestId='locked', kind='prefs', name='unexpected')))
        self.assertEqual(self.admin()['preferences']['name'], 'Synthetic Agent')
        self.api('call', 'one', 'logout')
        self.assertEqual(self.api('call', 'one', 'inbox', expected=False)['status'], 401)
        self.assertEqual(self.api('call', 'two', 'inbox')['status'], 200)
        self.api('close', 'one')
        self.assertEqual(self.api('call', 'two', 'inbox')['status'], 200)
        rejected = self.api('browser', 'two', url='http://127.0.0.1:1/app/js-only', tab='bad', expected=False)
        self.assertIn('origin', rejected['message'].lower())
        rejected = self.api('call', 'two', 'undeclared', expected=False)
        self.assertEqual(rejected['code'], 64)

    def test_cookie_and_captured_token_keys_are_redacted_without_echoing_secrets(self):
        self.open_api()
        error = self.api('call', route='secret-echo', expected=False, sensitive=True)
        # Refusal preserves fidelity; boolean-only failures cannot render a leaked object.
        self.assertTrue(error.get('kind') == 'redaction_conflict', 'secret-bearing response was not explicitly refused')
        self.assertTrue(error.get('needsBrowser') is True, 'redaction conflict did not request browser capability')
        self.assertTrue('data' not in error and 'text' not in error, 'refusal included a secret-bearing payload')

    def test_short_cookie_values_do_not_corrupt_ordinary_application_text(self):
        self.open_api()
        expected = dict(department='Engineering', project='Project123', description='engineering weekend')
        for route in ('short-cookie', 'short-flag'):
            with self.subTest(route=route):
                reply = self.api('call', route=route)
                self.assertEqual(reply['data'], expected)
                self.assertEqual(json.loads(reply['text']), expected)

    def test_structured_session_has_explicit_agent_task_ownership(self):
        self.open_api('owned', agent='alice', task='work')
        denied = self.api('call', 'owned', 'inbox', agent='bob', task='work', expected=False)
        self.assertEqual(denied['code'], 64)
        self.assertEqual(self.api('call', 'owned', 'inbox', agent='alice', task='work')['status'], 200)
        self.api('browser', 'owned', url=self.origin+'/app/js-only', tab='owned-view', agent='alice', task='work')
        self.api('close', 'owned', agent='alice', task='work')
        denied = self.rpc('eval', expected=False, tab='owned-view', script='return 1', agent='bob', task='work')
        self.assertEqual(denied['code'], 64)
        self.assertEqual(self.rpc('eval', tab='owned-view', script='return 17', agent='alice', task='work')['value'], 17)

    def test_redirect_cannot_send_private_session_to_another_origin(self):
        other = make_server()
        threading.Thread(target=other.serve_forever, daemon=True).start()
        try:
            self.open_api()
            redirect = self.api('call', route='scoped-redirect')
            self.assertFalse(redirect['data']['scopedCookiePresent'])
            self.assertTrue(self.api('call', route='scoped-check')['data']['scopedCookiePresent'])
            foreign = f'http://127.0.0.1:{other.server_address[1]}/api/received'
            self.api('call', route='redirect', query=json.dumps(dict(target=foreign)), expected=False)
            self.assertEqual(other.received_requests, [])
        finally:
            other.shutdown(); other.server_close()

    def test_fallback_cookie_rotation_and_profile_tabs_remain_isolated(self):
        self.open_api()
        self.api('browser', url=self.origin+'/app/js-only', tab='fallback')
        self.wait('fallback')
        self.assertFalse(next(x for x in self.rpc('tabs')['tabs'] if x['tab']=='fallback')['windowCreated'])
        code = self.value('fallback', 'const r=await fetch("/api/rotate",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({csrf:window.model.csrf})});return r.status')
        self.assertEqual(code, 200)
        self.assertEqual(self.api('call', route='inbox')['status'], 200)
        self.rpc('open', url=self.origin+'/app/inbox', tab='ordinary')
        self.assertEqual(self.value('ordinary', 'return document.title'), 'Sign in')
        self.open_api('separate')
        self.api('browser', 'separate', url=self.origin+'/app/js-only', tab='separate-browser')
        self.wait('separate-browser')
        self.value('fallback', 'const r=await fetch("/api/logout",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({csrf:window.model.csrf})});return r.status')
        self.assertEqual(self.api('call', route='inbox', expected=False)['status'], 401)
        self.assertEqual(self.api('call', 'separate', 'inbox')['status'], 200)
        self.value('separate-browser', 'window.survivor=17;return true')
        self.api('close', 'separate')
        self.assertEqual(self.value('separate-browser', 'return window.survivor'), 17)

    def test_api_popup_retains_private_authentication_and_owner_lane(self):
        owner = dict(agent='alice', task='popup-work')
        self.open_api('owned', **owner)
        self.api('browser', 'owned', url=self.origin+'/app/js-only', tab='opener', **owner)
        self.wait('opener', **owner)
        self.value('opener', 'const b=document.createElement("button");b.id="owned-popup";b.textContent="Open inbox";b.onclick=()=>window.open("/app/inbox");document.querySelector("main").prepend(b);return true', **owner)
        opened = self.rpc('click', tab='opener', target='#owned-popup', **owner)
        popup = opened.get('newTab')
        self.assertTrue(popup, 'native popup was not reported')
        self.wait(popup, **owner)
        denied = self.rpc('eval', expected=False, tab=popup, script='return document.title', agent='bob', task='popup-work')
        self.assertEqual(denied['code'], 64)
        self.assertGreater(self.value(popup, 'return window.model.count', **owner), 0)
        # Rotation from the popup must update the same API-private cookie store.
        code = self.value(popup, 'const r=await fetch("/api/rotate",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({csrf:window.model.csrf})});return r.status', **owner)
        self.assertEqual(code, 200)
        self.assertEqual(self.api('call', 'owned', 'inbox', **owner)['status'], 200)
        outcomes, failures = [], []
        def background(action):
            try:
                outcomes.append(action())
            except BaseException as error:
                failures.append(error)
        blocker = threading.Thread(target=lambda: background(lambda: self.rpc('eval',tab=popup,script='await new Promise(r=>setTimeout(r,1200));return 7',request='popup-blocker',**owner)))
        blocker.start()
        end = time.monotonic()+5
        while time.monotonic()<end:
            if any(x['request']=='popup-blocker' and x['state']=='running' for x in self.rpc('tasks')['pending']):
                break
            time.sleep(.01)
        else:
            blocker.join(10)
            self.fail('popup operation did not enter runtime')
        queued = threading.Thread(target=lambda: background(lambda: self.api('call','owned','inbox',request='popup-api',**owner)))
        queued.start()
        saw_queue = False
        end = time.monotonic()+1
        while time.monotonic()<end:
            pending = self.rpc('tasks')['pending']
            if any(x['request']=='popup-api' and x['state']=='queued' for x in pending):
                saw_queue = True
                break
            if not queued.is_alive():
                break
            time.sleep(.01)
        blocker.join(10); queued.join(10)
        self.assertFalse(blocker.is_alive() or queued.is_alive())
        self.assertEqual(failures, [])
        self.assertTrue(saw_queue, 'popup and API calls did not share their session lane')
        self.assertEqual(len(outcomes), 2)

    def test_cancel_after_commit_does_not_repeat_write(self):
        self.open_api()
        state = self.observe(query=dict(q='urgent', unread=1))
        request = 'cancel-after-commit'
        payload = dict(id=state['items'][0]['id'], revision=state['revision'], requestId=request)
        result, failures = [], []
        def post():
            try:
                result.append(self.api('call', route='slow', data=json.dumps(payload), request=request, expected=False))
            except BaseException as error:
                failures.append(error)
        thread = threading.Thread(target=post)
        thread.start()
        self.assertTrue(self.web.action_committed.wait(5), 'mutation did not reach the fixture')
        self.rpc('cancel', id=request)
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['code'], 2)
        self.assertEqual(result[0]['kind'], 'unknown_outcome')
        self.assertEqual(result[0]['outcome'], 'unknown')
        self.assertEqual(len(self.admin()['actions']), 1)
        # No transparent retry occurred. An explicit identical retry is server-idempotent.
        retry = self.api('call', route='slow', data=json.dumps(payload))
        self.assertTrue(retry['data']['replay'])
        self.assertEqual(len(self.admin()['actions']), 1)

    def test_committed_mutation_with_malformed_response_is_unknown_until_reconciled(self):
        self.open_api()
        state = self.observe(query=dict(q='urgent', unread=1))
        payload = dict(id=state['items'][0]['id'], revision=state['revision'], requestId='malformed-response')
        error = self.api('call', route='malformed', data=json.dumps(payload), expected=False)
        self.assertEqual(error['kind'], 'unknown_outcome')
        self.assertEqual(error['outcome'], 'unknown')
        self.assertEqual(error['requestId'], payload['requestId'])
        truth = self.admin()
        self.assertEqual(len(truth['actions']), 1)
        self.assertFalse(next(x for x in truth['items'] if x['id']==payload['id'])['unread'])
        # The server contract supplies idempotency. An explicit identical retry returns its receipt.
        reply = self.api('call', route='malformed', data=json.dumps(payload))
        self.assertTrue(reply['data']['replay'])
        self.assertEqual(reply['data']['revision'], truth['revision'])
        self.assertEqual(len(self.admin()['actions']), 1)

    def test_cli_and_mcp_share_the_same_declared_api_session(self):
        result = self.umbra('api', 'open', 'shared', '--contract', str(self.contract), '--json')
        self.assertExit(result, 0)
        proc = base.spawn('mcp', profile=self.profile, stdin=subprocess.PIPE)
        inbox = queue.Queue()
        def read():
            for line in proc.stdout:
                inbox.put(json.loads(line))
        threading.Thread(target=read, daemon=True).start()
        def rpc(id, method, params):
            proc.stdin.write((json.dumps(dict(jsonrpc='2.0', id=id, method=method, params=params))+'\n').encode()); proc.stdin.flush()
            response = inbox.get(timeout=15)
            self.assertEqual(response['id'], id)
            return response
        try:
            rpc('init', 'initialize', dict(protocolVersion='2025-06-18'))
            tools = rpc('tools', 'tools/list', {})['result']['tools']
            self.assertTrue({'api','render'}.issubset({x['name'] for x in tools}))
            call = lambda id, route, **args: rpc(id, 'tools/call', dict(name='api', arguments=dict(action='call',name='shared',route=route,**args)))
            self.assertFalse(call('handshake','handshake')['result'].get('isError',False))
            self.assertFalse(call('login','login',data=json.dumps(dict(username='agent',password='fixture-password')))['result'].get('isError',False))
            result = self.umbra('api','call','shared','inbox','--json')
            self.assertExit(result,0)
            self.assertGreater(json.loads(result.text)['data']['count'],0)
        finally:
            proc.stdin.close()
            try:proc.wait(5)
            except subprocess.TimeoutExpired:proc.kill();proc.wait()


if __name__ == '__main__':
    unittest.main()
