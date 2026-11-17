#!/usr/bin/env python3
"""Matched semantic task coverage; C3 layout, C3 equivalent script, and new DOM."""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import secrets
import shutil
import signal
import statistics
import tempfile
import threading
import time
from pathlib import Path

import hybrid as h
from semantic_site import make_server, contract

REPO = Path(__file__).resolve().parent.parent
SEMANTIC = re.search(r'let semanticJS = #"""(.*?)"""#', (REPO/'Sources/Semantic.swift').read_text(), re.S)[1]
# Equivalent public C3 eval control: no application-private model or injected page globals.
SCRIPT = re.sub(r'const doc = .*?;\n', 'const doc = String(performance.timeOrigin);\n', SEMANTIC, count=1)


def run(binary, mode, output, cycles=10, held=False):
    output.parent.mkdir(parents=True, exist_ok=True)
    name = 'impl-sem-'+secrets.token_hex(5)
    tmp = Path(tempfile.mkdtemp(prefix='umbra-semantic-'))
    (tmp/'sites.json').write_text('[]'); (tmp/'agents').mkdir()
    env = {**os.environ, 'UMBRA_PROFILE':name, 'UMBRA_IDLE':'60', 'UMBRA_AGENTS':str(tmp/'agents'),
           'UMBRA_SITES':str(tmp/'sites.json'), 'UMBRA_LOGOS':str(tmp/'logos'), 'UMBRA_UNSEEN':'1'}
    server = make_server(); threading.Thread(target=server.serve_forever,daemon=True).start()
    origin = f'http://127.0.0.1:{server.server_address[1]}'
    descriptor = tmp/'contract.json'; descriptor.write_text(json.dumps(contract(origin)))
    data = dict(profile=name, mode=mode, binary=binary, binarySHA256=hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
                fixtureSHA256=hashlib.sha256((REPO/'tests/semantic_site.py').read_bytes()+(REPO/'tests/hybrid_site.py').read_bytes()).hexdigest(),
                cycles=cycles, heldOut=held,benchmarkSHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), checks=[], timings=[], transitions=[], stateChecks=[], coverage={}, loadStart=list(os.getloadavg()))
    pid = None
    def check(label, value):
        data['checks'].append(dict(name=label,passed=bool(value)))
        if not value: raise AssertionError(label)
    def timed(label, work):
        start=time.perf_counter(); result=work()
        data['timings'].append(dict(task=label,milliseconds=(time.perf_counter()-start)*1000))
        return result
    try:
        pid=h.metrics.spawn_disclaimed([binary,'serve'],env,tmp/'daemon.log'); data['pid']=pid
        c=h.Client(h.metrics.SUPPORT/f'daemon-{name}.sock')
        for _ in range(200):
            try:
                if c.call('status')['pid']==pid:break
            except OSError:pass
            time.sleep(.025)
        else:raise TimeoutError('daemon startup')
        sampler=h.Sampler(pid); sampler.steady('daemon')
        def api(route,body=None,query=None,expected=True):
            args=dict(action='read' if mode=='dom' and body is None else 'call',name='work',route=route)
            if body is not None:args['data']=json.dumps(body)
            if query is not None:args['query']=json.dumps(query)
            return c.call('api',expected=expected,**args)
        c.call('api',action='open',name='work',contract=str(descriptor))
        # Authentication is explicit, mutating setup; never relabel it a read.
        if mode=='dom':c.call('api',action='call',name='work',route='handshake')
        else:api('handshake')
        api('login',body=dict(username='agent',password='fixture-password'))
        for view in ('inbox','dashboard','settings'):api(view)
        sampler.steady('engine_free_reads')
        check('no_webkit_for_declared_reads',all(p['kind'] not in ('WebContent','GPU') for r in sampler.rows if r['stage']=='engine_free_reads' for p in r['processes']))
        url=origin+'/app/js-only?q=urgent&unread=1'
        c.call('api',action='browser',name='work',url=url,tab='browser')
        def ready():
            c.wait('browser','window.ready===true && document.querySelectorAll("#items article a").length===document.querySelectorAll("#items article").length')
        ready()
        c.value('browser','window.guard="retained";localStorage.setItem("agent-marker","local");sessionStorage.setItem("agent-marker","session");window.readEvents=0;addEventListener("input",()=>readEvents++);addEventListener("change",()=>readEvents++);document.querySelector("#name").value="retained draft";return true')
        def state():
            return c.value('browser','return {url:location.href,title:document.title,epoch:performance.timeOrigin,guard:window.guard??null,draft:document.querySelector("#name")?.value,local:localStorage.getItem("agent-marker"),session:sessionStorage.getItem("agent-marker"),events:window.readEvents??0,revision:document.querySelector("#revision")?.textContent}')
        def window(stage):
            tab=next(t for t in c.call('tabs')['tabs'] if t['tab']=='browser')
            if not data['transitions'] or tab['windowCreated']!=data['transitions'][-1]['windowCreated']:
                data['transitions'].append(dict(stage=stage,windowCreated=tab['windowCreated'],rendered=tab['rendered']))
            return tab
        def structural(action, query=None):
            args=dict(action=action,selector='#items',max=100000)
            if action!='read':args['limit']=200
            if query is not None:args['query']=query
            if mode=='dom':return c.call('dom',tab='browser',**args)
            if mode=='script':
                scriptArgs={**args,'limit':200,'query':query or ''}
                return c.value('browser','const a='+json.dumps(scriptArgs)+';'+SCRIPT)
            if action in ('read','find'):
                text=c.call('text',tab='browser',target='#items')['value']
                if action=='read':return dict(text=' '.join(text.split()))
                return dict(records=[dict(text=line.strip()) for line in text.splitlines() if query.casefold() in line.casefold()])
            links=c.call('links',tab='browser',filter=origin+'/app/js-only?')['links']
            return dict(records=links)
        def oracle():
            with server.state.lock:
                return [dict(x) for x in server.state.messages if 'urgent' in x['subject'].casefold() and x['unread']][:100]
        def expected_text(rows):
            return ' '.join(' '.join((x['subject'],x['preview'],'Mark read' if x['unread'] else 'Read','Open '+str(x['id']))) for x in rows)
        query='Project update 1' if held else 'Project update 7'
        sampler.steady('ready')
        window('ready')
        for cycle in range(cycles):
            for view in ('inbox','dashboard','settings'):
                value=timed('api_'+view,lambda view=view:api(view,query=dict(q='urgent',unread=1)))['data']
                check('api_authoritative_revision',value['revision']==server.state.revision)
            before=state(); rows=oracle()
            read=timed('dom_read',lambda:structural('read'))
            check('exact_read_text',read['text']==expected_text(rows))
            found=timed('dom_find',lambda:structural('find',query))
            check('exact_loaded_search',[r['text'] for r in found['records']]==[x['subject'] for x in rows if query.casefold() in x['subject'].casefold()])
            links=timed('dom_links',lambda:structural('links'))
            check('exact_links',[(r['text'],r['href']) for r in links['records']]==[('Open '+str(x['id']),origin+'/app/js-only?q=urgent&unread=1&detail='+str(x['id'])) for x in rows])
            after=state();data['stateChecks'].append(dict(cycle=cycle,before=before,after=after))
            check('read_state_unchanged',before==after)
            check('read_did_not_promote',not window('read_'+str(cycle))['windowCreated'])
            if mode in ('dom','script'):
                check('full_results_not_truncated',not any(r['truncated'] for r in (read,found,links)))
                check('metadata_current',read['url']==before['url'] and read['title']==before['title'])
                check('same_document_reads',read['doc']==found['doc']==links['doc'])
            if cycle in (2,6):
                chosen=links['records'][min(cycle,len(links['records'])-1)]['href']
                oldEpoch=state()['epoch']
                timed('explicit_url_navigation',lambda:c.call('open',tab='browser',url=chosen))
                ready()
                now=state()
                check('navigation_new_document',now['epoch']!=oldEpoch)
                check('navigation_retains_session_storage',now['local']=='local' and now['session']=='session')
            if cycle==3:
                inbox=api('inbox',query=dict(q='urgent',unread=1))['data']
                api('mark',body=dict(id=inbox['items'][0]['id'],revision=inbox['revision'],requestId=name+'-one-api-write'))
                check('one_explicit_write',len(server.state.audit)==1)
                with server.state.lock:server.state.fresh()
                fresh=api('inbox',query=dict(q='urgent',unread=1))['data']
                check('api_fresh_after_server_change',fresh['items'][0]['id']==9001)
                timed('explicit_url_navigation',lambda:c.call('open',tab='browser',url=url+'&refresh='+str(cycle)));ready()
                check('dom_fresh_after_explicit_navigation','Fresh update' in structural('read')['text'])
            sampler.steady('mix_'+str(cycle))
        data['coverage']=dict(engineFreeReadCalls=3*cycles,structuralReadFindLinkCalls=3*cycles,explicitNavigationCalls=3,linkedNavigationCalls=2,freshnessNavigationCalls=1,
                              promotedReadCalls=0,logicalSurfaces=4,residentBrowserViews=1)
        sampler.steady('before_graphics')
        heldState=state()
        c.value('browser','window.trustedMark=null;document.querySelector("#mark").addEventListener("click",e=>window.trustedMark=e.isTrusted,{once:true});return true')
        def graphics():
            timed('trusted_native_write',lambda:c.call('click',tab='browser',target='#mark'))
            ready()
            check('trusted_native_event',c.value('browser','return trustedMark') is True)
            check('exact_two_writes_no_replay',len(server.state.audit)==2)
            timed('screenshot',lambda:c.call('screenshot',tab='browser',path=str(output.with_suffix('.png'))))
        sampler.peak('graphics_peak',graphics)
        check('graphics_promotes',window('graphics')['windowCreated'])
        check('graphics_same_document',heldState['epoch']==state()['epoch'])
        for _ in range(10):
            timed('warm_native_read',lambda:c.call('click',tab='browser',target='#apply'));ready()
        sampler.steady('post_native_burst');time.sleep(10);sampler.steady('idle10')
        data['tabsRetained']=c.call('tabs')['tabs']
        check('same_view_retained',len(data['tabsRetained'])==1)
        with server.state.lock:
            for session in server.state.sessions.values():session['expires']=0
        check('api_expiry',api('inbox',expected=False)['status']==401)
        c.call('open',tab='browser',url=origin+'/app/js-only?expired=1')
        c.wait('browser','location.pathname==="/login"')
        if mode=='dom':expired=c.call('dom',action='read',tab='browser')['text']
        else:expired=c.call('text',tab='browser')['value']
        check('expired_dom_not_cached','Project update' not in expired and 'password' in expired.casefold())
        data['requests']=[dict(method=m,path=p) for m,p in server.requests]
        data['authoritativeWrites']=len(server.state.audit)
        data['success']=True;data['samples']=sampler.rows
    except BaseException as error:
        data['success']=False;data['error']=type(error).__name__+': '+str(error)
        if 'sampler' in locals():data['samples']=sampler.rows
        raise
    finally:
        data['loadEnd']=list(os.getloadavg())
        if pid:data['cleanup']=h.cleanup(binary,env,name,pid)
        server.shutdown();server.server_close();shutil.rmtree(tmp,ignore_errors=True)
        output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(data,indent=2)+'\n')
    for stage in ('engine_free_reads','before_graphics','graphics_peak','post_native_burst','idle10'):
        values=[r['physicalFootprintBytes']/2**20 for r in data['samples'] if r['stage']==stage]
        print(mode,stage,round(max(values) if stage=='graphics_peak' else statistics.median(values),2),'MiB',flush=True)
    return data


if __name__=='__main__':
    signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(SystemExit(143)))
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--umbra',required=True);p.add_argument('--mode',choices=['layout','script','dom'],required=True)
    p.add_argument('--json',type=Path,required=True);p.add_argument('--cycles',type=int,default=10);p.add_argument('--held-out',action='store_true')
    a=p.parse_args()
    if a.cycles < 7:p.error("cycles must be at least 7 to include navigation and mutation gates")
    run(a.umbra,a.mode,a.json,a.cycles,a.held_out)
