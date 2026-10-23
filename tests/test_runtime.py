"""Runtime public-protocol regression tests: localhost pages and disposable profiles only."""
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


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.profile = base.new_profile()
        self.env = {'UMBRA_ACTIVE': '4', 'UMBRA_PENDING': '16', 'UMBRA_TABS': '40',
                    'UMBRA_CONNECTIONS': '40', 'UMBRA_REQUEST_BYTES': '4096'}
        self.server = Server()
        self.server.routes['/'] = lambda h: h.reply(body='<title>runtime</title><input id="name"><button id="popup" onclick="window.open(location.href)">popup</button><p>ready</p>')
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=40)
        result = base.umbra('runtime', profile=self.profile, env=self.env)
        self.assertEqual(result.code, 0, result.err)
        self.path = str(base.LOCKS / f'daemon-{self.profile}.sock')

    def tearDown(self):
        base.umbra('stop', profile=self.profile, env=self.env)
        self.pool.shutdown(wait=True)
        self.server.close()
        base.stop_daemons([self.profile])

    def call(self, command, **args):
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(20)
            connection.connect(self.path)
            connection.sendall((json.dumps({'id': 1, 'command': command, 'args': args}) + '\n').encode())
            data = connection.makefile('rb').readline()
            self.assertTrue(data, 'daemon closed without reply')
            return json.loads(data)

    def ok(self, command, **args):
        result = self.call(command, **args)
        self.assertTrue(result.get('ok'), result)
        return result['result']

    def open(self, name='a'):
        return self.ok('open', tab=name, url=self.server.base + '/')

    def pending(self, count=1):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            pending = self.ok('tasks')['pending']
            if len(pending) >= count:
                return pending
            time.sleep(.02)
        self.fail(f'never observed {count} pending requests')

    def form_page(self, server):
        server.page('/form', """<!doctype html><title>Unsaved form</title>
<input id=date type=date><input id=name><input id=ready type=checkbox>
<select id=choice><option value=a>A</option><option value=b>B</option></select>
<script>window.events=[]; for(const name of ['input','change']) addEventListener(name,e=>window.events.push({type:e.type,trusted:e.isTrusted}),true)</script>""")

    def reclaim_after_idle(self):
        time.sleep(5.2)
        self.ok('runtime', reclaim=True)
        return {tab['tab'] for tab in self.ok('tabs')['tabs']}

    def test_reclaim_preserves_explicit_setter_and_select_edits(self):
        self.form_page(self.server)
        for tab in ('date', 'select', 'receipt', 'noop', 'type', 'check', 'clean', 'scripted', 'failed'):
            self.ok('open', tab=tab, url=self.server.url('/form'))
        filled = self.ok('fill', tab='date', target='#date', value='2026-10-23')
        self.assertEqual(filled['method'], 'setter')
        self.assertFalse(filled['trusted'])
        self.assertEqual(filled['value'], '2026-10-23')
        self.ok('select', tab='select', target='#choice', values=['b'])
        for tab in ('date', 'select'):
            events = self.ok('eval', tab=tab, script='return window.events')['value']
            self.assertTrue(events, tab)
            self.assertTrue(all(not event['trusted'] for event in events), events)
        self.ok('uncheck', tab='noop', target='#ready')
        self.ok('type', tab='type', target='#name', text='')
        self.ok('eval', tab='check', script="document.querySelector('#ready').checked=true; return true")
        self.ok('check', tab='check', target='#ready')
        self.ok('select', tab='receipt', target='#choice', values=['b'], receipt=True)
        self.ok('eval', tab='scripted', script="document.querySelector('#name').value='page-owned'; document.querySelector('#name').dispatchEvent(new Event('input',{bubbles:true})); return true")
        self.ok('snapshot', tab='clean')
        self.assertFalse(self.call('select', tab='failed', target='#choice', values=['missing'])['ok'])
        tabs = self.reclaim_after_idle()
        self.assertTrue({'date', 'select', 'receipt', 'noop', 'type', 'check'} <= tabs, tabs)
        self.assertFalse({'clean', 'scripted', 'failed'} & tabs, tabs)
        self.assertEqual(self.ok('eval', tab='date', script="return document.querySelector('#date').value")['value'], '2026-10-23')
        self.assertEqual(self.ok('eval', tab='select', script="return document.querySelector('#choice').value")['value'], 'b')

    def test_reclaim_preserves_cross_origin_frame_form_edits(self):
        inner = Server()
        try:
            self.form_page(inner)
            self.server.page('/frames', f'<title>Parent</title><iframe style="width:700px;height:450px" src="{inner.url("/form")}"></iframe>')
            for tab in ('date', 'select', 'clean'):
                self.ok('open', tab=tab, url=self.server.url('/frames'))
            def child(tab):
                return next(frame['id'] for frame in self.ok('frames', tab=tab)['frames'] if frame['url'] == inner.url('/form'))
            date, select = child('date'), child('select')
            filled = self.ok('frame', tab='date', id=date, operation='fill', args=json.dumps({'target':'#date','value':'2026-10-23'}))
            self.assertEqual(filled['method'], 'setter')
            self.assertFalse(filled['trusted'])
            self.assertEqual(filled['value'], '2026-10-23')
            chosen = self.ok('frame', tab='select', id=select, operation='select', args=json.dumps({'target':'#choice','values':['b']}))
            self.assertEqual(chosen['values'], ['b'])
            for tab, frame in [('date', date), ('select', select)]:
                events = self.ok('frame', tab=tab, id=frame, operation='eval', args=json.dumps({'script':'return window.events'}))['value']
                self.assertTrue(events)
                self.assertTrue(all(not event['trusted'] for event in events), events)
            tabs = self.reclaim_after_idle()
            self.assertTrue({'date', 'select'} <= tabs, tabs)
            self.assertNotIn('clean', tabs)
            for tab, frame, selector, expected in [('date', date, '#date', '2026-10-23'), ('select', select, '#choice', 'b')]:
                state = self.ok('frame', tab=tab, id=frame, operation='eval', args=json.dumps({'script':f'return document.querySelector({json.dumps(selector)}).value'}))
                self.assertEqual(state['value'], expected)
        finally:
            inner.close()

    def test_cancelled_form_action_and_navigation_remain_reclaimable(self):
        self.form_page(self.server)
        self.server.page('/next', '<title>Next document</title><p>saved</p>')
        for tab in ('cancelled', 'navigated'):
            self.ok('open', tab=tab, url=self.server.url('/form'))
        slow = self.pool.submit(self.call, 'wait', tab='cancelled', ms=500)
        self.pending()
        queued = self.pool.submit(self.call, 'select', tab='cancelled', target='#choice', values=['b'], request='cancelled-form')
        self.pending(2)
        self.ok('cancel', id='cancelled-form')
        self.assertFalse(queued.result()['ok'])
        self.assertTrue(slow.result()['ok'])
        self.assertEqual(self.ok('eval', tab='cancelled', script="return document.querySelector('#choice').value")['value'], 'a')
        self.ok('select', tab='navigated', target='#choice', values=['b'])
        self.ok('open', tab='navigated', url=self.server.url('/next'))
        self.assertFalse({'cancelled', 'navigated'} & self.reclaim_after_idle())

    def test_tab_commands_serialize_and_other_tabs_progress(self):
        self.open('a'); self.open('b')
        slow = self.pool.submit(self.call, 'eval', tab='a', request='slow', script="window.order=['first']; await new Promise(r=>setTimeout(r,500)); window.order.push('last'); return window.order")
        self.pending()
        later = self.pool.submit(self.call, 'eval', tab='a', script="window.order.push('second'); return window.order")
        independent = self.ok('eval', tab='b', script='return 42')
        self.assertEqual(independent['value'], 42)
        self.assertEqual(slow.result()['result']['value'], ['first', 'last'])
        self.assertEqual(later.result()['result']['value'], ['first', 'last', 'second'])

    def test_parallel_tabs_run_at_the_same_time(self):
        for tab in ('a', 'b'): self.open(tab)
        script = 'const start=Date.now(); await new Promise(r=>setTimeout(r,500)); return [start,Date.now()]'
        jobs = [self.pool.submit(self.call, 'eval', tab=tab, script=script) for tab in ('a', 'b')]
        times = [job.result()['result']['value'] for job in jobs]
        self.assertLess(max(t[0] for t in times), min(t[1] for t in times), times)

    def test_lease_enforced_for_navigation_eval_and_close(self):
        self.open()
        result = base.umbra('lease', '--tab', 'a', '--agent', 'alice', '--task', 'work', '--json', profile=self.profile, env=self.env)
        self.assertEqual(result.code, 0, result.err)
        for command, args in [('eval', {'tab': 'a', 'script': 'return 1'}),
                              ('open', {'tab': 'a', 'url': self.server.base + '/'}),
                              ('tab', {'action': 'close', 'name': 'a'})]:
            self.assertFalse(self.call(command, **args)['ok'])
        self.assertEqual(self.ok('eval', tab='a', agent='alice', task='work', script='return 7')['value'], 7)
        self.ok('lease', tab='a', agent='alice', task='work', release=True)
        self.assertEqual(self.ok('eval', tab='a', script='return 8')['value'], 8)

    def test_popup_inherits_lease_and_shared_instrumentation_survives(self):
        self.open('a')
        self.ok('lease', tab='a', agent='alice')
        self.ok('click', tab='a', agent='alice', target='#popup')
        tabs = self.ok('tabs')['tabs']
        popup = next(tab['tab'] for tab in tabs if tab['tab'] != 'a')
        self.assertFalse(self.call('eval', tab=popup, script='return 1')['ok'])
        self.assertEqual(self.ok('eval', tab=popup, agent='alice', script='return 2')['value'], 2)
        self.ok('tab', action='close', name=popup, agent='alice')
        self.ok('fill', tab='a', agent='alice', target='#name', value='still instrumented')
        self.assertEqual(self.ok('eval', tab='a', agent='alice', script="return document.querySelector('#name').value")['value'], 'still instrumented')

    def test_expired_lease_becomes_available(self):
        self.open()
        self.ok('lease', tab='a', agent='alice', seconds=1)
        time.sleep(1.1)
        self.ok('lease', tab='a', agent='bob')
        self.assertFalse(self.call('eval', tab='a', agent='alice', script='return 1')['ok'])

    def test_cancel_queued_work_never_mutates(self):
        self.open()
        slow = self.pool.submit(self.call, 'wait', tab='a', ms=400, request='slow')
        self.pending()
        queued = self.pool.submit(self.call, 'eval', tab='a', script='window.cancelledMutation=1; return 1', request='queued')
        self.pending(2)
        self.ok('cancel', id='queued')
        self.assertFalse(queued.result()['ok'])
        self.assertTrue(slow.result()['ok'])
        self.assertTrue(self.ok('eval', tab='a', script="return typeof window.cancelledMutation === 'undefined'")['value'])

    def test_cancel_running_wait_releases_tab_and_checks_owner(self):
        self.open()
        waiting = self.pool.submit(self.call, 'wait', tab='a', js='false', request='waiting', agent='alice', task='x', timeout=10)
        self.pending()
        self.assertFalse(self.call('cancel', id='waiting', agent='bob', task='x')['ok'])
        self.ok('cancel', id='waiting', agent='alice', task='x')
        self.assertFalse(waiting.result()['ok'])
        self.assertEqual(self.ok('eval', tab='a', script='return 10')['value'], 10)

    def test_queue_deadline_includes_waiting_time(self):
        self.open()
        slow = self.pool.submit(self.call, 'wait', tab='a', ms=500)
        self.pending()
        expired = self.call('eval', tab='a', script='window.expiredMutation=1; return 1', timeout=.1)
        self.assertFalse(expired['ok'])
        self.assertEqual(expired['error']['code'], 2)
        slow.result()
        self.assertTrue(self.ok('eval', tab='a', script="return typeof window.expiredMutation === 'undefined'")['value'])
        self.assertGreaterEqual(self.ok('runtime')['timedOut'], 1)

    def test_admission_bounded_under_32_clients(self):
        self.open()
        jobs = [self.pool.submit(self.call, 'wait', tab='a', ms=150, request=f'load-{index}') for index in range(32)]
        results = [job.result() for job in jobs]
        successes = sum(result['ok'] for result in results)
        self.assertGreaterEqual(successes, 1)
        self.assertLess(successes, 32)
        status = self.ok('runtime')
        self.assertLessEqual(status['peakQueued'], 16)
        self.assertGreater(status['rejected'], 0)
        self.assertEqual(status['active'], 0)
        self.assertEqual(status['queued'], 0)

    def test_request_size_bound_and_persistent_connection(self):
        with socket.socket(socket.AF_UNIX) as connection:
            connection.connect(self.path)
            stream = connection.makefile('rb')
            for number in (1, 2):
                connection.sendall((json.dumps({'id': number, 'command': 'runtime'}) + '\n').encode())
                self.assertEqual(json.loads(stream.readline())['id'], number)
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(5); connection.connect(self.path)
            connection.sendall(b'x' * 4097)
            error = json.loads(connection.makefile('rb').readline())
            self.assertEqual(error['error']['code'], 64)
        self.ok('runtime')

    def test_reclaim_preserves_busy_leased_and_dirty_tabs(self):
        for tab in ('abandoned', 'busy', 'owned', 'dirty'): self.open(tab)
        self.ok('lease', tab='owned', agent='alice')
        self.ok('fill', tab='dirty', target='#name', value='unsaved')
        slow = self.pool.submit(self.call, 'wait', tab='busy', ms=8000, timeout=12)
        self.pending()
        time.sleep(5.2)
        result = self.ok('runtime', reclaim=True)
        tabs = {tab['tab'] for tab in self.ok('tabs')['tabs']}
        self.assertNotIn('abandoned', tabs)
        self.assertTrue({'busy', 'owned', 'dirty'} <= tabs, tabs)
        self.assertGreaterEqual(result['reclaimedTabs'], 1)
        self.assertTrue(slow.result()['ok'])

    def test_queued_tab_creation_rechecks_admission_at_execution(self):
        base.umbra('stop', profile=self.profile, env=self.env)
        base.stop_daemons([self.profile])
        self.env.update(UMBRA_ACTIVE='1', UMBRA_TABS='2')
        result = base.umbra('runtime', profile=self.profile, env=self.env)
        self.assertEqual(result.code, 0, result.err)
        self.open('a')
        slow = self.pool.submit(self.call, 'wait', tab='a', ms=800)
        self.pending()
        creations = [self.pool.submit(self.call, 'tab', action='new', name=f'new-{number}') for number in range(3)]
        self.pending(4)
        self.assertTrue(slow.result()['ok'])
        results = [job.result() for job in creations]
        self.assertEqual(sum(result['ok'] for result in results), 1, results)
        self.assertLessEqual(len(self.ok('tabs')['tabs']), 2)

    def test_active_limit_bounds_distinct_tabs(self):
        for number in range(8): self.open(f't{number}')
        jobs = [self.pool.submit(self.call, 'wait', tab=f't{number}', ms=600) for number in range(8)]
        self.pending(8)
        status = self.ok('runtime')
        self.assertEqual(status['active'], 4)
        self.assertEqual(status['queued'], 4)
        self.assertTrue(all(job.result()['ok'] for job in jobs))
        self.assertLessEqual(self.ok('runtime')['peakActive'], 4)

    def test_parent_cancellation_stops_running_child_and_new_children(self):
        self.open('a'); self.open('b')
        parent = self.pool.submit(self.call, 'wait', tab='a', ms=10000, request='parent', agent='alice', task='x', timeout=15)
        self.pending()
        child = self.pool.submit(self.call, 'wait', tab='b', js='false', request='child', agent='alice', task='x', _parent='parent', timeout=15)
        self.pending(2)
        survivor = self.pool.submit(self.call, 'eval', tab='a', request='survivor', script='await new Promise(r=>setTimeout(r,100)); return 7')
        self.pending(3)
        self.assertFalse(self.call('eval', tab='b', _parent='parent', agent='bob', script='return 1')['ok'])
        self.ok('cancel', id='parent', agent='alice', task='x')
        self.assertFalse(parent.result()['ok'])
        self.assertFalse(child.result()['ok'])
        self.assertEqual(survivor.result()['result']['value'], 7)
        self.assertFalse(self.call('eval', tab='b', _parent='parent', agent='alice', task='x', script='return 1')['ok'])
        self.assertFalse(self.call('runtime', _parent='parent', agent='alice', task='x', reclaim=True)['ok'])
        self.assertEqual(self.ok('runtime')['active'], 0)

    def test_connection_admission_is_bounded(self):
        connections = []
        rejected = 0
        try:
            for _ in range(45):
                connection = socket.socket(socket.AF_UNIX)
                connection.settimeout(3)
                try:
                    connection.connect(self.path)
                    connections.append(connection)
                except ConnectionRefusedError:
                    connection.close()
                    rejected += 1
                time.sleep(.005)
            for connection in connections:
                try:
                    connection.sendall(b'{"command":"runtime"}\n')
                    result = json.loads(connection.makefile('rb').readline())
                    rejected += not result.get('ok', False)
                    if result.get('ok'):
                        self.assertLessEqual(result['result']['connections'], 40)
                except (BrokenPipeError, ConnectionResetError):
                    rejected += 1
            self.assertGreaterEqual(rejected, 5)
        finally:
            for connection in connections: connection.close()
        self.ok('runtime')

    def test_request_id_and_deadline_validation(self):
        self.open()
        waiting = self.pool.submit(self.call, 'wait', tab='a', ms=200, request='unique')
        self.pending()
        self.assertFalse(self.call('eval', tab='a', request='unique', script='return 1')['ok'])
        self.assertFalse(self.call('eval', tab='a', deadline=time.time()-1, script='return 1')['ok'])
        self.assertFalse(self.call('eval', tab='a', _parent='missing', script='return 1')['ok'])
        waiting.result()


if __name__ == '__main__':
    unittest.main()
