#!/usr/bin/env python3
"""Bounded localhost soak of a frozen candidate; default 24h, no daemon/profile survives cleanup.

Launch only after candidate freeze:
 python3 bench/soak.py --umbra /abs/umbra --sha256 HASH --out /tmp/runtime-soak
A short --seconds run validates plumbing, never the 24-hour stability target. live.json records an
actual PID and heartbeat; final.json and cleanup.json are authoritative completion evidence.
"""
import argparse
import json
import math
import os
import signal
import statistics
import threading
import time
from pathlib import Path

import runtime


def append(stream, value):
    stream.write(json.dumps(value, sort_keys=True) + '\n'); stream.flush()


def snapshot(pid):
    members = []
    for member, kind in runtime.memory.family(pid).items():
        used = runtime.memory.usage(member)
        if used is not None: members.append({'pid':member,'kind':kind,'physicalFootprintBytes':used[0],'cpuSeconds':used[1]})
    return {'physicalFootprintBytes':sum(m['physicalFootprintBytes'] for m in members),'members':members}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--umbra',required=True); parser.add_argument('--sha256',required=True); parser.add_argument('--out',required=True)
    parser.add_argument('--route',choices=['matched','public_http'],default='public_http')
    parser.add_argument('--competing-workload',default='none declared',help='Explicit concurrent evaluation/load description retained in manifest')
    parser.add_argument('--memory-budget-mib',type=float,default=2048)
    parser.add_argument('--seconds',type=float,default=86400); parser.add_argument('--interval',type=float,default=10)
    args=parser.parse_args()
    if not 1<=args.seconds<=86400 or not .1<=args.interval<=25: parser.error('seconds must be1–86400 and interval0.1–25')
    if not math.isfinite(args.memory_budget_mib) or not 1<=args.memory_budget_mib<=2048: parser.error('memory budget must be 1–2048 MiB')
    if runtime.digest(args.umbra)!=args.sha256: parser.error('candidate SHA256 does not match freeze')
    out=Path(args.out);out.mkdir(parents=True,exist_ok=False)
    specification=json.loads(runtime.WORKLOAD.read_text())
    runtime.write(out/'manifest.json',{'binary':str(Path(args.umbra).resolve()),'binarySha256':args.sha256,
        'workloadSha256':runtime.digest(runtime.WORKLOAD),'harnessSha256':runtime.digest(__file__),
        'runtimeHarnessSha256':runtime.digest(runtime.__file__),'plannedSeconds':args.seconds,'interval':args.interval,'route':args.route,'memoryBudgetBytes':args.memory_budget_mib*2**20,'competingWorkload':args.competing_workload,
        'pid':os.getpid(),'startedUTC':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
        'claim':'A short run is plumbing evidence only.24-hour gate requires elapsed>=86400, no task/health failures, successful cleanup and stable post-warmup resource samples.'})
    site=runtime.Site(specification)
    try: session=runtime.Session(args.umbra,out/'browser')
    except Exception:
        site.close(); raise
    guard_records=(out/'resource-guard.jsonl').open('w')
    guard=runtime.Sampler(session,1,args.memory_budget_mib*2**20,max_samples=1,sample_sink=guard_records)
    interrupted=threading.Event()
    def stop(signum,frame): interrupted.set()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    started=time.monotonic();deadline=started+args.seconds;cycle=0;failures=0;consecutive=0;footprints=[];error=None
    records=(out/'samples.jsonl').open('w')
    try:
        with runtime.Client(session) as client:
            client.call('open',tab='live',url=site.base+'/app',until='window.jsTotal===210')
            while time.monotonic()<deadline and not interrupted.is_set():
                if runtime.digest(args.umbra)!=args.sha256: raise runtime.Failure('candidate binary changed during soak')
                cycle_started=time.monotonic();cycle_errors=[]
                for kind in specification['task_order']:
                    token=f'soak-{cycle}-{kind}'
                    try: runtime.task(client,site,kind,'live',token,args.route)
                    except Exception as exc: cycle_errors.append({'task':kind,'error':str(exc)})
                if cycle%5==0:
                    try:
                        client.call('open',tab='ephemeral',url=site.base+'/app',until='window.jsTotal===210')
                        if client.call('eval',tab='ephemeral',script='return window.jsTotal').get('value')!=210: raise runtime.Failure('ephemeral output mismatch')
                        client.call('tab',action='close',name='ephemeral')
                    except Exception as exc: cycle_errors.append({'task':'tab-lifecycle','error':str(exc)})
                state=client.call('runtime')
                if state.get('queued')!=0 or state.get('active')!=0: cycle_errors.append({'task':'health','error':'runtime did not return to idle'})
                if state.get('tabs')!=1: cycle_errors.append({'task':'health','error':f"unexpected tab count {state.get('tabs')}"})
                with session.lock: session.commands.clear()
                sample=snapshot(session.pid);footprints.append((time.monotonic()-started,sample['physicalFootprintBytes']))
                row={'cycle':cycle,'elapsedSeconds':time.monotonic()-started,'cycleMs':(time.monotonic()-cycle_started)*1000,
                     'runtime':state,'errors':cycle_errors,'loadAverage':os.getloadavg(),**sample}
                append(records,row)
                failures+=len(cycle_errors);consecutive=consecutive+1 if cycle_errors else 0
                runtime.write(out/'live.json',{'runnerPid':os.getpid(),'daemonPid':session.pid,'profile':session.profile,
                    'binarySha256':args.sha256,'cycle':cycle,'elapsedSeconds':row['elapsedSeconds'],
                    'heartbeatUTC':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'failures':failures,'terminal':False})
                if consecutive>=3: raise runtime.Failure('three consecutive unhealthy soak cycles')
                cycle+=1
                interrupted.wait(min(args.interval,max(0,deadline-time.monotonic())))
    except Exception as exc:
        error=str(exc)
    finally:
        elapsed=time.monotonic()-started
        records.close();guard.close();guard_records.close();session.close();site.close()
        post_cleanup=[]
        for member in (sample.get('members',[]) if 'sample' in locals() else []):
            used=runtime.memory.usage(member['pid'])
            post_cleanup.append({'pid':member['pid'],'kind':member['kind'],'alive':used is not None,'physicalFootprintBytes':used[0] if used else 0})
        warm=[value for t,value in footprints if t>=min(600,args.seconds/4)]
        window=max(1,len(warm)//10)
        early=statistics.median(warm[:window]) if warm else None
        late=statistics.median(warm[-window:]) if warm else None
        stability={'postWarmupSamples':len(warm),'earlyMedianBytes':early,'lateMedianBytes':late,
                   'lateOverEarly':late/early if early and late else None,
                   'gate':'late/early<=1.10 plus no sustained positive slope; inspect raw series and process lifecycle, not this ratio alone'}
        final={'terminal':True,'runnerPid':os.getpid(),'daemonPid':session.pid,'binarySha256':args.sha256,
            'plannedSeconds':args.seconds,'elapsedSeconds':elapsed,'cycles':cycle,'failures':failures,'error':error,
            'budgetAbort':session.abort_reason,'interrupted':interrupted.is_set(),'completedRequestedDuration':elapsed>=args.seconds and not interrupted.is_set() and error is None,
            'cleanup':session.cleanup,'postCleanupKnownMembers':post_cleanup,'stability':stability,
            'twentyFourHourDurationGate':args.seconds==86400 and elapsed>=86400 and not interrupted.is_set() and error is None and failures==0,
            'note':'Duration gate alone does not prove stable resource use. Inspect raw samples, process churn, plateau and cleanup.'}
        runtime.write(out/'final.json',final);runtime.write(out/'live.json',final)
        print(json.dumps(final,sort_keys=True))
    return 0 if error is None and failures==0 and not interrupted.is_set() and session.cleanup['daemonGone'] and session.cleanup['profileFilesGone'] else 1


if __name__=='__main__': raise SystemExit(main())
