"""End-to-end tests for Umbra's daemon and session commands, against 127.0.0.1 servers.

Black-box: only the CLI, the files AGENTS.md documents, the process list and the window server.
Every daemon runs under a throwaway profile and is stopped when its test ends. After every
session command the daemon's on-screen windows must be fully transparent and off every display,
and the daemon must not have become the frontmost app. The one-shot suites (read, fetch,
download) run a second time here with a daemon already running, since one-shots then go
through it.

    python3 tests/test_daemon.py [-v] [-k PATTERN] [Class.test_name ...]
"""
import json
import os
import re
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_umbra as base  # noqa: E402
from server import Server  # noqa: E402

lister = None  # tests/windows.swift, compiled and started on first use


def window_report(pids):
    global lister
    if lister is None:
        binary = base.TMP / 'windows'
        subprocess.run(['xcrun', 'swiftc', '-Onone', '-o', str(binary), str(base.REPO / 'tests/windows.swift')],
                       check=True, capture_output=True, timeout=300)
        lister = subprocess.Popen([str(binary)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        base.children.append(lister)
    lister.stdin.write(' '.join(map(str, pids)) + '\n')
    lister.stdin.flush()
    return json.loads(lister.stdout.readline())


def quit_app(pid):
    """Sends the app Quit, as the Dock or Command-Q does."""
    window_report([])  # starts the helper
    lister.stdin.write(f'quit {pid}\n')
    lister.stdin.flush()
    return json.loads(lister.stdout.readline())['sent']


def press(pid, title):
    """Presses the app's button with this title, as a person would; None without the Accessibility permission."""
    window_report([])
    lister.stdin.write(f'press {pid} {title}\n')
    lister.stdin.flush()
    answer = json.loads(lister.stdout.readline())
    return answer['pressed'] if answer['trusted'] else None


def texts(pid):
    """The static texts and buttons of the app's windows, in order, as (role, text); None without the Accessibility permission."""
    window_report([])
    lister.stdin.write(f'texts {pid}\n')
    lister.stdin.flush()
    answer = json.loads(lister.stdout.readline())
    return [tuple(item) for item in answer['items']] if answer['trusted'] else None


def pick(pid, label, item):
    """Opens the pull-down button with this accessibility label and picks the menu item, as a person
    would: (picked, whether the item had a checkmark), or None without the Accessibility permission."""
    window_report([])
    lister.stdin.write(f'pick {pid} {label}\t{item}\n')
    lister.stdin.flush()
    answer = json.loads(lister.stdout.readline())
    return (answer['picked'], answer['checked']) if answer['trusted'] else None


def front_pid():
    """The frontmost app's pid, as LaunchServices reports it."""
    asn = subprocess.run(['lsappinfo', 'front'], capture_output=True, text=True, timeout=10).stdout.strip()
    info = subprocess.run(['lsappinfo', 'info', '-only', 'pid', asn], capture_output=True, text=True, timeout=10).stdout
    found = re.search(r'pid"?\s*=\s*(\d+)', info)
    return int(found.group(1)) if found else None


def daemon_pids(name):
    return [pid for pid, owner in base.daemons().items() if owner == name]


known = {}  # profile -> its daemon pid, rechecked with ps only once that process is gone


def daemon_of(name):
    if not (known.get(name) and base.alive(known[name])):
        known[name] = next(iter(daemon_pids(name)), None)
    return [known[name]] if known[name] else []


def is_umbra(pid):
    command = subprocess.run(['ps', '-o', 'comm=', '-p', str(pid)], capture_output=True, text=True).stdout.strip()
    return command.endswith('/umbra')


def wait_for(condition, timeout, step=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(step)
    return condition()


def socket_file(name):
    return base.LOCKS / f'daemon-{name}.sock'


class Guarded:
    """Checks, after each command, that the profile's daemon stayed invisible and in the background."""

    def setUp(self):
        super().setUp()
        self.front = front_pid()

    def tearDown(self):
        # Stopped in the background: the next test has its own profile, and cleanup waits for all.
        base.spawn('stop', profile=self.profile)
        super().tearDown()

    def umbra(self, *args, guard=True, settle=0, **kwargs):
        result = super().umbra(*args, **kwargs)
        if guard:
            self.assertInvisible(kwargs.get('profile', self.profile), ' '.join(map(str, args[:2]))[:60], settle)
        return result

    def assertInvisible(self, profile, after='', settle=0):
        """Settle: seconds the window server may take to hide a window the command showed (handoff)."""
        # A subtest, so a violation fails the test without hiding what the rest of it checks.
        with self.subTest(invisible=after):
            pids = daemon_of(profile)
            if pids:
                def shown():
                    return [w for w in window_report(pids)['windows'] if w['onscreen'] and (w['alpha'] or w['visibleArea'])]
                if settle:
                    wait_for(lambda: not shown(), settle)
                self.assertEqual(shown(), [], 'an Umbra window can be seen')
            front = front_pid()
            # The person may switch apps while the suite runs; Umbra itself must never come forward.
            self.assertTrue(front == self.front or front not in pids, f'the daemon (pid {front}) became the frontmost app')


class SessionCase(Guarded, base.UmbraCase):
    def status(self, profile=None):
        """The daemon's status as JSON, or None when no daemon runs for the profile."""
        r = self.umbra('status', '--json', profile=profile or self.profile)
        self.assertExit(r, 0)
        return None if r.text.startswith('no daemon running') else json.loads(r.out)

    def json(self, *args, **kwargs):
        r = self.umbra(*args, '--json', **kwargs)
        self.assertExit(r, 0)
        return json.loads(r.out)


# MARK: One-shots through a running daemon

class ThroughDaemon(Guarded):
    """Runs a one-shot test class again, with this profile's daemon already running."""

    def setUp(self):
        super().setUp()
        self.assertExit(self.umbra('tabs'), 0)  # the first session command starts the daemon
        self.daemon = daemon_pids(self.profile)
        self.assertEqual(len(self.daemon), 1)

    def tearDown(self):
        try:
            self.assertEqual(daemon_pids(self.profile), self.daemon, 'the daemon should have served every run')
        finally:
            super().tearDown()


class ReadThroughDaemon(ThroughDaemon, base.ReadTests):
    pass


class FetchThroughDaemon(ThroughDaemon, base.FetchTests):
    pass


class DownloadThroughDaemon(ThroughDaemon, base.DownloadTests):
    pass


class SessionThroughDaemon(ThroughDaemon, base.SessionTests):
    @unittest.skip('a running daemon is a live browser: session-only cookies last as long as it does '
                   '(test_session_cookies_live_as_long_as_the_daemon checks that instead)')
    def test_session_cookies_are_dropped_without_keep_session(self):
        pass

    def assertKeptExpiry(self, shown, now):
        self.assertEqual(shown, 'session', 'kept only when the daemon exits')

    def test_session_cookies_live_as_long_as_the_daemon(self):
        for keep in [False, True]:
            with self.subTest(keepSession=keep):
                profile, sites = base.new_profile(), self.sites(keep)
                self.assertExit(self.umbra('tabs', profile=profile, sites=sites), 0)  # its daemon reads this sites file
                self.sign_in('/login-session', sites, profile)
                cookies, _ = self.sent_next_run(sites, profile)
                self.assertEqual(cookies.get('sess'), 'SESSVALUE-9f2c', 'sent while the same daemon lives')
                listed = self.umbra('cookies', '127.0.0.1', profile=profile, sites=sites)
                self.assertIn('sess\t127.0.0.1\tsession\n', listed.text)
                self.assertExit(self.umbra('stop', profile=profile, sites=sites), 0)
                self.assertTrue(wait_for(lambda: not daemon_pids(profile), 6))
                cookies, _ = self.sent_next_run(sites, profile)  # a one-shot now: no daemon
                self.assertEqual(cookies.get('sess'), 'SESSVALUE-9f2c' if keep else None, 'after the daemon stopped')


# MARK: Daemon lifecycle

class LifecycleTests(SessionCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = srv = Server()
        srv.route('/page')(lambda h: h.reply(200, f'<title>page {h.req.query.get("n", "")}</title>'))

    @classmethod
    def tearDownClass(cls):
        cls.srv.close()

    def test_status_and_stop_without_a_daemon(self):
        for command in ['status', 'stop']:
            with self.subTest(command):
                r = self.umbra(command)
                self.assertExit(r, 0)
                self.assertEqual(r.text, f'no daemon running for profile {self.profile}\n')
        self.assertEqual(daemon_pids(self.profile), [], 'status and stop must not start a daemon')

    def test_the_first_session_command_starts_the_daemon(self):
        self.assertIsNone(self.status())
        r = self.umbra('tabs')
        self.assertExit(r, 0)
        self.assertEqual(r.text, 'no tabs\n')
        pids = daemon_pids(self.profile)
        self.assertEqual(len(pids), 1)
        status = self.status()
        self.assertEqual((status['pid'], status['profile'], status['tabs']), (pids[0], self.profile, []))
        self.assertTrue(socket_file(self.profile).exists())

    def test_starting_the_daemon_never_takes_focus(self):
        # Regression (16c35ab): a daemon started by `open` made itself the active app at launch.
        for first in [['open', self.srv.url('/page')], ['tabs']]:
            with self.subTest(first[0]):
                profile = base.new_profile()
                before = front_pid()
                self.assertExit(self.umbra(*first, profile=profile, guard=False), 0)
                [pid] = daemon_pids(profile)
                for _ in range(10):  # activation lands a moment after launch
                    front = front_pid()
                    self.assertNotEqual(front, pid, 'the daemon became the frontmost app')
                    time.sleep(0.1)
                if not is_umbra(before):
                    self.assertEqual(front, before)
                base.stop_daemons([profile])

    def test_stop(self):
        self.assertExit(self.umbra('open', self.srv.url('/page')), 0)
        pid = self.status()['pid']
        r = self.umbra('stop')
        self.assertExit(r, 0)
        self.assertEqual(r.text, 'stopped\n')
        self.assertTrue(wait_for(lambda: not base.alive(pid), 6), 'the daemon is still running')
        self.assertFalse(socket_file(self.profile).exists())
        self.assertIsNone(self.status())

    def test_an_idle_daemon_exits(self):
        # UMBRA_IDLE=3, checked by a 15 s timer: the daemon goes between 3 and about 18 s after its last command.
        self.assertExit(self.umbra('tabs', env={'UMBRA_IDLE': '3'}), 0)
        last = time.monotonic()
        [pid] = daemon_pids(self.profile)
        gone = wait_for(lambda: not base.alive(pid) and time.monotonic(), 25, step=0.1)
        self.assertTrue(gone, 'the idle daemon did not exit within 25 s')
        self.assertGreaterEqual(gone - last, 3)
        self.assertLessEqual(gone - last, 3 + 15 + 2.5)
        self.assertFalse(socket_file(self.profile).exists())

    def test_sigterm_shuts_down_cleanly(self):
        self.assertExit(self.umbra('open', self.srv.url('/page')), 0)
        [pid] = daemon_pids(self.profile)
        os.kill(pid, signal.SIGTERM)
        self.assertTrue(wait_for(lambda: not base.alive(pid), 6), 'the daemon ignored SIGTERM')
        self.assertFalse(socket_file(self.profile).exists(), 'SIGTERM left the socket behind')
        self.assertIsNone(self.status())

    def test_a_stale_socket_is_recovered(self):
        self.assertExit(self.umbra('open', self.srv.url('/page')), 0)
        [pid] = daemon_pids(self.profile)
        os.kill(pid, signal.SIGKILL)
        self.assertTrue(wait_for(lambda: not base.alive(pid), 5))
        self.assertTrue(socket_file(self.profile).exists(), 'kill -9 should leave the socket file behind')
        self.assertIsNone(self.status())
        r = self.umbra('open', self.srv.url('/page?n=2'))
        self.assertExit(r, 0)
        self.assertIn('page 2', r.text)
        [again] = daemon_pids(self.profile)
        self.assertNotEqual(again, pid)
        self.assertEqual(self.status()['pid'], again)

    def test_concurrent_first_commands_start_one_daemon(self):
        procs = [base.spawn('open', '--tab', name, self.srv.url(f'/page?n={name}'), profile=self.profile)
                 for name in ['a', 'b']]
        for proc in procs:
            out, err = proc.communicate(timeout=60)
            self.assertEqual(proc.returncode, 0, err)
        # Both clients may start a daemon; the one that loses the race for the lock bows out.
        self.assertTrue(wait_for(lambda: len(daemon_pids(self.profile)) == 1, 5), base.daemons())
        time.sleep(0.5)
        self.assertEqual(len(daemon_pids(self.profile)), 1, base.daemons())
        tabs = {tab['tab']: tab['title'] for tab in self.json('tabs')['tabs']}
        self.assertEqual(tabs, {'a': 'page a', 'b': 'page b'})

    def diagnose(self, pid, tab, started):
        """What happened when a client gave up: did its request reach the server, does its page answer,
        does the daemon, and what is its main thread doing."""
        env = base.environment(self.profile, None, None)
        arrived = [(r.query.get('n'), round(r.t - started, 2)) for r in self.srv.requests('/page')]
        page = subprocess.run([base.UMBRA, 'eval', '--tab', tab, '--timeout', '5',
                               'return [document.readyState, location.href, document.title]'],
                              env=env, capture_output=True, text=True, timeout=20)
        status = subprocess.run([base.UMBRA, 'tabs', '--json'], env=env, capture_output=True, text=True, timeout=20)
        sample = subprocess.run(['sample', str(pid), '2', '-mayDie'], capture_output=True, text=True, timeout=30)
        main = sample.stdout.split('Thread_')[1][:4000] if 'Thread_' in sample.stdout else sample.stdout[:2000]
        return (f'/page requests (n, seconds after the opens): {arrived}\n'
                f'eval in {tab}: exit {page.returncode} {page.stdout.strip()} {page.stderr.strip()[-300:]}\n'
                f'tabs exit {status.returncode}: {status.stdout[:1500]} {status.stderr[-300:]}\nmain thread:\n{main}')

    def test_a_load_whose_web_process_stops_answering_moves_to_a_new_one(self):
        # A page script that holds its process for a minute stands in for a stuck web process
        # (seen on CI when several start at once: the request never leaves, scripts get no answer).
        served = []

        def stuck_once(h):
            served.append(h.req)
            h.reply(200, '<title>stuck</title><script>const t = Date.now(); while (Date.now() - t < 60000);</script>'
                    if len(served) == 1 else '<title>fresh</title>')
        self.srv.route('/stuck-once')(stuck_once)
        r = self.umbra('open', self.srv.url('/stuck-once'), '--json')
        self.assertExit(r, 0)
        self.assertEqual(json.loads(r.out)['title'], 'fresh')
        self.assertEqual(len(served), 2)
        self.assertLess(r.elapsed, 40)

    def test_five_concurrent_clients(self):
        self.assertExit(self.umbra('tabs'), 0)
        [pid] = daemon_pids(self.profile)
        started = time.monotonic()
        procs = [base.spawn('open', '--tab', f'c{n}', self.srv.url(f'/page?n={n}'), profile=self.profile) for n in range(5)]
        for n, proc in enumerate(procs):
            try:
                out, err = proc.communicate(timeout=75)  # past open's own 60 s, so its answer shows
            except subprocess.TimeoutExpired:
                self.fail(f'{proc.args[1:4]} gave no answer in 75 s\n{self.diagnose(pid, f"c{n}", started)}')
            if proc.returncode != 0:
                self.fail(f'{proc.args[1:4]} exited {proc.returncode}: {err.decode()}\n{self.diagnose(pid, f"c{n}", started)}')
        script = 'await new Promise(r => setTimeout(r, 300)); return location.search'
        procs = [base.spawn('eval', '--tab', f'c{n}', script, profile=self.profile) for n in range(5)]
        for n, proc in enumerate(procs):
            out, err = proc.communicate(timeout=60)
            self.assertEqual((proc.returncode, out), (0, f'?n={n}\n'.encode()), err)
        self.assertEqual(sorted(tab['tab'] for tab in self.json('tabs')['tabs']), [f'c{n}' for n in range(5)])
        self.assertEqual(daemon_pids(self.profile), [pid])

    def test_a_daemon_serves_only_its_own_profile(self):
        other = base.new_profile()
        self.assertExit(self.umbra('open', self.srv.url('/page?n=a')), 0)
        self.assertIsNone(self.status(other))
        r = self.umbra('tabs', profile=other)
        self.assertExit(r, 0)
        self.assertEqual(r.text, 'no tabs\n')
        self.assertNotEqual(self.status(other)['pid'], self.status()['pid'])
        self.assertEqual([tab['title'] for tab in self.json('tabs')['tabs']], ['page a'])
        base.stop_daemons([other])

    def test_the_profile_flag_selects_the_profile_before_anything_else(self):
        # Regression (e6f3d13): --profile was applied after the environment was read, so the
        # default profile's daemon started. UMBRA_IDLE=2 bounds the damage should it regress.
        before = {pid for pid, name in base.daemons().items() if name == ''}
        r = self.umbra('--profile', self.profile, 'open', self.srv.url('/page?n=flag'), env={'UMBRA_PROFILE': '', 'UMBRA_IDLE': '2'})
        started = [pid for pid, name in base.daemons().items() if name == '' and pid not in before]
        for pid in started:
            os.kill(pid, signal.SIGKILL)  # never leave a daemon on the real store
        self.assertEqual(started, [], 'the default profile\'s daemon started')
        self.assertExit(r, 0)
        status = self.status()
        self.assertEqual(status['profile'], self.profile)
        self.assertEqual(daemon_pids(self.profile), [status['pid']])
        self.assertTrue((base.LOCKS / f'daemon-{self.profile}.log').exists(), 'the daemon should log to its own file')
        flagged = self.umbra('--profile', self.profile, 'status', '--json', env={'UMBRA_PROFILE': ''})
        self.assertEqual(json.loads(flagged.out)['pid'], status['pid'])


# MARK: Session commands

PAGE_A = """<!doctype html><title>Page A</title><p id=marker>alpha</p><div id=spinner>loading</div>
<a id=dl href="/files/report.bin" download="report.txt">report</a>
<a id=blank href="/b?from=blank" target=_blank>pop-up</a>
<script>
window.pageGlobal = 'from page';
setTimeout(() => {
  document.getElementById('spinner').remove();
  const late = document.createElement('p');
  late.id = 'late';
  late.textContent = 'late arrival';
  document.body.append(late);
  history.pushState({}, '', '/a?step=2');
}, 500);
</script>"""

REPORT = os.urandom(64 << 10)

# What the page sees: its visibility, and whether ten animation frames arrive within 2 s.
VISIBLE = ('return [document.visibilityState, await new Promise(done => { let n = 0; '
           'const frame = () => ++n === 10 ? done(true) : requestAnimationFrame(frame); '
           'requestAnimationFrame(frame); setTimeout(() => done(false), 2000) })]')


class SessionCommandTests(SessionCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = srv = Server()
        srv.page('/a', PAGE_A)
        srv.page('/b', '<title>Page B</title>')
        srv.page('/files/report.bin', REPORT, ctype='application/octet-stream')
        srv.page('/hop', '<title>hop</title><script>setTimeout(() => location.assign("/b?hopped"), 300)</script>')
        srv.page('/cookie', '<title>cookie</title>', headers=[('Set-Cookie', 'sid=COOKIEVALUE-5e1f; Path=/; Max-Age=600')])
        srv.page('/probe', 'ok', ctype='text/plain')
        srv.route('/stall')(lambda h: h.stall())
        srv.page('/busy', '<title>busy</title><script>let n = 0; const t = setInterval(() => '
                          '{ fetch("/probe?n=" + n++); if (n === 8) clearInterval(t) }, 100)</script>')
        cls.visits = {}

        @srv.route('/count')
        def count(h):
            cls.visits[h.req.query['k']] = cls.visits.get(h.req.query['k'], 0) + 1
            h.reply(200, f'<title>count {cls.visits[h.req.query["k"]]}</title>')

    @classmethod
    def tearDownClass(cls):
        cls.srv.close()

    def open(self, url, *flags, **kwargs):
        r = self.umbra('open', url, *flags, **kwargs)
        self.assertExit(r, 0)
        return r

    def eval(self, script, *flags, **kwargs):
        r = self.umbra('eval', script, *flags, **kwargs)
        self.assertExit(r, 0)
        return r.text.rstrip('\n')

    def test_open_a_url(self):
        url = self.srv.url('/a')
        r = self.open(url)
        self.assertEqual(r.text, f'Page A · {url} · HTTP 200\n')
        info = self.json('open', self.srv.url('/b'))
        self.assertEqual((info['tab'], info['title'], info['url'], info['status']), ('main', 'Page B', self.srv.url('/b'), 200))

    def test_open_a_file_path(self):
        page = self.tmp / 'local page.html'
        page.write_text('<title>From disk</title><p>file</p>')
        info = self.json('open', str(page))
        self.assertEqual(info['title'], 'From disk')
        self.assertEqual(info['url'], page.as_uri())

    def test_open_a_data_url(self):
        info = self.json('open', 'data:text/html,<title>Inline</title>')
        self.assertEqual(info['title'], 'Inline')

    def test_open_a_bare_host_and_port(self):
        for host in ['localhost', '127.0.0.1']:
            with self.subTest(host):
                info = self.json('open', f'{host}:{self.srv.port}/b')
                self.assertEqual((info['title'], info['url']), ('Page B', f'http://{host}:{self.srv.port}/b'))

    def test_open_in_a_new_tab(self):
        self.open(self.srv.url('/a'))
        info = self.json('open', self.srv.url('/b'), '--new')
        self.assertNotEqual(info['tab'], 'main')
        tabs = self.json('tabs')['tabs']
        self.assertEqual([(t['tab'], t['title'], t['current']) for t in tabs],
                         [('main', 'Page A', False), (info['tab'], 'Page B', True)])

    def test_open_until(self):
        info = self.json('open', self.srv.url('/hop'), '--until', 'document.title == "Page B"')
        self.assertEqual((info['title'], info['url']), ('Page B', self.srv.url('/b?hopped')))
        r = self.umbra('open', self.srv.url('/a'), '--until', 'window.never === true', '--timeout', '1')
        self.assertTimedOut(r, 1)

    def test_back_forward_and_reload(self):
        k = os.urandom(4).hex()
        self.open(self.srv.url(f'/count?k={k}'))
        self.open(self.srv.url('/b'))
        self.assertEqual(self.json('back')['title'], 'count 1')
        self.assertEqual(self.json('forward')['title'], 'Page B')
        self.assertEqual(self.json('back')['url'], self.srv.url(f'/count?k={k}'))
        self.assertEqual(self.json('reload')['title'], 'count 2')
        self.assertEqual(self.visits[k], 2)

    def test_a_page_left_behind_is_not_kept_frozen_in_memory(self):
        # No back-forward cache: going back loads the page again (from the HTTP cache) rather than
        # thawing the old one, which would have stayed in memory all along.
        self.open(self.srv.url('/b'))
        self.eval('window.left = "still here"')
        self.open(self.srv.url('/a'))
        self.json('back')
        self.assertEqual(self.eval('return String(window.left)'), 'undefined')

    def test_back_with_nowhere_to_go_exits_4(self):
        self.open(self.srv.url('/b'))
        r = self.umbra('back')
        self.assertExit(r, 4)
        self.assertSays(r)

    def test_navigation_without_a_tab_exits_64(self):
        for command in ['back', 'forward', 'reload', 'eval']:
            with self.subTest(command):
                r = self.umbra(command, *(['return 1'] if command == 'eval' else []))
                self.assertExit(r, 64)
                self.assertSays(r)

    def test_tabs(self):
        self.assertEqual(self.umbra('tabs').text, 'no tabs\n')
        self.open(self.srv.url('/a'))
        self.assertEqual(self.json('tab', 'new', 'extra')['tab'], 'extra')
        self.assertEqual([(t['tab'], t['current']) for t in self.json('tabs')['tabs']], [('main', False), ('extra', True)])
        self.assertIn('* extra', self.umbra('tabs').text)
        self.assertEqual(self.json('tab', 'select', 'main')['title'], 'Page A')
        self.assertEqual(self.eval('return document.title'), 'Page A', 'commands act on the selected tab')
        self.assertEqual(self.umbra('tab', 'close', 'extra').text, 'closed extra\n')
        self.assertEqual([t['tab'] for t in self.json('tabs')['tabs']], ['main'])
        for args in [['new', 'main'], ['select', 'nosuch'], ['close', 'nosuch'], ['bogus']]:
            with self.subTest(args):
                r = self.umbra('tab', *args)
                self.assertExit(r, 64)
                self.assertSays(r)

    def test_commands_take_a_tab_by_name(self):
        self.open(self.srv.url('/a'), '--tab', 'left')
        self.open(self.srv.url('/b'), '--tab', 'right')
        self.assertEqual(self.eval('return document.title', '--tab', 'left'), 'Page A')
        self.assertEqual(self.eval('return document.title', '--tab', 'right'), 'Page B')
        r = self.umbra('eval', 'return 1', '--tab', 'nosuch')
        self.assertExit(r, 64)

    def test_eval(self):
        self.open(self.srv.url('/a'))
        self.assertEqual(self.eval('return window.pageGlobal'), 'from page')
        self.assertEqual(self.eval('await new Promise(r => setTimeout(r, 200)); return 6 * 7'), '42')
        self.assertEqual(self.eval('return {a: [1, "two"], b: "/x"}'), '{"a":[1,"two"],"b":"/x"}')
        self.assertEqual(self.json('eval', 'return [1, true, null]')['value'], [1, True, None])
        for args in [['-'], []]:
            with self.subTest(stdin=args):
                r = self.umbra('eval', *args, input='return document.title + "!"')
                self.assertExit(r, 0)
                self.assertEqual(r.text, 'Page A!\n')
        r = self.umbra('eval', 'throw new Error("kaboom")')
        self.assertExit(r, 2)
        self.assertSays(r, 'kaboom')

    def test_wait(self):
        k = os.urandom(3).hex()
        conditions = [['--selector', '#late'], ['--text', 'late arrival'], ['--url', 'step=2'], ['--url', r'/step=\d$/'],
                      ['--js', 'window.pageGlobal && document.querySelector("#late") !== null'], ['--gone', '#spinner']]
        for condition in conditions:
            with self.subTest(condition):
                self.open(self.srv.url(f'/a?k={k}'))
                start = time.monotonic()
                info = self.json('wait', *condition, '--timeout', '10')
                self.assertGreater(time.monotonic() - start, 0.2, 'returned before the page changed')
                self.assertEqual(info['url'], self.srv.url('/a?step=2'))
        r = self.umbra('wait', '--ms', '300')
        self.assertExit(r, 0)
        self.assertGreaterEqual(r.elapsed, 0.3)

    def test_wait_for_network_idle(self):
        self.open(self.srv.url('/busy'))
        self.assertExit(self.umbra('wait', '--idle', '--timeout', '20'), 0)
        probes = self.srv.requests('/probe')
        self.assertGreaterEqual(len(probes), 8)
        self.assertGreaterEqual(time.monotonic() - probes[-1].t, 0.4, 'returned while requests were still going')

    def test_wait_times_out_and_needs_a_condition(self):
        self.open(self.srv.url('/a'))
        for condition in [['--selector', '#never'], ['--text', 'never'], ['--url', 'never'], ['--gone', '#marker'], ['--js', 'false']]:
            with self.subTest(condition):
                self.assertTimedOut(self.umbra('wait', *condition, '--timeout', '1'), 1)
        r = self.umbra('wait')
        self.assertExit(r, 64)
        self.assertSays(r)

    def test_a_page_download_lands_in_the_profile_downloads_folder(self):
        self.open(self.srv.url('/a'))
        self.eval('document.getElementById("dl").click()')
        done = wait_for(lambda: [d for d in self.json('downloads')['downloads'] if d['state'] == 'complete'], 15, step=0.2)
        self.assertTrue(done, 'the download never completed')
        path = Path(done[0]['path'])
        self.assertEqual(path, base.DOWNLOADS / self.profile / 'report.txt')
        self.assertEqual(path.read_bytes(), REPORT)
        self.assertEqual(done[0]['bytes'], len(REPORT))

    def test_a_target_blank_link_opens_a_tab(self):
        self.open(self.srv.url('/a'))
        self.eval('document.getElementById("blank").click()')
        popped = wait_for(lambda: [t for t in self.json('tabs')['tabs'] if t['url'].endswith('/b?from=blank') and t['title']], 10, 0.2)
        self.assertTrue(popped, self.json('tabs'))
        self.assertEqual(popped[0]['title'], 'Page B')
        self.assertEqual(len(self.json('tabs')['tabs']), 2)

    def test_handoff_that_is_already_done_returns_at_once_and_hides_again(self):
        self.open(self.srv.url('/a'))
        before = front_pid()
        r = self.umbra('handoff', 'nothing to do', '--until', 'true', '--timeout', '10', settle=1)
        self.assertExit(r, 0)
        self.assertLess(r.elapsed, 5)
        self.assertIn('Page A', r.text)
        if not is_umbra(before):  # when an Umbra holds the front (a bug the guard reports), there is nothing to restore
            self.assertEqual(front_pid(), before, 'the app that was in front should be in front again')
        self.assertEqual(self.eval(VISIBLE), '["visible",true]', 'the tab must look visible to its page again')

    @unittest.skipUnless(base.window_server(), 'no window server (not in an Aqua session)')
    @unittest.skipIf(os.environ.get('UMBRA_TEST_GUI') == '0', 'UMBRA_TEST_GUI=0')
    def test_a_handoff_that_times_out_hides_its_window_again(self):
        # Also proves the guard can see a window: a handoff really shows one, for a moment.
        self.open(self.srv.url('/a'))
        for until in [['--until', 'false'], []]:
            with self.subTest(until=until):
                proc = base.spawn('handoff', 'look', *until, '--timeout', '2', profile=self.profile)
                pids = daemon_of(self.profile)
                seen = wait_for(lambda: [w for w in window_report(pids)['windows']
                                         if w['onscreen'] and w['alpha'] > 0 and w['visibleArea'] > 0], 5)
                out, err = proc.communicate(timeout=30)
                self.assertTrue(seen, 'the handoff window never showed')
                self.assertEqual(proc.returncode, 2, err)
                self.assertInvisible(self.profile, 'handoff timed out', settle=1)
                self.assertEqual(self.eval(VISIBLE), '["visible",true]')

    def test_pages_believe_they_are_visible(self):
        self.open(self.srv.url('/a'))
        self.assertEqual(self.eval(VISIBLE), '["visible",true]')
        self.json('open', self.srv.url('/b'), '--new')
        self.assertEqual(self.eval(VISIBLE), '["visible",true]')

    def test_cookies_show_names_never_values(self):
        self.open(self.srv.url('/cookie'))
        r = self.umbra('cookies')
        self.assertExit(r, 0)
        self.assertNotIn('COOKIEVALUE', r.text)
        self.assertRegex(r.text, r'(?m)^sid\t127\.0\.0\.1\t\d{4}-\d\d-\d\dT')
        listed = self.json('cookies', '127.0.0.1')['cookies']
        self.assertEqual([(c['name'], c['domain']) for c in listed], [('sid', '127.0.0.1')])
        self.assertEqual(set(listed[0]), {'name', 'domain', 'expires'})
        self.assertEqual(self.json('cookies', 'example.com')['cookies'], [])

    def test_one_shots_leave_no_tabs_behind(self):
        self.assertExit(self.umbra('tabs'), 0)
        for args, code in [(['read', self.srv.url('/a')], 0), (['read', self.srv.url('/stall'), '--timeout', '1'], 2),
                           (['fetch', self.srv.url('/b')], 0), (['fetch', self.srv.url('/stall'), '--timeout', '1'], 2)]:
            with self.subTest(args):
                before = self.json('tabs')['tabs']
                self.assertExit(self.umbra(*args, input='return 1'), code)
                self.assertEqual(self.json('tabs')['tabs'], before, 'a one-shot left its scratch tab open')

    def test_status_reports_the_daemon(self):
        self.open(self.srv.url('/a'))
        time.sleep(1.5)
        status = self.status()
        self.assertGreaterEqual(status['uptime'], 1, 'uptime counts from the daemon\'s start')
        self.assertEqual((status['profile'], status['idleLimit']), (self.profile, 60))
        self.assertEqual([t['title'] for t in status['tabs']], ['Page A'])
        self.assertIn(f'profile {self.profile}', self.umbra('status').text)


class RestingTabTests(SessionCase):
    """A tab no command uses rests: hidden after a third of UMBRA_IDLE, as a background tab is, and
    closed after all of it, so the tabs agents leave behind don't pile up in a daemon that never idles."""

    @classmethod
    def setUpClass(cls):
        cls.srv = srv = Server()
        srv.page('/a', '<title>A</title><h1>Resting</h1><p style="color:#c03">text that must render</p>'
                       '<script>window.seen = []; addEventListener("visibilitychange", () => seen.push(document.visibilityState))</script>')
        srv.page('/b', '<title>B</title>')

    @classmethod
    def tearDownClass(cls):
        cls.srv.close()

    def tabs(self):
        return [t['tab'] for t in self.status()['tabs']]  # status keeps the daemon in use, not any tab

    def test_a_tab_left_unused_closes_and_says_so(self):
        self.assertExit(self.umbra('open', self.srv.url('/a'), env={'UMBRA_IDLE': '4'}), 0)
        self.assertExit(self.umbra('--tab', 'kept', 'open', self.srv.url('/b')), 0)
        self.assertExit(self.umbra('tab', 'select', 'main'), 0)

        def only_kept():
            self.assertExit(self.umbra('--tab', 'kept', 'eval', 'return 1'), 0)
            return self.tabs() == ['kept']
        self.assertTrue(wait_for(only_kept, 15, step=0.5), self.tabs())
        r = self.umbra('eval', 'return 1')
        self.assertExit(r, 64)
        self.assertSays(r, 'no open tab')  # the current tab closed: commands never fall to another agent's tab
        r = self.umbra('--tab', 'main', 'eval', 'return 1')
        self.assertExit(r, 64)
        self.assertSays(r, 'main closed after 4 seconds unused')
        self.assertEqual(self.json('--tab', 'main', 'open', self.srv.url('/b'))['title'], 'B')

    def test_a_tab_stays_open_while_a_command_runs_on_it(self):
        self.assertExit(self.umbra('--tab', 'slow', 'open', self.srv.url('/a'), env={'UMBRA_IDLE': '2'}), 0)
        r = self.umbra('--tab', 'slow', 'wait', '--ms', '6000')
        self.assertExit(r, 0)
        self.assertEqual(self.tabs(), ['slow'])

    def test_popups_rest_too(self):
        self.assertExit(self.umbra('open', self.srv.url('/a'), env={'UMBRA_IDLE': '4'}), 0)
        self.assertExit(self.umbra('eval', 'window.open("/b")'), 0)
        self.assertEqual(len(wait_for(lambda: len(self.tabs()) == 2 and self.tabs(), 5, 0.2)), 2)

        def popup_gone():
            self.assertExit(self.umbra('eval', 'return 1'), 0)
            return self.tabs() == ['main']
        self.assertTrue(wait_for(popup_gone, 15, step=0.5), self.tabs())

    @unittest.skipUnless(base.window_server(), 'no window server (not in an Aqua session)')
    def test_a_resting_tab_is_hidden_until_the_next_command(self):
        self.assertExit(self.umbra('open', self.srv.url('/a'), env={'UMBRA_IDLE': '9'}), 0)
        before, after = self.tmp / 'before.png', self.tmp / 'after.png'
        self.assertExit(self.umbra('screenshot', str(before)), 0)
        pids = daemon_of(self.profile)
        hidden = wait_for(lambda: self.tabs() and not [w for w in window_report(pids)['windows'] if w['onscreen']], 8, 0.5)
        self.assertTrue(hidden, 'the unused tab was never hidden')
        self.assertEqual(self.eval_text('return [document.visibilityState, seen]'), '["visible",["hidden","visible"]]',
                         'the page saw itself hidden, and visible again before the command ran')
        self.assertEqual(self.eval_text(VISIBLE), '["visible",true]', 'animation frames come again')
        self.assertExit(self.umbra('screenshot', str(after)), 0)
        self.assertEqual(after.read_bytes(), before.read_bytes(), 'the woken tab renders as before')

    def eval_text(self, script):
        r = self.umbra('eval', script)
        self.assertExit(r, 0)
        return r.text.rstrip('\n')


# MARK: The sign-in window

def app_type(pid):
    """How the system runs a process: Foreground (Dock icon), UIElement, or BackgroundOnly."""
    asn = subprocess.run(['lsappinfo', 'find', f'pid={pid}'], capture_output=True, text=True, timeout=10).stdout.strip()
    info = subprocess.run(['lsappinfo', 'info', asn], capture_output=True, text=True, timeout=10).stdout
    found = re.search(r'type="(\w+)"', info)
    return found.group(1) if found else None


@unittest.skipUnless(base.window_server(), 'no window server (not in an Aqua session): skipping the sign-in window')
@unittest.skipIf(os.environ.get('UMBRA_TEST_GUI') == '0', 'UMBRA_TEST_GUI=0')
class SignInWindowTests(SessionCase):
    """The window signs a person in and then gets out of the way: a site that is signed in is
    never loaded, so the window can't be used to browse it."""

    @classmethod
    def setUpClass(cls):
        # Logos are cached for every profile: start without the test hosts' entries, so each run asks afresh.
        for host in ['127.0.0.1', 'localhost']:
            (base.LOCKS / 'Logos' / host).unlink(missing_ok=True)
        cls.srv = srv = Server()
        signed = [('Set-Cookie', 'sess=on; Path=/; Max-Age=3600')]
        # A single-page app with no cookie or probe in the sites file: signed in, its login page moves on to
        # /app/home by script; signed out, it asks for a password. /app-signin signs in by script, but only
        # where a person can see the page, as only a person would.
        srv.route('/app')(lambda h: h.reply(200, '<title>App</title><script>setTimeout(() => location.replace("/app/home"), 300)</script>'
                                            if h.req.cookies.get('sess') == 'on' else '<title>Log in</title><input type=password>'))
        srv.page('/app/home', '<title>Home</title>')
        srv.page('/app-signin', '<title>Log in</title><input type=password><script>if (document.visibilityState == "visible")'
                                ' setTimeout(async () => { await fetch("/set"); location = "/app/home" }, 800)</script>')
        srv.page('/set', '<title>set</title>', headers=signed)
        # A sign-in by script (XHR), then the site moves on to its feed four seconds later.
        srv.page('/login-xhr', '<title>Log in</title><script>setTimeout(async () => { await fetch("/set");'
                               ' setTimeout(() => location = "/feed", 4000) }, 300)</script>')
        srv.page('/feed', '<title>feed</title>')
        # A sign-in by redirect, as single sign-on ends: the cookie comes with a 302 to the home page.
        srv.page('/login-redirect', '<title>Log in</title><script>setTimeout(() => location = "/do-login", 300)</script>')
        srv.route('/do-login')(lambda h: h.redirect('/home', headers=signed))
        srv.page('/home', '<title>home</title><script>fetch("/home-ran")</script>')
        srv.page('/home-ran', 'ran')
        srv.route('/api/me')(lambda h: h.reply(200 if h.req.cookies.get('sess') == 'on' else 401, '{}', 'application/json'))
        srv.page('/set-stale', '<title>stale</title>', headers=[('Set-Cookie', 'sess=stale; Path=/; Max-Age=3600')])
        srv.page('/login-never', '<title>Log in</title>')
        # An account Umbra doesn't know yet: a visitor cookie as the page loads, then a sign-in by script.
        srv.page('/login-learn', '<title>Log in</title><script>setTimeout(() => fetch("/do-auth").then(() => location = "/welcome"), 1000)</script>',
                 headers=[('Set-Cookie', 'visitor_session=anon; Path=/; Max-Age=3600')])
        srv.page('/welcome', '<title>Welcome</title>')
        # A login page that sets a session-like cookie of its own while nobody signs in.
        srv.page('/login-guest', '<title>Log in</title><script>setTimeout(() => fetch("/guest"), 800)</script>')
        srv.page('/guest', 'ok', headers=[('Set-Cookie', 'guest_session=g; Path=/; Max-Age=3600')])
        srv.route('/api/flaky')(lambda h: h.reply(503, 'busy', 'text/plain'))
        srv.page('/do-auth', 'ok', headers=[('Set-Cookie', 'app_session=yes; Path=/; Max-Age=3600; HttpOnly'),
                                            ('Set-Cookie', 'csrftoken=t; Path=/; Max-Age=3600')])
        # Sign-ins whose cookie is session-only (no expiry): by script, as /login-xhr and /login-learn sign in.
        srv.page('/login-brief', '<title>Log in</title><script>setTimeout(() => fetch("/set-brief"), 300)</script>')
        srv.page('/set-brief', 'ok', headers=[('Set-Cookie', 'sess=on; Path=/')])
        srv.page('/login-plain', '<title>Log in</title><script>setTimeout(() => fetch("/do-auth-brief").then(() => location = "/home-plain"), 1000)</script>',
                 headers=[('Set-Cookie', 'visitor_session=anon; Path=/; Max-Age=3600')])
        srv.page('/do-auth-brief', 'ok', headers=[('Set-Cookie', 'app_session=yes; Path=/; HttpOnly'),
                                                  ('Set-Cookie', 'csrftoken=t; Path=/; Max-Age=3600')])
        srv.page('/home-plain', '<title>Welcome</title>')

    @classmethod
    def tearDownClass(cls):
        cls.srv.close()

    def setUp(self):
        super().setUp()
        self.sites = base.write_sites([
            {'name': 'Xhr', 'domain': '127.0.0.1', 'login': self.srv.url('/login-xhr'), 'cookie': 'sess'},
            {'name': 'Probe', 'domain': 'localhost', 'login': self.srv.url('/login-redirect', 'localhost'), 'probe': '/api/me'},
            {'name': 'Never', 'domain': '127.0.0.1', 'login': self.srv.url('/login-never'), 'cookie': 'never'},
            {'name': 'Both', 'domain': '127.0.0.1', 'login': self.srv.url('/login-xhr'), 'cookie': 'sess', 'probe': '/api/me'},
        ])
        self.mark = len(self.srv.log)  # the server is shared: this test's requests come after

    def run_umbra(self, *args, **kwargs):
        return self.umbra(*args, sites=self.sites, guard=False, timeout=90, **kwargs)

    def requests(self, path=None):
        return [r for r in self.srv.log[self.mark:] if path is None or r.path == path]

    def logins(self):
        return [r.path for r in self.requests() if r.path.startswith('/login')]

    STATUSES = {'Signed in', 'Signed out', 'Not checked', "Couldn't check", 'Checking…'}

    def row(self, pid, name):
        """What the window shows for a site: (its status, whether its Sign In button shows), or None while it isn't listed."""
        items = texts(pid)
        if items is None:
            self.skipTest('reading the window needs the Accessibility permission for this terminal')
        if ('AXStaticText', name) not in items:
            return None
        start = items.index(('AXStaticText', name))
        end = next((i for i in range(start, len(items)) if items[i][1] == f'More for {name}'), len(items))
        cells = items[start:end]
        statuses = [text for role, text in cells if role == 'AXStaticText' and text in self.STATUSES]
        self.assertEqual(len(statuses), 1, f'{name} should show one status: {cells}')
        return statuses[0], ('AXButton', 'Sign In') in cells

    def assertShows(self, pid, name, status, sign_in, timeout=10):
        wait_for(lambda: self.row(pid, name) == (status, sign_in), timeout, step=0.2)
        self.assertEqual(self.row(pid, name), (status, sign_in), f'{name} in the window: (status, Sign In shows)')

    def pick(self, pid, label, item):
        """Whether the menu item had a checkmark when picked."""
        picked = pick(pid, label, item)
        if picked is None:
            self.skipTest('the menu needs the Accessibility permission for this terminal')
        self.assertTrue(picked[0], f'no {item} in the menu {label}')
        return picked[1]

    def test_a_sign_in_by_script_closes_the_page_before_the_site_moves_on(self):
        r = self.run_umbra('sign-in', 'Xhr')
        self.assertExit(r, 0)
        self.assertEqual(r.text, 'Xhr: signed in\n')
        time.sleep(5)  # past the moment the page would have gone to its feed
        self.assertEqual(self.requests('/feed'), [], 'the signed-in site was loaded')
        self.assertInvisible(self.profile, 'sign-in done', settle=3)
        [pid] = daemon_pids(self.profile)
        self.assertEqual(app_type(pid), 'BackgroundOnly', 'no Dock icon once the window is gone')

    def test_a_probed_site_is_checked_before_its_page_shows(self):
        r = self.run_umbra('sign-in', 'Probe')
        self.assertExit(r, 0)
        self.assertEqual(r.text, 'Probe: signed in\n')
        self.assertTrue(self.requests('/home'), 'the sign-in never reached the home page')
        time.sleep(1)
        self.assertEqual(self.requests('/home-ran'), [], 'the signed-in page was rendered')
        self.assertEqual([q.cookies.get('sess') for q in self.requests('/api/me')][-1], 'on')
        self.assertInvisible(self.profile, 'sign-in done', settle=3)

    def test_signed_in_sites_are_never_loaded(self):
        for host in ['127.0.0.1', 'localhost']:
            self.assertExit(self.run_umbra('open', self.srv.url('/set', host)), 0)
        before = len(self.requests())
        r = self.run_umbra('sign-in', 'Xhr', 'Probe')
        self.assertExit(r, 0)
        self.assertEqual(r.text, 'Xhr: signed in\nProbe: signed in\n')
        self.assertLess(r.elapsed, 15)
        self.assertInvisible(self.profile, 'already signed in')  # no window at all
        # Opening Umbra: the window lists the sites, and still loads none of them.
        r = self.run_umbra('sign-in')
        self.assertExit(r, 0)
        [pid] = daemon_pids(self.profile)
        self.assertTrue(wait_for(lambda: app_type(pid) == 'Foreground', 5), 'the window should come with a Dock icon')
        shown = wait_for(lambda: [w for w in window_report([pid])['windows']
                                  if w['onscreen'] and w['alpha'] > 0 and w['visibleArea'] > 0], 5)
        self.assertTrue(shown, 'the sign-in window never showed')
        time.sleep(2)
        self.assertEqual(self.logins(), [], 'a login page was loaded for a signed-in site')
        # Only the probe reaches a signed-in site with its session; the window's logo lookup goes without cookies.
        icons = {'/apple-touch-icon.png', '/favicon.ico'}
        self.assertEqual({r.path for r in self.requests()[before:] if not (r.path in icons and not r.cookies)} - {'/api/me'}, set(),
                         'only the probe may reach a signed-in site')
        self.assertExit(self.run_umbra('tabs'), 0)  # agents keep working while the window is up

    def test_a_session_the_site_ended_is_signed_in_again(self):
        # The cookie is still there, but the site no longer accepts it: the probe tells.
        self.assertExit(self.run_umbra('open', self.srv.url('/set-stale')), 0)
        r = self.run_umbra('sign-in', 'Both')
        self.assertExit(r, 0)
        self.assertEqual(r.text, 'Both: signed in\n')
        self.assertEqual(self.logins(), ['/login-xhr'], 'the dead session should have opened the login page')
        self.assertEqual([q.cookies.get('sess') for q in self.requests('/api/me')], ['stale'],
                         'the stale cookie is checked once; the sign-in counts when its value changes')
        r = self.run_umbra('sign-in', 'Both')
        self.assertExit(r, 0)
        self.assertEqual(self.logins(), ['/login-xhr'], 'signed in now: no page')
        time.sleep(5)
        self.assertEqual(self.requests('/feed'), [])

    def test_a_script_checked_session_waits_for_real_auth_after_the_cookie_changes(self):
        self.srv.page('/login-rendered', '''<title>Log in</title><script>
          setTimeout(() => fetch('/set'), 200);
          setTimeout(() => fetch('/set-verified'), 1500);
        </script>''')
        self.srv.page('/set-verified', 'ok', headers=[('Set-Cookie', 'verified=on; Path=/; Max-Age=3600')])
        self.srv.route('/api/verified')(lambda h: h.reply(200 if h.req.cookies.get('verified') == 'on' else 401,
                                                       '{}', 'application/json'))
        self.sites = base.write_sites([{'name': 'Rendered', 'domain': '127.0.0.1',
                                      'login': self.srv.url('/login-rendered'), 'cookie': 'sess',
                                      'probe': '/home', 'probeScript': "return (await fetch('/api/verified')).ok;"}])
        self.assertExit(self.run_umbra('open', self.srv.url('/set-stale')), 0)
        r = self.run_umbra('sign-in', 'Rendered')
        self.assertExit(r, 0)
        self.assertEqual(r.text, 'Rendered: signed in\n')
        self.assertTrue(self.requests('/login-rendered'), 'a stale cookie must not skip sign-in')
        checks = self.requests('/api/verified')
        self.assertTrue(any(q.cookies.get('sess') == 'on' and q.cookies.get('verified') != 'on' for q in checks),
                        'a fresh cookie must not close the page before authenticated verification')
        self.assertEqual(checks[-1].cookies.get('verified'), 'on')
        self.assertInvisible(self.profile, 'script-checked sign-in done', settle=3)

    def test_an_added_account_learns_its_sign_in_cookie(self):
        self.sites = base.write_sites([{'name': 'Xhr', 'domain': '127.0.0.1', 'login': self.srv.url('/login-xhr'), 'cookie': 'sess'}])
        r = self.run_umbra('sign-in', self.srv.url('/login-learn', 'localhost'), '--save', 'Xhr')
        self.assertExit(r, 64)  # a name already taken
        proc = base.spawn('sign-in', self.srv.url('/login-learn', 'localhost'), '--save', 'Learnt',
                          profile=self.profile, sites=self.sites)
        self.srv.wait_for('/welcome')
        time.sleep(0.5)
        [pid] = daemon_pids(self.profile)
        pressed = press(pid, 'Done')
        if pressed is None:
            proc.kill()
            self.skipTest('pressing Done needs the Accessibility permission for this terminal')
        self.assertTrue(pressed, 'no Done button')
        out, err = proc.communicate(timeout=30)
        self.assertEqual((proc.returncode, out.decode()), (0, 'Learnt: signed in\n'), err)
        saved = json.loads(self.sites.read_text())
        self.assertEqual(saved[1], {'name': 'Learnt', 'domain': 'localhost', 'login': self.srv.url('/login-learn', 'localhost'),
                                    'cookie': 'app_session'})
        self.assertEqual(saved[0]['name'], 'Xhr', 'the other sites stay as they were')
        self.assertInvisible(self.profile, 'window closed', settle=3)
        # From now on it is checked like any site: signed in, so nothing opens.
        r = self.run_umbra('sign-in', 'Learnt')
        self.assertExit(r, 0)
        self.assertEqual(r.text, 'Learnt: signed in\n')
        self.assertEqual(self.logins(), ['/login-learn'])

    def test_the_callers_sites_file_counts_not_the_daemons(self):
        self.assertExit(self.run_umbra('tabs'), 0)  # the daemon starts with self.sites
        other = base.write_sites([{'name': 'Other', 'domain': '127.0.0.1', 'login': self.srv.url('/login-xhr'), 'cookie': 'sess'}])
        self.assertExit(self.run_umbra('open', self.srv.url('/set')), 0)
        r = self.umbra('sign-in', 'Other', sites=other, guard=False)
        self.assertExit(r, 0)
        self.assertEqual(r.text, 'Other: signed in\n')
        r = self.umbra('sign-out', 'Typo', sites=other)
        self.assertExit(r, 64)
        self.assertSays(r, 'Typo', 'Other')
        r = self.umbra('sign-out', 'Other', sites=other)
        self.assertExit(r, 0)
        self.assertEqual(r.text, 'signed out of Other (1 cookie removed)\n')

    def test_a_check_that_cant_tell_opens_nothing_and_keeps_the_session(self):
        # The probe answers 503: the session may well be fine, so it is neither shown nor deleted.
        self.sites = base.write_sites([{'name': 'Flaky', 'domain': '127.0.0.1', 'login': self.srv.url('/login-xhr'),
                                        'cookie': 'sess', 'probe': '/api/flaky'}])
        self.assertExit(self.run_umbra('open', self.srv.url('/set')), 0)
        r = self.run_umbra('sign-in', 'Flaky', '--timeout', '10')
        self.assertExit(r, 4)
        self.assertSays(r, 'Flaky', "couldn't tell")
        self.assertEqual(self.logins(), [], 'nothing should open')
        self.assertIn('sess\t127.0.0.1', self.run_umbra('cookies', '127.0.0.1').text, 'the session must survive')
        self.assertInvisible(self.profile, 'nothing opened')

    def test_closing_an_added_account_without_a_sign_in_learns_nothing(self):
        self.sites = base.write_sites([])
        proc = base.spawn('sign-in', self.srv.url('/login-guest'), '--save', 'Shop', profile=self.profile, sites=self.sites)
        self.srv.wait_for('/guest')
        time.sleep(0.5)
        [pid] = daemon_pids(self.profile)
        self.assertTrue(quit_app(pid))
        out, err = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 3, err)
        self.assertIn('no sign-in cookie appeared for Shop', err.decode())
        self.assertEqual(json.loads(self.sites.read_text()), [{'name': 'Shop', 'domain': '127.0.0.1', 'login': self.srv.url('/login-guest')}])

    def test_quit_ends_a_handoff_but_not_the_daemon(self):
        self.assertExit(self.run_umbra('open', self.srv.url('/welcome')), 0)
        [pid] = daemon_pids(self.profile)
        proc = base.spawn('handoff', 'look', '--timeout', '60', profile=self.profile, sites=self.sites)
        self.assertTrue(wait_for(lambda: [w for w in window_report([pid])['windows'] if w['onscreen'] and w['alpha'] > 0 and w['visibleArea'] > 0], 10))
        self.assertTrue(quit_app(pid))  # Command-Q in the handoff window, as the Dock or menu sends it
        out, err = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 0, err)
        self.assertEqual(daemon_pids(self.profile), [pid], 'Quit must not stop the daemon')
        self.assertInvisible(self.profile, 'handoff ended', settle=3)

    def test_with_no_sites_yet_the_window_opens_empty(self):
        self.sites = base.write_sites([])
        r = self.run_umbra('sign-in')
        self.assertExit(r, 0)
        self.assertEqual(r.text, "Umbra's sign-in window is open\n")
        [pid] = daemon_pids(self.profile)
        self.assertTrue(wait_for(lambda: app_type(pid) == 'Foreground', 5))

    def test_a_request_that_times_out_hands_the_window_to_the_next(self):
        never = base.spawn('sign-in', 'Never', '--timeout', '4', profile=self.profile, sites=self.sites)
        self.srv.wait_for('/login-never')
        xhr = base.spawn('sign-in', 'Xhr', '--timeout', '40', profile=self.profile, sites=self.sites)
        out, err = never.communicate(timeout=30)
        self.assertEqual(never.returncode, 2, err)
        out, err = xhr.communicate(timeout=40)
        self.assertEqual((xhr.returncode, out.decode()), (0, 'Xhr: signed in\n'), err)
        self.assertInvisible(self.profile, 'both done', settle=3)

    def test_no_sign_in_in_time_hides_the_window_again(self):
        r = self.run_umbra('sign-in', 'Never', '--timeout', '3')
        self.assertExit(r, 2)
        self.assertSays(r, 'Never')
        self.assertEqual(self.logins(), ['/login-never'])
        self.assertInvisible(self.profile, 'sign-in timed out', settle=3)
        [pid] = daemon_pids(self.profile)
        self.assertEqual(app_type(pid), 'BackgroundOnly')

    def test_sign_out_removes_only_that_sites_cookies(self):
        for host in ['127.0.0.1', 'localhost']:
            self.assertExit(self.run_umbra('open', self.srv.url('/set', host)), 0)
        r = self.run_umbra('sign-out', 'Xhr')
        self.assertExit(r, 0)
        self.assertEqual(r.text, 'signed out of Xhr (1 cookie removed)\n')
        self.assertEqual(self.run_umbra('cookies', '127.0.0.1').text, '')
        self.assertIn('sess\tlocalhost', self.run_umbra('cookies', 'localhost').text)

    def test_showing_the_window_again_checks_every_site_again(self):
        self.assertExit(self.run_umbra('sign-in'), 0)
        [pid] = daemon_pids(self.profile)
        for name in ['Xhr', 'Probe', 'Never', 'Both']:
            self.assertShows(pid, name, 'Signed out', True)
        # Signed in outside the window, by an agent: the window can't know until it checks again.
        for host in ['127.0.0.1', 'localhost']:
            self.assertExit(self.run_umbra('open', self.srv.url('/set', host)), 0)
        self.assertExit(self.run_umbra('sign-in'), 0)
        for name in ['Xhr', 'Probe', 'Both']:
            self.assertShows(pid, name, 'Signed in', False)
        self.assertShows(pid, 'Never', 'Signed out', True)

    def test_a_site_with_no_check_is_asked_through_its_login_page(self):
        self.sites = base.write_sites([{'name': 'App', 'domain': '127.0.0.1', 'login': self.srv.url('/app')}])
        self.assertExit(self.run_umbra('sign-in'), 0)
        [pid] = daemon_pids(self.profile)
        self.assertShows(pid, 'App', 'Signed out', True)
        self.assertExit(self.run_umbra('open', self.srv.url('/set')), 0)
        self.assertExit(self.run_umbra('sign-in'), 0)  # shown again: checked again
        self.assertShows(pid, 'App', 'Signed in', False)

    def test_a_signed_in_site_with_no_check_never_opens(self):
        # The window said such a site wasn't signed in, and its Sign In showed the site, signed in.
        self.sites = base.write_sites([{'name': 'App', 'domain': '127.0.0.1', 'login': self.srv.url('/app')}])
        self.assertExit(self.run_umbra('open', self.srv.url('/set')), 0)
        r = self.run_umbra('sign-in', 'App')
        self.assertExit(r, 0)
        self.assertEqual(r.text, 'App: signed in\n')
        self.assertInvisible(self.profile, 'already signed in')  # no window at all

    def test_a_site_with_no_check_closes_its_page_once_it_lets_the_person_in(self):
        self.sites = base.write_sites([{'name': 'App', 'domain': '127.0.0.1', 'login': self.srv.url('/app-signin')}])
        r = self.run_umbra('sign-in', 'App')  # no Done: the page moves on to /app/home and closes
        self.assertExit(r, 0)
        self.assertEqual(r.text, 'App: signed in\n')
        self.assertTrue(self.requests('/app/home'))
        self.assertInvisible(self.profile, 'window closed', settle=3)

    def test_a_session_only_sign_in_cookie_gets_keep_session(self):
        site = {'name': 'Brief', 'domain': '127.0.0.1', 'login': self.srv.url('/login-brief'), 'cookie': 'sess'}
        self.sites = base.write_sites([site])
        r = self.run_umbra('sign-in', 'Brief')
        self.assertExit(r, 0)
        self.assertEqual(r.text, 'Brief: signed in\n')
        self.assertEqual(json.loads(self.sites.read_text()), [{**site, 'keepSession': True}], 'saved by the time sign-in answers')

    def test_keep_signed_in_from_the_more_menu(self):
        site = {'name': 'Kept', 'domain': '127.0.0.1', 'login': self.srv.url('/login-never'), 'cookie': 'sess'}
        self.sites = base.write_sites([site])
        self.addCleanup(base.bootout, self.profile)
        plist = base.AGENTS / f'{base.agent_label(self.profile)}.plist'
        self.assertExit(self.run_umbra('sign-in'), 0)
        [pid] = daemon_pids(self.profile)
        command = subprocess.run(['ps', '-E', '-ww', '-o', 'command=', '-p', str(pid)], capture_output=True, text=True).stdout
        agents = re.findall(r' UMBRA_AGENTS=(\S+)', command)  # only this variable: the rest of the environment stays out of the log
        self.assertEqual(agents, [str(base.AGENTS)], 'the daemon installs the agent: it must have the test folder')
        self.assertShows(pid, 'Kept', 'Signed out', True)
        self.assertFalse(self.pick(pid, 'More for Kept', 'Keep Signed In'), 'not kept yet: no checkmark')
        self.assertTrue(wait_for(lambda: plist.exists() and base.agent_loaded(self.profile), 10), 'the launch agent never loaded')
        self.assertEqual(json.loads(self.sites.read_text()), [{**site, 'keepSignedIn': True}])
        self.assertFalse((Path.home() / 'Library/LaunchAgents' / plist.name).exists(), 'UMBRA_AGENTS was ignored')
        self.assertTrue(self.pick(pid, 'More for Kept', 'Keep Signed In'), 'kept: the item has a checkmark')
        self.assertTrue(wait_for(lambda: not plist.exists(), 10), 'the launch agent stayed')
        self.assertFalse(base.agent_loaded(self.profile))
        self.assertEqual(json.loads(self.sites.read_text()), [site])


if __name__ == '__main__':
    base.main()
