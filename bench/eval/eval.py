#!/usr/bin/env python3
"""Agent evaluation on Northwind Ops: does an agent with Umbra (and its skill) get real tasks done?

  eval.py setup --arm skill|bare [--tasks a,b] --port 9200   one site server, profile and workdir per task
  eval.py grade --arm skill|bare                              grade every candidate from server state and answers
  eval.py teardown --arm skill|bare                           stop servers and daemons, delete profiles
  eval.py run-codex --run NAME [--jobs 4]                     run every candidate of a set-up run with `codex exec`

Each candidate gets a wrapper that pins Umbra to its own throwaway profile, a workdir with its
own copy of any input file, and a prompt rendered from bench/tasks.json. Candidates write their
final answer to <workdir>/answer.txt. Graders and answer keys stay outside their prompt.
"""
import argparse, hashlib, json, os, shutil, signal, subprocess, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BENCH = Path(__file__).resolve().parent.parent
REPO = BENCH.parent
ROOT = Path(os.environ.get('EVAL_ROOT', '/private/tmp/umbra-eval'))
UMBRA = os.environ.get('UMBRA', str(Path.home() / '.local/bin/umbra'))
SKILL = REPO / 'skill' / 'SKILL.md'
UNATTENDED = {'captcha-handoff'}  # needs a person at the screen


def tasks():
    return json.loads((BENCH / 'tasks.json').read_text())['tasks']


def manifest(run):
    return ROOT / run / 'manifest.json'


def profile_store(name):
    h = hashlib.sha256(('umbra profile ' + name).encode()).hexdigest().upper()
    return Path.home() / 'Library/WebKit/com.randyren.umbra/WebsiteDataStore' / '-'.join(
        [h[0:8], h[8:12], h[12:16], h[16:20], h[20:32]]).lower()


def setup(arm, only, port, run):
    sys.path.insert(0, str(BENCH))
    import grade
    base_dir = ROOT / run
    shutil.rmtree(base_dir, ignore_errors=True)
    entries = []
    for i, task in enumerate(t for t in tasks() if t['id'] not in UNATTENDED and (not only or t['id'] in only)):
        tid = task['id']
        work = base_dir / tid
        (work / 'bin').mkdir(parents=True)
        (work / 'out').mkdir()
        profile = f'ev-{hashlib.sha1(run.encode()).hexdigest()[:6]}-{i:02d}'  # unique per run and task
        wrapper = work / 'bin' / 'umbra'
        wrapper.write_text(f'#!/bin/sh\nexport UMBRA_PROFILE={profile} UMBRA_IDLE=300\nexec "{UMBRA}" "$@"\n')
        wrapper.chmod(0o755)
        shutil.copy(BENCH / 'site/fixtures/invoice.pdf', work / 'invoice.pdf')
        p = port + i
        args = [sys.executable, str(BENCH / 'site/server.py'), '--port', str(p), '--seed', '7']
        if task.get('setup', {}).get('otp'):
            args.append('--otp')
        server = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        base = f'http://127.0.0.1:{p}'
        for _ in range(50):
            try:
                urllib.request.urlopen(base + '/__state', timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        request = grade.prompt(tid, base, str(work / 'out')).replace(
            str(BENCH / 'site/fixtures/invoice.pdf'), str(work / 'invoice.pdf'))
        notes = [
            '---',
            'Notes for this task:',
            f'- The browser on this Mac is Umbra. Always run it by this full path: {wrapper}',
            f'- Your working directory is {work}. Do not read anything under {REPO}.',
            f'- When you are done, write your final answer to {work}/answer.txt and also give it as your last message.',
        ]
        if arm == 'skill':
            notes += ['', 'You have this skill loaded:', '', SKILL.read_text()]
        else:
            notes.insert(2, f'- `{wrapper} help` explains how to use it.')
        (work / 'prompt.txt').write_text(request + '\n\n' + '\n'.join(notes) + '\n')
        entries.append({'task': tid, 'work': str(work), 'base': base, 'pid': server.pid, 'profile': profile})
    manifest(run).write_text(json.dumps(entries, indent=2))
    for e in entries:
        print(e['task'], e['work'] + '/prompt.txt')


def grade_all(run):
    rows = []
    for e in json.loads(manifest(run).read_text()):
        work = Path(e['work'])
        answer = (work / 'answer.txt').read_text() if (work / 'answer.txt').exists() else ''
        args = [sys.executable, str(BENCH / 'grade.py'), e['task'], '--base', e['base'], '--answer', answer, '--out', str(work / 'out')]
        if (work / 'transcript.txt').exists():
            args += ['--transcript', str(work / 'transcript.txt')]
        result = subprocess.run(args, capture_output=True, text=True)
        line = (result.stdout.strip() or result.stderr.strip()).splitlines()[-1] if (result.stdout or result.stderr) else 'FAIL (no output)'
        rows.append((e['task'], line))
        print(line)
    passed = sum(1 for _, line in rows if line.startswith('PASS'))
    print(f'\n{run}: {passed}/{len(rows)} passed')
    (ROOT / run / 'grades.json').write_text(json.dumps(rows, indent=2))


def run_codex(run, jobs):
    """Each candidate is a fresh, ephemeral `codex exec` in its workdir, with the user's Codex setup
    (model, skills, AGENTS.md) except where isolation needs otherwise: `umbra` on PATH is the
    candidate's wrapper, the Umbra MCP server is pinned to the candidate's profile, Codex's own
    browser REPL is off (it would take over the screen), turn notifications are off,
    and HERA_OFF keeps the runs out of the Hera vault. The JSON event stream is the transcript."""
    def one(e):
        work = Path(e['work'])
        env = {**os.environ, 'HERA_OFF': '1', 'UMBRA_PROFILE': e['profile'], 'UMBRA_IDLE': '300',
               'PATH': f"{work / 'bin'}:{os.environ['PATH']}"}
        args = ['codex', 'exec', '--ephemeral', '--skip-git-repo-check', '--json',
                '--dangerously-bypass-approvals-and-sandbox', '-C', str(work),
                '-o', str(work / 'last-message.txt'), '-c', 'notify=[]',
                '-c', f'mcp_servers.umbra.env.UMBRA_PROFILE="{e["profile"]}"',
                '-c', 'mcp_servers.umbra.env.UMBRA_IDLE="300"',
                '-c', 'mcp_servers.node_repl.enabled=false',
                (work / 'prompt.txt').read_text()]
        start = time.monotonic()
        with open(work / 'transcript.jsonl', 'w') as out:
            try:
                code = subprocess.run(args, env=env, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                      timeout=900).returncode
            except subprocess.TimeoutExpired:
                code = 'timeout'
        if not (work / 'answer.txt').exists() and (work / 'last-message.txt').exists():
            shutil.copy(work / 'last-message.txt', work / 'answer.txt')
        (work / 'run.json').write_text(json.dumps({'exit': code, 'seconds': round(time.monotonic() - start, 1)}))
        print(e['task'], code, round(time.monotonic() - start), 's', flush=True)
    with ThreadPoolExecutor(jobs) as pool:
        list(pool.map(one, json.loads(manifest(run).read_text())))


def teardown(run):
    for e in json.loads(manifest(run).read_text()):
        try:
            os.killpg(e['pid'], signal.SIGTERM)
        except OSError:
            pass
        subprocess.run([str(Path(e['work']) / 'bin/umbra'), 'stop'], capture_output=True)
        shutil.rmtree(profile_store(e['profile']), ignore_errors=True)
        for f in (Path.home() / 'Library/Application Support/Umbra').glob(f"*-{e['profile']}.*"):
            f.unlink()
        shutil.rmtree(Path.home() / 'Downloads/Umbra' / e['profile'], ignore_errors=True)
    print('torn down', run)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('action', choices=['setup', 'grade', 'teardown', 'run-codex'])
    parser.add_argument('--arm', choices=['skill', 'bare'])
    parser.add_argument('--tasks', default='')
    parser.add_argument('--port', type=int, default=9200)
    parser.add_argument('--run', help='name of this run (default: the arm)')
    parser.add_argument('--jobs', type=int, default=4, help='run-codex: candidates at once')
    options = parser.parse_args()
    run = options.run or options.arm
    if not run or (options.action == 'setup' and not options.arm):
        parser.error('setup needs --arm; the other actions need --run or --arm')
    if options.action == 'setup':
        setup(options.arm, [t for t in options.tasks.split(',') if t], options.port, run)
    elif options.action == 'grade':
        grade_all(run)
    elif options.action == 'run-codex':
        run_codex(run, options.jobs)
    else:
        teardown(run)
