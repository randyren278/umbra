#!/usr/bin/env python3
"""Compare active stock, nonvisual and declared-API hybrid sessions on localhost.

Run arms sequentially, alternating order across blocks. The fixture origin and Python
controller are excluded from client memory. Physical footprint is native per-PID
accounting, not summed RSS; shared/helper attribution remains best-effort.

    python3 bench/hybrid.py --umbra PATH --mode hybrid --runs 3 --json FILE
    python3 bench/hybrid.py --umbra PATH --mode nonvisual --held-out --promote-all --json FILE
"""
import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
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
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(REPO/'tests'))
from hybrid_site import make_server, contract
spec = importlib.util.spec_from_file_location('hybrid_metrics', REPO/'bench/memory.py')
metrics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metrics)


class Client:
    def __init__(self, path):
        self.path, self.counter = path, 0

    def call(self, command, expected=True, **args):
        self.counter += 1
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(30)
            connection.connect(str(self.path))
            connection.sendall(json.dumps(dict(id=self.counter, command=command, args=args)).encode()+b'\n')
            stream = connection.makefile('rb')
            reply = json.loads(stream.readline())
            stream.close()
        if reply.get('ok') != expected:
            raise AssertionError(reply)
        return reply['result'] if expected else reply['error']

    def value(self, tab, script):
        return self.call('eval', tab=tab, script=script)['value']

    def wait(self, tab, condition='window.ready === true'):
        end = time.monotonic()+15
        while time.monotonic() < end:
            try:
                if self.value(tab, 'return '+condition):
                    return
            except (AssertionError, OSError):
                pass
            time.sleep(.01)
        raise TimeoutError('fixture readiness: '+condition)


class Sampler:
    def __init__(self, pid):
        self.pid, self.rows, self.lock = pid, [], threading.Lock()
        self.stop = threading.Event()

    def sample(self, stage):
        started = time.perf_counter()
        processes = []
        for pid, kind in metrics.family(self.pid).items():
            info = metrics.Usage()
            if metrics.libc.proc_pid_rusage(pid, 4, ctypes.byref(info)) != 0 or info.exit:
                continue
            processes.append(dict(pid=pid, kind=kind, physicalFootprintBytes=info.footprint,
                                  residentBytes=info.resident,
                                  cpuSeconds=(info.user+info.system)*metrics.timebase.numer/metrics.timebase.denom/1e9))
        row = dict(stage=stage, monotonic=time.monotonic(), processes=processes,
                   physicalFootprintBytes=sum(x['physicalFootprintBytes'] for x in processes),
                   sampleDurationMs=(time.perf_counter()-started)*1000)
        assert len({x['pid'] for x in processes}) == len(processes)
        with self.lock:
            self.rows.append(row)
        return row

    def steady(self, stage):
        for i in range(5):
            self.sample(stage)
            if i < 4:
                time.sleep(.25)

    def peak(self, stage, work):
        self.stop.clear()
        def collect():
            while not self.stop.is_set():
                started = time.monotonic()
                self.sample(stage)
                self.stop.wait(max(0, .1-(time.monotonic()-started)))
        thread = threading.Thread(target=collect, daemon=True)
        thread.start()
        try:
            result = work()
            self.stop.wait(3)  # Include delayed layout/paint allocations after the action completes.
            return result
        finally:
            self.stop.set()
            thread.join(5)
            if thread.is_alive():
                raise RuntimeError('memory sampler did not finish')
            self.sample(stage)


def cleanup(binary, env, name, pid):
    try:
        subprocess.run([binary, 'stop'], env=env, capture_output=True, timeout=10)
    except subprocess.SubprocessError:
        pass
    if pid <= 0:
        raise ValueError('cleanup requires its own positive child PID')

    def reaped(timeout):
        deadline = time.monotonic()+timeout
        while True:
            try:
                found, _ = os.waitpid(pid, os.WNOHANG)
                if found:
                    return True
            except ChildProcessError:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(.05)

    if not reaped(7):
        # An unreaped child still owns its PID; never signal a reaped/reused PID.
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        if not reaped(2):
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
    for _ in range(3):
        shutil.rmtree(metrics.store_dir(name), ignore_errors=True)
        shutil.rmtree(metrics.DOWNLOADS/name, ignore_errors=True)
        for file in metrics.SUPPORT.glob(f'*-{name}.*'):
            file.unlink(missing_ok=True)
        time.sleep(.2)
    return dict(profile=name, storeExists=metrics.store_dir(name).exists(),
                supportFiles=[x.name for x in metrics.SUPPORT.glob(f'*-{name}.*')],
                downloadsExist=(metrics.DOWNLOADS/name).exists())


def run(binary, mode, run_id, output, cycles, held_out, promote_all):
    name = 'impl-'+secrets.token_hex(5)
    scratch = Path(tempfile.mkdtemp(prefix='umbra-hybrid-'))
    (scratch/'sites.json').write_text('[]')
    (scratch/'agents').mkdir()
    env = {**os.environ, 'UMBRA_PROFILE':name, 'UMBRA_IDLE':'60', 'UMBRA_SITES':str(scratch/'sites.json'),
           'UMBRA_AGENTS':str(scratch/'agents'), 'UMBRA_UNSEEN':'1', 'UMBRA_LOGOS':str(scratch/'logos')}
    server = make_server()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f'http://127.0.0.1:{server.server_address[1]}'
    descriptor = scratch/'contract.json'
    descriptor.write_text(json.dumps(contract(origin)))
    data = dict(mode=mode, run=run_id, profile=name, binarySHA256=hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
                heldOut=held_out, promoteAll=promote_all, cycles=cycles, checks=[], timings=[], loadStart=list(os.getloadavg()),
                fixtureSHA256=hashlib.sha256((REPO/'tests/hybrid_site.py').read_bytes()).hexdigest())
    pid = None
    def check(label, value):
        data['checks'].append(dict(name=label, passed=bool(value)))
        if not value:
            raise AssertionError(label)
    def timed(label, action):
        start = time.perf_counter(); value = action()
        elapsed=(time.perf_counter()-start)*1000
        pause=data.get('startupSamplingPauseMs',0) if label=='login_and_views' else 0
        data['timings'].append(dict(task=label, milliseconds=elapsed-pause, rawWallMilliseconds=elapsed, excludedSamplerPauseMs=pause))
        return value
    def admin(action='truth'):
        request = urllib.request.Request(origin+'/admin/'+action, data=None if action=='truth' else b'{}',
                                        headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)
    try:
        pid = metrics.spawn_disclaimed([binary, 'serve'], env, scratch/'daemon.log')
        data['pid'] = pid
        client = Client(metrics.SUPPORT/f'daemon-{name}.sock')
        for _ in range(200):
            try:
                if client.call('status')['pid'] == pid:
                    break
            except (OSError, ValueError):
                pass
            time.sleep(.025)
        else:
            raise TimeoutError('isolated daemon never became ready')
        sampler = Sampler(pid)
        sampler.steady('daemon_empty')
        def api(route, body=None, query=None, expected=True):
            args = dict(action='call',name='work',route=route)
            if body is not None: args['data']=json.dumps(body)
            if query is not None: args['query']=json.dumps(query)
            return client.call('api', expected=expected, **args)
        def model(tab):
            return client.value(tab, 'return window.model && {revision:window.model.revision,count:window.model.count,items:window.model.items.map(x=>({id:x.id,subject:x.subject,unread:x.unread})),preferences:window.model.preferences}')
        q = 'Project update 1' if held_out else 'urgent'
        unread = not held_out
        def observe(view='inbox', query=q, only_unread=unread):
            if mode=='hybrid' and view!='js-only':
                return api(view,query=dict(q=query,unread=int(only_unread)))['data']
            client.value(view, 'document.querySelector("#search").value='+json.dumps(query)+';document.querySelector("#filter").checked='+json.dumps(only_unread)+';document.querySelector("#apply").click();return true')
            client.wait(view)
            return model(view)
        def mark(view, state):
            item = next(x for x in state['items'] if x['unread'])
            if mode=='hybrid' and view!='js-only':
                reply = api('mark',body=dict(id=item['id'],revision=state['revision'],requestId=f'{name}-{view}-{len(data["timings"])}'))
                check('mark_acknowledged',reply['status']==200)
            else:
                selector = 'button[data-item="'+str(item['id'])+'"]'
                client.value(view, 'document.querySelector('+json.dumps(selector)+').click();return true')
                client.wait(view)
            check('mark_verified',next(x for x in admin()['items'] if x['id']==item['id'])['unread'] is False)
        def prefs(cycle):
            state = observe('settings')
            name_value, notifications = 'Heldout '+str(cycle) if held_out else 'Cycle '+str(cycle), cycle%2==0
            if mode=='hybrid':
                api('prefs',body=dict(name=name_value,notification=notifications,revision=state['revision'],requestId=f'{name}-prefs-{cycle}'))
            else:
                client.value('settings','document.querySelector("#name").value='+json.dumps(name_value)+';document.querySelector("#notify").checked='+json.dumps(notifications)+';document.querySelector("#save").click();return true')
                client.wait('settings')
            check('preferences_verified',admin()['preferences']==dict(name=name_value,notification=notifications))
        def open_views():
            if mode=='hybrid':
                client.call('api',action='open',name='work',contract=str(descriptor))
                api('handshake')
                check('api_login',api('login',body=dict(username='agent',password='fixture-password'))['data']['authenticated'])
                for view in ('inbox','dashboard','settings'): api(view)
                sampling_start=time.perf_counter()
                sampler.steady('three_api_views')
                data['startupSamplingPauseMs']=(time.perf_counter()-sampling_start)*1000
                unsupported=api('js-only',expected=False)
                check('unsupported_contract',unsupported['status']==404)
                client.call('api',action='browser',name='work',url=origin+'/app/js-only',tab='js-only')
                client.wait('js-only')
            else:
                options = {'nonvisual':True} if mode=='nonvisual' else {}
                client.call('open',url=origin+'/login',tab='inbox',**options)
                client.value('inbox', 'document.querySelector("#username").value="agent";document.querySelector("#password").value="fixture-password";setTimeout(()=>document.querySelector("form").requestSubmit(),0);return true')
                client.wait('inbox')
                for view in ('dashboard','settings','js-only'):
                    client.call('open',url=origin+'/app/'+view,tab=view,**options)
                    client.wait(view)
        timed('login_and_views',open_views)
        time.sleep(1)
        sampler.steady('four_surfaces_ready')
        for cycle in range(cycles):
            state=timed('search_filter',observe)
            expected=[x['id'] for x in admin()['items'] if (not q or q.lower() in x['subject'].lower()) and (not unread or x['unread'])]
            check('exact_query_results',[x['id'] for x in state['items']]==expected[:50] and state['count']==len(expected))
            timed('mark_read',lambda:mark('inbox',state))
            timed('preferences',lambda:prefs(cycle))
            timed('dashboard',lambda:observe('dashboard'))
            js=timed('js_only_query',lambda:observe('js-only'))
            check('js_only_usable',js is not None and js['count']>0)
            timed('js_only_mark',lambda:mark('js-only',js))
            if cycle==0:
                before=admin()['revision'];admin('bump')
                fresh=observe('inbox','urgent',True)
                check('freshness',fresh['revision']>before and fresh['items'][0]['id']==9001)
            # Exercise actual production isolated-world observation for every resident browser.
            for view in (('js-only',) if mode=='hybrid' else ('inbox','dashboard','settings','js-only')):
                outline=client.call('snapshot',tab=view,interactive=True)
                check('outline_available','Apply' in outline['text'])
            time.sleep(.5)
            sampler.steady('mix_'+str(cycle))
        def promote_and_act():
            client.value('js-only','document.querySelector("#apply").addEventListener("click",e=>window.actualTrusted=e.isTrusted,{once:true});return true')
            client.call('click',tab='js-only',target='#apply')
            client.wait('js-only','window.actualTrusted===true && window.ready')
            check('trusted_fallback',client.value('js-only','return window.actualTrusted') is True)
            if promote_all:
                for view in ('inbox','dashboard','settings'):
                    if mode=='nonvisual':client.call('render',tab=view)
        sampler.peak('promotion_peak', lambda: timed('native_promotion_and_click', promote_and_act))
        for _ in range(10):
            def warm_native():
                client.value('js-only', 'window.actualTrusted=false;document.querySelector("#apply").addEventListener("click",e=>window.actualTrusted=e.isTrusted,{once:true});return true')
                client.call('click', tab='js-only', target='#apply')
                client.wait('js-only', 'window.actualTrusted===true && window.ready')
            timed('native_warm_click', warm_native)
        sampler.steady('steady_promoted')
        data['tabsBeforeExpiry']=client.call('tabs')['tabs']
        expected_tabs=1 if mode=='hybrid' else 4
        check('all_views_retained',len(data['tabsBeforeExpiry'])==expected_tabs)
        admin('expire')
        if mode=='hybrid':check('api_expiry',api('inbox',expected=False)['status']==401)
        client.value('js-only','document.querySelector("#apply").click();return true')
        client.wait('js-only','document.querySelector("#status").textContent==="signed-out"')
        check('browser_expiry',client.value('js-only','return window.model===null') is True)
        data['samples']=sampler.rows
        data['success']=True
    except BaseException as error:
        data['success']=False
        data['error']=type(error).__name__+': '+str(error)
        if 'sampler' in locals():data['samples']=sampler.rows
        raise
    finally:
        data['loadEnd']=list(os.getloadavg())
        if pid:data['cleanup']=cleanup(binary,env,name,pid)
        server.shutdown();server.server_close()
        shutil.rmtree(scratch,ignore_errors=True)
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(data,indent=2)+'\n')
    for stage in ('four_surfaces_ready','mix_'+str(cycles-1),'promotion_peak','steady_promoted'):
        values=[x['physicalFootprintBytes']/2**20 for x in data['samples'] if x['stage']==stage]
        print(mode,run_id,stage,round(max(values) if stage=='promotion_peak' else statistics.median(values),2),'MiB',flush=True)
    return data


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--umbra',default=str(REPO/'build/Umbra.app/Contents/MacOS/umbra'))
    parser.add_argument('--mode',choices=['stock','nonvisual','hybrid'],required=True)
    parser.add_argument('--runs',type=int,default=3)
    parser.add_argument('--cycles',type=int,default=3)
    parser.add_argument('--json',required=True,type=Path)
    parser.add_argument('--held-out',action='store_true')
    parser.add_argument('--promote-all',action='store_true')
    options=parser.parse_args()
    if options.runs<1 or options.cycles<1:parser.error('runs/cycles must be positive')
    result=[]
    try:
        for i in range(options.runs):
            path=options.json.with_name(options.json.stem+f'-run{i+1}'+options.json.suffix)
            result.append(run(options.umbra,options.mode,i+1,path,options.cycles,options.held_out,options.promote_all))
    finally:
        options.json.parent.mkdir(parents=True,exist_ok=True)
        options.json.write_text(json.dumps(dict(mode=options.mode,runs=result,measurement='Native unique-PID physical footprint; sampled peaks, not globally deduplicated RAM.'),indent=2)+'\n')


if __name__=='__main__':
    signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(SystemExit(143)))
    main()
