"""Native process accounting via the public protocol, on disposable localhost profiles."""
import concurrent.futures
import json
import os
import signal
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_umbra as base
from server import Server


class ProcessMetricsTests(base.UmbraCase):
    def setUp(self):
        super().setUp()
        self.env = {'UMBRA_ACTIVE': '1'}
        self.server = Server()
        self.secret = 'metrics-cookie-sentinel'
        self.title = 'metrics-title-sentinel'
        self.server.route('/private')(lambda h: h.reply(
            body=f'<title>{self.title}</title><p>ready</p>',
            headers=[('Set-Cookie', f'metrics={self.secret}; Path=/')]))
        self.url = self.server.url('/private') + '?private-query-sentinel=1'
        self.result('open', self.url, '--tab', 'one')
        self.path = str(base.LOCKS / f'daemon-{self.profile}.sock')

    def tearDown(self):
        self.umbra('stop')
        self.server.close()

    def result(self, *args, **kwargs):
        kwargs.setdefault('env', self.env)
        result = self.umbra(*args, '--json', **kwargs)
        self.assertExit(result, 0)
        return json.loads(result.out)

    def metrics(self):
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(10)
            connection.connect(self.path)
            self.peer_pid = connection.getsockopt(0, 0x002)  # macOS SOL_LOCAL / LOCAL_PEERPID
            connection.sendall(b'{"id":1,"command":"processes","args":{}}\n')
            reply = json.loads(connection.makefile('rb').readline())
            self.assertTrue(reply.get('ok'), reply)
            return reply['result']

    def test_native_processes_have_unique_totals_and_own_tab_mapping(self):
        self.result('open', self.url, '--tab', 'two')
        metrics = self.result('processes')
        rows = metrics['processes']
        pids = [row['pid'] for row in rows]
        self.assertEqual(len(pids), len(set(pids)), rows)
        self.assertEqual(metrics['processCount'], len(rows))
        self.assertEqual(metrics['physicalFootprintBytes'], sum(row['physicalFootprintBytes'] for row in rows))
        self.assertAlmostEqual(metrics['cpuSeconds'], sum(row['cpuSeconds'] for row in rows))
        daemon = [row for row in rows if row['kind'] == 'daemon']
        self.assertEqual(len(daemon), 1)
        self.assertGreater(daemon[0]['physicalFootprintBytes'], 0)
        self.assertGreater(daemon[0]['cpuSeconds'], 0)
        self.metrics()
        self.assertEqual(daemon[0]['pid'], self.peer_pid)
        mapping = {tab['tab']: tab['pid'] for tab in metrics['tabs']}
        self.assertEqual(set(mapping), {'one', 'two'})
        self.assertTrue(metrics['coverage']['webContentPIDAvailable'], metrics['coverage'])
        for tab, pid in mapping.items():
            self.assertIsInstance(pid, int)
            row = next(row for row in rows if row['pid'] == pid)
            self.assertEqual(row['kind'], 'WebContent')
            self.assertIn(tab, row['tabs'])
            self.assertGreater(row['physicalFootprintBytes'], 0)
            self.assertEqual(row['sharedByTabs'], len(row['tabs']) > 1)
        self.assertEqual(metrics['coverage']['status'], 'partial')
        self.assertTrue(metrics['coverage']['gaps'])
        encoded = json.dumps(metrics)
        for excluded in (self.url, '/private', self.secret, self.title, 'private-query-sentinel'):
            self.assertNotIn(excluded, encoded)

    def test_webcontent_cpu_seconds_match_independent_process_time(self):
        def process_cpu(pid):
            output = subprocess.run(['ps', '-o', 'time=', '-p', str(pid)], check=True,
                                    capture_output=True, text=True, timeout=5).stdout.strip()
            days, separator, clock = output.rpartition('-')
            seconds = sum(float(value) * 60 ** index for index, value in enumerate(reversed((clock if separator else output).split(':'))))
            return seconds + (int(days) * 86400 if separator else 0)

        before = self.metrics()
        pid = next(tab['pid'] for tab in before['tabs'] if tab['tab'] == 'one')
        self.assertIsInstance(pid, int)
        cpu_before = next(row['cpuSeconds'] for row in before['processes'] if row['pid'] == pid)
        reference_before = process_cpu(pid)
        self.result('eval', 'const end=performance.now()+450; let n=0; while(performance.now()<end) n+=Math.sqrt(n+1); return n', '--tab', 'one')
        time.sleep(2.1)  # Expire the process-metrics cache before comparing cumulative deltas.
        after = self.metrics()
        cpu_after = next(row['cpuSeconds'] for row in after['processes'] if row['pid'] == pid)
        reference_delta = process_cpu(pid) - reference_before
        self.assertGreater(reference_delta, .2, 'fixture did not produce enough independently measured CPU work')
        self.assertAlmostEqual(cpu_after - cpu_before, reference_delta, delta=.05,
                               msg='native CPU seconds disagree with ps process time beyond rounding and sampling tolerance')

    def test_cache_expires_and_tab_closure_invalidates_mapping(self):
        first = self.metrics()
        second = self.metrics()
        self.assertLessEqual(second['cacheTTLSeconds'], 2)
        self.assertGreaterEqual(second['sampleAgeSeconds'], first['sampleAgeSeconds'])
        self.assertEqual(second['processes'], first['processes'])
        time.sleep(2.1)
        fresh = self.metrics()
        self.assertLess(fresh['sampleAgeSeconds'], .1)
        self.result('tab', 'close', 'one')
        self.assertEqual(self.metrics()['tabs'], [])

    def test_terminated_webcontent_is_removed_without_retaining_stale_bytes(self):
        original = self.metrics()
        pid = next(tab['pid'] for tab in original['tabs'] if tab['tab'] == 'one')
        self.assertIsInstance(pid, int)
        os.kill(pid, signal.SIGKILL)  # PID came from this disposable profile's own tracked tab.
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            current = self.metrics()
            if pid not in {row['pid'] for row in current['processes']}:
                self.assertEqual(current['physicalFootprintBytes'], sum(row['physicalFootprintBytes'] for row in current['processes']))
                break
            time.sleep(.02)
        else:
            self.fail('terminated WebContent remained in the process table beyond the cache lifetime')

    def test_other_profile_processes_are_excluded(self):
        other = base.new_profile()
        try:
            self.result('open', self.url, '--tab', 'other-profile', profile=other)
            elsewhere = self.result('processes', profile=other)
            own = self.metrics()
            own_pids = {row['pid'] for row in own['processes']}
            self.assertTrue(own_pids.isdisjoint(row['pid'] for row in elsewhere['processes']))
            self.assertEqual([tab['tab'] for tab in own['tabs']], ['one'])
        finally:
            self.umbra('stop', profile=other)

    def test_processes_remains_available_while_execution_is_saturated(self):
        with concurrent.futures.ThreadPoolExecutor() as pool:
            slow = pool.submit(self.result, 'eval', 'await new Promise(r=>setTimeout(r,2000)); return true', '--tab', 'one')
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if self.result('tasks')['pending']:
                    break
            else:
                self.fail('evaluation never became active')
            metrics = self.metrics()
            self.assertGreater(metrics['processCount'], 0)
            self.assertFalse(slow.done(), 'process inspection queued behind saturated execution')
            self.assertTrue(slow.result()['value'])

    def test_labels_are_bounded_and_urls_removed(self):
        name = 'http://127.0.0.1/private-label-sentinel'
        self.result('open', self.url, '--tab', name)
        metrics = self.metrics()
        encoded = json.dumps(metrics)
        self.assertNotIn('private-label-sentinel', encoded)
        self.assertTrue(all(len(tab['tab']) <= 80 for tab in metrics['tabs']))


if __name__ == '__main__':
    unittest.main()
