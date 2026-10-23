"""Bounded public-protocol popup ownership and native family lifetime probe."""
import argparse
import concurrent.futures
import json
import os
import signal
import socket
import sys
import threading
import time
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'bench')]
import test_umbra as base
import memory
from server import Server

parser = argparse.ArgumentParser()
parser.add_argument('--binary', required=True)
parser.add_argument('--out', required=True)
parser.add_argument('--mode', choices=['scratch','popup'], default='scratch')
parser.add_argument('--iterations', type=int, default=4, choices=range(1, 9))
opts = parser.parse_args()
base.UMBRA = str(Path(opts.binary).resolve())
profile = base.new_profile()
server = Server()
clicked, loading, release, stop = (threading.Event() for _ in range(4))
server.route('/clicked')(lambda h: (clicked.set(), h.reply(body='ok')))
def delayed(h):
    loading.set()
    release.wait(5)
    h.reply(body='<title>Independent scratch</title><p>done</p>')
server.route('/slow')(delayed)
server.page('/app', '''<title>Acted</title><button id=save>Save</button><output id=result></output><script>
save.onclick=e=>{window.trusted=e.isTrusted;fetch('/clicked');const end=performance.now()+600;
while(performance.now()<end){};result.textContent='Saved'};</script>'''.replace("fetch('/clicked');", "fetch('/clicked');" + ("window.open('/slow');" if opts.mode == 'popup' else '')))
path = str(base.LOCKS / f'daemon-{profile}.sock')
pid = memory.spawn_disclaimed([base.UMBRA, 'serve'], base.environment(profile, None), base.TMP / 'daemon.log')
started = time.monotonic()
records, samples, errors = [], [], []
phase = 'startup'
guard_failure = []
max_tabs = 0

def call(command, **args):
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(12)
        connection.connect(path)
        connection.sendall((json.dumps({'id':1,'command':command,'args':args})+'\n').encode())
        raw = connection.makefile('rb').readline()
        if not raw: raise AssertionError('connection closed')
        reply = json.loads(raw)
        if not reply.get('ok'): raise AssertionError(reply)
        return reply['result']

def sample():
    members = []
    for member,kind in memory.family(pid).items():
        usage = memory.usage(member)
        if usage:
            members.append({'pid':member,'kind':kind,'physicalFootprintBytes':usage[0]})
    row = {'seconds':time.monotonic()-started,'phase':phase,'members':members,
           'physicalFootprintBytes':sum(m['physicalFootprintBytes'] for m in members)}
    samples.append(row)
    if row['physicalFootprintBytes'] >= 1024**3:
        guard_failure.append(row)
        try: call('stop')
        except Exception: pass
        stop.set()

def watch():
    while not stop.wait(.25):
        try: sample()
        except Exception as exc: guard_failure.append({'error':str(exc)});stop.set()

def tabs():
    global max_tabs
    found = call('tabs')['tabs']; max_tabs = max(max_tabs,len(found))
    if len(found)>2: raise AssertionError('exceeded two-tab bound')
    if guard_failure: raise AssertionError('physical family guard failed')
    return found

thread = None
try:
    ready = time.monotonic()+10
    while True:
        try:
            if call('status')['pid']==pid: break
        except (OSError, AssertionError): pass
        if time.monotonic()>ready: raise AssertionError('daemon not ready')
        time.sleep(.05)
    thread = threading.Thread(target=watch,daemon=True);thread.start()
    for iteration in range(opts.iterations):
        phase=f'iteration-{iteration}'
        clicked.clear();loading.clear();release.clear()
        call('open',tab='acted',url=server.url('/app'))
        call('snapshot',tab='acted')
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            click = pool.submit(call,'click',tab='acted',target='#save',timeout=8,request=f'click-{iteration}')
            if not clicked.wait(3): raise AssertionError('native click not received')
            read = pool.submit(call,'read',url=server.url('/slow'),timeout=8,
                               script='await new Promise(r=>setTimeout(r,150)); return 7') if opts.mode == 'scratch' else None
            if not loading.wait(3): raise AssertionError('scratch load not received')
            tabs()
            time.sleep(.75)
            release.set()
            result=click.result(timeout=10);value=read.result(timeout=10) if read else {'output':'7'}
        if str(value.get('output')) != '7' or result.get('trusted') is not True: raise AssertionError('task semantics/trust failed: '+str((value,result)))
        if opts.mode == 'popup':
            if len(tabs()) != 2 or 'newTab' not in result: raise AssertionError('genuine popup missing')
            call('tab',action='close',name=result['newTab'])
        if len(tabs()) != 1: raise AssertionError('scratch/popup tab did not close')
        call('tab',action='close',name='acted')
        if tabs(): raise AssertionError('acted tab did not close')
        records.append({'iteration':iteration,'seconds':time.monotonic()-started,
                        'newTab':result.get('newTab'),'urlAfter':result.get('receipt',{}).get('urlAfter'),
                        'trusted':result.get('trusted'),'tabsAfterClose':0})
    phase='post-close-before-deadline'
    time.sleep(.5)
    phase='quiescence'
    time.sleep(8.5)
    tabs()
    if guard_failure: raise AssertionError('physical family guard failed')
except Exception as exc:
    errors.append(str(exc))
finally:
    release.set();stop.set()
    if thread: thread.join(2)
    try: call('stop')
    except Exception: pass
    until=time.monotonic()+7
    reaped=False
    while time.monotonic()<until:
        try:
            if os.waitpid(pid,os.WNOHANG)[0]==pid: reaped=True;break
        except ChildProcessError:reaped=True;break
        time.sleep(.05)
    if not reaped:
        os.kill(pid,signal.SIGKILL);os.waitpid(pid,0)
    server.close();base.cleanup()
    files_gone=not any(p.exists() for p in base.profile_files(profile))
    result={'binary':base.UMBRA,'mode':opts.mode,'iterations':opts.iterations,'maxLogicalTabs':max_tabs,
            'budgetBytes':1024**3,'peakPhysicalFootprintBytes':max((r['physicalFootprintBytes'] for r in samples),default=0),
            'records':records,'samples':samples,'guardFailure':guard_failure,'errors':errors,
            'cleanup':{'daemonGone':not base.alive(pid),'profileFilesGone':files_gone}}
    Path(opts.out).write_text(json.dumps(result,indent=2)+'\n')
    if errors or guard_failure or not files_gone:sys.exit(1)
