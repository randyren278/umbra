"""Native popup receipts stay with their opener amid independent scratch work."""
import concurrent.futures
import json
import sys
import threading
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_umbra as base
from server import Server


class PopupOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.profile = base.new_profile()
        self.server = Server()
        self.clicked = threading.Event()
        self.loading = threading.Event()
        self.release = threading.Event()
        self.server.route('/clicked')(lambda h: (self.clicked.set(), h.reply(body='ok')))
        def delayed(h):
            self.loading.set()
            self.release.wait(5)
            h.reply(body='<title>Independent page</title><p>loaded</p>')
        self.server.route('/slow')(delayed)
        self.server.page('/app', '''<title>Acted page</title><button id=save>Save</button><button id=popup>Open</button><output id=result></output><script>
save.onclick=e=>{window.clickedTrusted=e.isTrusted;fetch('/clicked');const end=performance.now()+600;
while(performance.now()<end){};result.textContent='Saved'};
popup.onclick=e=>{window.clickedTrusted=e.isTrusted;window.open('/slow')};
</script>''')
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        result = self.command('open', self.server.url('/app'), '--tab', 'acted')
        self.assertEqual(result['title'], 'Acted page')
        self.command('snapshot', '--tab', 'acted')

    def tearDown(self):
        self.release.set()
        base.umbra('stop', profile=self.profile)
        self.pool.shutdown(wait=True)
        self.server.close()
        base.stop_daemons([self.profile])

    def command(self, *args, **kwargs):
        result = base.umbra(*args, '--json', profile=self.profile, timeout=12, **kwargs)
        self.assertEqual(result.code, 0, result.err)
        return json.loads(result.text)

    def test_scratch_read_is_not_the_clicks_popup(self):
        clicked = self.pool.submit(self.command, 'click', '#save', '--tab', 'acted', '--timeout', '6')
        self.assertTrue(self.clicked.wait(3), 'native click handler did not run')
        read = self.pool.submit(self.command, 'read', self.server.url('/slow'), '--timeout', '6',
                                input='await new Promise(r=>setTimeout(r,150)); return 7')
        self.assertTrue(self.loading.wait(3), 'scratch page did not start')
        time.sleep(.75)  # let the native mouse handler end while the independent page is loading
        self.release.set()
        result = clicked.result(timeout=8)
        self.assertEqual(read.result(timeout=8), 7)
        self.assertNotIn('newTab', result, 'the click adopted another request\'s scratch tab')
        self.assertEqual(result['receipt']['urlAfter'], self.server.url('/app'))
        self.assertTrue(result['trusted'])
        self.assertEqual([tab['tab'] for tab in self.command('tabs')['tabs']], ['acted'])

    def test_native_popup_still_belongs_to_its_opener(self):
        clicked = self.pool.submit(self.command, 'click', '#popup', '--tab', 'acted', '--timeout', '6')
        self.assertTrue(self.loading.wait(3), 'native popup did not start')
        self.release.set()
        result = clicked.result(timeout=8)
        self.assertIn('newTab', result)
        self.assertTrue(result['trusted'])
        self.assertEqual(result['receipt']['urlAfter'], self.server.url('/slow'))
        tabs = self.command('tabs')['tabs']
        self.assertEqual(len(tabs), 2)
        self.assertIn(result['newTab'], [tab['tab'] for tab in tabs])


if __name__ == '__main__':
    unittest.main()
