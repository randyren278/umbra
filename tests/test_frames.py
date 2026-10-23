"""Cross-origin native frame evaluation/input and canvas coordinate input on localhost."""
import json
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_umbra as base
from server import Server


class FrameTests(base.UmbraCase):
    @classmethod
    def setUpClass(cls):
        cls.outer = Server()
        cls.inner = Server()
        cls.outer.page('/main', f'''<!doctype html><title>Frame parent</title>
<style>body{{margin:30px}} iframe{{border:3px solid black;width:700px;height:450px}}</style>
<iframe id=child src="{cls.inner.url('/child')}"></iframe><br><canvas id=canvas width=200 height=80></canvas>
<script>document.querySelector('#canvas').onclick=e=>window.canvasClick={{trusted:e.isTrusted,x:e.offsetX,y:e.offsetY}};</script>''')
        cls.inner.page('/child', f'''<!doctype html><title>Cross origin child</title><style>body{{margin:20px}} iframe{{width:450px;height:220px;border:5px solid black}}</style>
<label>Name<input id=name value=Before oninput="window.inputTrusted=event.isTrusted"></label>
<input id=secret type=password value=never-return-this><br>
<iframe src="{cls.outer.url('/grand')}"></iframe>''')
        cls.outer.page('/grand', '''<!doctype html><title>Nested grandchild</title>
<button id=button onclick="window.clicked=event.isTrusted">Grandchild button</button>
<label>Ready<input id=ready type=checkbox></label><p id=status>Before</p>
<table id=table><tr><td>Nested data</td></tr></table>''')
        cls.inner.page('/other', '<title>Replacement</title><button>Replacement</button>')

    @classmethod
    def tearDownClass(cls):
        cls.outer.close()
        cls.inner.close()

    def setUp(self):
        super().setUp()
        self.assertExit(self.umbra('open', self.outer.url('/main')), 0)

    def tearDown(self):
        self.umbra('stop')

    def result(self, *args):
        result = self.umbra(*args, '--json')
        self.assertExit(result, 0)
        return json.loads(result.text)

    def frame_id(self, url):
        frames = self.result('frames')['frames']
        self.assertGreaterEqual(len(frames), 3)
        return next(f['id'] for f in frames if f['url'] == url)

    def frame(self, frame_id, operation, **args):
        return self.result('frame', frame_id, operation, '--args', json.dumps(args))

    def test_native_cross_origin_snapshot_extraction_and_sop(self):
        child = self.frame_id(self.inner.url('/child'))
        grand = self.frame_id(self.outer.url('/grand'))
        self.assertIn('Name', self.frame(child, 'snapshot')['snapshot'])
        self.assertIn('Grandchild button', self.frame(grand, 'snapshot')['snapshot'])
        extracted = self.frame(grand, 'extract', target='#table', kind='table')
        self.assertEqual(extracted['data'][0]['cells'][0]['text'], 'Nested data')
        sop = self.result('eval', "return (() => {try {return !!document.querySelector('iframe').contentWindow.document.body} catch(e) {return e.name}})()")
        self.assertEqual(sop['value'], 'SecurityError')

    def test_trusted_click_and_check_in_nested_cross_origin_frame(self):
        grand = self.frame_id(self.outer.url('/grand'))
        click = self.frame(grand, 'click', target='#button')
        self.assertTrue(self.frame(grand, 'eval', script='return window.clicked')['value'])
        self.assertGreater(click['x'], 30)
        checked = self.frame(grand, 'check', target='#ready')
        self.assertTrue(checked['checked'])
        unchecked = self.frame(grand, 'uncheck', target='#ready')
        self.assertFalse(unchecked['checked'])

    def test_trusted_fill_and_password_redaction(self):
        child = self.frame_id(self.inner.url('/child'))
        result = self.frame(child, 'fill', target='#name', value='After')
        self.assertEqual(result['value'], 'After')
        self.assertTrue(result['trusted'])
        self.assertTrue(self.frame(child, 'eval', script='return window.inputTrusted')['value'])
        secret = self.frame(child, 'fill', target='#secret', value='replacement-secret')
        self.assertNotIn('never-return-this', json.dumps(secret))
        self.assertNotIn('replacement-secret', json.dumps(secret))

    def test_frame_watch_and_delta(self):
        grand = self.frame_id(self.outer.url('/grand'))
        first = self.frame(grand, 'observe')
        unchanged = self.frame(grand, 'observe', since=first['baseline'])
        self.assertFalse(unchanged['changed'])
        self.frame(grand, 'eval', script="setTimeout(()=>document.querySelector('#status').textContent='Later',600); true")
        watched = self.frame(grand, 'watch', target='#status', state='text', equals='Later', duration=2)
        self.assertTrue(watched['matched'])

    def test_transformed_frame_and_unknown_operation_fail_explicitly(self):
        child = self.frame_id(self.inner.url('/child'))
        self.result('eval', "document.querySelector('iframe').style.transform='translateX(1px)'")
        result = self.umbra('frame', child, 'fill', '--args', json.dumps({'target': '#name', 'value': 'After'}))
        self.assertExit(result, 2)
        self.assertIn('geometry', result.err)
        self.assertEqual(self.frame(child, 'eval', script="return document.querySelector('#name').value")['value'], 'Before')
        self.assertExit(self.umbra('frame', child, 'unsupported'), 64)

    def test_ancestor_overlay_refuses_native_click(self):
        grand = self.frame_id(self.outer.url('/grand'))
        self.result('eval', "const overlay=document.createElement('div'); overlay.style='position:fixed;inset:0;background:transparent;z-index:99999';document.body.append(overlay)")
        result = self.umbra('frame', grand, 'click', '--args', json.dumps({'target': '#button'}))
        self.assertExit(result, 2)
        self.assertIn('covered', result.err)
        self.assertFalse(self.frame(grand, 'eval', script='return window.clicked === true')['value'])

    def test_frame_navigation_invalidates_old_handle(self):
        child = self.frame_id(self.inner.url('/child'))
        self.frame(child, 'eval', script='location.href=' + json.dumps(self.inner.url('/other')))
        self.inner.wait_for('/other')
        self.result('wait', '--ms', '200')
        frames = self.result('frames')['frames']
        replacement = [f for f in frames if f['url'] == self.inner.url('/other')]
        self.assertEqual(len(replacement), 1)
        self.assertNotEqual(replacement[0]['id'], child)
        self.assertExit(self.umbra('frame', child, 'snapshot'), 64)

    def test_popup_frames_belong_to_popup_tab(self):
        before = self.result('frames')['frames']
        self.result('eval', 'window.open(' + json.dumps(self.inner.url('/other')) + ')')
        tabs = self.result('tabs')['tabs']
        self.assertEqual(len(tabs), 2)
        popup = next(t for t in tabs if t['url'] == self.inner.url('/other'))
        frames = self.result('frames', '--tab', popup['tab'])['frames']
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0]['url'], self.inner.url('/other'))
        parent = next(t for t in tabs if t['url'] == self.outer.url('/main'))
        after = self.result('frames', '--tab', parent['tab'])['frames']
        self.assertEqual({f['id'] for f in before}, {f['id'] for f in after})

    def test_canvas_pointer_trusted_and_validated(self):
        box = self.result('eval', "return (() => {const r=document.querySelector('#canvas').getBoundingClientRect();return {x:r.x+50,y:r.y+30}})()")['value']
        self.result('pointer', 'click', '--x', str(box['x']), '--y', str(box['y']))
        click = self.result('eval', 'return window.canvasClick')['value']
        self.assertTrue(click['trusted'])
        self.assertAlmostEqual(click['x'], 50, delta=2)
        self.assertAlmostEqual(click['y'], 30, delta=2)
        self.assertExit(self.umbra('pointer', 'click', '--x', '-1', '--y', '0'), 64)


if __name__ == '__main__':
    unittest.main()
