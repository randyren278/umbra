"""Public CLI tests for bounded batches, workflow recovery and optional operations."""
import concurrent.futures
import importlib.util
import json
import os
import stat
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_umbra as base
from server import Server


spec = importlib.util.spec_from_file_location('execution_sdk', Path(__file__).resolve().parents[1] / 'sdk/python/umbra.py')
sdk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sdk)


class ExecutionTests(base.UmbraCase):
    @classmethod
    def setUpClass(cls):
        cls.server = Server()
        cls.server.page('/page', '<title>Runtime fixture</title><button id=b onclick="window.count=(window.count||0)+1">Add</button><input id=name><p id=done>Ready</p>')
        cls.server.page('/public', '<title>Public</title><p>No script needed</p>')
        cls.server.page('/resource', '<title>Resource</title><img id=ready src=/image.svg>')
        cls.server.route('/image.svg')(lambda h: h.reply(200, '<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2"/>', 'image/svg+xml'))

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def setUp(self):
        super().setUp()
        self.assertExit(self.umbra('open', self.server.url('/page'), '--tab', 'work'), 0)
        self.folder = base.TMP / self.profile
        self.folder.mkdir()

    def tearDown(self):
        self.umbra('record', 'stop', '--tab', 'work')
        self.umbra('inspector', '--stop')
        self.umbra('stop')

    def result(self, *args, **kwargs):
        done = self.umbra(*args, '--json', **kwargs)
        self.assertExit(done, 0)
        return json.loads(done.text)

    def evaluate(self, script):
        return self.result('eval', script, '--tab', 'work')['value']

    def batch(self, rows, **kwargs):
        return self.result('batch', json.dumps(rows), **kwargs)

    def definition(self, rows):
        file = self.folder / 'workflow.json'
        file.write_text(json.dumps({'steps': rows}))
        return file, self.folder / 'checkpoint.json'

    def test_batch_preserves_order_values_and_stops_on_first_failure(self):
        result = self.batch([
            {'command': 'fill', 'args': {'tab': 'work', 'target': '#name', 'value': 'Ada'}},
            {'command': 'click', 'args': {'tab': 'work', 'target': '#b'}, 'after': {'js': 'window.count===1'}},
            {'command': 'eval', 'args': {'tab': 'work', 'script': 'return document.querySelector("#name").value'}},
        ])
        self.assertTrue(result['completed'])
        self.assertEqual(result['results'][2]['result']['value'], 'Ada')
        failed = self.umbra('batch', json.dumps([
            {'command': 'eval', 'args': {'tab': 'work', 'script': 'throw Error("stop here")'}},
            {'command': 'click', 'args': {'tab': 'work', 'target': '#b'}},
        ]))
        self.assertNotEqual(failed.code, 0)
        self.assertEqual(self.evaluate('return window.count'), 1)

    def failure_steps(self, stage):
        second = {'command': 'click', 'args': {'tab': 'work', 'target': '#b'}}
        if stage == 'command':
            second = {'command': 'eval', 'args': {'tab': 'work', 'script': 'throw Error("stop here")'}}
        elif stage == 'precondition':
            second['before'] = {'js': 'false'}
        else:
            second['after'] = {'js': 'false'}
        return [{'command': 'eval', 'args': {'tab': 'work', 'script': 'return 17'}}, second,
                {'command': 'eval', 'args': {'tab': 'work', 'script': 'window.skipped=true; return 3'}}]

    def check_failure(self, packet, stage):
        self.assertEqual(packet['code'], 2)
        self.assertFalse(packet['completed'])
        self.assertEqual(packet['count'], 3)
        self.assertEqual([(r['step'], r['result']['value']) for r in packet['results']], [(1, 17)])
        self.assertEqual(packet['failedStep'], {'step': 2, 'command': 'eval' if stage == 'command' else 'click',
                                              'stage': stage, 'dispatched': stage != 'precondition',
                                              'phase': 'ready' if stage == 'precondition' else 'pending'})
        self.assertEqual(self.evaluate('return typeof window.skipped'), 'undefined')

    def test_batch_cli_json_failure_preserves_completed_receipts(self):
        done = self.umbra('batch', json.dumps(self.failure_steps('command')), '--json')
        self.assertExit(done, 2)
        self.check_failure(json.loads(done.err)['error'], 'command')

    def test_batch_sdk_failure_reports_precondition_not_dispatched_and_postcondition_uncertain(self):
        with sdk.Umbra(self.profile) as client:
            for stage in ['precondition', 'postcondition']:
                with self.assertRaises(sdk.UmbraError) as failed:
                    client.call('batch', {'steps': json.dumps(self.failure_steps(stage))})
                self.check_failure(failed.exception.details, stage)
        self.assertEqual(self.evaluate('return window.count'), 1)

    def test_batch_mcp_failure_keeps_error_status_and_structured_partial_results(self):
        request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
            'name': 'batch', 'arguments': {'steps': json.dumps(self.failure_steps('command'))}}}
        done = self.umbra('mcp', input=json.dumps(request) + '\n')
        self.assertExit(done, 0)
        result = json.loads(done.text)['result']
        self.assertTrue(result['isError'])
        self.check_failure(result['structuredContent'], 'command')

    def test_batch_parent_deadline_preserves_completed_results(self):
        rows = self.failure_steps('command')
        rows[1]['args']['script'] = 'await new Promise(r=>setTimeout(r,2000)); return 9'
        with sdk.Umbra(self.profile) as client:
            with self.assertRaises(sdk.UmbraError) as failed:
                client.call('batch', {'steps': json.dumps(rows), 'timeout': .3})
        self.check_failure(failed.exception.details, 'command')

    def test_batch_parent_cancellation_preserves_completed_results(self):
        rows = self.failure_steps('command')
        rows[1]['args']['script'] = 'await new Promise(r=>setTimeout(r,8000)); return 9'
        with sdk.Umbra(self.profile) as client, concurrent.futures.ThreadPoolExecutor() as pool:
            running = pool.submit(client.call, 'batch', {'steps': json.dumps(rows)}, request_id='cancelled-batch')
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                tasks = self.result('tasks')
                if any(row['command'] == 'eval' for row in tasks['pending']) and any(row['command'] == 'eval' for row in tasks['history']):
                    break
                time.sleep(.01)
            else:
                self.fail('second batch command did not start')
            self.assertTrue(client.cancel('cancelled-batch')['wasRunning'])
            with self.assertRaises(sdk.UmbraError) as failed:
                running.result(timeout=5)
        self.check_failure(failed.exception.details, 'command')

    def test_batch_failure_output_remains_bounded_after_large_completed_results(self):
        with sdk.Umbra(self.profile) as client:
            rows = [{'command': 'eval', 'args': {'tab': 'work', 'script': 'return "x".repeat(1100000)'}}] * 2
            with self.assertRaises(sdk.UmbraError) as failed:
                client.call('batch', {'steps': json.dumps(rows)})
            packet = failed.exception.details
        self.assertEqual(packet['code'], 4)
        self.assertEqual(packet['results'][0]['step'], 1)
        self.assertEqual(len(packet['results'][0]['result']['value']), 1100000)
        self.assertIn('resultOmitted', packet['results'][1])
        self.assertEqual(packet['failedStep']['stage'], 'output')
        self.assertTrue(packet['failedStep']['dispatched'])
        self.assertLess(len(json.dumps(packet)), 4194304)

    def test_workflow_output_cap_preserves_verified_checkpoint_without_repeating(self):
        file, checkpoint = self.definition([
            {'command': 'eval', 'args': {'tab': 'work', 'script': 'return "x".repeat(1100000)'}},
            {'command': 'eval', 'args': {'tab': 'work', 'script': 'window.count=(window.count||0)+1; return "x".repeat(1100000)'}},
            {'command': 'eval', 'args': {'tab': 'work', 'script': 'window.finished=true; return true'}},
        ])
        with sdk.Umbra(self.profile) as client:
            with self.assertRaises(sdk.UmbraError) as failed:
                client.call('workflow', {'file': str(file), 'checkpoint': str(checkpoint)})
        self.assertEqual(failed.exception.details['failedStep']['step'], 2)
        state = json.loads(checkpoint.read_text())
        self.assertEqual((state['index'], state['phase']), (2, 'ready'))
        self.assertTrue(self.result('workflow', str(file), '--checkpoint', str(checkpoint), '--resume')['completed'])
        self.assertEqual(self.evaluate('return window.count'), 1)
        self.assertTrue(self.evaluate('return window.finished'))

    def test_batch_validates_every_step_before_dispatch_and_rejects_recursion(self):
        for bad in [{'command': 'stop'}, {'command': 'eval', 'args': {'script': 3}}, {'command': 'not-a-command'}]:
            done = self.umbra('batch', json.dumps([{'command': 'click', 'args': {'tab': 'work', 'target': '#b'}}, bad]))
            self.assertExit(done, 64)
            self.assertEqual(self.evaluate('return typeof window.count'), 'undefined')

    def test_workflow_substitutes_variables_and_complete_resume_never_repeats(self):
        file, checkpoint = self.definition([
            {'command': 'fill', 'args': {'tab': 'work', 'target': '#name', 'value': '${name}'}},
            {'command': 'click', 'args': {'tab': 'work', 'target': '#b'}, 'after': {'js': 'window.count===1'}},
        ])
        args = ('workflow', str(file), '--checkpoint', str(checkpoint), '--vars', '{"name":"private-test-value"}')
        self.assertTrue(self.result(*args)['completed'])
        self.assertEqual(self.evaluate('return window.count'), 1)
        self.assertTrue(self.result(*args, '--resume')['completed'])
        self.assertEqual(self.evaluate('return window.count'), 1)
        self.assertNotIn('private-test-value', checkpoint.read_text())
        self.assertEqual(stat.S_IMODE(checkpoint.stat().st_mode), 0o600)

    def test_pending_side_effect_requires_resolution_then_verifies_without_repeat(self):
        file, checkpoint = self.definition([
            {'command': 'click', 'args': {'tab': 'work', 'target': '#b'}, 'after': {'js': 'window.allow===true'}},
        ])
        args = ('workflow', str(file), '--checkpoint', str(checkpoint))
        failed = self.umbra(*args)
        self.assertNotEqual(failed.code, 0)
        self.assertEqual(json.loads(checkpoint.read_text())['phase'], 'pending')
        self.assertEqual(self.evaluate('return window.count'), 1)
        self.assertExit(self.umbra(*args, '--resume'), 4)
        self.evaluate('window.allow=true; return true')
        self.assertTrue(self.result(*args, '--resume', '--resolve', 'verified')['completed'])
        self.assertEqual(self.evaluate('return window.count'), 1)

    def test_resumed_resolution_failure_identifies_pending_step_and_prior_dispatch(self):
        file, checkpoint = self.definition([
            {'command': 'click', 'args': {'tab': 'work', 'target': '#b'}},
            {'command': 'click', 'args': {'tab': 'work', 'target': '#b'}, 'after': {'js': 'window.allow===true'}},
        ])
        args = {'file': str(file), 'checkpoint': str(checkpoint)}
        with sdk.Umbra(self.profile) as client:
            with self.assertRaises(sdk.UmbraError):
                client.call('workflow', args)
            with self.assertRaises(sdk.UmbraError) as failed:
                client.call('workflow', {**args, 'resume': True, 'resolve': 'verified'})
        self.assertEqual(failed.exception.details['failedStep'], {'step': 2, 'command': 'click', 'stage': 'resolution',
                                                                 'dispatched': True, 'phase': 'pending'})
        self.assertEqual(self.evaluate('return window.count'), 2)
        self.evaluate('window.allow=true; return true')
        self.assertTrue(self.result('workflow', str(file), '--checkpoint', str(checkpoint), '--resume', '--resolve', 'verified')['completed'])
        self.assertEqual(self.evaluate('return window.count'), 2)

    def test_checkpoint_rejects_definition_or_variable_changes(self):
        file, checkpoint = self.definition([{'command': 'eval', 'args': {'tab': 'work', 'script': 'return "${value}"'}}])
        args = ('workflow', str(file), '--checkpoint', str(checkpoint))
        self.result(*args, '--vars', '{"value":"one"}')
        self.assertExit(self.umbra(*args, '--resume', '--vars', '{"value":"two"}'), 64)

    def test_parallel_workflow_cannot_share_checkpoint(self):
        file, checkpoint = self.definition([{'command': 'eval', 'args': {'tab': 'work', 'script': 'await new Promise(r=>setTimeout(r,1000)); return 1'}}])
        args = ('workflow', str(file), '--checkpoint', str(checkpoint))
        with concurrent.futures.ThreadPoolExecutor() as pool:
            first = pool.submit(self.umbra, *args)
            deadline = time.monotonic() + 5
            while not checkpoint.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            other = self.umbra(*args, '--resume')
            self.assertExit(other, 1)
            self.assertExit(first.result(), 0)

    def test_http_is_bounded_scriptless_and_creates_no_tabs(self):
        before = len(self.result('tabs')['tabs'])
        result = self.result('http', self.server.url('/public'))
        self.assertIn('No script needed', result['body'])
        self.assertFalse(result['authenticated'])
        self.assertFalse(result['javascript'])
        self.assertEqual(len(self.result('tabs')['tabs']), before)
        self.assertExit(self.umbra('http', self.server.url('/public'), '--max', '8'), 4)

    def test_resources_apply_restore_and_fallback(self):
        self.assertEqual(self.result('resources', 'lean', '--tab', 'work')['blocked'], ['fonts', 'images', 'media', 'trackers'])
        self.assertEqual(self.result('resources', 'visual', '--tab', 'work')['blocked'], [])
        self.result('open', self.server.url('/resource'), '--tab', 'work')
        result = self.result('resources', 'lean', '--reload', '--until', 'document.querySelector("#ready").naturalWidth>0', '--tab', 'work')
        self.assertTrue(result['fallback'])
        self.assertEqual(result['blocked'], [])

    def test_inspector_is_loopback_token_protected_and_on_demand(self):
        url = self.result('inspector')['url']
        address, token = url.split('#')
        self.assertTrue(address.startswith('http://127.0.0.1:'))
        self.assertIn(b'Your agents', urllib.request.urlopen(address).read())
        with self.assertRaises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(address + 'state')
        self.assertEqual(denied.exception.code, 403)
        request = urllib.request.Request(address + 'state', headers={'Authorization': 'Bearer ' + token})
        state = json.load(urllib.request.urlopen(request))
        self.assertEqual(state['tabs'], [{'name': 'work'}])
        self.assertNotIn(self.server.url('/page'), json.dumps(state))
        frame = urllib.request.Request(address + 'frame', data=b'{"tab":"work"}', headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        self.assertEqual(json.load(urllib.request.urlopen(frame))['mime'], 'image/png')

    def test_recording_stops_at_duration_and_bundle_excludes_content(self):
        directory = self.folder / 'frames'
        self.result('record', 'start', '--path', str(directory), '--seconds', '1', '--fps', '2', '--tab', 'work')
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            status = self.result('record', 'status', '--tab', 'work')
            if status['finished']:
                break
            time.sleep(.1)
        self.assertTrue(status['finished'])
        manifest = json.loads((directory / 'recording.json').read_text())
        self.assertGreaterEqual(len(manifest['frames']), 1)
        self.assertLessEqual(len(manifest['frames']), 3)
        self.evaluate('window.secret="bundle-must-not-include-this"; return true')
        bundle = self.folder / 'diagnose.json'
        self.result('diagnose', str(bundle))
        self.assertNotIn('bundle-must-not-include-this', bundle.read_text())
        self.assertNotIn(self.server.url('/page'), bundle.read_text())
        self.assertEqual(stat.S_IMODE(bundle.stat().st_mode), 0o600)


if __name__ == '__main__':
    unittest.main()
