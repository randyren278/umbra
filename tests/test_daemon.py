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

    def diagnose(self, pid):
        """What the daemon was doing when a client gave up: does it still answer, and its main thread."""
        status = subprocess.run([base.UMBRA, 'tabs', '--json'], env=base.environment(self.profile, None, None),
                                capture_output=True, text=True, timeout=20)
        sample = subprocess.run(['sample', str(pid), '2', '-mayDie'], capture_output=True, text=True, timeout=30)
        main = sample.stdout.split('Thread_')[1][:4000] if 'Thread_' in sample.stdout else sample.stdout[:2000]
        return f'tabs exit {status.returncode}: {status.stdout[:1500]} {status.stderr[-300:]}\nmain thread:\n{main}'

    def test_five_concurrent_clients(self):
        self.assertExit(self.umbra('tabs'), 0)
        [pid] = daemon_pids(self.profile)
        procs = [base.spawn('open', '--tab', f'c{n}', self.srv.url(f'/page?n={n}'), profile=self.profile) for n in range(5)]
        for proc in procs:
            try:
                out, err = proc.communicate(timeout=60)
            except subprocess.TimeoutExpired:
                self.fail(f'{proc.args[1:4]} gave no answer in 60 s\n{self.diagnose(pid)}')
            self.assertEqual(proc.returncode, 0, err)
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


if __name__ == '__main__':
    base.main()
