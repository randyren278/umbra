"""Real-WebKit observation/extraction/watch tests on disposable localhost profiles."""
import json
import sys
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_umbra as base
from server import Server


PAGE = '''<!doctype html><title>Observe fixture</title>
<main><h1>Observation</h1><p id=message>Before</p>
<button id=change onclick="document.querySelector('#message').textContent='After'">Change</button>
<form id=form><label>Name<input id=name name=name value=Randy></label>
<label>Age<input name=age type=number value=24></label>
<label>Ready<input name=ready type=checkbox checked></label>
<input name=secret type=password value=never-return-this>
<input name=hidden type=hidden value=hidden-secret></form>
<table id=table><tr><th>Name</th><th>Time</th></tr><tr><td>Randy</td><td>10</td></tr></table>
<ul id=list><li>First</li><li>Second</li><li>Third</li></ul>
<section id=deep aria-label=Nested><div><div><button>Deep</button></div></div></section>
<div id=shadow></div></main>
<script>document.querySelector('#shadow').attachShadow({mode:'open'}).innerHTML='<button>Shadow</button>';</script>'''


class ObserveTests(base.UmbraCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = Server()
        cls.srv.page('/page', PAGE)
        cls.srv.page('/other', '<title>Other</title><button>New document</button>')

    @classmethod
    def tearDownClass(cls):
        cls.srv.close()

    def setUp(self):
        super().setUp()
        self.assertExit(self.umbra('open', self.srv.url('/page')), 0)

    def tearDown(self):
        self.umbra('stop')

    def result(self, *args):
        result = self.umbra(*args, '--json')
        self.assertExit(result, 0)
        return json.loads(result.text)

    def evaluate(self, expression):
        return self.result('eval', expression)

    def test_delta_reconstructs_snapshot_and_nochange(self):
        initial = self.result('observe')
        self.assertEqual(initial['mode'], 'full')
        unchanged = self.result('observe', '--since', initial['baseline'])
        self.assertEqual(unchanged['mode'], 'delta')
        self.assertFalse(unchanged['changed'])
        self.assertEqual(unchanged['patch']['delete'], 0)
        self.result('click', '#change')
        delta = self.result('observe', '--since', initial['baseline'])
        self.assertTrue(delta['changed'])
        patch = delta['patch']
        lines = initial['snapshot'].split('\n')
        lines[patch['start']:patch['start'] + patch['delete']] = patch['lines']
        self.assertEqual('\n'.join(lines), self.result('snapshot')['snapshot'])
        self.assertLess(len(unchanged['snapshot']), len(initial['snapshot']) // 2)

    def test_baselines_invalidate_after_navigation_options_or_eviction(self):
        first = self.result('observe')
        changed = self.result('observe', '--since', first['baseline'], '--scope', '#form')
        self.assertEqual(changed['reset'], 'options-changed')
        for _ in range(8):
            self.result('observe')
        self.assertEqual(self.result('observe', '--since', first['baseline'])['reset'], 'baseline-unavailable')
        self.result('open', self.srv.url('/other'))
        self.assertEqual(self.result('observe', '--since', changed['baseline'])['mode'], 'full')

    def test_budget_depth_and_shadow(self):
        full = self.result('observe')
        self.assertIn('Shadow', full['snapshot'])
        bounded = self.result('observe', '--max', '80')
        self.assertTrue(bounded['truncated'])
        self.assertLess(len(bounded['snapshot']), 250)
        shallow = self.result('snapshot', '--depth', '0')
        self.assertTrue(shallow['depthLimited'])
        self.assertNotIn('Deep', shallow['snapshot'])
        for flag, value in [('max', '0'), ('max', '100001'), ('depth', '-1'), ('depth', '1.5')]:
            self.assertExit(self.umbra('observe', '--' + flag, value), 64)

    def test_table_extraction_has_source_refs_and_limit(self):
        result = self.result('extract', '#table', '--kind', 'table', '--limit', '1')
        self.assertTrue(result['truncated'])
        self.assertEqual([v['text'] for v in result['data'][0]['cells']], ['Name', 'Time'])
        self.assertTrue(result['data'][0]['cells'][0]['header'])
        cell = result['data'][0]['cells'][0]['ref']
        self.assertIn('Name', self.result('get', cell)['name'])
        self.assertEqual(result['source']['url'], self.srv.url('/page'))

    def test_form_types_and_password_redaction(self):
        result = self.result('extract', '#form')
        fields = {f['name']: f for f in result['data']}
        self.assertEqual(fields['name']['value'], 'Randy')
        self.assertEqual(fields['age']['value'], 24)
        self.assertIs(fields['ready']['value'], True)
        self.assertTrue(fields['secret']['redacted'])
        self.assertNotIn('value', fields['secret'])
        self.assertNotIn('hidden', fields)
        self.assertNotIn('never-return-this', json.dumps(result))
        self.assertNotIn('hidden-secret', json.dumps(result))

    def test_list_and_schema_rejection(self):
        schema = {'type': 'array', 'minItems': 3, 'items': {'type': 'object', 'required': ['text', 'ref'], 'properties': {'text': {'type': 'string'}, 'ref': {'type': 'string'}}, 'additionalProperties': False}}
        result = self.result('extract', '#list', '--schema', json.dumps(schema))
        self.assertEqual([r['text'] for r in result['data']], ['First', 'Second', 'Third'])
        for invalid in [{'type': 'array', 'minItems': 4}, {'type': 'object'}, {'items': {'pattern': 'x'}}, False]:
            result = self.umbra('extract', '#list', '--schema', json.dumps(invalid))
            self.assertExit(result, 2)
        self.assertExit(self.umbra('extract', '#list', '--schema', '{bad'), 64)

    def test_watch_events_match_mutation_and_input(self):
        self.evaluate("setTimeout(() => document.querySelector('#message').textContent='Later', 600); true")
        watched = self.result('watch', '#message', '--state', 'text', '--equals', 'Later', '--duration', '2')
        self.assertTrue(watched['matched'])
        self.assertEqual(watched['value'], 'Later')
        self.evaluate("setTimeout(() => {const x=document.querySelector('#name'); x.value='Changed'; x.dispatchEvent(new Event('input',{bubbles:true}));},600); true")
        watched = self.result('watch', '#name', '--state', 'value', '--equals', 'Changed', '--duration', '2')
        self.assertTrue(watched['matched'])

    def test_watch_expiry_and_removed_element(self):
        result = self.result('watch', '#missing', '--duration', '.1')
        self.assertFalse(result['matched'])
        self.assertLess(result['elapsed'], 1000)
        self.evaluate("setTimeout(() => document.querySelector('#message').remove(),600); true")
        result = self.result('watch', '#message', '--state', 'detached', '--duration', '2')
        self.assertTrue(result['matched'])
        self.assertExit(self.umbra('watch', 'body', '--duration', '26'), 64)
        self.assertExit(self.umbra('watch', 'body', '--state', 'text'), 64)

    def test_receipt_observes_fill_and_redacts_password(self):
        filled = self.result('fill', '#name', 'Ren', '--receipt')
        self.assertEqual(filled['receipt']['before']['value'], 'Randy')
        self.assertEqual(filled['receipt']['after']['value'], 'Ren')
        self.assertTrue(filled['receipt']['changed'])
        secret = self.result('fill', '[name=secret]', 'replacement-secret', '--receipt')
        self.assertNotIn('never-return-this', json.dumps(secret['receipt']))
        self.assertNotIn('replacement-secret', json.dumps(secret['receipt']))

    def cancel_running(self, request, *command, delay=.1):
        process = base.spawn(*command, '--request', request, profile=self.profile)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            tasks = self.result('tasks')['pending']
            if any(t['request'] == request and t['state'] == 'running' for t in tasks):
                break
            time.sleep(.01)
        else:
            self.fail('request did not enter running state')
        time.sleep(delay)
        self.result('cancel', request)
        out, err = process.communicate(timeout=3)
        self.assertEqual(process.returncode, 2, (out, err))

    def test_cancelled_delayed_click_never_fires_after_target_appears(self):
        self.evaluate("window.lateClicks=0; setTimeout(()=>{const b=document.createElement('button');b.id='late';b.textContent='Late';b.onclick=()=>window.lateClicks++;document.body.append(b)},700)")
        self.cancel_running('cancel-late-click', 'click', '#late')
        self.result('wait', '--ms', '900')
        self.assertEqual(self.result('eval', 'return window.lateClicks')['value'], 0)
        self.result('click', '#late')
        self.assertEqual(self.result('eval', 'return window.lateClicks')['value'], 1)

    def test_cancelled_fill_cannot_type_into_following_command(self):
        self.evaluate("setTimeout(()=>{const x=document.createElement('input');x.id='latefield';document.body.append(x)},700)")
        self.cancel_running('cancel-late-fill', 'fill', '#latefield', 'CANCELLED')
        began = time.monotonic()
        self.result('fill', '#name', 'Following')
        self.assertLess(time.monotonic() - began, 2, 'cancelled action did not release its queue')
        self.result('wait', '--ms', '900')
        self.assertEqual(self.result('eval', "return document.querySelector('#latefield').value")['value'], '')
        self.assertEqual(self.result('eval', "return document.querySelector('#name').value")['value'], 'Following')

    def test_cancelled_fill_fallback_cannot_emit_late_native_keys(self):
        self.evaluate("window.lateKeys=0;addEventListener('keydown',()=>window.lateKeys++);const field=document.createElement('input');field.id='blurfield';field.onfocus=()=>field.blur();document.body.append(field)")
        self.cancel_running('cancel-fallback-fill', 'fill', '#blurfield', 'CANCELLED', delay=.005)
        keys_after_cancel = self.result('eval', 'return window.lateKeys')['value']
        self.result('fill', '#name', 'Following')
        self.result('wait', '--ms', '350')
        self.assertEqual(self.result('eval', 'return window.lateKeys')['value'], keys_after_cancel)
        self.assertEqual(self.result('eval', "return document.querySelector('#name').value")['value'], 'Following')

    def test_capability_disclosure(self):
        result = self.result('capabilities')
        self.assertTrue(result['sameOriginFrames'])
        self.assertTrue(result['crossOriginDOM'])
        self.assertFalse(result['canvasSemantics'])


if __name__ == '__main__':
    unittest.main()
