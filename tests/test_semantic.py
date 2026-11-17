"""Explicit structural reads, never a replacement for rendered/trusted actions."""
import json
import queue
import socket
import subprocess
import threading
import time
import unittest
from pathlib import Path

import test_umbra as base
import test_low_memory as low
import semantic_site as site


class SemanticTests(base.UmbraCase):
    rpc = low.LowMemoryTests.rpc
    value = low.LowMemoryTests.value
    wait = low.LowMemoryTests.wait
    admin = low.LowMemoryTests.admin
    api = low.LowMemoryTests.api
    open_api = low.LowMemoryTests.open_api
    login_api = low.LowMemoryTests.login_api

    def setUp(self):
        super().setUp()
        self.web = site.make_server()
        threading.Thread(target=self.web.serve_forever, daemon=True).start()
        self.origin = f'http://127.0.0.1:{self.web.server_address[1]}'
        self.contract = self.tmp/'contract.json'
        self.contract.write_text(json.dumps(site.contract(self.origin)))
        self.assertExit(self.umbra('tabs'), 0)
        self.path = base.LOCKS/f'daemon-{self.profile}.sock'
        self.counter = 0

    def tearDown(self):
        self.umbra('stop')
        self.web.shutdown()
        self.web.server_close()

    def page(self, path='/semantic', tab='notes', **owner):
        self.open_api(**owner)
        self.api('browser', url=self.origin+path, tab=tab, **owner)
        self.wait(tab, **owner)
        return tab

    def test_structural_read_find_links_exclusions_and_no_promotion(self):
        tab = self.page()
        state = self.value(tab, 'window.events=0;addEventListener("input",()=>events++);return {guard,local:localStorage.marker,session:sessionStorage.marker,scrollY}')
        read = self.rpc('dom', action='read', selector='#notes', tab=tab)
        self.assertIn('Hello world & café 😀', read['text'])
        self.assertIn('CSS HIDDEN CONTENT', read['text'])
        for private in ('fixture-private', 'FRAME ONLY TEXT', 'SHADOW ONLY TEXT'):
            self.assertNotIn(private, read['text'])
        self.assertFalse(read['visibilityChecked'])
        self.assertFalse(read['layoutChecked'])
        found = self.rpc('dom', action='find', selector='#notes', query='café', tab=tab)
        self.assertEqual([r['text'] for r in found['records']], ['& café 😀'])
        links = self.rpc('dom', action='links', selector='#notes', tab=tab)
        self.assertEqual([r['href'] for r in links['records']], [self.origin+'/app/inbox','https://example.invalid/read'])
        self.assertEqual(links['skippedLinks'], 2)
        self.assertEqual(read['doc'], found['doc'])
        self.assertEqual(read['doc'], links['doc'])
        self.assertTrue(all(not r['windowCreated'] for r in (read,found,links)))
        self.assertEqual(state, self.value(tab, 'return {guard,local:localStorage.marker,session:sessionStorage.marker,scrollY}'))
        self.assertEqual(self.value(tab, 'return events'), 0)
        self.assertFalse(self.rpc('status')['profileStoreUsed'])

    def test_limits_errors_and_live_dom_freshness(self):
        tab = self.page()
        for args in (dict(action='find'),dict(action='read',query='x'),dict(action='read',max=0),dict(action='links',limit=201)):
            self.assertEqual(self.rpc('dom',tab=tab,expected=False,**args)['code'],64)
        invalid = self.rpc('dom',action='read',selector='[',tab=tab,expected=False)
        self.assertEqual(invalid['kind'],'invalid_selector')
        ambiguous = self.rpc('dom',action='read',selector=':hover',tab=tab,expected=False)
        self.assertEqual(ambiguous['kind'],'needs_browser')
        before = self.rpc('dom',action='read',selector='#notes',max=5,tab=tab)
        self.assertTrue(before['truncated'])
        self.assertLessEqual(len(before['text']),5)
        self.value(tab, 'const p=document.createElement("p");p.textContent="New server-like update";document.querySelector("#notes").append(p);return true')
        after = self.rpc('dom',action='find',query='New server-like',tab=tab)
        self.assertEqual(after['records'][0]['text'],'New server-like update')
        self.assertEqual(before['doc'],after['doc'])
        self.assertFalse(after['windowCreated'])
        self.value(tab,'const f=document.createDocumentFragment();for(let i=0;i<51000;i++){const p=document.createElement("p");p.textContent="bounded";f.append(p)}document.body.replaceChildren(f);return true')
        limited = self.rpc('dom',action='read',max=100000,tab=tab)
        self.assertTrue(limited['truncated'])
        self.assertLessEqual(limited['visited'],50000)
        self.assertLessEqual(limited['scannedCharacters'],1000000)

    def test_navigation_preserves_auth_and_changes_document_token_without_render(self):
        tab = self.page()
        first = self.rpc('dom',action='read',tab=tab)
        self.rpc('open',url=self.origin+'/semantic?next=1',tab=tab)
        self.wait(tab)
        second = self.rpc('dom',action='read',tab=tab)
        self.assertNotEqual(first['doc'],second['doc'])
        self.assertIn('Read-only notes',second['text'])
        self.assertFalse(second['windowCreated'])
        self.assertEqual(self.value(tab,'return localStorage.marker'),'local')
        self.admin('expire')
        self.rpc('open',url=self.origin+'/semantic?expired=1',tab=tab)
        expired = self.rpc('dom',action='read',tab=tab)
        self.assertEqual(expired['text'],'Signed out')
        self.assertNotEqual(second['doc'],expired['doc'])
        self.assertEqual(self.api('read',route='inbox',expected=False)['status'],401)

    def test_api_read_method_gate_is_before_dispatch_and_still_fresh(self):
        self.open_api()
        first = self.api('read',route='inbox')
        self.assertEqual(first['backend'],'api')
        self.assertEqual(self.api('read',route='head')['status'],204)
        before = len(self.web.requests)
        for route,args in [('mark',{}),('login',{}),('prefs',{}),('headCapture',{}),('inbox',dict(data='{}'))]:
            self.api('read',route=route,expected=False,**args)
        self.assertEqual(len(self.web.requests),before)
        self.assertEqual(len(self.admin()['actions']),0)
        self.admin('bump')
        second = self.api('read',route='inbox')
        self.assertGreater(second['version'],first['version'])
        self.assertFalse(self.rpc('status')['profileStoreUsed'])
        self.assertEqual(self.rpc('tabs')['tabs'],[])

    def test_unchanged_read_new_reply_and_document_metadata(self):
        tab = self.page()
        first=self.rpc('dom',action='read',selector='#notes',tab=tab)
        unchanged=self.rpc('dom',action='read',selector='#notes',tab=tab)
        self.assertEqual(first['records'],unchanged['records'])
        self.assertEqual(first['doc'],unchanged['doc'])
        self.value(tab, 'document.title="New inbox title";history.pushState({},"","#reply");return true')
        meta=self.rpc('dom',action='read',selector='#notes',tab=tab)
        self.assertEqual(first['records'],meta['records'])
        self.assertEqual(first['doc'],meta['doc'])
        self.assertNotEqual(first['title'],meta['title'])
        self.assertNotEqual(first['url'],meta['url'])
        self.value(tab, 'const p=document.createElement("p");p.textContent="New reply from synthetic teammate";document.querySelector("#notes").append(p);return true')
        reply=self.rpc('dom',action='find',query='New reply',tab=tab)
        self.assertEqual(reply['records'][0]['text'],'New reply from synthetic teammate')
        self.assertFalse(reply['windowCreated'])

    def test_namespace_exclusions_and_exact_budget(self):
        tab=self.page()
        read=self.rpc('dom',action='read',selector='#notes',tab=tab)
        self.assertNotIn('fixture-private-svg',read['text'])
        found=self.rpc('dom',action='find',query='fixture-private-svg',tab=tab)
        self.assertEqual(found['records'],[])
        self.value(tab, 'document.body.innerHTML="<p>hello</p><p>hello again</p>";return true')
        exact=self.rpc('dom',action='find',query='hello',max=5,tab=tab)
        self.assertTrue(exact['truncated'])
        self.assertEqual(len(exact['records']),1)

    def test_rejected_node_budget_metadata_caps_and_gone_promotion(self):
        tab=self.page()
        self.value(tab, 'document.title="T".repeat(20000);const f=document.createDocumentFragment();for(let i=0;i<51000;i++)f.append(document.createComment("ignored"));document.body.replaceChildren(f);return true')
        limited=self.rpc('dom',action='read',max=100000,tab=tab)
        self.assertTrue(limited['truncated'])
        self.assertLessEqual(limited['visited'],50000)
        self.assertLessEqual(len(limited['title']),4096)
        self.assertTrue(limited['metadataTruncated']['title'])
        self.value(tab, 'const el=document.createElement("x-"+"long".repeat(2000));el.id="long";el.textContent="bounded";document.body.replaceChildren(el);return true')
        tag=self.rpc('dom',action='read',selector='#long',tab=tab)
        self.assertLessEqual(len(tag['records'][0]['tag']),128)
        self.rpc('wait',gone='#not-present',tab=tab)
        self.assertTrue(self.rpc('tabs')['tabs'][0]['windowCreated'])

    def test_owner_and_graphics_frame_fallback(self):
        tab = self.page('/semantic-frame',agent='owner',task='task')
        self.rpc('dom',action='read',tab=tab,expected=False,agent='other',task='task')
        before = self.rpc('dom',action='read',selector='#phase',tab=tab,agent='owner',task='task')
        self.assertEqual(before['text'],'pending')
        self.assertFalse(before['windowCreated'])
        # A structural read does not pretend a frame-dependent task is complete.
        self.rpc('get',target='#pixels',attr='box',tab=tab,agent='owner',task='task')
        self.wait(tab,'document.querySelector("#phase").textContent==="frame-ready"',agent='owner',task='task')
        after = self.rpc('dom',action='read',selector='#phase',tab=tab,agent='owner',task='task')
        self.assertEqual(after['text'],'frame-ready')
        self.assertEqual(before['doc'],after['doc'])
        self.assertTrue(after['windowCreated'])
        path=self.tmp/'frame.png'
        self.rpc('screenshot',tab=tab,path=str(path),agent='owner',task='task')
        self.assertGreater(path.stat().st_size,1000)

    def test_cli_mcp_share_semantic_session(self):
        tab = self.page()
        cli=self.umbra('dom','find','--query','café','--selector','#notes','--tab',tab,'--json')
        self.assertExit(cli,0)
        expected=json.loads(cli.text)
        api_cli=self.umbra('api','read','work','inbox','--json')
        self.assertExit(api_cli,0)
        self.assertGreater(json.loads(api_cli.text)['data']['count'],0)
        proc=base.spawn('mcp',profile=self.profile,stdin=subprocess.PIPE)
        inbox=queue.Queue()
        def read():
            for line in proc.stdout:inbox.put(json.loads(line))
        threading.Thread(target=read,daemon=True).start()
        def rpc(id,method,params):
            proc.stdin.write((json.dumps(dict(jsonrpc='2.0',id=id,method=method,params=params))+'\n').encode());proc.stdin.flush()
            out=inbox.get(timeout=15);self.assertEqual(out['id'],id);return out
        try:
            rpc('init','initialize',dict(protocolVersion='2025-06-18'))
            tools=rpc('tools','tools/list',{})['result']['tools']
            self.assertIn('dom',{x['name'] for x in tools})
            self.assertTrue(next(x for x in tools if x['name']=='dom')['annotations']['readOnlyHint'])
            self.assertFalse(next(x for x in tools if x['name']=='api')['annotations']['readOnlyHint'])
            api_read=rpc('api-read','tools/call',dict(name='api',arguments=dict(action='read',name='work',route='inbox')))['result']
            self.assertFalse(api_read.get('isError',False))
            result=rpc('read','tools/call',dict(name='dom',arguments=dict(action='find',query='café',selector='#notes',tab=tab)))['result']
            self.assertFalse(result.get('isError',False))
            structured=result.get('structuredContent')
            if structured is None:structured=json.loads(result['content'][0]['text'])
            self.assertEqual(structured['records'],expected['records'])
            self.assertEqual(structured['doc'],expected['doc'])
        finally:
            proc.stdin.close()
            try:proc.wait(5)
            except subprocess.TimeoutExpired:proc.kill();proc.wait()

    def test_implementation_has_no_render_or_action_queries(self):
        source=(base.REPO/'Sources/Semantic.swift').read_text()
        for forbidden in ('innerText','getBoundingClientRect','getComputedStyle','elementFromPoint','scrollIntoView','dispatchEvent','.click('):
            self.assertNotIn(forbidden,source)


if __name__=='__main__':
    base.main()
