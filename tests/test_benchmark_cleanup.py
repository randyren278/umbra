"""Benchmark cleanup never signals a child that has already been reaped."""
import importlib.util
import os
from pathlib import Path
import signal
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('cleanup_hybrid', Path(__file__).resolve().parent.parent/'bench/hybrid.py')
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)


class CleanupTests(unittest.TestCase):
    def clean(self, pid):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(h.subprocess, 'run'), patch.object(h.metrics, 'store_dir', return_value=root/'store'), \
                 patch.object(h.metrics, 'DOWNLOADS', root), patch.object(h.metrics, 'SUPPORT', root):
                return h.cleanup('not-executed', {}, 'synthetic-child', pid)

    def test_empty_workload_is_rejected_before_resources(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)/'result.json'
            script = Path(__file__).resolve().parent.parent/'bench/semantic.py'
            for count in ('0', '-1', '1', '2', '6'):
                result = subprocess.run([sys.executable, str(script), '--umbra', 'not-executed', '--mode', 'dom', '--cycles', count, '--json', str(output)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertIn('cycles must be at least 7', result.stderr)
                self.assertFalse(output.exists())

    def test_already_reaped_child_is_never_signaled(self):
        pid = os.fork()
        if not pid:
            os._exit(0)
        os.waitpid(pid, 0)
        with patch.object(h.os, 'kill', side_effect=AssertionError('reaped PID must not be signaled')):
            self.assertFalse(self.clean(pid)['storeExists'])

    def test_live_child_is_stopped_and_reaped(self):
        pid = os.fork()
        if not pid:
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            while True:
                signal.pause()
        try:
            self.assertFalse(self.clean(pid)['storeExists'])
            with self.assertRaises(ChildProcessError):
                os.waitpid(pid, os.WNOHANG)
        finally:
            try:
                found, _ = os.waitpid(pid, os.WNOHANG)
                if not found:
                    os.kill(pid, signal.SIGKILL)
                    os.waitpid(pid, 0)
            except ChildProcessError:
                pass


if __name__ == '__main__':
    unittest.main()
