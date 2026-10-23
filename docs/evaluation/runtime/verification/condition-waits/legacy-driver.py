import sys
import unittest
import test_daemon

test_daemon.window_report([])
names = [
    'SessionCommandTests.test_wait',
    'SessionCommandTests.test_wait_times_out_and_needs_a_condition',
    'SessionCommandTests.test_wait_for_network_idle',
    'SessionCommandTests.test_open_until',
    'ReadThroughDaemon.test_until_waits_across_redirects_and_forms',
    'ReadThroughDaemon.test_until_follows_a_late_hop_to_another_origin',
    'ReadThroughDaemon.test_an_invalid_until_expression_exits_2_at_the_timeout',
    'RestingTabTests.test_a_resting_tab_is_hidden_until_the_next_command',
]
result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(names, test_daemon))
sys.exit(not result.wasSuccessful())
