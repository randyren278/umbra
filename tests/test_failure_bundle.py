"""Opt-in failure evidence is bounded and redacted before snapshots leave WebKit."""
import json
import stat
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_umbra as base
from server import Server


class FailureBundleTests(base.UmbraCase):
    password = 'password-fixture-847392'
    token = 'token-fixture-Zx293jk-138472'
    text = 'text-fixture-privacy-' + 'LongPrivateValue' * 20
    shadow = 'shadow-fixture-private-3874'
    frame = 'frame-fixture-private-2178'

    @classmethod
    def setUpClass(cls):
        cls.web = Server()
        cls.web.page('/frame', f'<label>Frame field<input value="{cls.frame}"></label>')
        cls.web.page('/', f'''<title>Private title {cls.password}</title>
            <h1>Failure fixture</h1><p>Visible explanation</p>
            <label>Password<input type=password value="{cls.password}"></label>
            <label>Access token<input value="{cls.token}"></label>
            <label>Ordinary text<textarea>{cls.text}</textarea></label>
            <input type=hidden value="hidden-token-private-8127">
            <p>{cls.password} {cls.token} {cls.text}</p>
            <p>Bearer bearer-fixture-private-1927</p>
            <a href="/private?token=url-fixture-private-9362">Continue</a>
            <div id=host></div><iframe src=/frame></iframe>
            <script>host.attachShadow({{mode:'open'}}).innerHTML='<label>Shadow field<input value="{cls.shadow}"></label>';</script>''')

    @classmethod
    def tearDownClass(cls):
        cls.web.close()

    def setUp(self):
        super().setUp()
        self.owner = ('--agent', 'bundle-agent', '--task', 'bundle-task')
        self.assertExit(self.umbra('open', self.web.url('/') + '?query=private-url', '--tab', 'private', env={'UMBRA_ACTIVE': '1'}), 0)
        self.assertExit(self.umbra('lease', '--tab', 'private', *self.owner), 0)
        self.assertExit(self.umbra('eval', 'throw Error("error-contains-private-fixture-2137")', '--tab', 'private', *self.owner), 2)

    def tearDown(self):
        self.umbra('stop')

    def bundle(self, path):
        raw = path.read_text()
        value = json.loads(raw)
        self.assertLessEqual(path.stat().st_size, 524288)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        for secret in [self.password, self.token, self.text[:100], self.shadow, self.frame,
                       'hidden-token-private-8127', 'bearer-fixture-private-1927',
                       'url-fixture-private-9362', 'error-contains-private-fixture-2137', self.web.url('/')]:
            self.assertNotIn(secret, raw)
        self.assertTrue(any(row['command'] == 'eval' and row['errorCode'] == 2 for row in value['failures']))
        return value

    def test_default_bundle_is_metadata_only(self):
        path = self.tmp / 'metadata.json'
        self.assertExit(self.umbra('diagnose', str(path)), 0)
        value = self.bundle(path)
        self.assertEqual(value['content'], 'metadata only')
        self.assertNotIn('observation', value)
        self.assertNotIn('Failure fixture', path.read_text())

    def test_cli_redacted_observation_preserves_useful_refs_and_labels_at_active_limit_one(self):
        path = self.tmp / 'observed.json'
        self.assertExit(self.umbra('diagnose', str(path), '--observations', '--target', 'private', *self.owner), 0)
        value = self.bundle(path)
        self.assertEqual(value['content'], 'redacted observation')
        text = value['observation']['text']
        for useful in ['Failure fixture', 'Visible explanation', 'Password', 'Access token', 'Ordinary text', 'Shadow field', 'Frame field', '[ref=e', '[redacted]']:
            self.assertIn(useful, text)
        self.assertLessEqual(len(text.encode()), 16384)
        self.assertTrue(value['observation']['redacted'])
        self.assertNotIn('url', value['observation'])

    def test_mcp_opt_in_uses_same_redaction_and_byte_budget(self):
        path = self.tmp / 'mcp-observed.json'
        request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'diagnose', 'arguments': {
            'path': str(path), 'target': 'private', 'observations': True, 'agent': 'bundle-agent', 'task': 'bundle-task', 'max': 128}}}
        done = self.umbra('mcp', input=json.dumps(request) + '\n')
        self.assertExit(done, 0)
        result = json.loads(done.text)['result']
        self.assertFalse(result.get('isError', False))
        value = self.bundle(path)
        self.assertLessEqual(len(value['observation']['text'].encode()), 128)
        self.assertTrue(value['observation']['truncated'])

    def test_observation_requires_explicit_target_and_matching_owner(self):
        for flags in [('--observations',), ('--observations', '--target', 'private'),
                      ('--observations', '--target', 'private', '--agent', 'intruder', '--task', 'other')]:
            path = self.tmp / 'denied.json'
            self.assertExit(self.umbra('diagnose', str(path), *flags), 64)
            self.assertFalse(path.exists())
        self.assertExit(self.umbra('diagnose', str(self.tmp / 'large.json'), '--max', '65537'), 64)


if __name__ == '__main__':
    unittest.main()
