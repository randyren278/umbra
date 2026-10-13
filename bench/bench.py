#!/usr/bin/env python3
"""Umbra against Playwright (Chromium and WebKit) on the Northwind Ops pages: p50 and p95 per metric.

    python3 bench/bench.py [--runs 20] [--warmup 2] [--only cold,eval] [--no-playwright] [--umbra PATH]

Umbra is timed two ways: through the CLI, one process per command as an agent's shell runs it, and
through one persistent connection to the daemon's socket. Playwright runs as one persistent Node
process per browser (bench/pw.js), its best case. Results go to bench/results.md and
bench/results.json. A metric whose Umbra command isn't built yet shows "n/a (not built)".

Uses a throwaway UMBRA_PROFILE=bench-<random> with UMBRA_IDLE=60, and stops its daemon and deletes
the profile's store, socket, lock, log and downloads when it finishes.
"""
import argparse
import datetime
import json
import math
import os
import platform
import re
import secrets
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from hashlib import sha256
from pathlib import Path

BENCH = Path(__file__).resolve().parent
REPO = BENCH.parent
SUPPORT = Path.home() / 'Library/Application Support/Umbra'
STORES = Path.home() / 'Library/WebKit/com.randyren.umbra/WebsiteDataStore'
DOWNLOADS = Path.home() / 'Downloads/Umbra'

# (key, label, the Umbra commands it needs)
METRICS = [
    ('cold', 'Cold start to first page loaded', ['open']),
    ('navigate', 'Warm navigate to a local page', ['open']),
    ('snapshot', 'Accessibility snapshot (orders table)', ['snapshot']),
    ('click', 'Click + DOM update (role=switch)', ['click']),
    ('fill', 'Fill a field (React-controlled input)', ['fill']),
    ('screenshot', 'Viewport screenshot to a file', ['screenshot']),
    ('eval', 'Eval round trip', ['eval']),
    ('heavy', '/heavy load, 200 images', ['open']),
    ('heavy_blocked', '/heavy load, images blocked', ['open', 'block']),
]
COLUMNS = [('umbra_cli', 'Umbra CLI'), ('umbra_socket', 'Umbra socket'),
           ('chromium', 'Playwright Chromium'), ('webkit', 'Playwright WebKit')]


class Failed(Exception):
    pass


def store_dir(name):
    h = sha256(f'umbra profile {name}'.encode()).hexdigest()[:32]
    return STORES / '-'.join((h[:8], h[8:12], h[12:16], h[16:20], h[20:]))


def expect(ok, message):
    if not ok:
        raise Failed(message)


class Umbra:
    """One throwaway profile, driven through the CLI or straight through the daemon's socket."""

    def __init__(self, binary, base):
        self.binary, self.base = binary, base
        self.profile = 'bench-' + secrets.token_hex(4)
        self.env = {**os.environ, 'UMBRA_PROFILE': self.profile, 'UMBRA_IDLE': '60'}
        self.socket_path = SUPPORT / f'daemon-{self.profile}.sock'
        self.conn = None
        self.ids = 0
        schema = json.loads(subprocess.run([binary, 'schema'], env=self.env, capture_output=True, text=True, check=True).stdout)
        self.schema = {c['name']: c for c in schema['commands']}
        self.version = subprocess.run([binary, '--version'], env=self.env, capture_output=True, text=True).stdout.strip()

    def has(self, *commands):
        return all(c in self.schema for c in commands)

    def positionals(self, command):
        usage = self.schema[command]['usage']
        return [name for name in re.findall(r'<(\w+)(?: …)?>', usage) if name not in ('string', 'number', 'boolean', 'array')]

    def argv(self, command, args):
        """The CLI words for named args: positionals in usage order, the rest as --flags."""
        words = [command]
        for name in self.positionals(command):
            if name in args:
                value = args[name]
                words += [str(v) for v in value] if isinstance(value, list) else [str(value)]
        for name, value in args.items():
            if name in self.positionals(command):
                continue
            if value is True:
                words.append(f'--{name}')
            elif value not in (False, None):
                words += [f'--{name}', str(value)]
        return words

    def cli(self, command, **args):
        done = subprocess.run([self.binary, *self.argv(command, args)], env=self.env, capture_output=True, text=True)
        if done.returncode:
            raise Failed(f'umbra {command} exited {done.returncode}: {done.stderr.strip()[:200]}')
        return {'text': done.stdout}

    def call(self, command, **args):
        if self.conn is None:
            self.conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.conn.connect(str(self.socket_path))
            self.stream = self.conn.makefile('rwb')
        self.ids += 1
        self.stream.write(json.dumps({'id': self.ids, 'command': command, 'args': {**args, 'cwd': os.getcwd()}}).encode() + b'\n')
        self.stream.flush()
        reply = json.loads(self.stream.readline())
        if not reply.get('ok'):
            raise Failed(f"{command}: {reply.get('error', {}).get('message', 'failed')}")
        return reply['result']

    def run(self, via, command, **args):
        return self.cli(command, **args) if via == 'cli' else self.call(command, **args)

    def eval(self, script):
        return self.call('eval', script=script).get('value')

    def disconnect(self):
        if self.conn:
            self.conn.close()
        self.conn = None

    def daemon_pid(self):
        if not self.socket_path.exists():
            return None
        try:
            return self.call('status')['pid']
        except (OSError, Failed, ValueError):
            return None
        finally:
            self.disconnect()

    def stop(self):
        """Stops the daemon and waits until its process is gone, so the next command starts cold."""
        pid = self.daemon_pid()
        subprocess.run([self.binary, 'stop'], env=self.env, capture_output=True)
        self.disconnect()
        deadline = time.monotonic() + 15
        while pid and time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.02)

    def cleanup(self):
        self.stop()
        for _ in range(3):  # a WebKit helper can outlive the daemon by a moment
            shutil.rmtree(store_dir(self.profile), ignore_errors=True)
            for file in SUPPORT.glob(f'*-{self.profile}.*'):
                file.unlink(missing_ok=True)
            shutil.rmtree(DOWNLOADS / self.profile, ignore_errors=True)
            time.sleep(0.3)

    def target(self, selector, ref_pattern):
        """The element argument as an agent passes it: its ref from a snapshot (untimed), else the CSS selector."""
        if not self.has('snapshot'):
            return selector
        text = self.call('snapshot')['text']
        found = re.search(ref_pattern, text)
        expect(found, f'no ref matching {ref_pattern!r} in the snapshot')
        return found.group(1)


def measure(step, check, runs, warmup):
    samples = []
    for i in range(warmup + runs):
        start = time.perf_counter()
        out = step(i)
        ms = (time.perf_counter() - start) * 1000
        check(i, out)
        if i >= warmup:
            samples.append(ms)
    return samples


def bench_umbra(umbra, only, runs, warmup, tmp):
    base = umbra.base
    results = {key: {} for key, _, _ in METRICS}

    def cell(key, path, fn):
        try:
            results[key][f'umbra_{path}'] = {'samples': fn()}
        except (Failed, OSError, ValueError) as error:
            results[key][f'umbra_{path}'] = {'error': str(error)}
        print(f'  umbra {path:6} {key:14} {summary(results[key][f"umbra_{path}"])}', flush=True)

    def wanted(key, needs):
        if key not in only:
            return False
        if not umbra.has(*needs):
            missing = [c for c in needs if c not in umbra.schema]
            for path in ('cli', 'socket'):
                results[key][f'umbra_{path}'] = {'na': 'not built', 'missing': missing}
            return False
        return True

    needs = {key: commands for key, _, commands in METRICS}

    if wanted('cold', needs['cold']):
        def cold():
            samples = []
            for _ in range(runs):  # no warm-up: every run is cold
                umbra.stop()
                start = time.perf_counter()
                umbra.cli('open', url=base + '/login')
                samples.append((time.perf_counter() - start) * 1000)
                expect(umbra.eval('return document.title') == 'Sign in · Northwind Ops', 'the login page did not load')
            return samples
        cell('cold', 'cli', cold)
        results['cold']['umbra_socket'] = {'na': 'CLI only'}

    umbra.cli('open', url=base + '/__login?next=/app')
    for path in ('cli', 'socket'):
        if wanted('navigate', needs['navigate']):
            cell('navigate', path, lambda: measure(
                lambda i: umbra.run(path, 'open', url=base + '/app'),
                lambda i, _: expect(umbra.eval('return document.title') == 'Dashboard · Northwind Ops', 'dashboard did not load'),
                runs, warmup))

        if wanted('snapshot', needs['snapshot']):
            umbra.call('open', url=base + '/app/orders', until="document.querySelectorAll('#rows tr').length === 10")
            cell('snapshot', path, lambda: measure(
                lambda i: umbra.run(path, 'snapshot'),
                lambda i, out: expect('1040' in out.get('text', '') + json.dumps(out), 'snapshot is missing the orders table'),
                runs, warmup))

        if wanted('click', needs['click']):
            def click():
                umbra.call('open', url=base + '/app/settings')
                target = umbra.target('#notifications', r'switch[^\n]*Email notifications[^\n]*?\b(e\d+)\b')
                arg = umbra.positionals('click')[0]
                read = "return document.getElementById('notifications').getAttribute('aria-checked')"
                state = {'checked': umbra.eval(read)}

                def flipped(i, _):
                    now = umbra.eval(read)
                    expect(now != state['checked'], 'switch did not flip')
                    state['checked'] = now
                return measure(lambda i: umbra.run(path, 'click', **{arg: target}), flipped, runs, warmup)
            cell('click', path, click)

        if wanted('fill', needs['fill']):
            def fill():
                umbra.call('open', url=base + '/app/settings')
                target = umbra.target('#display-name', r'textbox[^\n]*Display name[^\n]*?\b(e\d+)\b')
                arg, value = umbra.positionals('fill')[:2]
                return measure(
                    lambda i: umbra.run(path, 'fill', **{arg: target, value: f'Jordan Lee {i}'}),
                    lambda i, _: expect(umbra.eval("return document.getElementById('display-name').value") == f'Jordan Lee {i}',
                                        'field did not take the value'),
                    runs, warmup)
            cell('fill', path, fill)

        if wanted('screenshot', needs['screenshot']):
            umbra.call('open', url=base + '/app')
            shot = tmp / f'shot-{path}.png'

            def written(i, _):
                expect(shot.exists() and shot.stat().st_size > 1000, 'no screenshot written')
                shot.unlink()
            cell('screenshot', path, lambda: measure(lambda i: umbra.run(path, 'screenshot', path=str(shot)), written, runs, warmup))

        if wanted('eval', needs['eval']):
            cell('eval', path, lambda: measure(
                lambda i: umbra.run(path, 'eval', script='return 1 + 1'),
                lambda i, out: expect(str(out.get('value', out.get('text', ''))).strip() == '2', f'eval returned {out}'),
                runs, warmup))

        loaded = "return [...document.images].filter(i => i.complete && i.naturalWidth > 0).length"
        if wanted('heavy', needs['heavy']):
            cell('heavy', path, lambda: measure(
                lambda i: umbra.run(path, 'open', url=base + '/heavy'),
                lambda i, _: expect(umbra.eval(loaded) == 200, 'not every image loaded'),
                runs, warmup))

        if wanted('heavy_blocked', needs['heavy_blocked']):
            umbra.call('block', types=['images'])
            cell('heavy_blocked', path, lambda: measure(
                lambda i: umbra.run(path, 'open', url=base + '/heavy'),
                lambda i, _: expect(umbra.eval(loaded) == 0, 'images were not blocked'),
                runs, warmup))
            umbra.call('block', none=True)
    return results


def bench_playwright(browser, base, runs, warmup, scale, tmp):
    out = tmp / f'pw-{browser}.json'
    done = subprocess.run(['node', str(BENCH / 'pw.js'), '--browser', browser, '--base', base, '--runs', str(runs),
                           '--warmup', str(warmup), '--scale', str(scale), '--out', str(out)],
                          capture_output=True, text=True)
    if done.returncode:
        raise Failed(f'pw.js {browser} failed: {done.stderr.strip()[-400:]}')
    return json.loads(out.read_text())


def percentile(samples, q):
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def stats(cell):
    if 'samples' in cell and cell['samples']:
        cell['p50'] = round(statistics.median(cell['samples']), 2)
        cell['p95'] = round(percentile(cell['samples'], 0.95), 2)
        cell['samples'] = [round(s, 2) for s in cell['samples']]
    return cell


def ms(value):
    return f'{value:.1f}' if value < 10 else f'{value:.0f}'


def summary(cell):
    if 'na' in cell:
        return f"n/a ({cell['na']})"
    if 'error' in cell:
        return 'failed: ' + cell['error']
    if 'samples' in cell:
        stats(cell)
        return f"{ms(cell['p50'])} / {ms(cell['p95'])}"
    return '—'


def machine():
    def sysctl(key):
        return subprocess.run(['sysctl', '-n', key], capture_output=True, text=True).stdout.strip()

    def sw_vers(flag):
        return subprocess.run(['sw_vers', flag], capture_output=True, text=True).stdout.strip()
    commit = subprocess.run(['git', '-C', str(REPO), 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(['git', '-C', str(REPO), 'status', '--porcelain', '--', 'Sources'], capture_output=True, text=True).stdout.strip()
    return {'model': sysctl('hw.model'), 'cpu': sysctl('machdep.cpu.brand_string'), 'cores': int(sysctl('hw.ncpu')),
            'memory_gb': int(sysctl('hw.memsize')) // 2**30, 'macos': sw_vers('-productVersion'), 'build': sw_vers('-buildVersion'),
            'python': platform.python_version(), 'load_average': [round(x, 2) for x in os.getloadavg()],
            'umbra_commit': commit + (' + uncommitted Sources changes' if dirty else '')}


def write_results(data):
    (BENCH / 'results.json').write_text(json.dumps(data, indent=1) + '\n')
    m = data['machine']
    lines = ['# Umbra vs Playwright on Northwind Ops', '',
             f"Measured {data['date']} on a {m['model']} ({m['cpu']}, {m['cores']} cores, {m['memory_gb']} GB), "
             f"macOS {m['macos']} ({m['build']}), load average {m['load_average'][0]} at the start.",
             f"{data['umbra_version']} at {m['umbra_commit']}; Playwright {data.get('playwright', 'not run')} "
             + ' and '.join(f"{name} {v}" for name, v in data.get('browsers', {}).items()) + '.',
             f"Each cell is p50 / p95 in milliseconds over {data['runs']} timed runs, after {data['warmup']} untimed warm-up runs "
             '(cold start has none: every run is cold). Every run is checked afterward (untimed): the page loaded, the switch '
             'flipped, the field holds the value, the images loaded or were blocked.', '',
             '| Metric | ' + ' | '.join(label for _, label in COLUMNS) + ' |',
             '|---|' + '---:|' * len(COLUMNS)]
    for key, label, _ in METRICS:
        row = data['metrics'].get(key, {})
        lines.append(f'| {label} | ' + ' | '.join(summary(row.get(col, {})) for col, _ in COLUMNS) + ' |')
    lines += ['', '## What each path measures', '',
              '- **Umbra CLI**: wall time of `umbra <command>` as a fresh process, as an agent\'s shell tool runs it '
              '(process start, connecting to the daemon, the command, printing). Cold start runs `umbra stop` first and waits '
              'for the daemon to exit, so it includes spawning the daemon and starting WebKit.',
              '- **Umbra socket**: one persistent connection to the daemon\'s unix socket, one JSON line per command '
              '(what `umbra mcp` and other long-lived clients pay).',
              '- **Playwright**: one persistent Node process per browser (`bench/pw.js`), headless, its best case. Cold start '
              'is `launch()` + `newContext()` + `newPage()` + `goto()` inside that process; Node\'s own start-up is not counted. '
              'Each context is non-persistent, so every launch uses a fresh temporary profile.',
              '', '## Notes', '',
              '- Accessibility snapshot: Umbra `snapshot` against Playwright `page.locator(\'body\').ariaSnapshot()` on /app/orders '
              'with 10 rows loaded.',
              '- Click: the settings page\'s `role=switch` button, whose handler flips `aria-checked` synchronously. Playwright\'s '
              'click includes its actionability checks.',
              '- Fill: the settings page\'s React-style controlled input, which ignores `.value` writes the framework never sees.',
              f"- Screenshots are the 1280×900 viewport at device pixel ratio {data['scale']} for every browser, written to a PNG file.",
              '- Image blocking: Umbra `block images`; Playwright `page.route()` aborting image URLs (a glob, so other requests '
              'are not intercepted). The page serves 200 incompressible 96×96 PNGs (about 4.9 MB) with `Cache-Control: no-store`.',
              '- The site is `bench/site/server.py` (Python, threaded, HTTP/1.1 keep-alive) on 127.0.0.1.',
              '- "n/a (not built)" means the Umbra build lacks a command that metric needs; rerun once it lands.']
    if data.get('errors'):
        lines += ['', '## Errors', ''] + [f'- {k}: {v}' for k, v in data['errors'].items()]
    (BENCH / 'results.md').write_text('\n'.join(lines) + '\n')


def start_site():
    site = subprocess.Popen([sys.executable, str(BENCH / 'site/server.py'), '--port', '0'], stdout=subprocess.PIPE, text=True)
    base = site.stdout.readline().strip().split(' on ')[-1]
    expect(base.startswith('http://'), 'the site did not start')
    return site, base


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--runs', type=int, default=20)
    parser.add_argument('--warmup', type=int, default=2)
    parser.add_argument('--only', help='comma-separated metrics: ' + ','.join(k for k, _, _ in METRICS))
    parser.add_argument('--no-playwright', action='store_true')
    parser.add_argument('--umbra', default=os.environ.get('UMBRA') or str(REPO / 'build/Umbra.app/Contents/MacOS/umbra'))
    options = parser.parse_args()
    only = set(options.only.split(',')) if options.only else {k for k, _, _ in METRICS}
    if not Path(options.umbra).exists():
        sys.exit(f'{options.umbra} is missing: run `make build` first')

    tmp = Path(tempfile.mkdtemp(prefix='umbra-bench-'))
    site, base = start_site()
    umbra = Umbra(options.umbra, base)

    def finish():
        umbra.cleanup()
        site.terminate()
        shutil.rmtree(tmp, ignore_errors=True)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))  # unwinds through the finally below

    data = {'date': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'), 'runs': options.runs, 'warmup': options.warmup,
            'machine': machine(), 'umbra_version': umbra.version, 'profile': umbra.profile, 'metrics': {}, 'errors': {}}
    print(f'site {base}, profile {umbra.profile}, {options.runs} runs + {options.warmup} warm-up', flush=True)
    try:
        run_all(options, only, umbra, base, tmp, data)
    finally:
        finish()
    for row in data['metrics'].values():
        for cell in row.values():
            stats(cell)
    write_results(data)
    print((BENCH / 'results.md').read_text())


def run_all(options, only, umbra, base, tmp, data):
    try:
        data['metrics'] = bench_umbra(umbra, only, options.runs, options.warmup, tmp)
        data['scale'] = umbra.eval('return devicePixelRatio') or 1
    finally:
        umbra.cleanup()
    if not options.no_playwright:
        data['browsers'] = {}
        for browser in ('chromium', 'webkit'):
            print(f'  playwright {browser}…', flush=True)
            try:
                result = bench_playwright(browser, base, options.runs, options.warmup, data['scale'], tmp)
            except Failed as error:
                data['errors'][browser] = str(error)
                continue
            data['playwright'] = result['playwright']
            data['browsers'][browser] = result.get('version')
            for key, _, _ in METRICS:
                if key not in only:
                    continue
                if key in result['metrics']:
                    data['metrics'][key][browser] = {'samples': result['metrics'][key]}
                elif key in result['errors']:
                    data['metrics'][key][browser] = {'error': result['errors'][key]}
                print(f'  {browser:13} {key:14} {summary(data["metrics"][key].get(browser, {}))}', flush=True)


if __name__ == '__main__':
    main()
