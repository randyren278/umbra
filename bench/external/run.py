#!/usr/bin/env python3
"""Run pinned, upstream BrowserGym MiniWoB tasks with Umbra / Playwright MCP.

Requires browsergym-miniwob, numpy and playwright in a disposable Python env.
No installed Umbra, owner profile, public-site browsing or custom reward logic.
"""
import argparse
import base64
import csv
import hashlib
import http.server
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import random
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor

REPO = Path(__file__).resolve().parents[2]
BINARY = REPO / 'build/Umbra.app/Contents/MacOS/umbra'
UMBRA_OBSERVE = {'snapshot', 'text', 'markdown', 'screenshot', 'info'}
UMBRA_ACT = {'click', 'fill', 'type', 'press', 'select', 'check', 'uncheck',
             'hover', 'scroll', 'drag', 'focus'}
PW_OBSERVE = {'browser_snapshot', 'browser_take_screenshot'}
PW_ACT = {'browser_click', 'browser_type', 'browser_press_key', 'browser_select_option',
          'browser_hover', 'browser_drag', 'browser_fill_form', 'browser_handle_dialog'}


def write(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n')
    tmp.replace(path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git(path):
    return subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip()


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def load_tasks(bgym):
    sys.path.insert(0, str(Path(bgym) / 'browsergym/miniwob/src'))
    module = importlib.import_module('browsergym.miniwob')
    return {c.get_task_id(): c for c in module.ALL_MINIWOB_TASKS}


def cleanup(profile, env):
    subprocess.run([str(BINARY), 'stop'], env=env, capture_output=True, timeout=20)
    h = hashlib.sha256(('umbra profile ' + profile).encode()).hexdigest()[:32]
    uuid = '-'.join((h[:8], h[8:12], h[12:16], h[16:20], h[20:]))
    store = Path.home() / 'Library/WebKit/com.randyren.umbra/WebsiteDataStore' / uuid
    files = Path.home() / 'Library/Application Support/Umbra'
    downloads = Path.home() / 'Downloads/Umbra' / profile
    for _ in range(3):
        shutil.rmtree(store, ignore_errors=True)
        shutil.rmtree(downloads, ignore_errors=True)
        for path in files.glob(f'*-{profile}.*'):
            path.unlink(missing_ok=True)
        time.sleep(.3)
    return not store.exists() and not downloads.exists() and not list(files.glob(f'*-{profile}.*'))


class UmbraPage:
    """The small Page interface used by the unmodified upstream task classes."""
    def __init__(self, env):
        self.env = env
        self.stream = None
        self.sock = None
        self.serial = 0

    def call(self, command, **args):
        if self.stream is None:
            self.sock = socket.socket(socket.AF_UNIX)
            self.sock.settimeout(65)
            self.sock.connect(str(Path.home() / 'Library/Application Support/Umbra' /
                                  f'daemon-{self.env["UMBRA_PROFILE"]}.sock'))
            self.stream = self.sock.makefile('rwb')
        self.serial += 1
        self.stream.write(json.dumps({'id': self.serial, 'command': command, 'args': args}).encode() + b'\n')
        self.stream.flush()
        reply = json.loads(self.stream.readline())
        if not reply['ok']:
            raise RuntimeError(reply['error'])
        return reply['result']

    def goto(self, url):
        if not url.startswith('http://127.0.0.1:'):
            raise ValueError('only loopback task pages are allowed')
        if self.stream is None:
            subprocess.run([str(BINARY), 'open', url], env=self.env,
                           capture_output=True, timeout=60, check=True)
        else:
            self.call('open', url=url)

    def evaluate(self, script):
        # Playwright treats arrow expressions as callable; Umbra takes a body.
        body = f'return ({script})();' if script.lstrip().startswith('() =>') else script
        return self.call('eval', script=body).get('value')

    def wait_for_function(self, script):
        self.call('wait', js=f'({script})()', timeout=5)

    @property
    def url(self):
        return self.call('info')['url']

    def close(self):
        if self.stream:
            self.stream.close()
        if self.sock:
            self.sock.close()


class MCP:
    def __init__(self, args, env, work):
        self.log = open(work / 'server.log', 'w')
        self.proc = subprocess.Popen(args, env=env, cwd=work, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=self.log, text=True,
                                     start_new_session=True)
        self.serial = 0
        self.request('initialize', {'protocolVersion': '2025-03-26', 'capabilities': {},
                                   'clientInfo': {'name': 'upstream-eval', 'version': '1'}})
        self.proc.stdin.write(json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) + '\n')
        self.proc.stdin.flush()

    def request(self, method, params=None):
        self.serial += 1
        self.proc.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': self.serial,
                                         'method': method, 'params': params or {}}) + '\n')
        self.proc.stdin.flush()
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError('upstream MCP exited')
            reply = json.loads(line)
            if reply.get('id') == self.serial:
                if 'error' in reply:
                    raise RuntimeError(reply['error'])
                return reply['result']

    def close(self):
        self.proc.stdin.close()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(self.proc.pid, signal.SIGTERM)
            self.proc.wait(timeout=5)
        self.log.close()


def tool_error(message):
    return {'content': [{'type': 'text', 'text': message}], 'isError': True}


def broker(case_file):
    """Only this controller sees upstream internals; the candidate gets filtered MCP."""
    case = json.loads(Path(case_file).read_text())
    work = Path(case_file).parent
    env = {**os.environ, 'UMBRA_PROFILE': case['profile'], 'UMBRA_IDLE': '60',
           'UMBRA_SITES': str(work / 'no-sites.json')}
    task_cls = load_tasks(case['browsergym'])[case['task']]
    task = task_cls(seed=case['seed'], base_url=case['base'])
    page = mcp = pw = context = None
    records = []
    started = time.monotonic()
    actions = 0
    observations = 0
    state = None
    try:
        if case['arm'] == 'umbra':
            page = UmbraPage(env)
            page.goto(case['base'] + case['task'].split('.', 1)[1] + '.html')
            page.call('viewport', **task.viewport)
            goal, _ = task.setup(page)
            mcp = MCP([str(BINARY), 'mcp'], env, work)
            allowed, mutations = UMBRA_OBSERVE | UMBRA_ACT, UMBRA_ACT
        else:
            from playwright.sync_api import sync_playwright
            pw = sync_playwright().start()
            port = free_port()
            context = pw.chromium.launch_persistent_context(
                str(work / 'chrome'), headless=True, executable_path=case['chromium'],
                viewport=task.viewport, args=[f'--remote-debugging-port={port}'],
                slow_mo=task.slow_mo)
            context.route('**/*', lambda route: route.continue_() if
                          route.request.url.startswith('http://127.0.0.1:') else route.abort())
            page = context.pages[0]
            goal, _ = task.setup(page)
            mcp = MCP(['node', case['playwright_mcp'], '--cdp-endpoint', f'http://127.0.0.1:{port}',
                       '--output-dir', str(work), '--no-webmcp'], env, work)
            allowed, mutations = PW_OBSERVE | PW_ACT, PW_ACT
        tools = [t for t in mcp.request('tools/list')['tools'] if t['name'] in allowed]
        tools.append({'name': 'task', 'description': 'Read the instruction for this browser task.',
                      'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}})
        write(work / 'ready.json', {'goal': goal, 'tools': [t['name'] for t in tools],
                                   'url': page.url, 'setup_seconds': time.monotonic() - started})
        started = time.monotonic()
        for line in sys.stdin:
            message = json.loads(line)
            if 'id' not in message:
                continue
            method = message['method']
            result = {}
            if method == 'initialize':
                result = {'protocolVersion': message['params']['protocolVersion'],
                          'capabilities': {'tools': {}},
                          'serverInfo': {'name': 'browser', 'version': '1'}}
            elif method == 'tools/list':
                result = {'tools': tools}
            elif method == 'tools/call':
                params = message['params']
                name = params['name']
                args = params.get('arguments', {})
                before = time.monotonic()
                reward, done, _, info = task.validate(page, [])
                if name == 'task':
                    result = {'content': [{'type': 'text', 'text': goal}]}
                elif done:
                    result = tool_error('The episode has ended. Give your final response.')
                elif name not in allowed:
                    result = tool_error('Tool unavailable in this evaluation.')
                elif actions >= case['max_actions'] or observations >= 40:
                    result = tool_error('The episode action/observation budget is exhausted. Give your final response.')
                elif any(k in args for k in ('js', 'script', 'path', 'file', 'filename')):
                    result = tool_error('Scripts and file access are unavailable. Screenshots return images directly.')
                else:
                    if name in mutations:
                        # fill_form batches several actions; count each field as an action.
                        count = len(args.get('fields', [])) if name == 'browser_fill_form' else 1
                        if actions + count > case['max_actions']:
                            result = tool_error('This call exceeds the remaining action budget.')
                        else:
                            actions += count
                            result = mcp.request('tools/call', params)
                    else:
                        observations += 1
                        result = mcp.request('tools/call', params)
                    reward, done, _, info = task.validate(page, [])
                    if done:
                        result.setdefault('content', []).append({'type': 'text', 'text': 'The episode has ended. Give your final response.'})
                # Image bytes remain in the candidate transcript; controller records a hash.
                saved = json.loads(json.dumps(result))
                for item in saved.get('content', []):
                    if item.get('type') == 'image' and 'data' in item:
                        data = base64.b64decode(item.pop('data'))
                        item['sha256'] = hashlib.sha256(data).hexdigest()
                records.append({'tool': name, 'arguments': args, 'seconds': time.monotonic() - before,
                                'result': saved, 'reward': reward, 'done': done, 'info': info})
                state = {'reward': reward, 'done': done, 'info': info,
                         'strict_success': bool(done and info.get('RAW_REWARD_GLOBAL') == 1.0),
                         'browsergym_success': bool(done and reward > 0),
                         'actions': actions, 'observations': observations,
                         'seconds': time.monotonic() - started}
                write(work / 'score.json', state)
                with open(work / 'calls.jsonl', 'a') as out:
                    out.write(json.dumps(records[-1]) + '\n')
            elif method == 'ping':
                pass
            else:
                print(json.dumps({'jsonrpc': '2.0', 'id': message['id'],
                                  'error': {'code': -32601, 'message': 'method not available'}}), flush=True)
                continue
            print(json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': result}), flush=True)
    except Exception as error:
        write(work / 'broker-error.json', {'error': repr(error)})
        raise
    finally:
        if page:
            try:
                reward, done, _, info = task.validate(page, [])
                state = {'reward': reward, 'done': done, 'info': info,
                         'strict_success': bool(done and info.get('RAW_REWARD_GLOBAL') == 1.0),
                         'browsergym_success': bool(done and reward > 0),
                         'actions': actions, 'observations': observations,
                         'seconds': time.monotonic() - started}
                write(work / 'score.json', state)
            except Exception:
                pass
        if mcp:
            mcp.close()
        if case['arm'] == 'umbra':
            if page:
                page.close()
            write(work / 'cleanup.json', {'clean': cleanup(case['profile'], env)})
        else:
            if context:
                context.close()
            if pw:
                pw.stop()
            shutil.rmtree(work / 'chrome', ignore_errors=True)
            write(work / 'cleanup.json', {'clean': not (work / 'chrome').exists()})


def freeze(options):
    import numpy as np
    root = Path(options.out).resolve()
    root.mkdir(parents=True, exist_ok=False)
    metadata = Path(options.browsergym) / 'browsergym/experiments/src/browsergym/experiments/benchmark/metadata/miniwob.csv'
    tasks = list(csv.DictReader(metadata.open()))
    # Preserve the upstream standard five-seed plan, even for reduced-repetition runs.
    # SEED_MAX is literally `2 ^ 32` (XOR, 34) in the pinned BrowserGym source.
    rng = np.random.RandomState(42)
    cases = []
    for task in tasks:
        seeds = rng.randint(0, 2 ^ 32, size=5)
        if options.tasks and task['task_name'] not in options.tasks.split(','):
            continue
        for repeat, seed in enumerate(seeds[:options.repeats]):
            pair = ['umbra', 'playwright']
            random.Random(f'{task["task_name"]}:{repeat}:42').shuffle(pair)
            for arm in pair:
                cases.append({'task': task['task_name'], 'seed': int(seed), 'repeat': repeat,
                              'arm': arm, 'split': task['browsergym_split']})
    source_files = ['browsergym/miniwob/src/browsergym/miniwob/base.py',
                    'browsergym/miniwob/src/browsergym/miniwob/all.py',
                    'browsergym/experiments/src/browsergym/experiments/benchmark/configs.py',
                    'browsergym/experiments/src/browsergym/experiments/benchmark/utils.py',
                    'browsergym/experiments/src/browsergym/experiments/loop.py']
    manifest = {'umbra_revision': git(REPO), 'binary_sha256': digest(BINARY),
                'harness_sha256': digest(__file__), 'miniwob_revision': git(options.miniwob),
                'browsergym_revision': git(options.browsergym),
                'source_hashes': {f: digest(Path(options.browsergym) / f) for f in source_files},
                'metadata_sha256': digest(metadata), 'max_actions': 10,
                'seconds_per_episode': options.timeout, 'repeats': options.repeats,
                'standard_repeats': 5, 'cases': cases,
                'model': tomllib.loads((Path.home() / '.codex/config.toml').read_text())['model'],
                'reasoning_effort': tomllib.loads((Path.home() / '.codex/config.toml').read_text()).get('model_reasoning_effort', 'medium'),
                'agent': subprocess.check_output(['codex', '--version'], text=True).strip(),
                'python': sys.version,
                'dependencies': {p: importlib.metadata.version(p) for p in
                                 ['browsergym-core', 'browsergym-miniwob', 'numpy', 'playwright']},
                'playwright_mcp_version': json.loads((Path(options.playwright_mcp).parent / 'package.json').read_text())['version'],
                'chromium_sha256': digest(options.chromium),
                'mcp_source_sha256': digest(options.playwright_mcp),
                'system': subprocess.check_output(['sw_vers'], text=True).strip(),
                'limitations': ['Adapted MCP action surface, not BrowserGym HighLevelActionSet',
                                'No arbitrary JavaScript or source/file access in either arm',
                                'No claim of published-leaderboard comparability',
                                'Timing includes model inference; tool latency logged separately',
                                'Four concurrent episodes: timing is under shared machine load',
                                'BrowserGym screenshot scale annotation is not applied by these MCP servers',
                                'Upstream WebKit rendering can differ from recommended Chromium']}
    write(root / 'manifest.json', manifest)
    write(root / 'config.json', vars(options))
    print(f'Frozen {len(cases)} episodes: {len(set(c["task"] for c in cases))} task types x {options.repeats} seeds x 2 arms', flush=True)


def run(options):
    root = Path(options.out).resolve()
    config = json.loads((root / 'config.json').read_text())
    manifest = json.loads((root / 'manifest.json').read_text())
    if digest(BINARY) != manifest['binary_sha256'] or digest(__file__) != manifest['harness_sha256']:
        raise RuntimeError('binary or harness changed since freeze')
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(Path(config['miniwob']) / 'miniwob/html'), **kwargs)
        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Quiet)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}/miniwob/'
    lock = threading.Lock()
    def one(case):
        name = f'{case["task"]}-{case["repeat"]}-{case["arm"]}'
        work = root / name
        if (work / 'run.json').exists():
            return
        work.mkdir(exist_ok=True)
        full = {**case, **{k: config[k] for k in ('browsergym', 'playwright_mcp', 'chromium')},
                'profile': 'external-' + secrets.token_hex(6), 'base': base, 'max_actions': manifest['max_actions']}
        write(work / 'case.json', full)
        script = str(Path(__file__).resolve())
        prompt = ('Use only the browser MCP tools to complete the task already loaded in the browser. '
                  'Read the page with a snapshot first. Do not open another page or restart the task. '
                  'You have at most 10 interaction actions and 40 observation calls. '
                  'The task and starting page will be supplied by the browser setup. '
                  'When the episode ends or cannot be completed, give a concise final response. '
                  'Do not attempt to inspect or modify benchmark internals, reward, source, timers or hidden state. '
                  'The browser MCP server has a task tool named task; call it to read your instruction.')
        # The task instruction is a read-only MCP tool added in the agent-facing wrapper.
        args = ['codex', 'exec', '--ignore-user-config', '--ignore-rules', '--ephemeral',
                '--skip-git-repo-check', '--json', '--dangerously-bypass-approvals-and-sandbox',
                '-C', str(work), '-o', str(work / 'answer.txt'),
                '-c', 'notify=[]', '-c', 'web_search="disabled"',
                '-c', f'model={json.dumps(manifest["model"])}',
                '-c', f'model_reasoning_effort={json.dumps(manifest["reasoning_effort"])}',
                '-c', f'mcp_servers.browser.command={json.dumps(sys.executable)}',
                '-c', f'mcp_servers.browser.args={json.dumps([script, "broker", str(work / "case.json")])}',
                '-c', 'mcp_servers.browser.startup_timeout_sec=90',
                '-c', 'features.skip_host_skill_discovery=true']
        for feature in ('shell_tool', 'apps', 'plugins', 'browser_use', 'computer_use',
                        'multi_agent', 'image_generation', 'view_image', 'skill_search'):
            args += ['--disable', feature]
        args += [prompt]
        write(work / 'invocation.json', {'argv': args, 'prompt': prompt})
        env = {**os.environ, 'HERA_OFF': '1', 'UMBRA_PROFILE': full['profile'], 'UMBRA_IDLE': '60'}
        begin = time.monotonic()
        with open(work / 'transcript.jsonl', 'w') as out, open(work / 'agent.log', 'w') as err:
            proc = subprocess.Popen(args, env=env, stdout=out, stderr=err,
                                    stdin=subprocess.DEVNULL, start_new_session=True)
            try:
                code = proc.wait(timeout=config['timeout'])
            except subprocess.TimeoutExpired:
                code = 'timeout'
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
        # Codex terminates MCP servers at turn end, which may interrupt their finally blocks.
        # Reap only processes whose arguments contain this exact case directory.
        listing = subprocess.check_output(['ps', '-ax', '-o', 'pid=,command='], text=True)
        owned = []
        for line in listing.splitlines():
            pid, command = line.strip().split(' ', 1)
            if str(work) in command and int(pid) != os.getpid():
                owned.append(int(pid))
                try:
                    os.kill(int(pid), signal.SIGTERM)
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + 5
        while owned and time.monotonic() < deadline:
            live = []
            for pid in owned:
                try:
                    os.kill(pid, 0)
                    live.append(pid)
                except ProcessLookupError:
                    pass
            owned = live
            if owned:
                time.sleep(.1)
        for pid in owned:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if case['arm'] == 'umbra':
            clean = cleanup(full['profile'], env)
        else:
            shutil.rmtree(work / 'chrome', ignore_errors=True)
            clean = not (work / 'chrome').exists()
        write(work / 'cleanup.json', {'clean': clean, 'parent_verified': True})
        value = {'exit': code, 'seconds': time.monotonic() - begin}
        write(work / 'run.json', value)
        with lock:
            score = json.loads((work / 'score.json').read_text()) if (work / 'score.json').exists() else {}
            print(name, code, 'PASS' if score.get('browsergym_success') else 'FAIL', round(value['seconds'], 1), 's', flush=True)
    try:
        with ThreadPoolExecutor(options.jobs) as pool:
            list(pool.map(one, manifest['cases']))
    finally:
        server.shutdown()
        server.server_close()
    summarize(root)


def summarize(root):
    root = Path(root)
    manifest = json.loads((root / 'manifest.json').read_text())
    rows = []
    for case in manifest['cases']:
        work = root / f'{case["task"]}-{case["repeat"]}-{case["arm"]}'
        row = dict(case)
        for filename in ('run.json', 'score.json', 'cleanup.json', 'broker-error.json'):
            if (work / filename).exists():
                row[filename.removesuffix('.json')] = json.loads((work / filename).read_text())
        rows.append(row)
    write(root / 'results.json', rows)
    for arm in ('umbra', 'playwright'):
        group = [r for r in rows if r['arm'] == arm]
        success = sum(bool(r.get('score', {}).get('browsergym_success')) for r in group)
        complete = sum('run' in r for r in group)
        print(f'{arm}: {success}/{len(group)} success; {complete}/{len(group)} finished', flush=True)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'broker':
        broker(sys.argv[2])
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['freeze', 'run', 'report'])
    parser.add_argument('--out', required=True)
    parser.add_argument('--browsergym')
    parser.add_argument('--miniwob')
    parser.add_argument('--playwright-mcp')
    parser.add_argument('--chromium')
    parser.add_argument('--tasks', default='')
    parser.add_argument('--repeats', type=int, choices=range(1, 6), default=1)
    parser.add_argument('--timeout', type=int, default=120)
    parser.add_argument('--jobs', type=int, default=4)
    options = parser.parse_args()
    if options.action == 'freeze':
        freeze(options)
    elif options.action == 'run':
        run(options)
    else:
        summarize(options.out)


if __name__ == '__main__':
    main()
