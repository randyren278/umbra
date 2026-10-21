#!/usr/bin/env python3
"""Umbra's memory while agents come and go: one live agent keeps the daemon up, as a busy Mac does,
while others each open a heavy page in a tab of their own, use it, and never come back to it.

    python3 bench/memory.py [--abandoned 10] [--watch 150] [--idle 60] [--umbra PATH] [--json FILE]

Every --step seconds it sums the physical footprint (Activity Monitor's Memory column) of the
daemon and of every process macOS holds it responsible for (its WebKit web content, GPU and
networking processes), and the CPU time they use. The daemon is started with its responsibility
disclaimed, so those processes are told apart from any other Umbra's. Uses a throwaway
UMBRA_PROFILE=memory-<random> and deletes its store, socket, lock, log and downloads afterwards.
"""
import argparse
import ctypes
import http.server
import json
import os
import secrets
import shutil
import statistics
import struct
import subprocess
import threading
import time
import zlib
from hashlib import sha256
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SUPPORT = Path.home() / 'Library/Application Support/Umbra'
STORES = Path.home() / 'Library/WebKit/com.randyren.umbra/WebsiteDataStore'
DOWNLOADS = Path.home() / 'Downloads/Umbra'
libc = ctypes.CDLL(None)
libc.responsibility_get_pid_responsible_for_pid.argtypes = [ctypes.c_int]


class Usage(ctypes.Structure):  # rusage_info_v4
    _fields_ = [('uuid', ctypes.c_uint8 * 16)] + [(name, ctypes.c_uint64) for name in (
        'user system idle_wakeups interrupt_wakeups pageins wired resident footprint start exit '
        'child_user child_system child_idle_wakeups child_interrupt_wakeups child_pageins child_elapsed '
        'read written qos_default qos_maintenance qos_background qos_utility qos_legacy '
        'qos_user_initiated qos_user_interactive billed_system serviced_system logical_writes '
        'lifetime_max_footprint instructions cycles billed_energy serviced_energy interval_max_footprint '
        'runnable flags').split()]


class Timebase(ctypes.Structure):
    _fields_ = [('numer', ctypes.c_uint32), ('denom', ctypes.c_uint32)]


timebase = Timebase()
libc.mach_timebase_info(ctypes.byref(timebase))


def usage(pid):
    """(footprint bytes, CPU seconds), or None once the process is gone."""
    info = Usage()
    if libc.proc_pid_rusage(pid, 4, ctypes.byref(info)) != 0:
        return None
    return info.footprint, (info.user + info.system) * timebase.numer / timebase.denom / 1e9


def spawn_disclaimed(argv, env, log):
    """posix_spawn with responsibility disclaimed: the child answers for the processes it starts."""
    attr, actions, pid = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_int()
    libc.posix_spawnattr_init(ctypes.byref(attr))
    libc.posix_spawnattr_setflags(ctypes.byref(attr), ctypes.c_short(0x0400))  # POSIX_SPAWN_SETSID
    libc.responsibility_spawnattrs_setdisclaim(ctypes.byref(attr), ctypes.c_bool(True))
    libc.posix_spawn_file_actions_init(ctypes.byref(actions))
    libc.posix_spawn_file_actions_addopen(ctypes.byref(actions), 0, b'/dev/null', os.O_RDONLY, 0)
    libc.posix_spawn_file_actions_addopen(ctypes.byref(actions), 1, str(log).encode(), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    libc.posix_spawn_file_actions_adddup2(ctypes.byref(actions), 1, 2)
    cargs = (ctypes.c_char_p * (len(argv) + 1))(*[a.encode() for a in argv], None)
    cenv = (ctypes.c_char_p * (len(env) + 1))(*[f'{k}={v}'.encode() for k, v in env.items()], None)
    code = libc.posix_spawn(ctypes.byref(pid), argv[0].encode(), ctypes.byref(actions), ctypes.byref(attr), cargs, cenv)
    libc.posix_spawn_file_actions_destroy(ctypes.byref(actions))
    libc.posix_spawnattr_destroy(ctypes.byref(attr))
    if code:
        raise OSError(code, 'posix_spawn failed')
    return pid.value


def family(daemon):
    """{pid: kind} for the daemon and every process it is responsible for."""
    out = subprocess.run(['ps', '-axo', 'pid=,comm='], capture_output=True, text=True).stdout
    found = {daemon: 'daemon'}
    for row in out.splitlines():
        pid, command = row.strip().split(None, 1)
        pid = int(pid)
        if pid != daemon and libc.responsibility_get_pid_responsible_for_pid(pid) == daemon:
            found[pid] = command.rsplit('.', 1)[-1] if 'WebKit' in command else command.rsplit('/', 1)[-1]
    return found


# A page that holds memory as a real web app does: a JS heap, a typed array, a drawn canvas, decoded
# images and a few thousand DOM nodes, kept busy by an animation and a timer.
HEAVY = """<!doctype html><title>heavy {n}</title>
<style>@keyframes spin {{ to {{ transform: rotate(360deg) }} }} .spin {{ animation: spin 1s linear infinite; width: 40px; height: 40px; background: teal }}</style>
<h1>Heavy page {n}</h1><div class=spin></div><p id=frames>0</p>
<button onclick="this.textContent='clicked'">Press</button><input aria-label=Query>
<img src="/img.png?{n}-1" width=400><img src="/img.png?{n}-2" width=400><img src="/img.png?{n}-3" width=400>
<canvas id=c width=2048 height=2048 style="width:400px"></canvas><div id=list></div>
<script>
window.keep = [];
for (let i = 0; i < 300000; i++) keep.push({{i, s: 'item ' + i + ' ' + Math.random()}});
const block = new Float64Array(4e6); block.fill(1.5); keep.push(block);
const c = document.getElementById('c').getContext('2d');
for (let i = 0; i < 3000; i++) {{ c.fillStyle = `hsl(${{i % 360}},60%,50%)`; c.fillRect(Math.random() * 2048, Math.random() * 2048, 60, 60); }}
const list = document.getElementById('list');
for (let i = 0; i < 3000; i++) {{ const p = document.createElement('p'); p.textContent = 'row ' + i; list.appendChild(p); }}
let frames = 0; (function tick() {{ frames++; if (frames % 30 == 0) document.getElementById('frames').textContent = frames; requestAnimationFrame(tick); }})();
setInterval(() => {{ let x = 0; for (let i = 0; i < 20000; i++) x += Math.sqrt(i); window.x = x; }}, 200);
</script>"""


def png(width, height, rgb):
    raw = (b'\x00' + bytes(rgb) * width) * height

    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))


IMAGE = png(2000, 2000, (200, 120, 40))


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        path, _, query = self.path.partition('?')
        if path == '/heavy':
            body, kind = HEAVY.format(n=query or '0').encode(), 'text/html; charset=utf-8'
        elif path == '/img.png':
            body, kind = IMAGE, 'image/png'
        else:
            body, kind = b'<title>blank</title>', 'text/html'
        self.send_response(200)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def store_dir(name):
    h = sha256(f'umbra profile {name}'.encode()).hexdigest()[:32]
    return STORES / '-'.join((h[:8], h[8:12], h[12:16], h[16:20], h[20:]))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--umbra', default=str(REPO / 'build/Umbra.app/Contents/MacOS/umbra'))
    parser.add_argument('--abandoned', type=int, default=10, help='agents that open a tab and leave it')
    parser.add_argument('--pages', type=int, default=1, help='pages each of them opens in its tab, one after another')
    parser.add_argument('--idle', type=int, default=60, help='UMBRA_IDLE for the daemon, in seconds')
    parser.add_argument('--watch', type=int, default=150, help='seconds to watch after the last agent leaves')
    parser.add_argument('--step', type=float, default=5)
    parser.add_argument('--json', help='write the samples and summary here')
    opts = parser.parse_args()

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_address[1]}'
    name = 'memory-' + secrets.token_hex(4)
    env = {**os.environ, 'UMBRA_PROFILE': name, 'UMBRA_IDLE': str(opts.idle)}

    def umbra(*args, timeout=90):
        start = time.monotonic()
        done = subprocess.run([opts.umbra, *args], env=env, capture_output=True, text=True, timeout=timeout)
        if done.returncode:
            raise SystemExit(f'umbra {" ".join(args)} exited {done.returncode}: {done.stderr.strip()}')
        return done.stdout, (time.monotonic() - start) * 1000

    daemon = spawn_disclaimed([opts.umbra, 'serve'], env, SUPPORT / f'daemon-{name}.log')
    samples, latencies, stop = [], [], threading.Event()
    try:
        for _ in range(200):  # `status` never starts a daemon: wait until this one answers
            if umbra('status')[0].startswith('umbra daemon'):
                break
            time.sleep(0.05)
        umbra('--tab', 'live', 'open', f'{base}/heavy?live')

        def live():  # the agent still at work: a snapshot every 10 s keeps its tab and the daemon in use
            while not stop.wait(10):
                latencies.append(umbra('--tab', 'live', 'snapshot', '--interactive')[1])
        threading.Thread(target=live, daemon=True).start()

        for i in range(opts.abandoned):
            tab = f'agent-{i}'
            for page in range(opts.pages):  # every other page on another site: localhost, then 127.0.0.1
                host = base.replace('127.0.0.1', 'localhost') if page % 2 else base
                umbra('--tab', tab, 'open', f'{host}/heavy?{i}-{page}')
            umbra('--tab', tab, 'snapshot', '--interactive')
            umbra('--tab', tab, 'click', 'text=Press')
            umbra('--tab', tab, 'text')
        left = time.monotonic()
        cpu = {}
        while (t := time.monotonic() - left) <= opts.watch:
            members = family(daemon)
            totals = {'t': round(t), 'tabs': len(json.loads(umbra('status', '--json')[0])['tabs'])}
            for pid, kind in members.items():
                used = usage(pid)
                if used is None:
                    continue
                key = {'daemon': 'daemon', 'WebContent': 'web', 'GPU': 'gpu', 'Networking': 'net'}.get(kind, 'other')
                totals[key] = totals.get(key, 0) + used[0] / 2**20
                totals[key + '_n'] = totals.get(key + '_n', 0) + 1
                cpu[pid] = used[1]
            totals['total'] = sum(v for k, v in totals.items() if k in ('daemon', 'web', 'gpu', 'net', 'other'))
            totals['cpu'] = sum(cpu.values())
            samples.append(totals)
            print(f"{totals['t']:4d}s  tabs {totals['tabs']:3d}  web {totals.get('web_n', 0):3d} procs "
                  f"{totals.get('web', 0):7.0f} MB  daemon {totals.get('daemon', 0):5.0f}  gpu {totals.get('gpu', 0):5.0f}  "
                  f"net {totals.get('net', 0):4.0f}  total {totals['total']:7.0f} MB  cpu {totals['cpu']:6.1f} s", flush=True)
            time.sleep(max(0, opts.step - (time.monotonic() - left - t)))
    finally:
        stop.set()
        subprocess.run([opts.umbra, 'stop'], env=env, capture_output=True, timeout=30)
        for _ in range(100):
            if usage(daemon) is None:
                break
            time.sleep(0.1)
        server.shutdown()
        shutil.rmtree(store_dir(name), ignore_errors=True)
        shutil.rmtree(DOWNLOADS / name, ignore_errors=True)
        for leftover in SUPPORT.glob(f'*-{name}.*'):
            leftover.unlink(missing_ok=True)

    late = [s for s in samples if s['t'] >= opts.idle + 30]
    summary = {
        'peak_mb': round(max(s['total'] for s in samples)),
        'end_mb': round(samples[-1]['total']),
        'end_tabs': samples[-1]['tabs'],
        'end_web_processes': samples[-1].get('web_n', 0),
        'idle_cpu_s_per_min': round((late[-1]['cpu'] - late[0]['cpu']) / max(1, late[-1]['t'] - late[0]['t']) * 60, 2) if len(late) > 1 else None,
        'live_snapshot_ms_p50': round(statistics.median(latencies), 1) if latencies else None,
        'live_snapshot_ms_max': round(max(latencies), 1) if latencies else None,
    }
    print(json.dumps(summary))
    if opts.json:
        Path(opts.json).write_text(json.dumps({'summary': summary, 'samples': samples, 'options': vars(opts)}, indent=1))


if __name__ == '__main__':
    main()
