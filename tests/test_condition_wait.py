"""General condition waits react to page events and clean up within their request."""
import concurrent.futures
import json
import socket
import statistics
import sys
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_umbra as base
from server import Server


class ConditionWaitTests(unittest.TestCase):
    def setUp(self):
        self.profile = base.new_profile()
        self.server = Server()
        self.server.page('/page', '<title>Conditions</title><p id=ready>Waiting</p><input id=field>')
        self.server.page('/final', '<title>Final</title><p id=ready>Ready</p>')
        self.server.page('/csp', '<title>CSP</title><p id=ready>Waiting</p><input id=field>',
                         headers=[('Content-Security-Policy', "default-src 'none'; script-src 'none'")])
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=3)
        result = base.umbra('open', self.server.url('/page'), '--tab', 'condition', profile=self.profile)
        self.assertEqual(result.code, 0, result.err)
        self.path = str(base.LOCKS / f'daemon-{self.profile}.sock')

    def tearDown(self):
        base.umbra('stop', profile=self.profile)
        self.pool.shutdown(wait=True)
        self.server.close()
        base.stop_daemons([self.profile])

    def call(self, command, **args):
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(12)
            connection.connect(self.path)
            connection.sendall((json.dumps({'id':1,'command':command,'args':args})+'\n').encode())
            raw = connection.makefile('rb').readline()
            self.assertTrue(raw, 'daemon closed without reply')
            return json.loads(raw)

    def ok(self, command, **args):
        result = self.call(command, **args)
        self.assertTrue(result.get('ok'), result)
        return result['result']

    def evaluate(self, script):
        return self.ok('eval', tab='condition', script=script)['value']

    def arm(self, delay, change):
        self.evaluate(f'''window.checks=0;window.changedAt=0;window.matchedAt=0;
document.querySelector('#ready').textContent='Waiting';document.querySelector('#field').value='';
window.probe=()=>{{if(++window.checks===1)setTimeout(()=>{{{change};window.changedAt=performance.now()}},{delay});
const ready=document.querySelector('#ready').textContent==='Ready'||document.querySelector('#field').value==='Ready';
if(ready)window.matchedAt=performance.now();return ready}};return true''')

    def test_dom_and_input_signals_beat_the_polling_interval(self):
        lags = []
        for delay in [10,20,30,40,50]:
            self.arm(delay, "document.querySelector('#ready').textContent='Ready'")
            self.ok('wait', tab='condition', js='window.probe()', timeout=2)
            lags.append(self.evaluate('return window.matchedAt-window.changedAt'))
        self.assertLess(statistics.median(lags), 40, f'DOM change to predicate match milliseconds: {lags}')
        self.arm(20, "const field=document.querySelector('#field');field.value='Ready';field.dispatchEvent(new Event('input',{bubbles:true}))")
        self.ok('wait', tab='condition', js='window.probe()', timeout=2)
        self.assertLess(self.evaluate('return window.matchedAt-window.changedAt'), 50)

    def test_global_and_time_conditions_keep_the_fallback(self):
        self.evaluate('window.readyGlobal=false;setTimeout(()=>window.readyGlobal=true,25);window.future=Date.now()+130;return true')
        self.ok('wait', tab='condition', js='window.readyGlobal && Date.now()>=window.future', timeout=2)
        self.assertTrue(self.evaluate('return window.readyGlobal'))

    def test_async_predicates_do_not_overlap_between_slices(self):
        self.evaluate('window.active=0;window.peak=0;window.started=0;return true')
        condition = '''await (async()=>{window.started++;window.active++;window.peak=Math.max(window.peak,window.active);
try{await new Promise(resolve=>setTimeout(resolve,250));return window.started>=2}finally{window.active--}})()'''
        self.ok('wait', tab='condition', js=condition, timeout=2)
        self.assertEqual(self.evaluate('return [window.started,window.peak,window.active]'), [2,1,0])

    def test_page_boolean_and_global_names_do_not_shadow_the_predicate(self):
        self.evaluate('window.Boolean=()=>{throw Error("page Boolean")};for(const name of ["id","predicate","remaining","timer","deadline","resolve","check"])window[name]=true;return true')
        self.ok('wait', tab='condition', js='id && predicate && remaining && timer && deadline && resolve && check', timeout=.4)

    def test_original_apis_survive_page_overrides(self):
        self.evaluate('''const later=setTimeout.bind(window);window.checks=0;
window.probe=()=>{if(++window.checks===1)later(()=>document.querySelector('#ready').textContent='Ready',20);
return document.querySelector('#ready').textContent==='Ready'};
window.setTimeout=window.clearTimeout=window.Promise=window.Map=window.MutationObserver=window.Math=()=>{throw Error('page override')};
EventTarget.prototype.addEventListener=EventTarget.prototype.removeEventListener=()=>{throw Error('page listener override')};return true''')
        self.ok('wait', tab='condition', js='window.probe()', timeout=2)
        self.assertEqual(self.evaluate("return document.querySelector('#ready').textContent"), 'Ready')

    def test_native_predicate_works_under_restrictive_csp(self):
        self.ok('open', tab='condition', url=self.server.url('/csp'))
        self.arm(20, "document.querySelector('#ready').textContent='Ready'")
        self.ok('wait', tab='condition', js='window.probe()', timeout=2)
        self.assertLess(self.evaluate('return window.matchedAt-window.changedAt'), 50)

    def test_navigation_retries_in_the_current_document(self):
        other = Server()
        other.page('/final', '<title>Final</title>')
        try:
            for target in [self.server.url('/final'),other.url('/final')]:
                self.ok('open', tab='condition', url=self.server.url('/page'))
                self.evaluate('setTimeout(()=>location.replace('+json.dumps(target)+'),40);return true')
                result = self.ok('wait', tab='condition', js='document.title === "Final"', timeout=3)
                self.assertEqual(result['url'], target)
        finally:
            other.close()

    def test_false_throwing_and_malformed_conditions_keep_timeout(self):
        for condition in ['false','document.querySelector("#absent").textContent === "Ready"','document.title ==']:
            started=time.monotonic()
            result=self.call('wait',tab='condition',js=condition,timeout=.3)
            self.assertFalse(result['ok'], result)
            self.assertEqual(result['error']['code'], 2)
            self.assertIn('wait', result['error']['message'])
            self.assertGreaterEqual(time.monotonic()-started,.25)
        self.assertEqual(self.evaluate('return 7'),7)

    def test_self_mutating_throwing_predicate_yields_and_stops(self):
        self.evaluate('window.checks=0;return true')
        result=self.call('wait',tab='condition',timeout=.35,js='(document.body.setAttribute("data-check",String(++window.checks)),(()=>{throw Error("not yet")})())')
        self.assertFalse(result['ok'],result)
        before=self.evaluate('return window.checks')
        self.assertLess(before,200,'predicate evaluation did not yield between mutation signals')
        self.ok('wait',tab='condition',ms=150)
        self.assertEqual(self.evaluate('return window.checks'),before,'finished observer still evaluated the predicate')

    def test_cancelled_and_deadlined_async_waits_release_capacity_and_public_id(self):
        self.server.page('/entered','ok')
        for index in range(20):
            deadline = {'deadline':time.time()+.15} if index % 2 else {}
            waiting=self.pool.submit(self.call,'wait',tab='condition',js=f'(fetch("/entered?i={index}"),await new Promise(()=>{{}}))',
                                     request='reused',agent='alice',task='condition',timeout=25,**deadline)
            until=time.monotonic()+3
            while len(self.server.requests('/entered'))<=index:
                self.assertFalse(waiting.done(), f'wait finished before cancellation: {waiting.result() if waiting.done() else None}')
                self.assertLess(time.monotonic(),until,'predicate did not start')
                time.sleep(.005)
            if not deadline:
                self.ok('cancel',id='reused',agent='alice',task='condition')
            result=waiting.result(timeout=3)
            self.assertFalse(result['ok'], result)
            self.assertIn('deadline' if deadline else 'cancelled', result['error']['message'])
        result=self.ok('wait',tab='condition',js='true',request='reused',agent='alice',task='condition',timeout=1)
        self.assertEqual(result['request'],'reused')
        self.assertEqual(self.ok('tasks')['pending'],[])


if __name__ == '__main__':
    unittest.main()
