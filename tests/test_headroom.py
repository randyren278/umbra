"""Native memory-headroom admission exercised through the public CLI."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_umbra as base
from server import Server


class HeadroomTests(base.UmbraCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = Server()
        cls.srv.page('/work', '<title>Must not start under the reserve</title>')

    @classmethod
    def tearDownClass(cls):
        cls.srv.close()

    def tearDown(self):
        self.umbra('stop')

    def status(self, env):
        result = self.umbra('runtime', '--json', env=env)
        self.assertExit(result, 0)
        return json.loads(result.text)

    def test_reserve_rejects_new_tabs_and_queues_work_before_network_dispatch(self):
        env = {'UMBRA_MIN_AVAILABLE_MB': '16384'}
        state = self.status(env)
        headroom = state['headroom']
        if headroom['estimatedAvailableBytes'] is None:
            self.skipTest('host VM statistics unavailable')
        if headroom['estimatedAvailableBytes'] > headroom['reserveBytes']:
            self.skipTest('host headroom exceeds the largest configured reserve')
        self.assertEqual(state['effectiveActiveLimit'], 0)
        before = len(self.srv.requests('/work'))
        self.assertExit(self.umbra('open', self.srv.url('/work')), 1)
        self.assertExit(self.umbra('http', self.srv.url('/work'), '--timeout', '.2'), 2)
        self.assertEqual(len(self.srv.requests('/work')), before)
        self.assertEqual(self.status(env)['queued'], 0)
        self.assertExit(self.umbra('stop'), 0)

    def test_large_per_job_estimate_reduces_admitted_parallel_capacity(self):
        state = self.status({'UMBRA_JOB_MB': '4096', 'UMBRA_MIN_AVAILABLE_MB': '0', 'UMBRA_ACTIVE': '32'})
        headroom = state['headroom']
        available = headroom['estimatedAvailableBytes']
        if available is None:
            self.skipTest('host VM statistics unavailable')
        if available >= headroom['perActiveJobEstimateBytes'] * state['limits']['active']:
            self.skipTest('host has enough headroom for every configured 4 GiB job estimate')
        self.assertLess(state['effectiveActiveLimit'], state['limits']['active'])
        self.assertIn('estimate', headroom['scope'])


if __name__ == '__main__':
    unittest.main()
