"""End-to-end tests for Umbra: every command and exit code, against real 127.0.0.1 servers.

Every run gets a throwaway UMBRA_PROFILE, never the default (signed-in) store, and a sites file
the test controls, never ~/.config/umbra/sites.json. The profile stores and lock files are
removed when the suite exits, pass or fail. The sign-in and handoff windows open unseen
(UMBRA_UNSEEN=1: transparent, and never in front), so the Mac stays usable while the suite runs;
UMBRA_TEST_GUI=1 shows them for real, taking focus, and checks what only a real window can show.

    python3 tests/test_umbra.py [-v] [TestClass.test_name ...]
    UMBRA=/path/to/umbra python3 tests/test_umbra.py
"""
import atexit
import datetime
import hashlib
import json
import os
import plistlib
import random
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True  # no __pycache__ in tests/
sys.path.insert(0, str(Path(__file__).resolve().parent))
from server import Server, free_port  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
UMBRA = os.environ.get('UMBRA') or str(REPO / 'build/Umbra.app/Contents/MacOS/umbra')
GUI = os.environ.get('UMBRA_TEST_GUI') == '1'
STORES = Path.home() / 'Library/WebKit/com.randyren.umbra/WebsiteDataStore'
LOCKS = Path.home() / 'Library/Application Support/Umbra'
DOWNLOADS = Path.home() / 'Downloads/Umbra'  # page downloads land in DOWNLOADS/<profile>
DOWNLOADS_EXISTED = DOWNLOADS.exists()
REAL_STORE = '6f1c7a52-3d0b-4e8f-9a61-f1a57c0de001'
RUN = 'test-' + secrets.token_hex(4)
TMP = Path(tempfile.mkdtemp(prefix='umbra-tests-'))
NO_SITES = TMP / 'no-sites.json'  # never created: no sites at all
AGENTS = TMP / 'agents'  # UMBRA_AGENTS for every run: no launch agent ever lands in ~/Library/LaunchAgents
AGENTS.mkdir()
AGENT = 'com.randyren.umbra.keep-signed-in'  # the default profile's label: tests only ever use AGENT.<profile>

profiles = []
children = []


def store_dir(name):
    """Where WebKit keeps UMBRA_PROFILE=name's store (umbra.swift's uuid(named:), lowercased)."""
    h = hashlib.sha256(f'umbra profile {name}'.encode()).hexdigest()[:32]
    return STORES / '-'.join((h[:8], h[8:12], h[12:16], h[16:20], h[20:]))


def profile_files(name):
    """The profile's store, its downloads and every per-profile file (store-NAME.lock, daemon-NAME.sock…)."""
    return [store_dir(name), DOWNLOADS / name, *LOCKS.glob(f'*-{name}.*')]


def daemons():
    """Running `umbra serve` processes as {pid: profile}, '' for the default profile."""
    listing = subprocess.run(['ps', '-axww', '-E', '-o', 'pid=,command='], capture_output=True, text=True, timeout=30).stdout
    found = {}
    for words in map(str.split, listing.splitlines()):
        if len(words) > 2 and words[1].endswith('/umbra') and words[2] == 'serve':
            named = [w.split('=', 1)[1] for w in words[3:] if w.startswith('UMBRA_PROFILE=')]
            found[int(words[0])] = named[0] if named else ''
    return found


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def stop_daemons(names):
    """Stops these profiles' daemons as a user would (`umbra stop`), killing any that linger."""
    running = {pid: name for pid, name in daemons().items() if name in names}
    for name in set(running.values()) | {n for n in names if (LOCKS / f'daemon-{n}.sock').exists()}:
        try:
            subprocess.run([UMBRA, 'stop'], env=environment(name, None), capture_output=True, timeout=10)
        except subprocess.TimeoutExpired:
            pass
    deadline = time.monotonic() + 6
    while any(map(alive, running)) and time.monotonic() < deadline:
        time.sleep(0.05)
    for pid in filter(alive, running):
        os.kill(pid, signal.SIGKILL)


def new_profile():
    name = f'{RUN}-{len(profiles)}'
    assert name.startswith('test-') and store_dir(name).name != REAL_STORE
    profiles.append(name)
    return name


def agent_label(name):
    """A test profile's keep-signed-in launch agent label (never the default profile's)."""
    assert name in profiles and name.startswith('test-')
    return f'{AGENT}.{name}'


def launchctl(*args):
    try:
        return subprocess.run(['launchctl', *args], capture_output=True, timeout=10).returncode
    except (OSError, subprocess.SubprocessError):
        return None


def agent_loaded(name):
    return launchctl('print', f'gui/{os.getuid()}/{agent_label(name)}') == 0


def bootout(name):
    launchctl('bootout', f'gui/{os.getuid()}/{agent_label(name)}')


def cleanup():
    for child in children:
        if child.poll() is None:
            child.kill()
            child.wait()
    for name in profiles:  # before the stores go: a loaded agent would run umbra against them again
        bootout(name)
    stop_daemons(profiles)
    # A WebKit helper can outlive its umbra by a moment, so check the stores stay gone.
    for _ in range(3):
        for name in profiles:
            for path in profile_files(name):
                shutil.rmtree(path, ignore_errors=True) if path.is_dir() else path.unlink(missing_ok=True)
        time.sleep(0.2)
        if not any(path.exists() for name in profiles for path in profile_files(name)):
            break
    if not DOWNLOADS_EXISTED and DOWNLOADS.is_dir() and not any(DOWNLOADS.iterdir()):
        DOWNLOADS.rmdir()
    shutil.rmtree(TMP, ignore_errors=True)


def interrupted(signum, frame):
    # unittest would record SystemExit as a test error and carry on, so clean up and leave here.
    cleanup()
    os._exit(128 + signum)


atexit.register(cleanup)
for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
    signal.signal(sig, interrupted)


class Result:
    def __init__(self, code, out, err, elapsed):
        self.code = code
        self.out = out
        self.err = err.decode(errors='replace')
        self.elapsed = elapsed

    @property
    def text(self):
        return self.out.decode()


def environment(profile, sites, env=None):
    """A test run's environment: its own profile, sites file and launch agents folder; a daemon it
    starts idles out in a minute."""
    assert profile in profiles, 'tests must use new_profile(): never the default store, always cleaned up'
    return {**os.environ, 'UMBRA_PROFILE': profile, 'UMBRA_SITES': str(sites or NO_SITES), 'UMBRA_IDLE': '60',
            'UMBRA_AGENTS': str(AGENTS), **({} if GUI else {'UMBRA_UNSEEN': '1'}), **(env or {})}


def spawn(*args, profile, input=None, sites=None, stdin=subprocess.DEVNULL, binary=UMBRA, cwd=None, env=None):
    """Starts umbra with input as its script (stdin is /dev/null without one)."""
    proc = subprocess.Popen([binary, *args], stdin=subprocess.PIPE if input is not None else stdin,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment(profile, sites, env),
                            cwd=cwd)
    children.append(proc)
    if input is not None:
        proc.stdin.write(input.encode() if isinstance(input, str) else input)
        proc.stdin.close()
    return proc


def umbra(*args, timeout=60, **kwargs):
    """Runs umbra to completion."""
    start = time.monotonic()
    proc = spawn(*args, **kwargs)
    try:
        out, err = proc.communicate(timeout=timeout)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    return Result(proc.returncode, out, err, time.monotonic() - start)


def write_sites(entries):
    path = TMP / f'sites-{secrets.token_hex(4)}.json'
    path.write_text(json.dumps(entries))
    return path


def expiry(text):
    return datetime.datetime.fromisoformat(text.replace('Z', '+00:00'))


class UmbraCase(unittest.TestCase):
    def setUp(self):
        self.profile = new_profile()
        self.tmp = Path(tempfile.mkdtemp(dir=TMP))

    def umbra(self, *args, **kwargs):
        kwargs.setdefault('profile', self.profile)
        return umbra(*args, **kwargs)

    def assertExit(self, result, code):
        self.assertEqual(result.code, code, f'stderr: {result.err[-800:]!r} stdout: {result.out[:300]!r}')

    def assertSays(self, result, *facts):
        """Stderr explains the failure and carries these facts; its wording is free to change."""
        self.assertTrue(result.err.strip(), 'no message on stderr')
        for fact in facts:
            self.assertIn(fact, result.err)

    def assertTimedOut(self, result, timeout):
        self.assertExit(result, 2)
        self.assertSays(result)
        self.assertGreaterEqual(result.elapsed, timeout - 0.1)
        self.assertLess(result.elapsed, timeout + 5)


FIXTURE = """<!doctype html><title>Umbra fixture</title><p id=marker>in the shadow</p>
<script>setTimeout(() => { window.__answer = 'set later' }, 300)</script>"""


class ReadTests(UmbraCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = srv = Server()
        srv.page('/', FIXTURE)
        srv.page('/probe', 'ok', ctype='text/plain')
        srv.page('/set-cookie', '<title>cookie set</title>', headers=[('Set-Cookie', 'sid=abc123; Path=/; Max-Age=600')])
        srv.page('/js-redirect', '<title>A</title><script>setTimeout(() => location.replace("/final"), 800)</script>')
        srv.page('/then-unreachable', '<title>still here</title><script>setTimeout(() => '
                 f'location.assign("http://127.0.0.1:{free_port()}/"), 100)</script>')
        srv.page('/final', '<title>Final</title><p id=done>arrived</p>')
        # The Kaltura LTI shape: a page that POSTs a hidden form, then a redirect to the player.
        form = ('<title>Launching</title><form method=post action="/lti/launch">'
                '<input type=hidden name=id_token value="tok123"><input type=hidden name=state value="s1"></form>')
        srv.page('/lti/start', form + '<script>document.forms[0].submit()</script>')
        srv.page('/lti/start-late', form + '<script>setTimeout(() => document.forms[0].submit(), 400)</script>')
        srv.route('/lti/launch')(lambda h: h.redirect('/lti/player', 302, [('Set-Cookie', 'kaltura=k1; Path=/')]))
        srv.page('/lti/player', '<title>Player</title><div id=entry>entry-42</div>')
        srv.route('/r1')(lambda h: h.redirect('/r2', 302))
        srv.route('/r2')(lambda h: h.redirect(srv.url('/r3'), 301))
        srv.route('/r3')(lambda h: h.redirect('/r4', 307))
        srv.route('/r4')(lambda h: h.redirect('/final', 303))
        srv.page('/unicode', '<title>Ünïcödé 🌑</title>')
        # A hop to a second origin after a delay the test picks: a script redirect or a posted form.
        cls.second = second = Server()
        second.page('/landing', '<title>Second</title>' + '<p>filler</p>' * 20000 + '<p id=marker>second origin</p>')
        second.route('/lti/launch')(lambda h: h.redirect('/lti/player', 302))
        second.page('/lti/player', '<title>Player</title>' + '<p>filler</p>' * 20000 + '<div id=entry>entry-42</div>')

        @srv.route('/hop')
        def hop(h):
            delay, kind = h.req.query['delay'], h.req.query['kind']
            if kind == 'js':
                h.reply(200, f'<title>First</title><script>setTimeout(() => location.assign("{second.base}/landing"), '
                             f'{delay})</script>')
            else:
                h.reply(200, f'<title>First</title><form method=post action="{second.base}/lti/launch">'
                             f'<input type=hidden name=id_token value=tok></form>'
                             f'<script>setTimeout(() => document.forms[0].submit(), {delay})</script>')
        srv.route('/stall')(lambda h: h.stall())

    @classmethod
    def tearDownClass(cls):
        cls.srv.close()
        cls.second.close()

    def read(self, path, script=None, *flags, **kwargs):
        url = path if path.startswith('http') else self.srv.url(path)
        return self.umbra('read', url, *flags, input=script, **kwargs)

    def test_string_is_printed_raw(self):
        r = self.read('/', 'return document.title')
        self.assertExit(r, 0)
        self.assertEqual(r.out, b'Umbra fixture\n')

    def test_string_ending_in_newline_is_not_doubled(self):
        r = self.read('/', 'return "two\\nlines\\n"')
        self.assertExit(r, 0)
        self.assertEqual(r.out, b'two\nlines\n')

    def test_numbers_are_printed(self):
        for script, out in [('return 42', b'42\n'), ('return 3.5', b'3.5\n'), ('return -0.25', b'-0.25\n')]:
            with self.subTest(script):
                r = self.read('/', script)
                self.assertExit(r, 0)
                self.assertEqual(r.out, out)

    def test_booleans_are_printed_as_json(self):
        for script, out in [('return true', b'true\n'), ('return 1 > 2', b'false\n')]:
            with self.subTest(script):
                r = self.read('/', script)
                self.assertExit(r, 0)
                self.assertEqual(r.out, out)

    def test_object_is_printed_as_json(self):
        r = self.read('/', 'return {a: 1, b: "two", c: [true, null], d: {e: "/path"}}')
        self.assertExit(r, 0)
        self.assertEqual(json.loads(r.out), {'a': 1, 'b': 'two', 'c': [True, None], 'd': {'e': '/path'}})
        self.assertIn(b'"/path"', r.out, 'slashes are not escaped')
        self.assertEqual(r.out.count(b'\n'), 1)

    def test_array_is_printed_as_json(self):
        r = self.read('/', 'return [1, "x", {y: 2}, [false]]')
        self.assertExit(r, 0)
        self.assertEqual(json.loads(r.out), [1, 'x', {'y': 2}, [False]])

    def test_undefined_and_null_print_nothing(self):
        for script in ['return undefined', 'let x = 1', 'return null', 'return']:
            with self.subTest(script):
                r = self.read('/', script)
                self.assertExit(r, 0)
                self.assertEqual(r.out, b'')

    def test_no_script_prints_the_page_html(self):
        for name, kwargs in [('/dev/null', {}), ('empty pipe', {'script': ''}), ('blank pipe', {'script': ' \n\t\n'})]:
            with self.subTest(name):
                r = self.read('/', **kwargs)
                self.assertExit(r, 0)
                self.assertTrue(r.text.startswith('<html'), r.text[:100])
                self.assertIn('<p id="marker">in the shadow</p>', r.text)

    def test_terminal_stdin_prints_the_page_html(self):
        # A terminal on stdin means no script; umbra must not sit waiting for input.
        main, side = os.openpty()
        try:
            r = self.umbra('read', self.srv.url('/'), stdin=side, timeout=30)
        finally:
            os.close(side)
            os.close(main)
        self.assertExit(r, 0)
        self.assertIn('<p id="marker">in the shadow</p>', r.text)

    def test_script_can_await_timers(self):
        r = self.read('/', 'await new Promise(r => setTimeout(r, 400)); return {late: true}')
        self.assertExit(r, 0)
        self.assertEqual(r.out, b'{"late":true}\n')

    def test_script_can_wait_for_a_page_global(self):
        r = self.read('/', 'while (!window.__answer) await new Promise(r => setTimeout(r, 50)); return window.__answer')
        self.assertExit(r, 0)
        self.assertEqual(r.out, b'set later\n')

    def test_script_runs_in_the_page_world(self):
        r = self.read('/', 'return [document.getElementById("marker").textContent, location.pathname]')
        self.assertExit(r, 0)
        self.assertEqual(json.loads(r.out), ['in the shadow', '/'])

    def test_a_throwing_script_exits_2(self):
        for script in ['throw new Error("boom")', 'await Promise.reject(new Error("boom"))', 'return boom.boom']:
            with self.subTest(script):
                r = self.read('/', script)
                self.assertExit(r, 2)
                self.assertEqual(r.out, b'')
                self.assertSays(r, 'boom')

    def test_a_syntax_error_exits_2(self):
        r = self.read('/', 'return {{{')
        self.assertExit(r, 2)
        self.assertSays(r)
        self.assertLess(r.elapsed, 10)

    def test_a_slow_script_exits_2_at_the_timeout(self):
        r = self.read('/', 'await new Promise(r => setTimeout(r, 10000)); return 1', '--timeout', '1')
        self.assertTimedOut(r, 1)
        self.assertEqual(r.out, b'')

    def test_a_page_that_never_loads_exits_2_at_the_timeout(self):
        r = self.read('/stall', 'return 1', '--timeout', '1')
        self.assertTimedOut(r, 1)

    def test_an_unreachable_page_exits_4(self):
        r = self.read(f'http://127.0.0.1:{free_port()}/', 'return 1', '--timeout', '20')
        self.assertExit(r, 4)
        self.assertSays(r)

    def test_cookie_present_exits_0(self):
        r = self.read('/set-cookie', 'return document.title', '--cookie', 'sid')
        self.assertExit(r, 0)
        self.assertEqual(r.out, b'cookie set\n')

    def test_cookie_absent_exits_3_without_running_the_script(self):
        token = secrets.token_hex(4)
        r = self.read('/', f'await fetch("/probe?k={token}"); return 1', '--cookie', 'sid')
        self.assertExit(r, 3)
        self.assertEqual(r.out, b'')
        self.assertSays(r, '127.0.0.1')
        self.assertFalse([q for q in self.srv.requests('/probe') if q.query.get('k') == token])

    def test_cookie_on_another_host_does_not_count(self):
        self.assertExit(self.read(self.srv.url('/set-cookie', 'localhost'), 'return 1'), 0)
        self.assertExit(self.read('/', 'return 1', '--cookie', 'sid'), 3)
        self.assertExit(self.read(self.srv.url('/', 'localhost'), 'return 1', '--cookie', 'sid'), 0)

    def test_wait_lets_a_script_redirect_finish(self):
        r = self.read('/js-redirect', 'return document.title')
        self.assertExit(r, 0)
        self.assertEqual(r.out, b'A\n', 'without --wait the first page is read')
        r = self.read('/js-redirect', 'return document.title', '--wait', '2')
        self.assertExit(r, 0)
        self.assertEqual(r.out, b'Final\n')
        self.assertGreaterEqual(r.elapsed, 2)

    def test_a_navigation_failure_after_the_page_loaded_is_ignored(self):
        r = self.read('/then-unreachable', 'return document.title', '--wait', '1')
        self.assertExit(r, 0)
        self.assertEqual(r.out, b'still here\n')

    def test_wait_lets_an_auto_submitted_form_finish(self):
        for path in ['/lti/start', '/lti/start-late']:
            with self.subTest(path):
                before = len(self.srv.requests('/lti/launch'))
                r = self.read(path, 'return document.title + "|" + document.getElementById("entry").textContent',
                              '--wait', '1.5')
                self.assertExit(r, 0)
                self.assertEqual(r.out, b'Player|entry-42\n')
                posts = self.srv.requests('/lti/launch')[before:]
                self.assertEqual([p.method for p in posts], ['POST'])
                self.assertIn(b'id_token=tok123', posts[0].body)

    def test_until_waits_across_redirects_and_forms(self):
        for path, until, title in [('/js-redirect', 'document.title == "Final"', b'Final\n'),
                                   ('/lti/start-late', 'document.title == "Player"', b'Player\n'),
                                   # throws on the first page (no #entry yet), which counts as not yet
                                   ('/lti/start-late', 'document.getElementById("entry").textContent == "entry-42"',
                                    b'Player\n')]:
            with self.subTest(path=path, until=until):
                r = self.read(path, 'return document.title', '--until', until, '--timeout', '20')
                self.assertExit(r, 0)
                self.assertEqual(r.out, title)
                self.assertLess(r.elapsed, 10)

    def test_until_follows_a_late_hop_to_another_origin(self):
        port = str(self.second.port)
        cases = [('js', f"location.port == '{port}'",
                  'return document.getElementById("marker").textContent + "|" + location.port', f'second origin|{port}'),
                 ('form', 'document.getElementById("entry") !== null',
                  'return [document.title, document.getElementById("entry").textContent, location.port].join("|")',
                  f'Player|entry-42|{port}')]
        for kind, until, script, expected in cases:
            for delay in random.sample(range(500, 3001), 2):
                with self.subTest(kind=kind, delay=delay):
                    r = self.read(f'/hop?kind={kind}&delay={delay}', script, '--until', until, '--timeout', '20')
                    self.assertExit(r, 0)
                    self.assertEqual(r.out, f'{expected}\n'.encode())
                    self.assertGreaterEqual(r.elapsed, delay / 1000)

    def test_an_invalid_until_expression_exits_2_at_the_timeout(self):
        for until in ['document.title ==', ')(', 'return true']:
            with self.subTest(until):
                r = self.read('/', 'return 1', '--until', until, '--timeout', '1')
                self.assertTimedOut(r, 1)
                self.assertEqual(r.out, b'')

    def test_until_never_true_exits_2_at_the_timeout(self):
        r = self.read('/', 'return 1', '--until', 'window.never === true', '--timeout', '1')
        self.assertTimedOut(r, 1)
        self.assertEqual(r.out, b'')

    def test_http_redirect_chain_is_followed(self):
        r = self.read('/r1', 'return [location.pathname, document.title]')
        self.assertExit(r, 0)
        self.assertEqual(json.loads(r.out), ['/final', 'Final'])

    def test_unicode_survives(self):
        r = self.read('/unicode', 'return document.title + " " + "世界".length + " ü"')
        self.assertExit(r, 0)
        self.assertEqual(r.out, 'Ünïcödé 🌑 2 ü\n'.encode())
        r = self.read('/unicode', 'return {moon: "🌑", accents: "àéîõü"}')
        self.assertExit(r, 0)
        self.assertEqual(json.loads(r.out), {'moon': '🌑', 'accents': 'àéîõü'})

    def test_large_output_is_complete(self):
        r = self.read('/', 'return "umbra🌑".repeat(600000)')
        self.assertExit(r, 0)
        expected = 'umbra🌑'.encode() * 600000 + b'\n'
        self.assertEqual(len(r.out), len(expected))
        self.assertTrue(r.out == expected)

    def test_bad_usage_exits_64(self):
        url = self.srv.url('/')
        for args in [[], [url, url], [url, '--timeout', 'soon'], [url, '--timeout', '-1'], [url, '--wait', 'x'],
                     [url, '--until']]:
            with self.subTest(args):
                r = self.umbra('read', *args)
                self.assertExit(r, 64)
                self.assertSays(r)
                self.assertEqual(r.out, b'')


class FetchTests(UmbraCase):
    JSON = b'{"ok": true, "items": [1, 2, 3], "name": "caf\xc3\xa9"}'
    TEXT = b'line one\r\nline two\n\ttabbed \xf0\x9f\x8c\x91 no newline at the end'

    @classmethod
    def setUpClass(cls):
        cls.srv = srv = Server()
        srv.page('/robots.txt', 'User-agent: *\n', ctype='text/plain')
        srv.page('/data.json', cls.JSON, ctype='application/json')
        srv.page('/text.txt', cls.TEXT, ctype='text/plain; charset=utf-8')
        srv.page('/trailing.txt', b'ends with blank lines\n\n', ctype='text/plain')
        srv.page('/canvas.json', b'while(1);[{"id":1,"name":"Assignment"}]', ctype='application/json')
        srv.page('/app/start', '<title>app</title>', headers=[
            ('Set-Cookie', 'tok=FROMPAGE; Path=/'), ('Set-Cookie', 'hidden=HTTPONLY; Path=/; HttpOnly')])
        srv.route('/api/cookies')(lambda h: h.reply(200, h.headers.get('Cookie', ''), 'text/plain'))
        srv.page('/gone', 'nothing here', status=404, ctype='text/plain')
        srv.page('/broken', '{"error": "boom"}', status=500, ctype='application/json')
        srv.page('/created', 'made', status=201, ctype='text/plain')
        srv.page('/empty', '', status=204, ctype='text/plain')
        srv.route('/old')(lambda h: h.redirect('/data.json', 302))
        srv.route('/old-gone')(lambda h: h.redirect('/gone', 301))
        srv.route('/stall')(lambda h: h.stall())
        cls.bare = bare = Server()  # no robots.txt: it answers 404
        bare.page('/data.json', cls.JSON, ctype='application/json')

    @classmethod
    def tearDownClass(cls):
        cls.srv.close()
        cls.bare.close()

    def fetch(self, url, *flags, **kwargs):
        return self.umbra('fetch', url if url.startswith('http') else self.srv.url(url), *flags, **kwargs)

    def test_json_body_is_printed_exactly(self):
        r = self.fetch('/data.json')
        self.assertExit(r, 0)
        self.assertEqual(r.out, self.JSON)

    def test_text_body_is_printed_exactly(self):
        for path, body in [('/text.txt', self.TEXT), ('/trailing.txt', b'ends with blank lines\n\n')]:
            with self.subTest(path):
                r = self.fetch(path)
                self.assertExit(r, 0)
                self.assertEqual(r.out, body)

    def test_default_from_is_robots_txt(self):
        start = len(self.srv.log)
        self.assertExit(self.fetch('/data.json'), 0)
        self.assertEqual([q.path for q in self.srv.log[start:]], ['/robots.txt', '/data.json'])

    def test_default_from_works_when_robots_txt_is_missing(self):
        r = self.umbra('fetch', self.bare.url('/data.json'))
        self.assertExit(r, 0)
        self.assertEqual(r.out, self.JSON)
        self.assertEqual([q.path for q in self.bare.log[-2:]], ['/robots.txt', '/data.json'])

    def test_from_a_custom_page(self):
        for page in ['/app/start', self.srv.url('/app/start')]:
            with self.subTest(page):
                start = len(self.srv.log)
                r = self.fetch('/data.json', '--from', page)
                self.assertExit(r, 0)
                self.assertEqual(r.out, self.JSON)
                self.assertEqual([q.path for q in self.srv.log[start:]], ['/app/start', '/data.json'])

    def test_cookies_set_by_the_from_page_are_sent(self):
        r = self.fetch('/api/cookies', '--from', '/app/start')
        self.assertExit(r, 0)
        self.assertIn('tok=FROMPAGE', r.text)
        self.assertIn('hidden=HTTPONLY', r.text)

    def test_http_errors_exit_5_and_print_the_body(self):
        for path, status, body in [('/gone', 404, b'nothing here'), ('/broken', 500, b'{"error": "boom"}')]:
            with self.subTest(path):
                r = self.fetch(path)
                self.assertExit(r, 5)
                self.assertEqual(r.out, body)
                self.assertIn(f'HTTP {status}', r.err.splitlines())

    def test_other_2xx_statuses_exit_0(self):
        r = self.fetch('/created')
        self.assertExit(r, 0)
        self.assertEqual(r.out, b'made')
        r = self.fetch('/empty')
        self.assertExit(r, 0)
        self.assertEqual(r.out, b'')

    def test_a_server_that_never_answers_exits_2_at_the_timeout(self):
        r = self.fetch('/stall', '--timeout', '2')
        self.assertTimedOut(r, 2)

    def test_a_canvas_json_prefix_is_passed_through(self):
        r = self.fetch('/canvas.json')
        self.assertExit(r, 0)
        self.assertEqual(r.out, b'while(1);[{"id":1,"name":"Assignment"}]')

    def test_redirects_are_followed(self):
        r = self.fetch('/old')
        self.assertExit(r, 0)
        self.assertEqual(r.out, self.JSON)
        r = self.fetch('/old-gone')
        self.assertExit(r, 5)
        self.assertEqual(r.out, b'nothing here')

    def test_a_cross_origin_url_without_cors_exits_4(self):
        r = self.umbra('fetch', self.bare.url('/data.json'), '--from', self.srv.url('/app/start'))
        self.assertExit(r, 4)
        self.assertSays(r)
        self.assertEqual(r.out, b'')

    def test_an_unreachable_site_exits_4(self):
        r = self.fetch(f'http://127.0.0.1:{free_port()}/data.json', '--timeout', '20')
        self.assertExit(r, 4)

    def test_bad_usage_exits_64(self):
        url = self.srv.url('/data.json')
        for args in [[], ['/data.json'], [url, url], ['file:///etc/hosts'], [url, '--from'], [url, '--timeout', 'x']]:
            with self.subTest(args):
                r = self.umbra('fetch', *args)
                self.assertExit(r, 64)
                self.assertSays(r)
                self.assertEqual(r.out, b'')


class DownloadTests(UmbraCase):
    SIGNED_IN = [('auth', ''), ('auth_lax', '; SameSite=Lax; HttpOnly'), ('auth_strict', '; SameSite=Strict; HttpOnly')]

    @classmethod
    def setUpClass(cls):
        cls.big = os.urandom(50 << 20)
        cls.small = os.urandom(300 << 10)
        cls.srv = srv = Server()
        cls.other = other = Server()

        def send(h, data, extra=()):
            h.send_response(200)
            h.send_header('Content-Type', 'application/octet-stream')
            h.send_header('Content-Length', str(len(data)))
            for name, value in extra:
                h.send_header(name, value)
            h.end_headers()
            view = memoryview(data)
            for i in range(0, len(data), 1 << 20):
                h.wfile.write(view[i:i + (1 << 20)])

        srv.route('/big.bin')(lambda h: send(h, cls.big))
        srv.route('/small.bin')(lambda h: send(h, cls.small))
        srv.route('/odd-name')(lambda h: send(h, cls.small, [(
            'Content-Disposition',
            'attachment; filename="../../evil \\"name\\".txt"; filename*=UTF-8\'\'..%2F..%2F%E2%98%83%20evil.txt')]))
        srv.route('/elsewhere')(lambda h: h.redirect(other.url('/small.bin'), 302))
        srv.page('/sso/set', '<title>signed in</title>', headers=[
            ('Set-Cookie', f'{name}=ok-{name}; Path=/; Max-Age=600{attributes}') for name, attributes in cls.SIGNED_IN])
        srv.page('/sso/login', '<!doctype html><title>Log in</title><form><input name=password></form>')

        @srv.route('/protected.bin')
        def protected(h):
            need = h.req.query['need']
            if h.req.cookies.get(need) == f'ok-{need}':
                send(h, cls.small)
            else:
                h.redirect('/sso/login', 302)
        other.route('/small.bin')(lambda h: send(h, cls.small))

        @srv.route('/truncated')
        def truncated(h):
            h.send_response(200)
            h.send_header('Content-Type', 'application/octet-stream')
            h.send_header('Content-Length', str(1 << 20))
            h.end_headers()
            h.wfile.write(cls.small[:100 << 10])
            h.wfile.flush()
            h.close_connection = True

        @srv.route('/late')
        def late(h):
            h.send_response(200)
            h.send_header('Content-Type', 'application/octet-stream')
            h.send_header('Content-Length', str(len(cls.small)))
            h.end_headers()
            h.wfile.write(cls.small[:100 << 10])
            h.wfile.flush()
            h.server.owner.closing.wait(3)
            h.wfile.write(cls.small[100 << 10:])

        @srv.route('/stalled')
        def stalled(h):
            h.send_response(200)
            h.send_header('Content-Type', 'application/octet-stream')
            h.send_header('Content-Length', str(1 << 20))
            h.end_headers()
            h.wfile.write(cls.small[:100 << 10])
            h.wfile.flush()
            h.stall()

    @classmethod
    def tearDownClass(cls):
        cls.srv.close()
        cls.other.close()

    def download(self, path, target, *flags, **kwargs):
        url = path if path.startswith('http') else self.srv.url(path)
        return self.umbra('download', url, str(target), *flags, **kwargs)

    def part(self, target):
        return target.parent / f'.{target.name}.umbra-part'

    def assertOnly(self, directory, names):
        self.assertEqual(sorted(p.name for p in directory.iterdir()), sorted(names))

    def test_a_large_binary_arrives_intact(self):
        target = self.tmp / 'big.bin'
        r = self.download('/big.bin', target, timeout=120)
        self.assertExit(r, 0)
        self.assertEqual(r.out, b'')
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), hashlib.sha256(self.big).hexdigest())
        self.assertOnly(self.tmp, ['big.bin'])

    def test_an_existing_file_is_replaced(self):
        target = self.tmp / 'small.bin'
        target.write_text('stale')
        self.part(target).write_text('left by a crashed run')
        r = self.download('/small.bin', target)
        self.assertExit(r, 0)
        self.assertEqual(target.read_bytes(), self.small)
        self.assertOnly(self.tmp, ['small.bin'])

    def test_a_relative_target_path_is_resolved(self):
        r = self.umbra('download', self.srv.url('/small.bin'), 'rel.bin', cwd=self.tmp)
        self.assertExit(r, 0)
        self.assertEqual((self.tmp / 'rel.bin').read_bytes(), self.small)

    def test_an_unreachable_server_exits_4(self):
        r = self.download(f'http://127.0.0.1:{free_port()}/x.bin', self.tmp / 'x.bin', '--timeout', '20')
        self.assertExit(r, 4)
        self.assertSays(r)
        self.assertOnly(self.tmp, [])

    def test_a_download_sends_the_sites_cookies(self):
        # Regression: a signed-in Canvas file came back as the SSO login page, because the download
        # went out with no first party and WebKit withheld the site's cookies.
        self.assertExit(self.umbra('read', self.srv.url('/sso/set'), input='return 1'), 0)
        for name, _ in self.SIGNED_IN:
            with self.subTest(name):
                target = self.tmp / f'{name}.bin'
                r = self.download(f'/protected.bin?need={name}', target)
                self.assertExit(r, 0)
                got = target.read_bytes() if target.exists() else b''
                self.assertNotIn(b'<title>Log in</title>', got, 'downloaded the login page: no cookie was sent')
                self.assertEqual(got, self.small)
                sent = [q for q in self.srv.requests('/protected.bin') if q.query['need'] == name][-1]
                self.assertEqual(sent.cookies.get(name), f'ok-{name}')

    def test_a_cross_origin_redirect_still_downloads(self):
        target = self.tmp / 'moved.bin'
        r = self.download('/elsewhere', target)
        self.assertExit(r, 0)
        self.assertEqual(target.read_bytes(), self.small)
        self.assertTrue(self.other.requests('/small.bin'))

    def test_an_http_error_exits_5_and_writes_nothing(self):
        target = self.tmp / 'missing.bin'
        r = self.download('/missing', target)
        self.assertExit(r, 5)
        self.assertIn('HTTP 404', r.err.splitlines())
        self.assertOnly(self.tmp, [])

    def test_an_http_error_leaves_an_existing_file_alone(self):
        target = self.tmp / 'keep.bin'
        target.write_text('keep me')
        r = self.download('/missing', target)
        self.assertExit(r, 5)
        self.assertEqual(target.read_text(), 'keep me')
        self.assertOnly(self.tmp, ['keep.bin'])

    def test_the_suggested_filename_is_ignored(self):
        inner = self.tmp / 'outer' / 'inner'
        inner.mkdir(parents=True)
        target = inner / 'wanted.bin'
        r = self.download('/odd-name', target)
        self.assertExit(r, 0)
        self.assertEqual(target.read_bytes(), self.small)
        self.assertOnly(inner, ['wanted.bin'])
        self.assertOnly(self.tmp / 'outer', ['inner'])
        self.assertOnly(self.tmp, ['outer'])

    def test_an_unwritable_directory_exits_4(self):
        locked = self.tmp / 'locked'
        locked.mkdir()
        locked.chmod(0o500)
        try:
            r = self.download('/small.bin', locked / 'x.bin', '--timeout', '20')
            self.assertExit(r, 4)
            self.assertSays(r)
            self.assertOnly(locked, [])
        finally:
            locked.chmod(0o700)

    def test_a_missing_directory_exits_4(self):
        r = self.download('/small.bin', self.tmp / 'nowhere' / 'x.bin', '--timeout', '20')
        self.assertExit(r, 4)
        self.assertSays(r)
        self.assertOnly(self.tmp, [])

    def test_a_directory_in_the_way_exits_4_and_cleans_up(self):
        target = self.tmp / 'taken'
        (target / 'child').mkdir(parents=True)
        r = self.download('/small.bin', target, '--timeout', '20')
        self.assertExit(r, 4)
        self.assertSays(r, str(target))
        self.assertOnly(self.tmp, ['taken'])

    def test_a_truncated_transfer_exits_4_and_keeps_the_old_file(self):
        target = self.tmp / 'cut.bin'
        target.write_text('previous')
        r = self.download('/truncated', target, '--timeout', '20')
        self.assertExit(r, 4)
        self.assertSays(r)
        self.assertEqual(target.read_text(), 'previous')
        self.assertOnly(self.tmp, ['cut.bin'])

    def test_a_stalled_transfer_exits_2_and_cleans_up(self):
        target = self.tmp / 'stalled.bin'
        target.write_text('previous')
        r = self.download('/stalled', target, '--timeout', '2')
        self.assertTimedOut(r, 2)
        self.assertEqual(target.read_text(), 'previous')
        self.assertOnly(self.tmp, ['stalled.bin'])

    def test_a_timed_out_download_never_lands_later(self):
        target = self.tmp / 'late.bin'
        target.write_text('previous')
        self.assertTimedOut(self.download('/late', target, '--timeout', '1'), 1)
        time.sleep(3.5)  # the server finishes sending meanwhile
        self.assertEqual(target.read_bytes(), b'previous', 'the download landed after umbra reported a timeout')
        self.assertOnly(self.tmp, ['late.bin'])

    def test_bad_usage_exits_64(self):
        url = self.srv.url('/small.bin')
        for args in [[], [url], [url, 'a', 'b'], [url, 'a', '--timeout', 'later']]:
            with self.subTest(args):
                r = self.umbra('download', *args)
                self.assertExit(r, 64)
                self.assertSays(r)
                self.assertEqual(r.out, b'')


class SessionTests(UmbraCase):
    """The core promise: a signed-in session outlives the process that made it."""

    @classmethod
    def setUpClass(cls):
        cls.srv = srv = Server()
        srv.page('/login-session', '<title>in</title>', headers=[
            ('Set-Cookie', 'sess=SESSVALUE-9f2c; Path=/'),
            ('Set-Cookie', 'hsess=HTTPONLYVALUE-77ab; Path=/; HttpOnly')])
        srv.page('/login-persistent', '<title>in</title>', headers=[
            ('Set-Cookie', 'keep=PERSISTVALUE-41d0; Path=/; Max-Age=3600')])
        srv.page('/echo', '<title>echo</title>')

    @classmethod
    def tearDownClass(cls):
        cls.srv.close()

    def sites(self, keep, domain='127.0.0.1'):
        return write_sites([{'name': 'local', 'domain': domain, 'login': self.srv.url('/login-session'),
                             'cookie': 'sess', 'keepSession': keep}])

    def sent_next_run(self, sites=None, profile=None):
        """Cookies the server receives from a fresh umbra process, and what the page script sees."""
        token = secrets.token_hex(4)
        r = self.umbra('read', self.srv.url(f'/echo?k={token}'), input='return document.cookie',
                       sites=sites, profile=profile or self.profile)
        self.assertExit(r, 0)
        request = [q for q in self.srv.requests('/echo') if q.query.get('k') == token][0]
        return request.cookies, r.text

    def sign_in(self, path, sites=None, profile=None):
        r = self.umbra('read', self.srv.url(path), input='return document.title', sites=sites,
                       profile=profile or self.profile)
        self.assertExit(r, 0)

    def assertKeptExpiry(self, shown, now):
        """A kept session cookie is given 30 days when the run that holds it ends."""
        self.assertAlmostEqual((expiry(shown) - now).total_seconds(), 30 * 86400, delta=3600)

    def test_kept_session_cookies_survive_the_next_run(self):
        sites = self.sites(True)
        self.sign_in('/login-session', sites)
        cookies, visible = self.sent_next_run(sites)
        self.assertEqual(cookies.get('sess'), 'SESSVALUE-9f2c')
        self.assertEqual(cookies.get('hsess'), 'HTTPONLYVALUE-77ab')
        self.assertIn('sess=SESSVALUE-9f2c', visible)
        self.assertNotIn('HTTPONLYVALUE', visible, 'keeping a session must not drop HttpOnly')
        cookies, _ = self.sent_next_run(sites)
        self.assertEqual(cookies.get('sess'), 'SESSVALUE-9f2c', 'still there a run later')

    def test_session_cookies_are_dropped_without_keep_session(self):
        for name, sites in [('keepSession false', self.sites(False)), ('another domain', self.sites(True, 'localhost')),
                            ('no sites file', None)]:
            with self.subTest(name):
                profile = new_profile()
                self.sign_in('/login-session', sites, profile)
                cookies, _ = self.sent_next_run(sites, profile)
                self.assertEqual(cookies, {})

    def test_persistent_cookies_survive_regardless(self):
        self.sign_in('/login-persistent')
        cookies, _ = self.sent_next_run()
        self.assertEqual(cookies, {'keep': 'PERSISTVALUE-41d0'})

    def test_cookies_lists_names_and_expiry_but_never_values(self):
        sites = self.sites(True)
        self.sign_in('/login-session', sites)
        self.sign_in('/login-persistent', sites)
        self.assertExit(self.umbra('read', self.srv.url('/login-persistent', 'localhost'), input='return 1'), 0)
        now = datetime.datetime.now(datetime.timezone.utc)
        r = self.umbra('cookies', '127.0.0.1', sites=sites)
        self.assertExit(r, 0)
        for value in ['SESSVALUE', 'HTTPONLYVALUE', 'PERSISTVALUE']:
            self.assertNotIn(value, r.text)
        rows = {line.split('\t')[0]: line.split('\t') for line in r.text.splitlines()}
        self.assertEqual(sorted(rows), ['hsess', 'keep', 'sess'])
        self.assertEqual({row[1] for row in rows.values()}, {'127.0.0.1'})
        self.assertAlmostEqual((expiry(rows['keep'][2]) - now).total_seconds(), 3600, delta=300)
        self.assertKeptExpiry(rows['sess'][2], now)
        everything = self.umbra('cookies', sites=sites)
        self.assertExit(everything, 0)
        self.assertEqual(sorted(line.split('\t')[1] for line in everything.text.splitlines()),
                         ['127.0.0.1'] * 3 + ['localhost'])
        self.assertNotIn('PERSISTVALUE', everything.text)
        self.assertEqual(self.umbra('cookies', 'example.com').out, b'')

    def test_profiles_are_isolated(self):
        a, b = new_profile(), new_profile()
        self.sign_in('/login-persistent', profile=a)
        self.assertEqual(self.sent_next_run(profile=b)[0], {})
        self.assertEqual(self.umbra('cookies', profile=b).out, b'')
        self.assertEqual(self.sent_next_run(profile=a)[0], {'keep': 'PERSISTVALUE-41d0'})
        self.assertTrue(store_dir(a).is_dir(), f'{store_dir(a)} should hold profile {a}')

    def test_a_copied_binary_uses_the_same_store(self):
        # The bundle identifier is embedded in the binary, so the store does not depend on the app.
        copy = self.tmp / 'umbra'
        shutil.copy2(UMBRA, copy)
        self.assertEqual(self.umbra('--version', binary=copy).out, self.umbra('--version').out)
        self.assertExit(self.umbra('read', self.srv.url('/login-persistent'), input='return 1', binary=copy), 0)
        listed = self.umbra('cookies', '127.0.0.1')
        self.assertIn('keep\t127.0.0.1\t', listed.text)


class LockTests(UmbraCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = srv = Server()
        srv.page('/page', '<title>lock</title>')
        for path in ['/begin', '/end']:
            srv.page(path, 'ok', ctype='text/plain')

    @classmethod
    def tearDownClass(cls):
        cls.srv.close()

    def window(self, i):
        times = [q.t for q in self.srv.log if q.query.get('i') == i]
        return min(times), max(times)

    def test_concurrent_runs_take_turns(self):
        tag = secrets.token_hex(3)
        script = ('await fetch("/begin?i=%s"); await new Promise(r => setTimeout(r, 250));'
                  ' await fetch("/end?i=%s"); return "%s"')
        ids = [f'{tag}-{n}' for n in range(5)]
        procs = [spawn('read', self.srv.url(f'/page?i={i}'), input=script % (i, i, i), profile=self.profile)
                 for i in ids]
        for i, proc in zip(ids, procs):
            out, err = proc.communicate(timeout=90)
            self.assertEqual(proc.returncode, 0, err)
            self.assertEqual(out, f'{i}\n'.encode())
        windows = sorted(self.window(i) for i in ids)
        self.assertEqual(len(windows), 5)
        for (_, end), (start, _) in zip(windows, windows[1:]):
            self.assertGreater(start, end, f'runs overlapped: {windows}')

    def test_another_profile_is_not_blocked(self):
        tag = secrets.token_hex(3)
        slow = spawn('read', self.srv.url(f'/page?i={tag}-a'), profile=self.profile,
                     input=f'await fetch("/begin?i={tag}-a"); await new Promise(r => setTimeout(r, 4000)); return "a"')
        deadline = time.monotonic() + 30
        while not [q for q in self.srv.requests('/begin') if q.query.get('i') == f'{tag}-a']:
            self.assertIsNone(slow.poll(), slow.stderr.read() if slow.poll() is not None else '')
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.02)
        r = self.umbra('read', self.srv.url(f'/page?i={tag}-b'), input='return "b"', profile=new_profile())
        self.assertExit(r, 0)
        self.assertEqual(r.out, b'b\n')
        self.assertIsNone(slow.poll(), 'the other profile should not have waited for this one')
        out, err = slow.communicate(timeout=30)
        self.assertEqual((slow.returncode, out), (0, b'a\n'), err)


def window_server():
    try:
        return subprocess.run(['launchctl', 'managername'], capture_output=True, text=True,
                              timeout=5).stdout.strip() == 'Aqua'
    except (OSError, subprocess.SubprocessError):
        return False


class SignInTests(UmbraCase):
    def test_an_unknown_site_exits_64(self):
        sites = write_sites([{'name': 'Local', 'domain': '127.0.0.1', 'login': 'http://127.0.0.1:9/login'}])
        r = self.umbra('sign-in', 'nosuch', sites=sites)
        self.assertExit(r, 64)
        self.assertSays(r, 'nosuch', 'Local')

    def test_save_needs_one_login_url(self):
        # (With no sites at all, `umbra sign-in` opens the window empty: test_daemon's SignInWindowTests.)
        sites = write_sites([{'name': 'Local', 'domain': '127.0.0.1', 'login': 'http://127.0.0.1:9/login', 'cookie': 'c'}])
        for args in [['sign-in', '--save', 'X'], ['sign-in', 'Local', '--save', 'X']]:
            with self.subTest(args):
                r = self.umbra(*args, sites=sites)
                self.assertExit(r, 64)
                self.assertSays(r, '--save')
        umbra('stop', profile=self.profile)

    def test_a_malformed_sites_file_exits_78_for_every_command(self):
        broken = TMP / 'broken-sites.json'
        broken.write_text('[{"name": "x", "domain": ')
        wrong = write_sites([{'name': 'x', 'domain': 'example.com'}])  # no login
        empty = TMP / 'empty-sites.json'
        empty.write_text('')
        commands = [['read', 'http://127.0.0.1:9/'], ['fetch', 'http://127.0.0.1:9/x'],
                    ['download', 'http://127.0.0.1:9/x', str(self.tmp / 'x')], ['cookies'], ['sign-in'],
                    ['sign-in', 'x'], ['keep-signed-in'], ['--version'], ['help']]
        for sites in [broken, wrong, empty]:
            for args in commands:
                with self.subTest(sites=sites.name, args=args):
                    r = self.umbra(*args, sites=sites, input='return 1')
                    self.assertExit(r, 78)
                    self.assertSays(r, str(sites))
                    self.assertEqual(r.out, b'')

    @unittest.skipUnless(window_server(), 'no window server (not in an Aqua session): skipping the sign-in window')
    def test_the_sign_in_window_opens_and_stays_up(self):
        srv = Server()
        srv.page('/login', '<title>Log in</title><form><input name=user></form>')
        try:
            proc = spawn('sign-in', srv.url('/login'), profile=self.profile)
            srv.wait_for('/login', timeout=20)
            time.sleep(2)
            self.assertIsNone(proc.poll(), proc.stderr.read() if proc.poll() is not None else '')
            proc.terminate()
            proc.wait(10)
        finally:
            umbra('stop', profile=self.profile)  # the daemon shows the window
            srv.close()


class KeepSignedInTests(UmbraCase):
    """`umbra keep-signed-in`: visits the sites marked keepSignedIn and reports whether each is still
    signed in; naming sites marks them and installs the launch agent that runs it every 10 minutes.
    Agents are written to a temporary UMBRA_AGENTS under this profile's own label, and booted out
    after each test and again when the suite exits."""

    SIGNED_OUT = '{0}: signed out; `umbra sign-in {0}` signs in again'
    UNKNOWN = "{0}: couldn't check (HTTP 503)"
    NONE_KEPT = 'no site is kept signed in; `umbra keep-signed-in <site>` turns it on\n'

    def setUp(self):
        super().setUp()
        self.agents = self.tmp / 'agents'
        self.agents.mkdir()
        # A server per test, so a launch agent's late visit can't land in another test's requests.
        self.srv = srv = Server()
        srv.page('/', '<title>home</title>')
        srv.page('/login', '<title>Log in</title><form><input name=user></form>')
        srv.page('/set', '<title>in</title>', headers=[('Set-Cookie', 'sess=on; Path=/; Max-Age=3600')])
        srv.page('/set-session', '<title>in</title>', headers=[('Set-Cookie', 'sess=on; Path=/')])
        srv.page('/echo', '<title>echo</title>')
        # A login page that sends someone signed in on into the site, and asks anyone else for a password.
        srv.route('/enter')(lambda h: h.redirect('/home') if h.req.cookies.get('sess') == 'on'
                            else h.reply(200, '<title>Log in</title><form><input type=password name=p></form>'))
        srv.page('/home', '<title>home</title>')
        # Login pages that keep someone signed in where they are, as WRDS's does, offering a way out:
        # a POST form, a link, or a scripted button. Anyone else is asked for an email, then a password.
        stays = {'form': '<form method=post action="/accounts/logout/"><button>Logout</button></form>',
                 'link': '<a href="/sign_out">Your account</a>', 'button': '<button onclick="leave()">Log out</button>'}
        srv.route('/stay')(lambda h: h.reply(200, '<title>Login</title><p>You are logged in as Pat.</p>' + stays[h.req.query['kind']]
                                             if h.req.cookies.get('sess') == 'on' else
                                             '<title>Login</title><form><input type=email name=e><button>Next</button></form>'))
        srv.route('/api/me')(lambda h: h.reply(200 if h.req.cookies.get('sess') == 'on' else 401, '{}', 'application/json'))
        srv.page('/api/forbidden', '{}', status=403, ctype='application/json')
        srv.route('/api/moved')(lambda h: h.redirect('/login'))
        srv.page('/api/flaky', 'busy', status=503, ctype='text/plain')
        # A rolling session: each answer renews the cookie with a new value and no expiry.
        srv.page('/seed-roll', '<title>in</title>', headers=[('Set-Cookie', 'roll=r0; Path=/; Max-Age=3600')])
        self.rolled = rolled = []

        @srv.route('/api/roll')
        def roll(h):
            if 'roll' not in h.req.cookies:
                return h.reply(401, '{}', 'application/json')
            rolled.append(f'r{len(rolled) + 1}-{secrets.token_hex(3)}')
            h.reply(200, '{}', 'application/json', [('Set-Cookie', f'roll={rolled[-1]}; Path=/')])

    def tearDown(self):
        bootout(self.profile)
        self.srv.close()
        super().tearDown()

    def umbra(self, *args, env=None, **kwargs):
        return super().umbra(*args, env={'UMBRA_AGENTS': str(self.agents), **(env or {})}, **kwargs)

    def keep(self, *args, sites, **kwargs):
        kwargs.setdefault('timeout', 90)
        return self.umbra('keep-signed-in', *args, sites=sites, **kwargs)

    def site(self, name, **fields):
        return {'name': name, 'domain': '127.0.0.1', 'login': self.srv.url('/login'), **fields}

    def kept(self, *entries):
        """A sites file with these sites marked keepSignedIn by hand: what `keep-signed-in` alone visits."""
        return write_sites([{**entry, 'keepSignedIn': True} for entry in entries])

    def sign_in(self, path='/set', profile=None):
        self.assertExit(self.umbra('read', self.srv.url(path), input='return 1', profile=profile or self.profile), 0)

    def plist(self):
        return self.agents / f'{agent_label(self.profile)}.plist'

    def log(self):
        path = LOCKS / f'keep-signed-in-{self.profile}.log'
        return path.read_text(errors='replace') if path.exists() else ''

    def eventually(self, condition, timeout):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            time.sleep(0.1)
        return condition()

    def assertAt(self, stamp):
        """An ISO-8601 time in UTC, about now."""
        self.assertRegex(stamp, r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|\+00:00)$')
        now = datetime.datetime.now(datetime.timezone.utc)
        self.assertLess(abs((expiry(stamp) - now).total_seconds()), 300)

    def assertReport(self, text, *lines):
        """The report is exactly these lines, in this order, each after the time of the visit."""
        got = text.splitlines()
        self.assertEqual([line.partition(' ')[2] for line in got], list(lines), text)
        for line in got:
            self.assertAt(line.partition(' ')[0])

    def assertNothingInstalled(self):
        self.assertEqual(list(self.agents.iterdir()), [])
        self.assertFalse(agent_loaded(self.profile))

    def test_a_probe_that_answers_2xx_is_signed_in(self):
        self.sign_in()
        r = self.keep(sites=self.kept(self.site('Probe', probe='/api/me')))
        self.assertExit(r, 0)
        self.assertReport(r.text, 'Probe: signed in')
        probes = self.srv.requests('/api/me')
        self.assertTrue(probes, 'the probe was never requested')
        self.assertEqual({q.cookies.get('sess') for q in probes}, {'on'}, 'the probe goes with the session cookie')

    def test_a_probe_that_refuses_or_redirects_is_signed_out(self):
        names = ['Refused', 'Forbidden', 'Moved']
        sites = self.kept(self.site('Refused', probe='/api/me'), self.site('Forbidden', probe='/api/forbidden'),
                          self.site('Moved', probe='/api/moved'))
        r = self.keep(sites=sites)
        self.assertExit(r, 3)
        self.assertEqual(r.out, b'')
        self.assertReport(r.err, *[self.SIGNED_OUT.format(name) for name in names])
        for path in ['/api/me', '/api/forbidden', '/api/moved']:
            self.assertTrue(self.srv.requests(path), f'{path} was never requested')

    def test_a_probe_that_cant_tell_exits_4(self):
        r = self.keep(sites=self.kept(self.site('Flaky', probe='/api/flaky')))
        self.assertExit(r, 4)
        self.assertEqual(r.out, b'')
        self.assertReport(r.err, self.UNKNOWN.format('Flaky'))

    def test_a_rendered_probe_refreshes_the_app_session_and_awaits_its_check_once(self):
        self.srv.page('/app', '''<title>Account</title><script>
          window.heartbeat = fetch('/api/roll').then(r => { if (!r.ok) throw Error('expired'); });
        </script>''')
        self.sign_in()
        self.sign_in('/seed-roll')
        script = "await window.heartbeat; return (await fetch('/api/me')).ok;"
        sites = self.kept(self.site('App', cookie='sess', probe='/app', probeScript=script))
        r = self.keep(sites=sites)
        self.assertExit(r, 0)
        self.assertReport(r.text, 'App: signed in')
        self.assertEqual(len(self.srv.requests('/api/roll')), 1, 'the rendered app refreshes once')
        self.assertEqual(len(self.srv.requests('/api/me')), 1, 'await the script without repeating its requests')
        self.assertExit(self.umbra('read', self.srv.url('/echo'), input='return 1', sites=sites), 0)
        self.assertEqual(self.srv.requests('/echo')[-1].cookies.get('roll'), self.rolled[-1],
                         'the refreshed session cookie survives a fresh process')

    def test_a_rendered_probe_waits_for_the_requested_page_instead_of_initial_blank(self):
        @self.srv.route('/slow-app')
        def slow_app(h):
            time.sleep(1)
            h.reply(200, '<title>App</title><script>window.appReady = true;</script>')

        sites = self.kept(self.site('App', probe='/slow-app',
                                   probeScript="if (!window.appReady) throw Error('not initialized'); return true;"))
        r = self.keep(sites=sites)
        self.assertExit(r, 0)
        self.assertReport(r.text, 'App: signed in')
        self.assertEqual(len(self.srv.requests('/slow-app')), 1)

    def test_a_rendered_probe_rejects_a_stale_cookie_and_a_public_200_shell(self):
        self.sign_in()
        self.srv.page('/app', '<title>Public app shell</title>')
        sites = self.kept(self.site('App', cookie='sess', probe='/app',
                                   probeScript="return (await fetch('/api/forbidden')).ok;"))
        r = self.keep(sites=sites)
        self.assertExit(r, 3)
        self.assertReport(r.err, self.SIGNED_OUT.format('App'))
        self.assertEqual(len(self.srv.requests('/api/forbidden')), 1)

    def test_a_rendered_probe_error_is_unknown_and_does_not_expose_script_errors(self):
        sites = self.kept(self.site('App', probe='/home', probeScript="throw Error('private-token-value');"))
        r = self.keep(sites=sites)
        self.assertExit(r, 4)
        self.assertReport(r.err, "App: couldn't check (the signed-in script failed or didn't return a boolean)")
        self.assertNotIn('private-token-value', r.err)

    def test_a_rendered_probe_requires_a_boolean_instead_of_a_numeric_truth_value(self):
        for value in ['1', '0']:
            with self.subTest(value=value):
                sites = self.kept(self.site('App', probe='/home', probeScript=f'return {value};'))
                r = self.keep(sites=sites)
                self.assertExit(r, 4)
                self.assertReport(r.err, "App: couldn't check (the signed-in script failed or didn't return a boolean)")

    def test_a_cookie_site_is_visited_at_its_root_and_its_cookie_decides(self):
        sites = self.kept(self.site('Cookie', cookie='sess'))
        self.sign_in()
        r = self.keep(sites=sites)
        self.assertExit(r, 0)
        self.assertReport(r.text, 'Cookie: signed in')
        roots = self.srv.requests('/')
        self.assertTrue(roots, 'the root page was never loaded')
        self.assertEqual({q.cookies.get('sess') for q in roots}, {'on'})
        r = self.keep(sites=sites, profile=new_profile())  # a store without the cookie
        self.assertExit(r, 3)
        self.assertEqual(r.out, b'')
        self.assertReport(r.err, self.SIGNED_OUT.format('Cookie'))

    def test_a_missing_cookie_is_signed_out_without_asking_the_probe(self):
        sites = self.kept(self.site('Both', cookie='sess', probe='/api/me'))
        r = self.keep(sites=sites)
        self.assertExit(r, 3)
        self.assertReport(r.err, self.SIGNED_OUT.format('Both'))
        self.assertEqual(self.srv.requests('/api/me'), [], 'no cookie: no probe request')
        self.sign_in()
        r = self.keep(sites=sites)
        self.assertExit(r, 0)
        self.assertReport(r.text, 'Both: signed in')
        self.assertEqual([q.cookies.get('sess') for q in self.srv.requests('/api/me')][-1:], ['on'])

    def test_sites_are_reported_in_file_order_and_the_worst_state_sets_the_exit_code(self):
        self.sign_in()
        said = {'Out': self.SIGNED_OUT.format('Out'), 'Flaky': self.UNKNOWN.format('Flaky'),
                'In': 'In: signed in', 'Plain': 'Plain: signed in'}
        out, flaky, signed, plain = (self.site('Out', probe='/api/moved'), self.site('Flaky', probe='/api/flaky'),
                                     self.site('In', probe='/api/me'), self.site('Plain', login=self.srv.url('/enter')))
        for entries, code in [([flaky, out, signed], 3), ([signed, flaky], 4), ([plain, signed], 0)]:
            names = [entry['name'] for entry in entries]
            with self.subTest(names):
                r = self.keep(sites=self.kept(*entries))
                self.assertExit(r, code)
                self.assertReport(r.err if code else r.text, *[said[name] for name in names])
                if code:
                    self.assertEqual(r.out, b'')
        self.assertTrue(self.srv.requests('/enter'), 'a site with neither cookie nor probe is asked through its login page')

    def test_a_site_with_neither_cookie_nor_probe_is_asked_through_its_login_page(self):
        sites = self.kept(self.site('Plain', login=self.srv.url('/enter')))
        r = self.keep(sites=sites)
        self.assertExit(r, 3)
        self.assertReport(r.err, self.SIGNED_OUT.format('Plain'))  # it stayed at the password
        self.sign_in()
        r = self.keep(sites=sites)
        self.assertExit(r, 0)
        self.assertReport(r.text, 'Plain: signed in')  # it moved on to /home

    def test_a_login_page_that_keeps_someone_signed_in_where_they_are_says_so(self):
        # Regression: WRDS kept a signed-in person on its login page, with a Logout form, so it always read signed out.
        sites = self.kept(*[self.site(kind.title(), login=self.srv.url(f'/stay?kind={kind}')) for kind in ['form', 'link', 'button']])
        r = self.keep(sites=sites)
        self.assertExit(r, 3)
        self.assertReport(r.err, *[self.SIGNED_OUT.format(name) for name in ['Form', 'Link', 'Button']])  # no password asked yet, still out
        self.sign_in()
        r = self.keep(sites=sites)
        self.assertExit(r, 0)
        self.assertReport(r.text, 'Form: signed in', 'Link: signed in', 'Button: signed in')

    def test_a_renewed_session_cookie_is_kept_for_the_next_request(self):
        # keepSignedIn implies keepSession: the entry has no keepSession, yet the renewed
        # session-only cookie must outlive the run.
        sites = self.kept(self.site('Roll', probe='/api/roll'))
        self.assertNotIn('keepSession', json.loads(sites.read_text())[0])
        self.sign_in('/seed-roll')
        for _ in range(2):
            r = self.keep(sites=sites)
            self.assertExit(r, 0)
            self.assertReport(r.text, 'Roll: signed in')
        sent = [q.cookies.get('roll') for q in self.srv.requests('/api/roll')]
        self.assertEqual(sent[0], 'r0')
        self.assertEqual(sent[-1], self.rolled[-2], 'the second visit sends the value the first one was given')
        token = secrets.token_hex(4)
        self.assertExit(self.umbra('read', self.srv.url(f'/echo?k={token}'), input='return 1', sites=sites), 0)
        [echo] = [q for q in self.srv.requests('/echo') if q.query.get('k') == token]
        self.assertEqual(echo.cookies.get('roll'), self.rolled[-1], 'a fresh process sends the renewed value')
        now = datetime.datetime.now(datetime.timezone.utc)
        listed = self.umbra('cookies', '127.0.0.1', sites=sites)
        self.assertExit(listed, 0)
        rows = {line.split('\t')[0]: line.split('\t') for line in listed.text.splitlines()}
        self.assertAlmostEqual((expiry(rows['roll'][2]) - now).total_seconds(), 30 * 86400, delta=3600)

    @unittest.skipUnless(window_server(), 'no window server (not in an Aqua session): no gui launchd domain')
    def test_naming_a_site_marks_it_and_installs_a_launch_agent_that_runs_at_once(self):
        self.sign_in()
        sites = write_sites([self.site('Kept', cookie='sess', probe='/api/me'),
                             self.site('Other', domain='localhost', login=self.srv.url('/login', 'localhost'),
                                       cookie='c', keepSession=True)])
        before = json.loads(sites.read_text())
        r = self.keep('kept', sites=sites)  # names are case-insensitive
        self.assertExit(r, 0)
        self.assertReport(r.text, 'Kept: signed in')
        text = sites.read_text()
        self.assertEqual(json.loads(text), [{**before[0], 'keepSignedIn': True}, before[1]])
        lines = text.splitlines()
        self.assertEqual(len(lines), 4, f'one site per line:\n{text}')
        self.assertEqual(list(json.loads(lines[1].rstrip(','))),
                         ['name', 'domain', 'login', 'cookie', 'probe', 'keepSignedIn'])

        plist = self.plist()
        self.assertEqual(list(self.agents.iterdir()), [plist])
        self.assertFalse((Path.home() / 'Library/LaunchAgents' / plist.name).exists(), 'UMBRA_AGENTS was ignored')
        with open(plist, 'rb') as f:
            agent = plistlib.load(f)
        self.assertEqual(agent['Label'], agent_label(self.profile))
        program, *rest = agent['ProgramArguments']
        self.assertEqual(rest, ['keep-signed-in'])
        self.assertTrue(os.path.isabs(program) and os.path.samefile(program, UMBRA), program)
        self.assertEqual(str(Path(program).resolve()), program, 'the executable with symlinks resolved')
        env = agent['EnvironmentVariables']
        self.assertEqual(set(env), {'UMBRA_SITES', 'UMBRA_PROFILE'})
        self.assertEqual(env['UMBRA_PROFILE'], self.profile)
        self.assertTrue(os.path.isabs(env['UMBRA_SITES']) and os.path.samefile(env['UMBRA_SITES'], sites), env)
        self.assertEqual((agent['StartCalendarInterval'], agent['RunAtLoad']), ([{'Minute': m} for m in range(0, 60, 10)], True))
        log = str(LOCKS / f'keep-signed-in-{self.profile}.log')
        self.assertEqual((agent['StandardOutPath'], agent['StandardErrorPath']), (log, log))
        self.assertTrue(agent_loaded(self.profile), 'launchctl print: the agent is not loaded')

        # RunAtLoad: launchd runs `umbra keep-signed-in` at once, which visits Kept again and logs it.
        self.srv.wait_for('/api/me', timeout=30, count=2)
        self.assertTrue(self.eventually(lambda: 'Kept: signed in' in self.log(), 30), self.log())
        logged = [line for line in self.log().splitlines() if line.endswith(' Kept: signed in')]
        self.assertTrue(logged, self.log())
        self.assertAt(logged[-1].partition(' ')[0])

        visits = len(self.srv.requests())
        r = self.keep('Kept', '--off', sites=sites)
        self.assertExit(r, 0)
        self.assertEqual(r.text, 'Kept: no longer kept signed in\n')
        self.assertEqual(json.loads(sites.read_text()), before, 'the key goes; everything else stays')
        self.assertFalse(plist.exists())
        self.assertFalse(agent_loaded(self.profile), 'launchctl print: the agent is still loaded')
        self.assertEqual(len(self.srv.requests()), visits, '--off visits nothing')

    @unittest.skipUnless(window_server(), 'no window server (not in an Aqua session): no gui launchd domain')
    def test_the_agent_stays_while_any_site_is_kept(self):
        self.sign_in()
        first, second = self.site('First', probe='/api/me'), self.site('Second', cookie='sess')
        sites = write_sites([first, second])
        r = self.keep('Second', 'First', sites=sites)
        self.assertExit(r, 0)
        self.assertReport(r.text, 'First: signed in', 'Second: signed in')  # the sites file's order
        self.assertTrue(self.plist().exists())
        self.assertTrue(agent_loaded(self.profile))
        # Let the run at load finish, so it can't race the changes below.
        self.assertTrue(self.eventually(lambda: 'Second: signed in' in self.log(), 30), self.log())
        r = self.keep('first', '--off', sites=sites)
        self.assertExit(r, 0)
        self.assertEqual(r.text, 'First: no longer kept signed in\n')
        self.assertEqual(json.loads(sites.read_text()), [first, {**second, 'keepSignedIn': True}])
        self.assertTrue(self.plist().exists(), 'Second is still kept: the agent stays')
        self.assertTrue(agent_loaded(self.profile))
        r = self.keep('Second', '--off', sites=sites)
        self.assertExit(r, 0)
        self.assertEqual(r.text, 'Second: no longer kept signed in\n')
        self.assertEqual(json.loads(sites.read_text()), [first, second])
        self.assertFalse(self.plist().exists())
        self.assertFalse(agent_loaded(self.profile))

    def test_with_no_site_kept_it_says_so_and_does_nothing(self):
        unmarked = write_sites([self.site('Plain', cookie='sess', probe='/api/me', keepSession=True)])
        before = unmarked.read_text()
        for sites in [NO_SITES, write_sites([]), unmarked]:
            with self.subTest(sites=sites.name):
                r = self.keep(sites=sites)
                self.assertExit(r, 0)
                self.assertEqual(r.text, self.NONE_KEPT)
        self.assertEqual(unmarked.read_text(), before)
        self.assertFalse(NO_SITES.exists())
        self.assertEqual(self.srv.requests(), [], 'nothing is visited')
        self.assertNothingInstalled()

    def test_bad_usage_exits_64(self):
        sites = write_sites([self.site('Kept', cookie='sess'), self.site('Other', probe='/api/me')])
        before = sites.read_text()
        for args, facts in [(['--off'], []), (['Nosuch'], ['Nosuch', 'Kept', 'Other']),
                            (['Nosuch', '--off'], ['Nosuch', 'Kept', 'Other']), ([self.srv.url('/login')], [])]:
            with self.subTest(args):
                r = self.keep(*args, sites=sites)
                self.assertExit(r, 64)
                self.assertSays(r, *facts)
                self.assertEqual(r.out, b'')
        self.assertEqual(sites.read_text(), before)
        self.assertEqual(self.srv.requests(), [])
        self.assertNothingInstalled()

    def test_a_failed_install_leaves_the_sites_file_as_it_was(self):
        blocked = self.tmp / 'not-a-folder'
        blocked.write_text('')
        sites = write_sites([self.site('Kept', cookie='sess'), self.site('Other', probe='/api/me', keepSignedIn=True)])
        before = json.loads(sites.read_text())
        r = self.keep('Kept', sites=sites, env={'UMBRA_AGENTS': str(blocked)})
        self.assertExit(r, 1)
        self.assertSays(r, str(blocked))
        self.assertEqual(json.loads(sites.read_text()), before)
        self.assertEqual(blocked.read_text(), '')
        self.assertFalse(agent_loaded(self.profile))

    def test_json_gives_the_time_and_each_sites_state(self):
        self.sign_in()
        r = self.keep('--json', sites=self.kept(self.site('In', probe='/api/me'), self.site('Plain', login=self.srv.url('/enter'))))
        self.assertExit(r, 0)
        data = json.loads(r.out)
        self.assertEqual(sorted(data), ['at', 'sites'])
        self.assertAt(data['at'])
        self.assertEqual(data['sites'], [{'site': 'In', 'state': 'signedIn'}, {'site': 'Plain', 'state': 'signedIn'}])

    def test_it_runs_through_a_running_daemon_and_never_starts_one(self):
        sites = self.kept(self.site('Kept', cookie='sess', probe='/api/me'))

        def running():
            return [pid for pid, name in daemons().items() if name == self.profile]
        r = self.keep(sites=sites)
        self.assertExit(r, 3)  # on its own: the store has no cookie yet
        self.assertEqual(running(), [], 'a daemon was left running')
        self.assertFalse((LOCKS / f'daemon-{self.profile}.log').exists(), 'a daemon was started')
        try:
            # The daemon holds a session-only cookie no other process has (and the store lock), so
            # only a visit made by the daemon can find the site signed in.
            self.assertExit(self.umbra('open', self.srv.url('/set-session'), sites=sites), 0)
            daemon = running()
            self.assertEqual(len(daemon), 1)
            r = self.keep(sites=sites, timeout=45)
            self.assertExit(r, 0)
            self.assertReport(r.text, 'Kept: signed in')
            self.assertEqual([q.cookies.get('sess') for q in self.srv.requests('/api/me')][-1:], ['on'])
            r = self.keep('--json', sites=sites, timeout=45)
            self.assertExit(r, 0)
            data = json.loads(r.out)
            self.assertEqual(sorted(data), ['at', 'sites'])
            self.assertAt(data['at'])
            self.assertEqual(data['sites'], [{'site': 'Kept', 'state': 'signedIn'}])
            self.assertEqual(running(), daemon, 'the same daemon served both runs')
        finally:
            umbra('stop', profile=self.profile)

    def test_it_is_a_command_but_not_an_mcp_tool(self):
        r = self.umbra('help', 'keep-signed-in')
        self.assertExit(r, 0)
        self.assertIn('keep-signed-in', r.text)
        r = self.umbra('schema')
        self.assertExit(r, 0)
        [command] = [c for c in json.loads(r.out)['commands'] if c['name'] == 'keep-signed-in']
        self.assertIs(command['mcp'], False)


class CliTests(UmbraCase):
    def test_version_matches_info_plist(self):
        with open(REPO / 'Info.plist', 'rb') as f:
            version = plistlib.load(f)['CFBundleShortVersionString']
        self.assertRegex(version, r'^\d+\.\d+\.\d+$')
        for arg in ['--version', 'version']:
            with self.subTest(arg):
                r = self.umbra(arg)
                self.assertExit(r, 0)
                self.assertEqual(r.text, f'umbra {version}\n')

    def test_help_prints_usage(self):
        for arg in ['help', '-h', '--help']:
            with self.subTest(arg):
                r = self.umbra(arg)
                self.assertExit(r, 0)
                self.assertEqual(r.err, '')
                for command in ['read', 'fetch', 'download', 'sign-in', 'cookies']:
                    self.assertRegex(r.text, rf'(?m)\b{command}\b')

    def test_an_unknown_command_exits_64(self):
        for args in [['frobnicate'], ['READ', 'http://127.0.0.1:9/']]:
            with self.subTest(args):
                r = self.umbra(*args)
                self.assertExit(r, 64)
                self.assertSays(r)
                self.assertEqual(r.out, b'')

    def test_a_non_numeric_timeout_exits_64(self):
        for args in [['read', 'http://127.0.0.1:9/'], ['fetch', 'http://127.0.0.1:9/x'],
                     ['download', 'http://127.0.0.1:9/x', str(self.tmp / 'x')]]:
            with self.subTest(args[0]):
                r = self.umbra(*args, '--timeout', 'ten', input='return 1')
                self.assertExit(r, 64)
                self.assertSays(r)
                self.assertEqual(r.out, b'')


def main(module='__main__'):
    """Runs a test module, then proves its profiles left nothing behind: no store, file or daemon."""
    if not os.access(UMBRA, os.X_OK):
        sys.exit(f'no umbra binary at {UMBRA}: run make build (or set UMBRA)')
    try:
        passed = unittest.main(module=module, exit=False).result.wasSuccessful()
    finally:
        cleanup()
    left = [str(path) for name in profiles for path in profile_files(name) if path.exists()]
    left += [f'daemon pid {pid} ({name})' for pid, name in daemons().items() if name in profiles]
    left += [f'launch agent {agent_label(name)}' for name in profiles if agent_loaded(name)]
    if left:
        print('left behind:\n  ' + '\n  '.join(left), file=sys.stderr)
    sys.exit(0 if passed and not left else 1)


if __name__ == '__main__':
    main()
