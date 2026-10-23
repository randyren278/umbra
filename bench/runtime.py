#!/usr/bin/env python3
"""Frozen localhost mixed workload, paired binaries, responsibility-family memory and raw evidence.

Dry run: python3 bench/runtime.py --baseline /abs/umbra --out /tmp/runtime-pilot --concurrency 1 --repeats 1 --cycles 1 --controls 3
Full: add --candidate /abs/umbra --candidate-sha256 HASH --routes matched,public_http
No installed/default browser profiles are read. Results retain failures and every measured task.
"""
import argparse
import atexit
import concurrent.futures
import contextlib
import hashlib
import http.server
import json
import math
import os
import platform
import queue
import re
import secrets
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory

ROOT = Path(__file__).resolve().parent.parent
WORKLOAD = Path(__file__).with_name('runtime_workload.json')
ADMISSION = ROOT / 'docs/evaluation/runtime/ADMISSION.json'
CONTROL = ROOT / 'docs/evaluation/runtime/CONTROL.json'
OBSERVATION = ROOT / 'docs/evaluation/runtime/OBSERVATION.json'
TOKENIZER = None
TOKENIZER_VERSION = None


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    temporary.replace(path)


def percentile(values, fraction):
    if not values: return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def distribution(values):
    return {'n': len(values), 'p50': percentile(values, .5), 'p95': percentile(values, .95),
            'min': min(values) if values else None, 'max': max(values) if values else None}


class Failure(Exception):
    pass


class Site:
    def __init__(self, specification):
        self.spec = specification
        self.lock = threading.Lock()
        self.requests = {}
        self.receipts = {}
        rows = ''.join(f'<tr><td>{n}</td><td>row {n}</td></tr>' for n in range(1, specification['rows'] + 1))
        self.static = ('<!doctype html><title>Frozen report</title><h1>Report</h1><table><tbody>' + rows + '</tbody></table>'
                       '<script id="payload" type="application/json">' + json.dumps(specification['expected']) + '</script>')
        self.app = (self.static + '''<label>Entry <input id="entry"></label><button id="submit">Submit</button><output id="result"></output>
<script>
window.events=[]; window.clickTrusted=false;
entry.addEventListener('input',e=>events.push(e.isTrusted));
submit.addEventListener('click',async e=>{clickTrusted=e.isTrusted; const value=entry.value;
 const response=await fetch('/receipt?token='+encodeURIComponent(value)+'&trusted='+String(events.length>0&&events.every(Boolean)&&e.isTrusted));
 if(response.ok){ result.textContent=value; result.dataset.done=value; }});
setTimeout(()=>{window.jsTotal=Array.from(document.querySelectorAll('tbody tr')).reduce((sum,row)=>sum+Number(row.cells[0].textContent),0)},10);
</script>''')
        owner = self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                parsed = urllib.parse.urlsplit(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                token = query.get('token', [''])[0]
                with owner.lock:
                    if token: owner.requests[(parsed.path, token)] = owner.requests.get((parsed.path, token), 0) + 1
                if parsed.path == '/static': body, kind = owner.static, 'text/html; charset=utf-8'
                elif parsed.path == '/app': body, kind = owner.app, 'text/html; charset=utf-8'
                elif parsed.path == '/api': body, kind = json.dumps(owner.spec['expected']), 'application/json'
                elif parsed.path == '/receipt':
                    trusted = query.get('trusted') == ['true']
                    with owner.lock: owner.receipts[token] = {'trusted': trusted, 'count': owner.receipts.get(token, {}).get('count', 0) + 1}
                    body, kind = json.dumps({'accepted': trusted}), 'application/json'
                else: body, kind = '<title>helper</title>', 'text/html'
                raw = body.encode()
                self.send_response(200)
                self.send_header('Content-Type', kind)
                self.send_header('Content-Length', str(len(raw)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                try: self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError): pass
        class FixtureHTTPServer(http.server.ThreadingHTTPServer):
            request_queue_size = 128
        self.server = FixtureHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'

    def close(self):
        self.server.shutdown(); self.server.server_close()


class Client:
    def __init__(self, session):
        self.session = session
        if session.abort_event.is_set(): raise Failure('cell aborted: ' + session.abort_reason['reason'])
        self.socket = socket.socket(socket.AF_UNIX)
        self.socket.settimeout(70)
        self.socket.connect(str(session.path))
        self.stream = self.socket.makefile('rb')
        self.serial = 0

    def call(self, command, **args):
        if self.session.abort_event.is_set(): raise Failure('cell aborted: ' + self.session.abort_reason['reason'])
        allow_error = args.pop('_allow_error',False)
        self.serial += 1
        args.setdefault('timeout', 60)
        request = {'id': self.serial, 'command': command, 'args': args}
        started = time.perf_counter()
        self.socket.sendall((json.dumps(request, separators=(',', ':')) + '\n').encode())
        data = self.stream.readline()
        elapsed = (time.perf_counter() - started) * 1000
        self.last_reply_bytes = len(data)
        if not data: raise Failure(f'{command}: connection closed')
        result = json.loads(data)
        if result.get('id') != self.serial: raise Failure(f'{command}: response ID mismatch')
        with self.session.lock:
            self.session.commands.append({'command': command, 'elapsedMs': elapsed, 'replyBytes': len(data),
                                          'started':started,'ended':time.perf_counter(),'ok': result.get('ok', False), 'transport': 'socket'})
        if allow_error: return result
        if not result.get('ok'): raise Failure(f'{command}: {result.get("error")}')
        return result.get('result', {})

    def close(self):
        self.stream.close(); self.socket.close()

    def __enter__(self): return self
    def __exit__(self, *args): self.close()


class Session:
    def __init__(self, binary, out):
        self.binary = str(Path(binary).resolve())
        self.out = Path(out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.profile = 'runtime-bench-' + secrets.token_hex(5)
        self.temporary = Path(tempfile.mkdtemp(prefix='umbra-runtime-bench-'))
        agents = self.temporary / 'agents'; agents.mkdir()
        sites = self.temporary / 'sites.json'; sites.write_text('[]')
        self.env = {**os.environ, 'UMBRA_PROFILE': self.profile, 'UMBRA_IDLE': '60', 'UMBRA_UNSEEN': '1',
                    'UMBRA_AGENTS': str(agents), 'UMBRA_SITES': str(sites)}
        # Ambient developer tuning must not silently select different limits in paired arms.
        for key in ('UMBRA_ACTIVE', 'UMBRA_PENDING', 'UMBRA_TABS', 'UMBRA_CONNECTIONS', 'UMBRA_REQUEST_BYTES', 'UMBRA_QUEUE_BYTES', 'UMBRA_MIN_AVAILABLE_MB', 'UMBRA_JOB_MB'):
            self.env.pop(key, None)
        self.path = memory.SUPPORT / f'daemon-{self.profile}.sock'
        self.log = self.out / 'daemon.log'
        self.commands = []
        self.lock = threading.Lock()
        self.closed = False
        self.abort_event = threading.Event()
        self.abort_reason = None
        self.pid = memory.spawn_disclaimed([self.binary, 'serve'], self.env, self.log)
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            try:
                with Client(self) as client:
                    result = client.call('status')
                    if result.get('pid') == self.pid: return
            except (OSError, ValueError, Failure): pass
            time.sleep(.05)
        self.close()
        raise Failure('daemon did not become ready')

    def cli(self, command, **args):
        argv = [self.binary, command]
        if command in ('read', 'fetch'): argv.append(args.pop('url'))
        elif command == 'eval': argv.append(args.pop('script'))
        for key, value in args.items():
            argv.extend(['--' + key, str(value)])
        argv.append('--json')
        started = time.perf_counter()
        result = subprocess.run(argv, env=self.env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=70)
        elapsed = (time.perf_counter() - started) * 1000
        self.last_cli_stdout = result.stdout
        with self.lock:
            self.commands.append({'command': command, 'elapsedMs': elapsed, 'replyBytes': len(result.stdout.encode()),
                                  'started':started,'ended':time.perf_counter(),'ok': result.returncode == 0, 'transport': 'cli'})
        if result.returncode: raise Failure(f'CLI {command}: {result.stderr.strip()}')
        if command == 'read': return {'output': result.stdout.rstrip('\n')}
        if command == 'fetch': return {'body': result.stdout, 'status': 200}
        return json.loads(result.stdout)

    def close(self):
        if self.closed: return
        self.closed = True
        try: subprocess.run([self.binary, 'stop'], env=self.env, capture_output=True, timeout=12)
        except subprocess.TimeoutExpired: pass
        reaped = False
        def running():
            nonlocal reaped
            if reaped: return False
            try:
                child, _ = os.waitpid(self.pid, os.WNOHANG)
                if child == self.pid: reaped = True; return False
            except ChildProcessError:
                reaped = True; return False
            return True
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline and running(): time.sleep(.1)
        if running():
            try: os.kill(self.pid, signal.SIGTERM)
            except ProcessLookupError: pass
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and running(): time.sleep(.1)
            if running():
                try: os.kill(self.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                try: os.waitpid(self.pid, 0)
                except ChildProcessError: pass
                reaped = True
        label = 'com.randyren.umbra.keep-signed-in.' + self.profile
        subprocess.run(['launchctl', 'bootout', f'gui/{os.getuid()}/{label}'], capture_output=True)
        paths = [memory.store_dir(self.profile), memory.DOWNLOADS / self.profile]
        for _ in range(3):
            for path in paths:
                shutil.rmtree(path, ignore_errors=True)
            for path in memory.SUPPORT.glob(f'*-{self.profile}.*'): path.unlink(missing_ok=True)
            time.sleep(.2)
        shutil.rmtree(self.temporary, ignore_errors=True)
        self.cleanup = {'profile': self.profile, 'daemonGone': not running(),
                        'profileFilesGone': not any(p.exists() for p in paths) and not list(memory.SUPPORT.glob(f'*-{self.profile}.*'))}
        write(self.out / 'cleanup.json', self.cleanup)


class Sampler:
    def __init__(self, session, interval, budget_bytes=None, max_samples=None, sample_sink=None):
        self.sample_lock = threading.RLock()
        self.max_samples = max_samples; self.sample_sink = sample_sink
        self.budget_bytes = budget_bytes
        self.session = session; self.interval = interval; self.samples = []; self.stop = threading.Event(); self.cpu = {}
        self.elapsed = []
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def sample(self):
        with self.sample_lock: return self._sample()

    def _sample(self):
        start = time.monotonic(); members = []
        for pid, kind in memory.family(self.session.pid).items():
            used = memory.usage(pid)
            if used is not None:
                members.append({'pid': pid, 'kind': kind, 'physicalFootprintBytes': used[0], 'cpuSeconds': used[1]})
                self.cpu[pid] = used[1]
        self.elapsed.append(time.monotonic() - start)
        self.samples.append({'time': start, 'physicalFootprintBytes': sum(m['physicalFootprintBytes'] for m in members),
                             'cpuSecondsObserved': sum(self.cpu.values()), 'members': members})
        if self.sample_sink:
            self.sample_sink.write(json.dumps(self.samples[-1],sort_keys=True)+'\n'); self.sample_sink.flush()
        footprint = self.samples[-1]['physicalFootprintBytes']
        if self.max_samples: del self.samples[:-self.max_samples]
        if self.budget_bytes and footprint > self.budget_bytes and not self.session.abort_event.is_set():
            self.session.abort_reason = {'time':start,'physicalFootprintBytes':footprint,'budgetBytes':self.budget_bytes,
                'reason':'Whole browser-family physical footprint exceeded the predeclared budget. Remaining tasks fail; no ratio gate may use this cell.'}
            self.session.abort_event.set()
            write(self.session.out/'budget-abort.json',self.session.abort_reason)
            try: os.kill(self.session.pid,signal.SIGTERM)
            except ProcessLookupError: pass

    def run(self):
        while not self.stop.is_set():
            try: self.sample()
            except Exception as exc:
                self.session.abort_reason = {'reason':'Resource sampler failed: ' + str(exc)}
                self.session.abort_event.set()
                write(self.session.out/'budget-abort.json',self.session.abort_reason)
                try: os.kill(self.session.pid,signal.SIGTERM)
                except ProcessLookupError: pass
                return
            self.stop.wait(self.interval)

    def close(self):
        self.stop.set(); self.thread.join(timeout=10)
        if not self.session.abort_event.is_set(): self.sample()



class Admission:
    def __init__(self, session, sampler):
        self.session = session; self.sampler = sampler
        self.spec = json.loads(ADMISSION.read_text())
        self.condition = threading.Condition()
        self.reserved = self.spec['keepaliveReservedBytes']
        self.deadline = time.monotonic() + self.spec['cellTimeoutSeconds']
        self.tabs = []; self.waits = []; self.keepalives = []; self.stop = threading.Event()
        self.timer = threading.Timer(self.spec['cellTimeoutSeconds'],lambda:self.abort('Secondary 900-second cell deadline'))
        self.timer.daemon = True; self.timer.start()
        self.thread = threading.Thread(target=self.keepalive, daemon=True)
        self.thread.start()

    def abort(self, reason):
        if self.session.abort_event.is_set(): return
        self.session.abort_reason = {'reason':reason,'time':time.monotonic()}
        self.session.abort_event.set()
        write(self.session.out/'admission-abort.json',self.session.abort_reason)
        try: os.kill(self.session.pid,signal.SIGTERM)
        except ProcessLookupError: pass

    def fresh(self):
        if self.session.abort_event.is_set(): raise Failure('cell aborted: ' + self.session.abort_reason['reason'])
        if time.monotonic() >= self.deadline:
            self.abort('Secondary 900-second cell deadline')
            raise Failure('secondary 900-second cell deadline')
        try: self.sampler.sample()
        except Exception as exc:
            self.abort('Secondary resource sampler failed: '+str(exc))
            raise
        if self.session.abort_event.is_set(): raise Failure('cell aborted: ' + self.session.abort_reason['reason'])
        return self.sampler.samples[-1]['physicalFootprintBytes']

    @contextlib.contextmanager
    def claim(self):
        start = time.monotonic()
        deadline = min(self.deadline, start + self.spec['admissionTimeoutSeconds'])
        reservation = self.spec['reservationBytes']
        timer = threading.Timer(max(0,deadline-start),lambda:self.abort('Secondary admission exceeded 180 seconds or cell deadline'))
        timer.daemon = True; timer.start()
        try:
            with self.condition:
                while self.fresh() + self.reserved + reservation > self.spec['admissionWatermarkBytes']:
                    if time.monotonic() >= deadline:
                        self.abort('Secondary admission exceeded 180 seconds or cell deadline')
                        raise Failure('secondary admission exceeded 180 seconds or cell deadline')
                    self.condition.wait(min(.25, max(0, deadline-time.monotonic())))
                self.reserved += reservation
                self.waits.append({'start':start,'waitSeconds':time.monotonic()-start,'reservedBytes':self.reserved})
        finally: timer.cancel()
        try: yield
        finally:
            with self.condition:
                try: self.fresh()
                finally:
                    self.reserved -= reservation
                    self.condition.notify_all()

    def add(self, tab):
        with self.condition: self.tabs.append(tab)

    def keepalive(self):
        try:
            with Client(self.session) as client:
                while not self.stop.wait(self.spec['keepaliveIntervalSeconds']):
                    with self.condition: tabs = list(self.tabs)
                    for tab in tabs:
                        if self.stop.is_set(): return
                        with self.condition:
                            self.fresh()
                            started = time.monotonic()
                            value = client.call('eval',tab=tab,script='return 1')
                            if value.get('value') != 1: raise Failure('secondary keepalive output mismatch')
                            self.fresh()
                            self.keepalives.append({'tab':tab,'time':started,'elapsedSeconds':time.monotonic()-started})
        except Exception as exc:
            if not self.stop.is_set():
                self.abort('Secondary keepalive failed: ' + str(exc))

    def close(self):
        self.stop.set(); self.timer.cancel(); self.thread.join(timeout=10)
        if self.thread.is_alive():
            self.abort('Secondary keepalive failed to stop')
            self.thread.join(timeout=5)


def task(client, site, kind, tab, token, route):
    expected = site.spec['expected']
    if kind == 'static_html':
        url = site.base + '/static?token=' + token
        result = client.call('http' if route == 'public_http' else 'read', url=url)
        html = result.get('body', result.get('output', ''))
        match = re.search(r'<script id="payload" type="application/json">(.*?)</script>', html, re.S)
        value = json.loads(match.group(1)) if match else None
        if value != expected: raise Failure(f'static semantic mismatch: {value!r}')
        with site.lock:
            if site.requests.get(('/static', token)) != 1: raise Failure('static server completion missing/duplicated')
    elif kind == 'json_api':
        result = client.call('http' if route == 'public_http' else 'fetch', url=site.base + '/api?token=' + token)
        if result.get('status') != 200 or json.loads(result['body']) != expected: raise Failure('JSON semantic mismatch')
        with site.lock:
            if site.requests.get(('/api', token)) != 1: raise Failure('JSON server completion missing/duplicated')
    elif kind == 'javascript_dom':
        result = client.call('eval', tab=tab, script="return {total:window.jsTotal,rows:document.querySelectorAll('tbody tr').length}")
        if result.get('value') != expected: raise Failure(f'JS semantic mismatch: {result!r}')
    elif kind == 'native_form':
        client.call('fill', tab=tab, target='#entry', value=token)
        client.call('click', tab=tab, target='#submit')
        client.call('wait', tab=tab, js='document.querySelector("#result").dataset.done === ' + json.dumps(token))
        result = client.call('eval', tab=tab, script="return {value:document.querySelector('#entry').value,output:document.querySelector('#result').textContent,trusted:window.events.length>0&&window.events.every(Boolean)&&window.clickTrusted}")
        if result.get('value') != {'value': token, 'output': token, 'trusted': True}: raise Failure(f'native semantic mismatch: {result!r}')
        with site.lock:
            if site.receipts.get(token) != {'trusted': True, 'count': 1}: raise Failure('native server receipt mismatch')
    else: raise Failure('unknown frozen task')
    return result


def token_counts(full, delta):
    if TOKENIZER is None: return None
    counts = {'encoding':TOKENIZER.name,'tiktokenVersion':TOKENIZER_VERSION,
              'fullPayload':len(TOKENIZER.encode(json.dumps(full,sort_keys=True,separators=(',',':')))),
              'deltaPayload':len(TOKENIZER.encode(json.dumps(delta,sort_keys=True,separators=(',',':')))),
              'fullText':len(TOKENIZER.encode(full['text'])),'deltaText':len(TOKENIZER.encode(delta['text'])),
              'scope':'Actual counts of canonical compact JSON result payloads under fixed o200k_base encoding; not a claim about GPT-6 billing tokenizer. Raw result payloads retained.'}
    counts['payloadReduction'] = 1-counts['deltaPayload']/counts['fullPayload']
    counts['textReduction'] = 1-counts['deltaText']/counts['fullText']
    counts['fiftyPercentPayloadGate'] = counts['payloadReduction'] >= .5
    return counts


def observations(session):
    try:
        with Client(session) as client:
            before = client.call('observe', tab='worker-0')
            client.call('fill', tab='worker-0', target='#entry', value='observation-delta')
            delta = client.call('observe', tab='worker-0', since=before['baseline'])
            delta_wire = client.last_reply_bytes
            full = client.call('observe', tab='worker-0')
            full_wire = client.last_reply_bytes
            lines = before['text'].split('\n')
            patch = delta['patch']
            lines[patch['start']:patch['start']+patch['delete']] = patch['lines']
            reconstructed = '\n'.join(lines)
            if reconstructed != full['text']: raise Failure('delta reconstruction does not equal fresh full observation')
            return {'available':True,'reconstructionMatches':True,'fullReplyBytes':full_wire,'deltaReplyBytes':delta_wire,
                    'wireByteReduction':1-delta_wire/full_wire,'fullTextBytes':len(full['text'].encode()),
                    'deltaTextBytes':len(delta['text'].encode()),
                    'tokenCounts':token_counts(full,delta),'beforePayload':before,'fullPayload':full,'deltaPayload':delta,
                    'note':'UTF-8 bytes and fixed-encoding token counts are separate measurements; reconstruction verified.'}
    except Exception as exc:
        return {'available':False,'error':str(exc),'tokenCounts':None}


def cell(binary, label, candidate, route, concurrency, repetition, specification, out, cycles, controls, budget_bytes, admission=False):
    site = Site(specification)
    try: session = Session(binary, out)
    except Exception:
        site.close(); raise
    sampler = Sampler(session, specification['memory_sample_seconds'], budget_bytes)
    controller = Admission(session,sampler) if admission else None
    def claim(): return controller.claim() if controller else contextlib.nullcontext()
    runtime_metrics = {}
    rows = []; loaded = []; load_stop = threading.Event(); load_thread = None
    try:
        if candidate:
            with Client(session) as client:
                with claim(): runtime_metrics['initial']=client.call('runtime')
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            def setup(index):
                with Client(session) as client:
                    with claim(): client.call('open', tab=f'worker-{index}', url=site.base + '/app', until='window.jsTotal===210')
                    if controller: controller.add(f'worker-{index}')
            list(pool.map(setup, range(concurrency)))
        def worker(index, warmup=False):
            with Client(session) as client:
                for cycle in range(specification['warmup_cycles'] if warmup else cycles):
                    for kind in specification['task_order']:
                        token = f'{"warm" if warmup else "task"}-{index}-{cycle}-{kind}'
                        start = time.perf_counter()
                        error = None
                        try:
                            with claim(): task(client, site, kind, f'worker-{index}', token, 'public_http' if candidate and route == 'public_http' else 'matched')
                        except Exception as exc: error = str(exc)
                        record = {'worker': index, 'cycle': cycle, 'task': kind, 'warmup': warmup,
                                  'started':start,'ended':time.perf_counter(),'elapsedMs': (time.perf_counter() - start) * 1000, 'ok': error is None, 'error': error}
                        with session.lock: rows.append(record)
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool: list(pool.map(lambda i: worker(i, True), range(concurrency)))
        active_start = time.monotonic()
        def loaded_controls():
            scheduled = active_start
            try:
                with Client(session) as client:
                    while not load_stop.wait(max(0,scheduled-time.monotonic())):
                        started = time.monotonic(); error = None
                        try:
                            with claim(): value = client.call('eval',tab='worker-0',script='return 1')
                            if value.get('value') != 1: raise Failure('loaded control output mismatch')
                        except Exception as exc: error = str(exc)
                        ended = time.monotonic()
                        loaded.append({'phase':'loaded','transport':'socket','scheduled':scheduled,'started':started,'ended':ended,
                                       'elapsedMs':(ended-started)*1000,'ok':error is None,'error':error})
                        scheduled = active_start + (math.floor((ended-active_start)/.1)+1)*.1
                        if session.abort_event.is_set(): return
            except Exception as exc:
                loaded.append({'phase':'loaded','transport':'socket','started':time.monotonic(),'ok':False,'error':str(exc),'elapsedMs':0})
        load_thread = threading.Thread(target=loaded_controls,daemon=True); load_thread.start()
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool: list(pool.map(worker, range(concurrency)))
        finally:
            load_stop.set(); load_thread.join(timeout=245 if admission else 75)
            if load_thread.is_alive(): raise Failure('loaded control did not finish within bounded request/admission deadlines')
        active_end = time.monotonic()
        if candidate and not session.abort_event.is_set():
            with Client(session) as client:
                with claim(): runtime_metrics['afterActive']=client.call('runtime')
        control = []
        for transport in (() if session.abort_event.is_set() else ('socket', 'cli')):
            with Client(session) as client:
                for _ in range(controls):
                    start = time.perf_counter()
                    try:
                        with claim(): value = client.call('eval', tab='worker-0', script='return 1') if transport == 'socket' else session.cli('eval', tab='worker-0', script='return 1')
                        if value.get('value') != 1: raise Failure('control output mismatch')
                        error = None
                    except Exception as exc: error = str(exc)
                    control.append({'phase':'idle','transport': transport,'started':start,'ended':time.perf_counter(),'elapsedMs': (time.perf_counter() - start) * 1000, 'ok': error is None, 'error': error})
        observation = observations(session) if candidate and concurrency == 1 and not session.abort_event.is_set() and not admission else None
        if controller: controller.close()
        sampler.close()
        active_samples = [s for s in sampler.samples if active_start <= s['time'] <= active_end]
        measured = [row for row in rows if not row['warmup']]
        good = [row for row in measured if row['ok']]
        seconds = active_end - active_start
        result = {'label': label, 'route': route, 'concurrency': concurrency, 'repeat': repetition,
                  'admission':{'protocolSha256':digest(ADMISSION),'waits':controller.waits,'keepalives':controller.keepalives} if controller else None,
                  'budgetAbort': session.abort_reason,
                  'binary': str(Path(binary).resolve()), 'sha256': digest(binary), 'profile': session.profile,
                  'runtimeMetrics':runtime_metrics,'tasks': rows, 'commands': session.commands, 'controls': control,'loadedControls':loaded, 'observation': observation, 'memorySamples': sampler.samples,
                  'activeStart': active_start, 'activeEnd': active_end,
                  'summary': {'idleControlFailures':sum(not r['ok'] for r in control),'loadedControlFailures':sum(not r['ok'] for r in loaded),'loadedControlLatencyMs':distribution([r['elapsedMs'] for r in loaded]),'tasks': len(measured), 'successes': len(good), 'failures': len(measured)-len(good),
                              'warmupFailures': sum(not r['ok'] for r in rows if r['warmup']), 'seconds': seconds,
                              'successfulTasksPerSecond': len(good)/seconds, 'taskLatencyMs': distribution([r['elapsedMs'] for r in measured]),
                              'peakActivePhysicalFootprintBytes': max((s['physicalFootprintBytes'] for s in active_samples), default=None),
                              'medianActivePhysicalFootprintBytes': statistics.median(s['physicalFootprintBytes'] for s in active_samples) if active_samples else None,
                              'activeMemorySamples': len(active_samples), 'samplerDurationMs': distribution([n*1000 for n in sampler.elapsed]),
                              'cpuSecondsObservedDelta': (active_samples[-1]['cpuSecondsObserved']-active_samples[0]['cpuSecondsObserved']) if len(active_samples)>1 else None,
                              'taskTypes': {kind: {'successes':sum(r['ok'] for r in measured if r['task']==kind), 'failures':sum(not r['ok'] for r in measured if r['task']==kind), 'latencyMs':distribution([r['elapsedMs'] for r in measured if r['task']==kind])} for kind in specification['task_order']},
                              'controls': {transport: distribution([r['elapsedMs'] for r in control if r['transport']==transport]) for transport in ('socket','cli')}}}
        write(Path(out) / 'results.json', result)
        return result
    finally:
        load_stop.set()
        if load_thread and load_thread.is_alive(): load_thread.join(timeout=245 if admission else 75)
        if controller: controller.close()
        if sampler.thread.is_alive(): sampler.close()
        if not (Path(out)/'results.json').exists():
            write(Path(out)/'partial.json',{'runtimeMetrics':runtime_metrics,'tasks':rows,'commands':session.commands,'loadedControls':loaded,'memorySamples':sampler.samples,'budgetAbort':session.abort_reason,'admission':{'waits':controller.waits,'keepalives':controller.keepalives} if controller else None})
        session.close(); site.close()



def runtime_phase(binary, concurrency, out, budget_bytes):
    out = Path(out); out.mkdir(parents=True,exist_ok=True)
    site = Site(json.loads(WORKLOAD.read_text()))
    try: session = Session(binary,out/'browser')
    except Exception:
        site.close(); raise
    sampler = Sampler(session,.25,budget_bytes)
    result = {'concurrency':concurrency,'expectedActors':concurrency,'binarySha256':digest(binary),'protocolSha256':digest(CONTROL),
              'ownership':[],'cancellation':[],'progress':[],'errors':[],'metrics':[],'started':time.monotonic()}
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=max(3,concurrency));admin=None
    def actor(index): return {'tab':f'owner-{index}','agent':f'actor-{index}','task':'isolation'}
    def invoke(command, **args):
        with Client(session) as client: return client.call(command,**args)
    def pending(request, state):
        deadline = time.monotonic()+8
        while time.monotonic()<deadline:
            value = admin.call('tasks')
            result['metrics'].append({'phase':'cancellation','time':time.monotonic(),'runtime':admin.call('runtime'),'tasks':value})
            if any(row.get('request')==request and row.get('state')==state for row in value['pending']): return
            time.sleep(.02)
        raise Failure(f'{request} was never observed {state}')
    def drain_connections():
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            value=admin.call('runtime')
            result['metrics'].append({'phase':'connection-drain','time':time.monotonic(),'runtime':value})
            if value['connections']<=1: return
            time.sleep(.01)
        raise Failure('semantic worker connections did not drain to sole admin')
    try:
        admin=Client(session)
        for index in range(concurrency):
            owner=actor(index)
            admin.call('open',url=site.base+'/app',**owner)
            admin.call('lease',seconds=300,**owner)
            denied=admin.call('eval',tab=owner['tab'],agent='intruder',task='isolation',script='window.compromised=true;return 1',_allow_error=True)
            value=admin.call('eval',script="return typeof window.compromised==='undefined'",**owner)
            valid=denied.get('ok') is False and denied.get('error',{}).get('code')==64 and value.get('value') is True
            result['ownership'].append({'actor':index,'denied':denied,'unmodified':value,'ok':valid})
            if not valid: raise Failure(f'ownership isolation failed for actor {index}')
        fairness_start=time.monotonic();last=[fairness_start]*concurrency;counts=[0]*concurrency;lock=threading.Lock()
        def progress(index):
            owner=actor(index)
            with Client(session) as client:
                for turn in range(3):
                    start=time.monotonic()
                    value=client.call('eval',script='await new Promise(r=>setTimeout(r,50));window.progress=(window.progress||0)+1;return window.progress',request=f'progress-{index}-{turn}',timeout=5,**owner)
                    end=time.monotonic()
                    if value.get('value')!=turn+1 or end-start>5 or end-fairness_start>10: raise Failure(f'fairness deadline/output failed actor {index}')
                    with lock:
                        counts[index]+=1
                        result['progress'].append({'actor':index,'round':turn,'started':start,'ended':end,'secondsSinceOwnProgress':end-last[index],
                            'counts':list(counts),'maxStarvationCounter':max(counts)-min(counts)})
                        last[index]=end
        futures=[pool.submit(progress,index) for index in range(concurrency)]
        while not all(future.done() for future in futures):
            metrics=admin.call('runtime')
            result['metrics'].append({'phase':'fairness','time':time.monotonic(),'runtime':metrics})
            time.sleep(.02)
        for future in futures: future.result()
        if counts!=[3]*concurrency: raise Failure('not every owner completed three progress rounds')
        result['fairness']={'counts':counts,'seconds':time.monotonic()-fairness_start,
            'maxSecondsWithoutOwnProgress':max(row['secondsSinceOwnProgress'] for row in result['progress']),
            'maxStarvationCounter':max(row['maxStarvationCounter'] for row in result['progress'])}
        drain_connections()
        for index in range(concurrency):
            owner=actor(index);wait_id=f'running-{index}';queued_id=f'queued-{index}'
            running=pool.submit(invoke,'wait',js='false',request=wait_id,timeout=10,_allow_error=True,**owner)
            pending(wait_id,'running')
            queued=pool.submit(invoke,'eval',script='window.cancelledMutation=true;return 1',request=queued_id,timeout=10,_allow_error=True,**owner)
            pending(queued_id,'queued')
            wrong=admin.call('cancel',id=wait_id,agent='intruder',task='isolation',_allow_error=True)
            queued_cancel=admin.call('cancel',id=queued_id,agent=owner['agent'],task=owner['task'])
            queued_reply=queued.result(timeout=12)
            cancel_started=time.monotonic()
            running_cancel=admin.call('cancel',id=wait_id,agent=owner['agent'],task=owner['task'])
            running_reply=running.result(timeout=12)
            cancel_elapsed=(time.monotonic()-cancel_started)*1000
            state=admin.call('eval',script="return {progress:window.progress,unmodified:typeof window.cancelledMutation==='undefined'}",**owner)
            valid=(wrong.get('ok') is False and wrong.get('error',{}).get('code')==64 and queued_reply.get('ok') is False
                   and queued_reply.get('error',{}).get('code')==2 and running_reply.get('ok') is False
                   and running_reply.get('error',{}).get('code')==2 and 'cancel' in running_reply.get('error',{}).get('message','').lower()
                   and queued_cancel.get('cancelled')==queued_id and running_cancel.get('cancelled')==wait_id
                   and running_cancel.get('wasRunning') is True and state.get('value')=={'progress':3,'unmodified':True})
            result['cancellation'].append({'actor':index,'wrongOwner':wrong,'queuedCancel':queued_cancel,'queuedReply':queued_reply,
                'runningCancel':running_cancel,'runningReply':running_reply,'runningCancelCompletionMs':cancel_elapsed,'after':state,'ok':valid})
            if not valid: raise Failure(f'cancellation failed actor {index}')
            drain_connections()
        result['finalTasks']=admin.call('tasks')
        final=admin.call('runtime');result['finalRuntime']=final
        if result['finalTasks']['pending'] or final['active'] or final['queued']: raise Failure('runtime did not drain after semantic phase')
        result['connectionAccounting']={'configuredWorkerLoad':concurrency,'fairnessBound':concurrency+1,'cancellationBound':3,
            'fairnessMaxObserved':max((row['runtime']['connections'] for row in result['metrics'] if row['phase']=='fairness'),default=0),
            'cancellationMaxObserved':max((row['runtime']['connections'] for row in result['metrics'] if row['phase']=='cancellation'),default=0),
            'productConnectionLimit':final['limits']['connections']}
        if result['connectionAccounting']['fairnessMaxObserved']>concurrency+1 or result['connectionAccounting']['cancellationMaxObserved']>3:
            raise Failure('semantic harness exceeded declared phase connection bound')
    except Exception as exc: result['errors'].append(str(exc))
    finally:
        pool.shutdown(wait=True,cancel_futures=True)
        if admin: admin.close()
        sampler.close();session.close();site.close()
        result.update({'ended':time.monotonic(),'finalBinarySha256':digest(binary),'budgetAbort':session.abort_reason,'commands':session.commands,
                       'memorySamples':sampler.samples,'cleanup':session.cleanup})
        result['ok']=result['finalBinarySha256']==result['binarySha256'] and not result['errors'] and not session.abort_reason and all(session.cleanup[k] for k in ('daemonGone','profileFilesGone'))
        write(out/'results.json',result)
    return result



class ObservationMCP:
    def __init__(self, session):
        self.log = (session.out/'mcp.log').open('wb')
        self.process = subprocess.Popen([session.binary,'mcp'],env=session.env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log)
        self.inbox = queue.Queue(); self.serial = 0
        def read():
            try:
                for line in self.process.stdout:
                    if len(line)>16*1024*1024: raise Failure('MCP observation frame exceeds16MiB')
                    self.inbox.put(json.loads(line))
            except Exception as exc: self.inbox.put(exc)
        self.reader = threading.Thread(target=read,daemon=True);self.reader.start()
        try:
            self.rpc('initialize',{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'runtime-observation-journey','version':'1'}})
            self.send({'jsonrpc':'2.0','method':'notifications/initialized'})
        except Exception:
            self.close();raise

    def send(self,value):
        self.process.stdin.write((json.dumps(value)+'\n').encode());self.process.stdin.flush()

    def rpc(self,method,params):
        self.serial += 1
        self.send({'jsonrpc':'2.0','id':self.serial,'method':method,'params':params})
        deadline=time.monotonic()+70
        while time.monotonic()<deadline:
            value=self.inbox.get(timeout=max(.01,deadline-time.monotonic()))
            if isinstance(value,Exception): raise value
            if value.get('id')!=self.serial:
                if 'id' not in value: continue
                raise Failure('MCP observation response ID mismatch')
            if 'error' in value or value.get('result',{}).get('isError'): raise Failure('MCP observation error: '+json.dumps(value))
            return value
        raise Failure('MCP observation response deadline')

    def observe(self,args): return self.rpc('tools/call',{'name':'observe','arguments':args})

    def close(self):
        try:
            if self.process.stdin: self.process.stdin.close()
        except OSError: pass
        try: self.process.wait(timeout=10)
        except subprocess.TimeoutExpired: self.process.kill();self.process.wait(timeout=5)
        self.reader.join(timeout=2);self.log.close()


def observation_journey(binary, sdk_path, out, budget_bytes):
    import importlib.util
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    site=Site(json.loads(WORKLOAD.read_text()))
    try: session=Session(binary,out/'browser')
    except Exception:
        site.close();raise
    sampler=Sampler(session,.25,budget_bytes);mcp=None;sdk_client=None
    result={'protocolSha256':digest(OBSERVATION),'binarySha256':digest(binary),'sdkSha256':digest(sdk_path),
            'expectedStates':16,'expectedNavigationResets':3,'states':[],'errors':[]}
    previous={};transports=['python-sdk-raw-result','cli-json-without-human-text','mcp-tool-result']
    def text_of(value): return value.get('text',value.get('snapshot',''))
    def normal_mcp(wire):
        value=wire['result'];normalized=dict(value.get('structuredContent',{}))
        normalized['text']='\n'.join(c.get('text','') for c in value.get('content',[]) if c.get('type')=='text')
        return normalized
    def count(value):
        raw=json.dumps(value,sort_keys=True,separators=(',',':'))
        return {'tokens':len(TOKENIZER.encode(raw)),'canonicalBytes':len(raw.encode())}
    def capture(label,reset=False,outputs=None):
        row={'state':len(result['states']),'label':label,'navigationReset':reset,'outputs':outputs,'transports':{}}
        result['states'].append(row)
        for transport in transports:
            args={'tab':'journey'};old=previous.get(transport);wire={}
            if transport=='python-sdk-raw-result':
                full=sdk_client.call('observe',args)
                delta=sdk_client.call('observe',{**args,'since':old['baseline']}) if old else full
                full_payload,delta_payload=full,delta
            elif transport=='cli-json-without-human-text':
                full=session.cli('observe',**args);full_stdout=session.last_cli_stdout
                delta=session.cli('observe',**args,since=old['baseline']) if old else full
                wire={'fullStdout':full_stdout,'deltaStdout':session.last_cli_stdout if old else full_stdout}
                full_payload,delta_payload=full,delta
                if 'text' in full or 'text' in delta: raise Failure('CLI --json unexpectedly retained human text')
            else:
                full_wire=mcp.observe(args)
                delta_wire=mcp.observe({**args,'since':old['baseline']}) if old else full_wire
                wire={'fullResponse':full_wire,'deltaResponse':delta_wire}
                full_payload,delta_payload=full_wire['result'],delta_wire['result']
                full,delta=normal_mcp(full_wire),normal_mcp(delta_wire)
            full_text=text_of(full)
            if old and delta.get('mode')=='delta':
                lines=old['text'].split('\n');patch=delta['patch']
                lines[patch['start']:patch['start']+patch['delete']]=patch['lines']
                reconstructed='\n'.join(lines)
            else: reconstructed=text_of(delta)
            fields=['refs','url','title','truncated','depthLimited','doc']
            metadata=dict(old['metadata']) if old and delta.get('mode')=='delta' else {}
            metadata.update({k:delta[k] for k in fields if k in delta})
            matches=reconstructed==full_text and all(k in full and k in metadata and metadata[k]==full[k] for k in fields)
            ref_ids=re.findall(r'\[ref=([^] ]+)\]',reconstructed)
            matches=matches and ref_ids==re.findall(r'\[ref=([^] ]+)\]',full_text)
            if reset and (delta.get('mode')!='full' or not delta.get('reset')): matches=False
            row['transports'][transport]={'wire':wire,'full':full_payload,'delta':delta_payload,'fullCounts':count(full_payload),
                'deltaCounts':count(delta_payload),'fullTextTokens':len(TOKENIZER.encode(full_text)),
                'deltaTextTokens':len(TOKENIZER.encode(text_of(delta))),'reconstructionMatches':matches,
                'reconstructedText':reconstructed,'reconstructedMetadata':metadata,'reconstructedRefIDs':ref_ids,'mode':delta.get('mode'),'reset':delta.get('reset'),
                'changed':delta.get('changed')}
            if not matches: raise Failure(f'observation reconstruction/reset mismatch {label} {transport}')
            previous[transport]={'baseline':delta['baseline'],'text':reconstructed,'metadata':metadata}
        write(out/'partial.json',result)
    try:
        if TOKENIZER is None: raise Failure('observation journey requires pinned actual tokenizer')
        spec=importlib.util.spec_from_file_location('umbra_runtime_observation_sdk',sdk_path)
        sdk=importlib.util.module_from_spec(spec);spec.loader.exec_module(sdk)
        sdk_client=sdk.Umbra(session.profile,binary=session.binary,socket_path=session.path)
        mcp=ObservationMCP(session)
        with Client(session) as client:
            opened=client.call('open',tab='journey',url=site.base+'/app',until='window.jsTotal===210')
            capture('initial',outputs=opened)
            for turn in range(3):
                token=f'observation-journey-{turn}'
                filled=client.call('fill',tab='journey',target='#entry',value=token)
                field=client.call('eval',tab='journey',script="return document.querySelector('#entry').value")
                if field.get('value')!=token: raise Failure('journey fill mismatch')
                capture(f'{turn}-native-fill',outputs={'action':filled,'field':field})
                clicked=client.call('click',tab='journey',target='#submit')
                waited=client.call('wait',tab='journey',js='document.querySelector("#result").dataset.done === '+json.dumps(token))
                state=client.call('eval',tab='journey',script="return {value:document.querySelector('#entry').value,output:document.querySelector('#result').textContent,trusted:window.events.length>0&&window.events.every(Boolean)&&window.clickTrusted}")
                if state.get('value')!={'value':token,'output':token,'trusted':True}: raise Failure('journey native semantic mismatch')
                with site.lock:
                    if site.receipts.get(token)!={'trusted':True,'count':1}: raise Failure('journey server receipt mismatch')
                capture(f'{turn}-native-submit',outputs={'action':clicked,'wait':waited,'state':state,'serverReceipt':dict(site.receipts[token])})
                reads={kind:task(client,site,kind,'journey',f'journey-read-{turn}-{kind}','public_http') for kind in ('static_html','json_api','javascript_dom')}
                capture(f'{turn}-read-bundle',outputs=reads)
                cleared=client.call('fill',tab='journey',target='#entry',value='')
                field=client.call('eval',tab='journey',script="return document.querySelector('#entry').value")
                if field.get('value')!='': raise Failure('journey clear mismatch')
                capture(f'{turn}-native-clear',outputs={'action':cleared,'field':field})
                opened=client.call('open',tab='journey',url=site.base+f'/app?journey-reset={turn}',until='window.jsTotal===210')
                capture(f'{turn}-navigation-reset',True,outputs=opened)
    except Exception as exc: result['errors'].append(str(exc))
    finally:
        if sdk_client: sdk_client.close()
        if mcp: mcp.close()
        sampler.close();session.close();site.close()
        result.update({'memorySamples':sampler.samples,'commands':session.commands,'budgetAbort':session.abort_reason,'cleanup':session.cleanup,
            'finalBinarySha256':digest(binary),'finalSDKSha256':digest(sdk_path),
            'tokenizer':{'version':TOKENIZER_VERSION,'encoding':TOKENIZER.name if TOKENIZER else None,
                'scope':'Canonical compact JSON consumer result payloads; observation responses only; fixed encoding is not GPT-6 billing equivalence.'}})
        complete=(len(result['states'])==16 and sum(s['navigationReset'] for s in result['states'])==3 and not result['errors']
                  and result['binarySha256']==result['finalBinarySha256'] and result['sdkSha256']==result['finalSDKSha256']
                  and not session.abort_reason and all(session.cleanup[k] for k in ('daemonGone','profileFilesGone')))
        summaries={}
        for transport in transports:
            rows=[s['transports'][transport] for s in result['states'] if transport in s['transports']]
            full=sum(r['fullCounts']['tokens'] for r in rows);delta=sum(r['deltaCounts']['tokens'] for r in rows)
            valid=complete and len(rows)==16 and all(r['reconstructionMatches'] for r in rows)
            summaries[transport]={'states':len(rows),'expectedStates':16,'fullTokens':full,'deltaTokens':delta,
                'fullCanonicalBytes':sum(r['fullCounts']['canonicalBytes'] for r in rows),'deltaCanonicalBytes':sum(r['deltaCounts']['canonicalBytes'] for r in rows),
                'tokenReduction':1-delta/full if full else None,'completeValidJourney':valid,
                'fiftyPercentGate':valid and delta<=.5*full,'fullFallbacks':sum(r['mode']=='full' for r in rows),
                'changedDeltas':sum(r['changed'] is True for r in rows),'unchangedDeltas':sum(r['changed'] is False for r in rows)}
        result['summary']=summaries;result['ok']=complete and all(r['completeValidJourney'] for r in summaries.values())
        write(out/'results.json',result)
    return result


def summarize(cells, budget_mib, expected=None):
    comparisons = []
    for candidate in [c for c in cells if c['label'] == 'candidate']:
        baseline = next((b for b in cells if b['label']=='baseline' and all(b[k]==candidate[k] for k in ('route','concurrency','repeat'))), None)
        if baseline is None: continue
        b, c = baseline['summary'], candidate['summary']
        bm, cm = b['peakActivePhysicalFootprintBytes'], c['peakActivePhysicalFootprintBytes']
        valid = (b['failures']==c['failures']==b['warmupFailures']==c['warmupFailures']==0
                 and bm is not None and cm is not None and not baseline.get('budgetAbort') and not candidate.get('budgetAbort')
                 and not baseline.get('cellError') and not candidate.get('cellError')
                 and b.get('loadedControlFailures',1)==c.get('loadedControlFailures',1)==0
                 and b.get('idleControlFailures',1)==c.get('idleControlFailures',1)==0)
        latency = {}
        for kind in b.get('taskTypes',{}):
            bp, cp = b['taskTypes'][kind]['latencyMs']['p95'], c.get('taskTypes',{}).get(kind,{}).get('latencyMs',{}).get('p95')
            latency[kind] = {'baselineP95Ms':bp,'candidateP95Ms':cp,'ratio':cp/bp if bp and cp is not None else None}
        latency_gate = valid and bool(latency) and all(row['ratio'] is not None and row['ratio']<=1.10 for row in latency.values())
        control_latency = {}
        for transport in ('socket','cli'):
            bp, cp = b.get('controls',{}).get(transport,{}).get('p95'), c.get('controls',{}).get(transport,{}).get('p95')
            control_latency[transport] = {'baselineP95Ms':bp,'candidateP95Ms':cp,'ratio':cp/bp if bp and cp is not None else None}
        control_gate = valid and all(row['ratio'] is not None and row['ratio']<=1.10 for row in control_latency.values())
        bl, cl = b.get('loadedControlLatencyMs',{}).get('p95'), c.get('loadedControlLatencyMs',{}).get('p95')
        loaded_ratio = cl/bl if bl and cl is not None else None
        ratio = c['successfulTasksPerSecond']/b['successfulTasksPerSecond'] if b['successfulTasksPerSecond'] else None
        comparisons.append({'route': candidate['route'], 'concurrency': candidate['concurrency'], 'repeat': candidate['repeat'],
                            'validCompletePair': valid, 'throughputRatio': ratio,
                            'taskP95Latency':latency,'noTaskP95RegressionOver10PercentGate':latency_gate,
                            'loadedControlP95Latency':{'baselineP95Ms':bl,'candidateP95Ms':cl,'ratio':loaded_ratio},
                            'noLoadedControlP95RegressionOver10PercentGate':valid and loaded_ratio is not None and loaded_ratio<=1.10,
                            'controlP95Latency':control_latency,'noControlP95RegressionOver10PercentGate':control_gate,
                            'noP95RegressionOver10PercentGate':latency_gate and loaded_ratio is not None and loaded_ratio<=1.10,
                            'peakPhysicalFootprintReduction': 1-cm/bm if bm and cm else None,
                            'sameBudgetBytes': budget_mib*2**20, 'bothWithinBudget': bool(bm and cm and max(bm,cm)<=budget_mib*2**20),
                            'twofoldGate': valid and ratio is not None and ratio>=2 and max(bm,cm)<=budget_mib*2**20,
                            'memory30PercentGate': valid and cm<=.7*bm})
    groups=[]; routes=[]
    if expected:
        expected_routes, levels, repeats = expected
        for route in expected_routes:
            route_pairs=[row for row in comparisons if row['route']==route]
            for level in levels:
                group=[row for row in route_pairs if row['concurrency']==level]
                groups.append({'route':route,'concurrency':level,'expectedRepeats':repeats,'observedRepeats':len(group),
                    'eligibleRepeats':sum(row['validCompletePair'] for row in group),
                    'throughputRatioSpread':distribution([row['throughputRatio'] for row in group if row['throughputRatio'] is not None]),
                    'peakFootprintReductionSpread':distribution([row['peakPhysicalFootprintReduction'] for row in group if row['peakPhysicalFootprintReduction'] is not None])})
            complete=len(route_pairs)==len(levels)*repeats and all(row['validCompletePair'] for row in route_pairs)
            routes.append({'route':route,'expectedPairs':len(levels)*repeats,'observedPairs':len(route_pairs),
                'eligiblePairs':sum(row['validCompletePair'] for row in route_pairs),'fullSelectedGridEligible':complete,
                'twofoldEverySelectedCellGate':complete and all(row['twofoldGate'] for row in route_pairs),
                'memory30PercentEverySelectedCellGate':complete and all(row['memory30PercentGate'] for row in route_pairs),
                'noP95RegressionEverySelectedCellGate':complete and all(row['noP95RegressionOver10PercentGate'] for row in route_pairs)})
    return {'pairs':comparisons,'repeatGroups':groups,'routeGates':routes,
        'note':'Every selected cell/repeat must be eligible for a route-wide gate. Repeat spreads retain all recorded ratios and are descriptive when any arm failed; no isolated passing cell establishes the whole workload target. CPU counters undercount processes that appear and exit entirely between samples.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    global TOKENIZER, TOKENIZER_VERSION
    parser.add_argument('--baseline', required=True); parser.add_argument('--candidate'); parser.add_argument('--candidate-sha256')
    parser.add_argument('--out', required=True); parser.add_argument('--workload', default=str(WORKLOAD))
    parser.add_argument('--concurrency', default='1,4,8,16,32'); parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--cycles', type=int, default=20); parser.add_argument('--controls', type=int, default=20)
    parser.add_argument('--sdk',default=str(ROOT/'sdk/python/umbra.py'),help='Actual candidate Python SDK source for observation journey')
    parser.add_argument('--runtime-only',action='store_true',help='Frozen candidate semantic isolation/cancellation/fairness grid only')
    parser.add_argument('--admission',action='store_true',help='Secondary frozen common-admission experiment; primary remains unchanged')
    parser.add_argument('--routes', default='matched,public_http'); parser.add_argument('--memory-budget-mib', type=float, default=2048)
    args = parser.parse_args()
    if args.candidate:
        try:
            import tiktoken
            import importlib.metadata
            cache = tempfile.mkdtemp(prefix='umbra-runtime-tokenizer-')
            os.environ['TIKTOKEN_CACHE_DIR'] = cache
            atexit.register(shutil.rmtree,cache,True)
            TOKENIZER = tiktoken.get_encoding('o200k_base')
            TOKENIZER_VERSION = importlib.metadata.version('tiktoken')
            if TOKENIZER_VERSION != '0.12.0': parser.error('candidate measurement requires tiktoken==0.12.0')
        except ImportError:
            parser.error('candidate measurement requires pinned tiktoken==0.12.0 in a disposable environment')
    if args.candidate and (not args.candidate_sha256 or digest(args.candidate)!=args.candidate_sha256): parser.error('candidate requires matching --candidate-sha256 supplied after freeze')
    if not math.isfinite(args.memory_budget_mib) or not 1<=args.memory_budget_mib<=2048: parser.error('memory budget must be 1–2048 MiB on this 16 GiB host')
    if min(args.repeats,args.cycles,args.controls)<1: parser.error('counts must be positive')
    routes_requested=args.routes.split(',')
    if not routes_requested or len(set(routes_requested))!=len(routes_requested) or any(n not in ('matched','public_http') for n in routes_requested): parser.error('routes must be unique frozen matched/public_http values')
    levels = [int(n) for n in args.concurrency.split(',')]
    if len(set(levels))!=len(levels) or any(n not in (1,4,8,16,32) for n in levels): parser.error('concurrency must be from frozen 1/4/8/16/32 grid')
    if args.candidate and not Path(args.sdk).is_file(): parser.error('candidate observation journey requires actual --sdk source path')
    if args.runtime_only and not args.candidate: parser.error('--runtime-only requires frozen candidate')
    if args.admission and args.memory_budget_mib!=2048: parser.error('secondary frozen admission requires exactly 2048 MiB hard budget')
    output = Path(args.out); output.mkdir(parents=True, exist_ok=False)
    specification = json.loads(Path(args.workload).read_text())
    write(output/'manifest.json', {'created': time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()), 'workloadSha256':digest(args.workload),
          'harnessSha256':digest(__file__), 'memoryHelperSha256':digest(Path(__file__).with_name('memory.py')),
          'baselineSha256':digest(args.baseline), 'candidateSha256':digest(args.candidate) if args.candidate else None,
          'python':sys.version, 'platform':platform.platform(),'hostPhysicalMemoryBytes':int(subprocess.check_output(['sysctl','-n','hw.memsize'],text=True)),'hostCPUCount':os.cpu_count(),'fixtureListenBacklog':128, 'tokenizer': {'encoding':TOKENIZER.name,'tiktokenVersion':TOKENIZER_VERSION} if TOKENIZER else None, 'options':vars(args), 'loadAtStart':os.getloadavg(),
          'observationProtocolSha256':digest(OBSERVATION),'sdkSha256':digest(args.sdk) if args.candidate else None,
          'loadedControlProtocolSha256':digest(CONTROL),'admissionProtocolSha256':digest(ADMISSION) if args.admission else None,
          'classification':'secondary-common-admission' if args.admission else 'primary',
          'fullFrozenPerformanceConfiguration':not args.runtime_only and levels==specification['concurrency'] and args.repeats==specification['repeats'] and args.cycles==specification['cycles'] and args.controls==specification['controls'] and routes_requested==['matched','public_http'],
          'budgetPolicy':'Sampler aborts the browser at first sample above the declared budget, stops dispatching further commands, retains all task failures and raw footprint overshoot. Sampling can overshoot by one interval; no failed cell establishes a ratio.',
          'method':'Sequential cells; AB then BA order alternates by repeat. Warmups excluded. Persistent socket workload, matched socket/CLI controls. Responsibility-attributed whole-family physical footprint. No model/agent score.'})
    cells = []
    routes = routes_requested if args.candidate else ['matched']
    for route in ([] if args.runtime_only else routes):
        for level in levels:
            for repeat in range(args.repeats):
                arms = [('baseline',args.baseline)] + ([('candidate',args.candidate)] if args.candidate else [])
                if repeat%2: arms.reverse()
                for label,binary in arms:
                    if label=='candidate' and digest(binary)!=args.candidate_sha256: raise Failure('candidate changed during benchmark')
                    name=f'{route}-{level}-{repeat}-{label}'
                    print(name, flush=True)
                    write(output/'live.json',{'runnerPid':os.getpid(),'cell':name,'terminal':False,'completedCells':len(cells),'heartbeatUTC':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())})
                    try:
                        value=cell(binary,label,label=='candidate',route,level,repeat,specification,output/name,args.cycles,args.controls,args.memory_budget_mib*2**20,args.admission)
                    except Exception as exc:
                        planned=level*args.cycles*len(specification['task_order'])
                        partial_path=output/name/'partial.json'
                        partial=json.loads(partial_path.read_text()) if partial_path.exists() else {}
                        completed=[r for r in partial.get('tasks',[]) if not r['warmup']]
                        successes=sum(r['ok'] for r in completed)
                        value={'label':label,'route':route,'concurrency':level,'repeat':repeat,'sha256':digest(binary),'cellError':str(exc),
                            'budgetAbort':partial.get('budgetAbort'),'completedTasks':len(completed),
                            'summary':{'tasks':planned,'successes':successes,'failures':planned-successes,'warmupFailures':level*len(specification['task_order']),
                                'peakActivePhysicalFootprintBytes':None,'successfulTasksPerSecond':0},
                            'note':'Uncompleted cell remains fully in denominator. No performance ratio may be claimed for this cell.'}
                        (output/name).mkdir(parents=True,exist_ok=True)
                        write(output/name/'cell-error.json',value)
                    cells.append(value)
                    write(output/'summary.json',summarize(cells,args.memory_budget_mib,(routes,levels,args.repeats)))
    runtime_results=[]
    if args.candidate and not args.admission:
        for level in levels:
            if digest(args.candidate)!=args.candidate_sha256: raise Failure('candidate changed before runtime phase')
            print(f'runtime-semantics-{level}',flush=True)
            runtime_results.append(runtime_phase(args.candidate,level,output/f'runtime-semantics-{level}',args.memory_budget_mib*2**20))
            write(output/'runtime-grid.json',runtime_results)
    journey=None
    if args.candidate and not args.admission and not args.runtime_only:
        print('observation-journey',flush=True)
        journey=observation_journey(args.candidate,args.sdk,output/'observation-journey',args.memory_budget_mib*2**20)
    final={'performanceRouteGates':summarize(cells,args.memory_budget_mib,(routes,levels,args.repeats))['routeGates'],
        'observationToken50PercentGates':{k:v['fiftyPercentGate'] for k,v in journey['summary'].items()} if journey else None,
        'observationJourneyValid':journey['ok'] if journey else None,
        'budgetAbortedCells':sum(bool(c.get('budgetAbort')) for c in cells),'cellErrors':sum(bool(c.get('cellError')) for c in cells),'controlFailures':sum(c['summary'].get('idleControlFailures',0)+c['summary'].get('loadedControlFailures',0) for c in cells),'runtimeGridLevels':len(runtime_results),'runtimeGridFailures':sum(not r['ok'] for r in runtime_results),'cells':len(cells),'failures':sum(c['summary']['failures'] for c in cells),'out':str(output),'terminal':True,'runnerPid':os.getpid()}
    write(output/'live.json',final)
    print(json.dumps(final,sort_keys=True))
    return 0 if (journey is None or journey['ok']) and final['failures']==0 and final['controlFailures']==0 and final['runtimeGridFailures']==0 and all(not c.get('budgetAbort') and not c.get('cellError') for c in cells) else 1


if __name__=='__main__': raise SystemExit(main())
