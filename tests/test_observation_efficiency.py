"""Lossless baseline-relative metadata deltas through the public CLI."""
import json
import re
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_umbra as base
from server import Server


FIELDS = ('refs', 'url', 'title', 'truncated', 'depthLimited', 'doc')
PAGE = '<title>Observation metadata</title><main><button>First</button><p>Before</p></main>'


class ObservationEfficiencyTests(base.UmbraCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = Server()
        cls.srv.page('/page', PAGE)
        cls.srv.page('/other', PAGE)

    @classmethod
    def tearDownClass(cls):
        cls.srv.close()

    def setUp(self):
        super().setUp()
        self.result('open', self.srv.url('/page'))

    def tearDown(self):
        self.umbra('stop')

    def result(self, *args):
        result = self.umbra(*args, '--json')
        self.assertExit(result, 0)
        return json.loads(result.text)

    def evaluate(self, expression):
        return self.result('eval', expression)

    def reconstruct(self, baseline, delta, *options):
        if delta['mode'] == 'full':
            state = {key: delta[key] for key in FIELDS}
            text = delta['snapshot']
        else:
            state = {key: delta.get(key, baseline[key]) for key in FIELDS}
            lines = baseline['snapshot'].split('\n')
            patch = delta['patch']
            lines[patch['start']:patch['start'] + patch['delete']] = patch['lines']
            text = '\n'.join(lines)
        full = self.result('observe', *options)
        self.assertEqual(state, {key: full[key] for key in FIELDS})
        self.assertEqual(text, full['snapshot'])
        self.assertEqual(re.findall(r'\[ref=([^] ]+)\]', text), re.findall(r'\[ref=([^] ]+)\]', full['snapshot']))
        return {**state, 'snapshot': text, 'baseline': delta['baseline']}

    def test_unchanged_metadata_is_inherited_and_human_text_is_precise(self):
        first = self.result('observe')
        delta = self.result('observe', '--since', first['baseline'])
        self.assertEqual(delta['mode'], 'delta')
        self.assertFalse(delta['changed'])
        self.assertTrue(all(key not in delta for key in FIELDS))
        self.reconstruct(first, delta)
        human = self.umbra('observe', '--since', first['baseline'])
        self.assertExit(human, 0)
        self.assertEqual(human.text.strip(), '(no outline changes)')

    def test_metadata_only_changes_use_the_explicit_baseline(self):
        first = self.result('observe')
        self.evaluate("document.title='Changed title'; history.replaceState(null,'','?state=changed')")
        changed = self.result('observe', '--since', first['baseline'])
        self.assertFalse(changed['changed'])
        self.assertEqual(changed['snapshot'], '(no outline changes)')
        self.assertEqual(changed['title'], 'Changed title')
        self.assertEqual(changed['url'], self.srv.url('/page') + '?state=changed')
        self.assertTrue(all(key not in changed for key in ('refs', 'doc', 'truncated', 'depthLimited')))
        state = self.reconstruct(first, changed)
        unchanged = self.result('observe', '--since', state['baseline'])
        self.assertTrue(all(key not in unchanged for key in FIELDS))
        self.reconstruct(state, unchanged)
        old_again = self.result('observe', '--since', first['baseline'])
        self.assertEqual(old_again['title'], changed['title'])
        self.assertEqual(old_again['url'], changed['url'])
        self.reconstruct(first, old_again)

    def test_changed_ref_count_is_returned(self):
        first = self.result('observe')
        self.evaluate("document.querySelector('main').insertAdjacentHTML('beforeend','<button>Second</button>')")
        delta = self.result('observe', '--since', first['baseline'])
        self.assertEqual(delta['refs'], first['refs'] + 1)
        self.reconstruct(first, delta)

    def test_truncation_and_depth_flags_return_false_transitions(self):
        self.evaluate("document.body.innerHTML='<p>'+('long text '.repeat(100))+'</p>'")
        options = ('--max', '80')
        first = self.result('observe', *options)
        self.assertTrue(first['truncated'])
        self.evaluate("document.body.innerHTML='<p>Short</p>'")
        delta = self.result('observe', '--since', first['baseline'], *options)
        self.assertIs(delta['truncated'], False)
        self.reconstruct(first, delta, *options)
        self.evaluate("document.body.innerHTML='<h1>Heading<span>Nested</span></h1>'")
        options = ('--depth', '0')
        first = self.result('observe', *options)
        self.assertTrue(first['depthLimited'])
        self.evaluate("document.body.innerHTML='<button>Shallow</button>'")
        delta = self.result('observe', '--since', first['baseline'], *options)
        self.assertIs(delta['depthLimited'], False)
        self.assertIs(delta['truncated'], False)
        self.reconstruct(first, delta, *options)

    def test_metadata_counts_toward_the_cache_byte_limit(self):
        self.evaluate("document.title='x'.repeat(90000)")
        first = self.result('observe')
        self.result('observe')
        self.result('observe')
        reset = self.result('observe', '--since', first['baseline'])
        self.assertEqual(reset['mode'], 'full')
        self.assertEqual(reset['reset'], 'baseline-unavailable')
        self.assertTrue(all(key in reset for key in FIELDS))
        self.reconstruct(None, reset)

    def test_full_resets_and_legacy_snapshot_stay_independent(self):
        first = self.result('observe')
        reset = self.result('observe', '--since', first['baseline'], '--interactive')
        self.assertEqual(reset['reset'], 'options-changed')
        self.reconstruct(None, reset, '--interactive')
        self.result('open', self.srv.url('/other'))
        reset = self.result('observe', '--since', first['baseline'])
        self.assertEqual(reset['reset'], 'baseline-unavailable')
        self.reconstruct(None, reset)
        for _ in range(2):
            legacy = self.result('snapshot')
            self.assertTrue(all(key in legacy for key in FIELDS if key != 'doc'))
            self.assertEqual(legacy['snapshot'], reset['snapshot'])


if __name__ == '__main__':
    unittest.main()
