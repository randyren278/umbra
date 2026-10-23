"""Public resource-lifetime regressions on isolated localhost profiles."""
import json
import re
import threading
import socket
import sys
import time
import unittest
from pathlib import Path
from urllib.parse import urlsplit

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_umbra as base
from server import Server


class ResourceCleanupTests(base.UmbraCase):
    def setUp(self):
        super().setUp()
        self.server = Server()
        self.server.page('/', '<title>Resource cleanup</title>')
        self.hits = []
        self.server.route('/side-effect')(lambda h: (self.hits.append(True), h.reply(body='done')))
        self.assertExit(self.umbra('open', self.server.url('/'), '--tab', 'kept'), 0)

    def tearDown(self):
        self.umbra('inspector', '--stop')
        self.umbra('stop')
        self.server.close()

    def result(self, *args):
        result = self.umbra(*args, '--json')
        self.assertExit(result, 0)
        return json.loads(result.text)

    def test_repeated_inspector_stop_closes_pending_clients_and_listener(self):
        for _ in range(8):
            address = urlsplit(self.result('inspector')['url'])
            clients = []
            try:
                for _ in range(12):
                    client = socket.create_connection((address.hostname, address.port), timeout=3)
                    client.sendall(b'GET /state HTTP/1.1\r\n')
                    clients.append(client)
                self.result('inspector', '--stop')
                for client in clients:
                    try:
                        self.assertEqual(client.recv(1), b'')
                    except ConnectionResetError:
                        pass
                with self.assertRaises(OSError):
                    socket.create_connection((address.hostname, address.port), timeout=.5)
            finally:
                for client in clients:
                    client.close()

    def test_successful_and_failed_scratch_reads_release_tabs_and_argument_bytes(self):
        for _ in range(12):
            result = self.umbra('read', self.server.url('/'), input='return 7')
            self.assertExit(result, 0)
            self.assertEqual(result.text.strip(), '7')
            failed = self.umbra('read', self.server.url('/'), input='throw Error("fixture")')
            self.assertExit(failed, 2)
            runtime = self.result('runtime')
            self.assertEqual(runtime['tabs'], 1)
            self.assertEqual(runtime['argumentBytes'], 0)
            self.assertEqual(runtime['active'], 0)
            self.assertEqual(runtime['queued'], 0)
            self.assertEqual([tab['tab'] for tab in self.result('tabs')['tabs']], ['kept'])

    def test_cancelled_scratch_read_does_not_run_delayed_script(self):
        process = base.spawn('read', self.server.url('/'), '--wait', '.7', '--request', 'cancel-read',
                             input='await fetch("/side-effect"); return true', profile=self.profile)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if self.result('runtime')['tabs'] == 2:
                break
            time.sleep(.01)
        else:
            self.fail('scratch read did not become active')
        time.sleep(.2)
        self.result('cancel', 'cancel-read')
        process.communicate(timeout=3)
        self.assertEqual(process.returncode, 2)
        time.sleep(1)
        self.assertEqual(self.hits, [], 'cancelled scratch read ran its script after cancellation')
        self.assertEqual(self.result('runtime')['tabs'], 1)

    def test_expired_scratch_read_does_not_run_delayed_script(self):
        path = str(base.LOCKS / f'daemon-{self.profile}.sock')
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(3); client.connect(path)
            client.sendall((json.dumps({'id': 1, 'command': 'read', 'args': {
                'url': self.server.url('/'), 'wait': .7, 'timeout': .2,
                'script': 'await fetch("/side-effect"); return true', 'request': 'expired-read'
            }}) + '\n').encode())
            response = json.loads(client.makefile('rb').readline())
        self.assertFalse(response['ok'])
        self.assertEqual(response['error']['code'], 2)
        time.sleep(1)
        self.assertEqual(self.hits, [], 'expired scratch read ran its script after its deadline')
        runtime = self.result('runtime')
        self.assertEqual(runtime['tabs'], 1)
        self.assertEqual(runtime['argumentBytes'], 0)

    def test_virtualized_recycled_rows_use_fresh_observation_for_native_action(self):
        self.server.page('/virtual', '''<title>Virtual list</title><style>
#list{height:240px;width:400px;overflow:auto;position:relative}#spacer{height:40000px}
button{position:absolute;height:36px;width:200px;left:0}</style>
<div id=list><div id=spacer></div></div><script>
const list=document.querySelector('#list');
const rows=Array.from({length:6},()=>{const b=document.createElement('button');list.append(b);b.onclick=()=>window.selected=Number(b.dataset.row);return b});
window.firstRow=rows[0];
function render(){const start=Math.floor(list.scrollTop/40);rows.forEach((b,i)=>{const row=start+i;b.dataset.row=row;b.textContent='Select row '+row;b.style.top=(row*40)+'px'})}
list.addEventListener('scroll',render);render();
</script>''')
        self.result('open', self.server.url('/virtual'), '--tab', 'kept')
        self.assertIn('Select row 0', self.result('snapshot', '--tab', 'kept')['snapshot'])
        self.result('scroll', '--target', '#list', '--dy', '900', '--tab', 'kept')
        row = self.result('eval', 'return Number(window.firstRow.dataset.row)', '--tab', 'kept')['value']
        self.assertGreater(row, 0)
        self.assertTrue(self.result('eval', 'return window.firstRow===document.querySelector("button")', '--tab', 'kept')['value'])
        snapshot = self.result('snapshot', '--tab', 'kept')['snapshot']
        match = re.search(r'Select row ' + str(row) + r'\" \[ref=(e\d+)\]', snapshot)
        self.assertIsNotNone(match, snapshot)
        self.result('click', match[1], '--tab', 'kept')
        self.assertEqual(self.result('eval', 'return window.selected', '--tab', 'kept')['value'], row)

    def test_pressure_policy_preserves_handoff_and_in_progress_page_download(self):
        release = threading.Event()
        def slow_download(h):
            h.send_response(200)
            h.send_header('Content-Type', 'application/octet-stream')
            h.send_header('Content-Disposition', 'attachment; filename=fixture.bin')
            h.send_header('Content-Length', '65536')
            h.end_headers(); h.wfile.write(b'x' * 32768); h.wfile.flush()
            release.wait(20)
            h.wfile.write(b'x' * 32768)
        self.server.route('/held-download')(slow_download)
        for tab in ('control', 'download', 'human'):
            self.result('open', self.server.url('/'), '--tab', tab)
        self.result('eval', 'const a=document.createElement("a");a.href="/held-download";a.click(); return true', '--tab', 'download')
        self.server.wait_for('/held-download')
        process = base.spawn('handoff', '--tab', 'human', '--request', 'held-handoff', '--timeout', '20', profile=self.profile)
        try:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if any(job['request']=='held-handoff' and job['state']=='running' for job in self.result('tasks')['pending']):
                    break
                time.sleep(.01)
            else:
                self.fail('handoff did not become active')
            time.sleep(5.2)
            runtime = self.result('runtime', '--reclaim')
            tabs = {tab['tab'] for tab in self.result('tabs')['tabs']}
            self.assertTrue({'download', 'human'} <= tabs, tabs)
            self.assertNotIn('control', tabs)
            self.assertGreaterEqual(runtime['reclaimedTabs'], 1)
            self.assertEqual([job['request'] for job in self.result('tasks')['pending']], ['held-handoff'])
            release.set()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                downloads = self.result('downloads', '--tab', 'download')['downloads']
                if downloads:
                    self.assertEqual(downloads[-1]['state'], 'complete')
                    self.assertEqual(downloads[-1]['bytes'], 65536)
                    break
                time.sleep(.05)
            else:
                self.fail('protected page download did not complete')
        finally:
            release.set()
            self.result('cancel', 'held-handoff')
            process.communicate(timeout=3)


if __name__ == '__main__':
    unittest.main()
