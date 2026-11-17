"""Local synthetic signed-in workbench for low-memory correctness and benchmarks."""
import hashlib
import html
import http.cookies
import http.server
import json
import secrets
import socket
import threading
import time
import urllib.parse

VIEWS = ('inbox', 'dashboard', 'settings', 'js-only')
SUPPORTED = VIEWS[:3]
STYLE = '''<style>body{font:15px system-ui;background:#f5f7fa;color:#243348;margin:0}nav{padding:18px;background:#e7edf5}main{max-width:1040px;margin:auto;padding:24px}button,input,select{font:inherit;margin:5px;padding:7px}article{padding:12px;background:white;border-bottom:1px solid #d9e0e9}article h2{font-size:16px}label{display:inline-block}#items{margin-top:20px}output{font-variant-numeric:tabular-nums}.muted{color:#64748b}.unread{border-left:4px solid #3b82f6}canvas{width:900px;max-width:100%;height:180px}</style>'''
APP_JS = r'''
const view = VIEW;
window.model = null; window.catalog = null; window.ready = false; window.rafCount = 0;
(function frame(){window.rafCount++;requestAnimationFrame(frame)})();
let query = new URLSearchParams(location.search);
const search = document.querySelector('#search'), filter = document.querySelector('#filter');
search.value=query.get('q')||''; filter.checked=query.get('unread')==='1';
const stateURL = view==='js-only'?'/internal/state':'/api/state';
const actionURL = view==='js-only'?'/internal/action':'/api/action';
const status = value => {document.querySelector('#status').textContent=value; document.body.dataset.status=value};
function failed(code){
 status(code===401?'signed-out':code===409?'conflict':code===403?'csrf-error':'error');
 if(code===401){window.model=null;window.ready=false;document.querySelector('#items').replaceChildren();}
}
function render(){
 const m=window.model, list=document.querySelector('#items'); list.replaceChildren();
 document.querySelector('#revision').textContent=String(m.revision);
 document.querySelector('#count').textContent=String(m.count);
 document.querySelector('#name').value=m.preferences.name;
 document.querySelector('#notify').checked=m.preferences.notification;
 // The catalog is the actual cached inbox used by local sort and selection UI.
 const full=window.catalog?.[view==='dashboard'?'tasks':'messages']||m.items;
 const q=search.value.toLowerCase();
 const matching=full.filter(x=>(!q||x.subject.toLowerCase().includes(q))&&(!filter.checked||x.unread));
 window.localMatches=matching.map(x=>x.id);
 // Rendered action state always uses the freshly fetched authoritative query, never stale localMatches.
 for(const item of m.items.slice(0,100)){
  const card=document.createElement('article');card.dataset.id=String(item.id);card.className=item.unread?'unread':'';
  const title=document.createElement('h2');title.textContent=item.subject;card.append(title);
  const p=document.createElement('p');p.textContent=item.preview||('Assigned to '+(item.owner||'agent'));card.append(p);
  const b=document.createElement('button');b.textContent=item.unread?'Mark read':'Read';b.dataset.item=String(item.id);b.className='mark';
  if(!list.querySelector('#mark'))b.id='mark';
  b.onclick=()=>act({kind:'mark',id:item.id});card.append(b);list.append(card);
 }
 if(view==='dashboard'){
  const c=document.querySelector('#chart').getContext('2d');c.clearRect(0,0,900,180);
  const rows=window.catalog?.tasks||m.items;
  for(let i=0;i<Math.min(rows.length,60);i++){c.fillStyle='#4b86b4';const h=(rows[i].id*17)%140+20;c.fillRect(i*15,180-h,10,h)}
 }
 status('ready');window.ready=true;
}
async function refresh(){
 status('loading');window.ready=false;
 const params=new URLSearchParams({view,q:search.value,unread:filter.checked?'1':'0'});
 const response=await fetch(stateURL+'?'+params,{credentials:'same-origin',cache:'no-store'});
 if(!response.ok){failed(response.status);return false}
 window.model=await response.json();render();return true;
}
async function act(fields){
 if(!window.model){status('signed-out');return false}
 status('saving');window.ready=false;
 const payload={view,...fields,csrf:window.model.csrf,revision:window.model.revision,requestId:crypto.randomUUID()};
 const response=await fetch(actionURL,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
 if(!response.ok){failed(response.status);return false}
 window.lastAction=await response.json();return await refresh();
}
window.refresh=refresh;window.act=act;
document.querySelector('#apply').onclick=()=>refresh();
document.querySelector('#save').onclick=()=>act({kind:'prefs',name:document.querySelector('#name').value,notification:document.querySelector('#notify').checked});
(async()=>{
 const boot=await fetch('/internal/bootstrap',{credentials:'same-origin',cache:'no-store'});
 if(!boot.ok){failed(boot.status);return}
 window.catalog=await boot.json();await refresh();
})();
'''


class State:
    def __init__(self):
        self.lock = threading.RLock()
        self.revision = 1
        self.messages = [dict(id=i, subject=('Urgent: ' if i % 7 == 0 else '') + f'Project update {i}', unread=i % 3 != 0,
                              preview=f'Engineering team correspondence {i}: schedule, design decision, next action and review notes.',
                              sender=f'team-{i % 12}@fixture.invalid', received=f'2026-11-{i % 15 + 1:02d}T09:00:00Z') for i in range(1, 2001)]
        self.tasks = [dict(id=10000+i, subject=('Urgent: ' if i % 5 == 0 else '')+f'Design task {i}', unread=i % 4 != 0,
                           owner=f'Team {i % 8}', preview=f'Deliverable {i}: requirements, progress, review and approval.') for i in range(1, 201)]
        self.preferences = dict(name='Synthetic Agent', notification=True)
        self.sessions = {}
        self.prelogin = {}
        self.receipts = {}
        self.audit = []

    def fresh(self):
        self.revision += 1
        self.messages.insert(0, dict(id=9001, subject='Urgent: Fresh update', unread=True,
                                    preview='Authoritative data changed after the agent first read it.', sender='fixture@fixture.invalid', received='2026-11-16T12:00:00Z'))


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def setup(self):
        super().setup()
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def log_message(self, *args):
        pass

    @property
    def state(self):
        return self.server.state

    def cookies(self):
        jar = http.cookies.SimpleCookie()
        try:
            jar.load(self.headers.get('Cookie', ''))
        except http.cookies.CookieError:
            pass
        return {k: m.value for k, m in jar.items()}

    def session(self):
        token = self.cookies().get('sid')
        value = self.state.sessions.get(token)
        return value if value and value['expires'] > time.monotonic() else None

    def body(self):
        n = int(self.headers.get('Content-Length', '0'))
        if n > 1024 * 1024:
            raise ValueError('body-too-large')
        data = self.rfile.read(n)
        if 'application/json' in self.headers.get('Content-Type', ''):
            return json.loads(data or b'{}')
        return {k: v[-1] for k, v in urllib.parse.parse_qs(data.decode(), keep_blank_values=True).items()}

    def send(self, code, value=None, kind='application/json', headers=None):
        if value is None:
            data = b''
        elif isinstance(value, bytes):
            data = value
        elif kind.startswith('text/'):
            data = str(value).encode()
        else:
            data = json.dumps(value, separators=(',', ':')).encode()
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def document(self, title, body, mainattrs=''):
        return '<!doctype html><html><head><meta charset="utf-8"><title>'+html.escape(title)+'</title>'+STYLE+'</head><body><nav>Synthetic Workbench · Inbox · Dashboard · Preferences</nav><main'+mainattrs+'>'+body+'</main></body></html>'

    def state_payload(self, view, q='', unread=False, session=None, internal=False):
        rows = [] if view == 'settings' else self.state.tasks if view == 'dashboard' else self.state.messages
        found = [x for x in rows if (not q or q.lower() in x['subject'].lower()) and (not unread or x['unread'])]
        return dict(view=view, revision=self.state.revision, csrf=session['csrf'], items=found[:100 if internal else 50],
                    count=len(found), preferences=dict(self.state.preferences))

    def login_page(self):
        preauth = secrets.token_urlsafe(24)
        csrf = secrets.token_urlsafe(24)
        self.state.prelogin[preauth] = csrf
        page = self.document('Sign in', '<h1>Sign in to Synthetic Workbench</h1><form method="post" action="/login">'
                             f'<input type="hidden" name="csrf" value="{csrf}">'
                             '<label>Username<input id="username" name="username" autocomplete="username"></label>'
                             '<label>Password<input id="password" type="password" name="password" autocomplete="current-password"></label>'
                             '<button id="login" type="submit">Sign in</button></form>')
        self.send(200, page, 'text/html; charset=utf-8', {'Set-Cookie': f'preauth={preauth}; Path=/; HttpOnly; SameSite=Lax'})

    def app(self, view):
        if not self.session():
            return self.send(303, headers={'Location': '/login'})
        body = f'<h1>{html.escape(view.title())}</h1><p>State revision <output id="revision"></output> · Matches <output id="count"></output> · <output id="status">loading</output></p>'
        body += '<section><label>Search<input id="search" aria-label="Search"></label><label><input type="checkbox" id="filter">Unread only</label><button id="apply">Apply</button></section>'
        body += '<section><label>Display name<input id="name" aria-label="Display name"></label><label><input id="notify" type="checkbox">Notifications</label><button id="save">Save preferences</button></section>'
        body += '<canvas id="chart" width="900" height="180"></canvas><section id="items"></section>'
        script = APP_JS.replace('VIEW', json.dumps(view))
        self.send(200, self.document('Workbench '+view, body+'<script>'+script+'</script>'), 'text/html; charset=utf-8')

    def html_page(self, view, params):
        session = self.session()
        if not session:
            return self.send(401, self.document('Sign in required', '<h1 data-auth="signed-out">Sign in required</h1><a href="/login">Sign in</a>'), 'text/html; charset=utf-8')
        if view == 'js-only':
            return self.send(200, self.document('JS-only shell', '<h1>Interactive application</h1><p>This surface requires the application runtime.</p>', ' data-unsupported="true" data-view="js-only"'), 'text/html; charset=utf-8')
        q = params.get('q', [''])[0]
        unread = params.get('unread', ['0'])[0] == '1'
        value = self.state_payload(view, q, unread, session)
        esc = lambda x: html.escape(str(x), quote=True)
        hidden = lambda name, val: f'<input type="hidden" name="{esc(name)}" value="{esc(val)}">'
        metadata = f'<section id="state" data-view="{esc(view)}" data-revision="{value["revision"]}" data-count="{value["count"]}">'
        body = metadata+f'<h1>{esc(view.title())}</h1><output id="revision">{value["revision"]}</output><output id="count">{value["count"]}</output>'
        body += '<form id="search-form" method="get" action="/html/'+esc(view)+'"><input name="q" value="'+esc(q)+'"><label><input name="unread" type="checkbox" value="1"'+(' checked' if unread else '')+'>Unread only</label><button type="submit">Search</button></form>'
        for item in value['items']:
            body += f'<article data-id="{item["id"]}" data-unread="{str(item["unread"]).lower()}"><h2><span class="subject">{esc(item["subject"])}</span></h2><p>{esc(item.get("preview", ""))}</p>'
            body += '<form method="post" action="/html/action">'+''.join(hidden(k,v) for k,v in dict(view=view,kind='mark',id=item['id'],csrf=session['csrf'],revision=value['revision'],requestId=secrets.token_urlsafe(12),q=q,unread='1' if unread else '0').items())+'<button type="submit">Mark read</button></form></article>'
        body += '<form id="prefs-form" method="post" action="/html/action">'+''.join(hidden(k,v) for k,v in dict(view=view,kind='prefs',csrf=session['csrf'],revision=value['revision'],requestId=secrets.token_urlsafe(12),q=q,unread='1' if unread else '0').items())
        body += '<input id="name" name="name" value="'+esc(value['preferences']['name'])+'"><label><input id="notify" name="notification" type="checkbox" value="1"'+(' checked' if value['preferences']['notification'] else '')+'>Notifications</label><button type="submit">Save</button></form></section>'
        self.send(200, self.document('HTML '+view, body, f' data-view="{esc(view)}" data-revision="{value["revision"]}" data-count="{value["count"]}"'), 'text/html; charset=utf-8')

    def action(self, payload, internal=False):
        session = self.session()
        if not session:
            return 401, dict(error='signed-out')
        view = payload.get('view', 'inbox')
        if view not in SUPPORTED and not (internal and view == 'js-only'):
            return 404, dict(error='unsupported', capability='requires-browser')
        if payload.get('csrf') != session['csrf']:
            return 403, dict(error='csrf-error')
        rid = payload.get('requestId')
        if not isinstance(rid, str) or not rid or len(rid) > 128:
            return 400, dict(error='requestId-required')
        canonical = {k: v for k,v in payload.items() if k not in ('q','unread','csrf')}
        fingerprint = hashlib.sha256(json.dumps(canonical,sort_keys=True).encode()).hexdigest()
        if rid in self.state.receipts:
            old = self.state.receipts[rid]
            return (200, dict(old['value'], replay=True)) if old['fingerprint'] == fingerprint else (409, dict(error='idempotency-conflict'))
        try:
            version = int(payload.get('revision', -1))
        except (TypeError, ValueError):
            version = -1
        if version != self.state.revision:
            return 409, dict(error='conflict', revision=self.state.revision)
        kind = payload.get('kind')
        if kind == 'mark':
            try:
                item_id = int(payload.get('id', -1))
            except (TypeError, ValueError):
                item_id = -1
            rows = self.state.tasks if view == 'dashboard' else self.state.messages
            item = next((x for x in rows if x['id'] == item_id), None)
            if item is None:
                return 404, dict(error='item-not-found')
            item['unread'] = False
            result = dict(id=item_id, unread=False)
        elif kind == 'prefs':
            name = str(payload.get('name','')).strip()
            if not name or len(name)>80:
                return 422, dict(error='invalid-name')
            raw = payload.get('notification', False)
            notification = raw is True or str(raw).lower() in ('1','true','on')
            self.state.preferences.update(name=name,notification=notification)
            result = dict(self.state.preferences)
        else:
            return 400, dict(error='unsupported-action')
        self.state.revision += 1
        value = dict(revision=self.state.revision,result=result,replay=False)
        self.state.receipts[rid] = dict(fingerprint=fingerprint,value=value)
        self.state.audit.append(dict(kind=kind,view=view,revision=self.state.revision,requestId=rid))
        return 200, value

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        params = urllib.parse.parse_qs(url.query,keep_blank_values=True)
        path = url.path
        with self.state.lock:
            if path == '/api/session':
                preauth, csrf = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
                self.state.prelogin[preauth] = csrf
                return self.send(200, dict(csrf=csrf), headers={'Set-Cookie': f'preauth={preauth}; Path=/; HttpOnly; SameSite=Lax'})
            if path == '/api/secret-echo':
                session = self.session()
                if not session:
                    return self.send(401, dict(error='signed-out'))
                sid = self.cookies().get('sid')
                # Deliberate authenticated redaction sentinel; never logged or returned to test output.
                return self.send(200, {sid: {'nested': [{session['csrf']: session['csrf']}], 'value': sid}, 'ordinary': 'safe'})
            if path in ('/api/short-cookie', '/api/short-flag'):
                marker = 'locale=en' if path == '/api/short-cookie' else 'flag=1'
                return self.send(200, dict(department='Engineering', project='Project123', description='engineering weekend'),
                                 headers={'Set-Cookie': marker+'; Path=/; HttpOnly; SameSite=Lax'})
            if path == '/api/scoped-redirect':
                nonce = secrets.token_urlsafe(24)
                return self.send(302, headers={'Location': '/outside', 'Set-Cookie': f'scopednonce={nonce}; Path=/api; HttpOnly; SameSite=Lax'})
            if path in ('/outside', '/api/scoped-check'):
                return self.send(200, dict(scopedCookiePresent='scopednonce' in self.cookies()))
            if path == '/api/redirect':
                return self.send(307, headers={'Location': params.get('target', ['/api/state?view=inbox'])[0]})
            if path == '/api/received':
                self.server.received_requests.append(dict(cookiePresent=bool(self.headers.get('Cookie'))))
                return self.send(200, dict(received=True))
            if path in ('/','/login'):
                return self.login_page()
            if path == '/admin/truth':
                return self.send(200,dict(revision=self.state.revision,messageCount=len(self.state.messages),unreadCount=sum(x['unread'] for x in self.state.messages),preferences=self.state.preferences,actions=self.state.audit,items=[dict(id=x['id'],subject=x['subject'],unread=x['unread']) for x in self.state.messages],tasks=[dict(id=x['id'],subject=x['subject'],unread=x['unread']) for x in self.state.tasks]))
            if path.startswith('/app/'):
                view = path.rsplit('/',1)[-1]
                return self.app(view) if view in VIEWS else self.send(404,dict(error='not-found'))
            if path.startswith('/html/') and path != '/html/action':
                view = path.rsplit('/',1)[-1]
                return self.html_page(view,params) if view in VIEWS else self.send(404,dict(error='not-found'))
            if path in ('/api/state','/internal/state','/internal/bootstrap'):
                session = self.session()
                if not session:
                    return self.send(401,dict(error='signed-out'))
                if path == '/internal/bootstrap':
                    return self.send(200,dict(messages=self.state.messages,tasks=self.state.tasks,revision=self.state.revision))
                view = params.get('view',['inbox'])[0]
                internal = path.startswith('/internal/')
                if view not in SUPPORTED and not (internal and view=='js-only'):
                    return self.send(404,dict(error='unsupported',capability='requires-browser'))
                return self.send(200,self.state_payload(view,params.get('q',[''])[0],params.get('unread',['0'])[0]=='1',session,internal))
            return self.send(404,dict(error='not-found'))

    def do_POST(self):
        try:
            payload = self.body()
        except (ValueError,json.JSONDecodeError):
            return self.send(400,dict(error='invalid-body'))
        path = urllib.parse.urlsplit(self.path).path
        if path in ('/api/slow-action', '/api/drop-action', '/api/malformed-action'):
            with self.state.lock:
                code, value = self.action(payload)
            self.server.action_committed.set()
            if path == '/api/malformed-action':
                if code == 200 and not value.get('replay'):
                    return self.send(200, b'{"committed":', 'application/json')
                return self.send(code, value)
            if path == '/api/drop-action':
                self.close_connection = True
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                return
            time.sleep(1)
            return self.send(code, value)
        with self.state.lock:
            if path in ('/api/rotate', '/api/logout'):
                session = self.session()
                if not session:
                    return self.send(401, dict(error='signed-out'))
                if payload.get('csrf') != session['csrf']:
                    return self.send(403, dict(error='csrf-error'))
                old = self.cookies().get('sid')
                self.state.sessions.pop(old, None)
                if path == '/api/logout':
                    return self.send(200, dict(signedOut=True), headers={'Set-Cookie': 'sid=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax'})
                sid = secrets.token_urlsafe(32)
                self.state.sessions[sid] = session
                return self.send(200, dict(rotated=True), headers={'Set-Cookie': f'sid={sid}; Path=/; HttpOnly; SameSite=Lax'})
            if path == '/api/session':
                pre = self.cookies().get('preauth')
                if not pre or payload.get('csrf') != self.state.prelogin.pop(pre, None):
                    return self.send(403, dict(error='csrf-error'))
                if payload.get('username') != 'agent' or payload.get('password') != 'fixture-password':
                    return self.send(401, dict(error='invalid-credentials'))
                sid = secrets.token_urlsafe(32)
                self.state.sessions[sid] = dict(csrf=secrets.token_urlsafe(24), expires=time.monotonic()+3600)
                return self.send(200, dict(authenticated=True, csrf=self.state.sessions[sid]['csrf']), headers={'Set-Cookie': f'sid={sid}; Path=/; HttpOnly; SameSite=Lax'})
            if path == '/login':
                pre = self.cookies().get('preauth')
                if not pre or payload.get('csrf') != self.state.prelogin.pop(pre,None):
                    return self.send(403,dict(error='csrf-error'))
                if payload.get('username')!='agent' or payload.get('password')!='fixture-password':
                    return self.send(401,dict(error='invalid-credentials'))
                sid = secrets.token_urlsafe(32)
                self.state.sessions[sid] = dict(csrf=secrets.token_urlsafe(24),expires=time.monotonic()+3600)
                return self.send(303,headers={'Location':'/app/inbox','Set-Cookie':f'sid={sid}; Path=/; HttpOnly; SameSite=Lax'})
            if path == '/admin/expire':
                for session in self.state.sessions.values():
                    session['expires'] = 0
                return self.send(200,dict(expired=True))
            if path == '/admin/bump':
                self.state.fresh()
                return self.send(200,dict(revision=self.state.revision,insertedId=9001))
            if path in ('/api/action','/internal/action','/html/action'):
                code,value = self.action(payload,internal=path.startswith('/internal/'))
                if path == '/html/action' and code == 200:
                    view=payload.get('view','inbox');query=urllib.parse.urlencode(dict(q=payload.get('q',''),unread=payload.get('unread','0')))
                    return self.send(303,headers={'Location':f'/html/{view}?{query}'})
                return self.send(code,value)
            return self.send(404,dict(error='not-found'))


def make_server():
    server = Server(('127.0.0.1',0),Handler)
    server.state = State()
    server.received_requests = []
    server.action_committed = threading.Event()
    return server


create = make_server


def contract(origin):
    """Explicit route declaration; token captures never enter benchmark output."""
    routes = {
        'handshake': dict(method='GET', path='/api/session', capture={'preCsrf': 'csrf'}),
        'login': dict(method='POST', path='/api/session', defaults={'csrf': '$token:preCsrf'}, capture={'csrf': 'csrf'}, authentication=True),
    }
    for view in SUPPORTED:
        routes[view] = dict(method='GET', path='/api/state?view='+view, capture={'csrf': 'csrf'})
    for name, path in [('mark', '/api/action'), ('slow', '/api/slow-action'), ('drop', '/api/drop-action'), ('malformed', '/api/malformed-action')]:
        routes[name] = dict(method='POST', path=path, defaults={'view': 'inbox', 'kind': 'mark', 'csrf': '$token:csrf'}, versionField='revision', idempotencyField='requestId')
    routes['badcsrf'] = dict(method='POST', path='/api/action', defaults={'view': 'inbox', 'kind': 'mark', 'csrf': 'invalid'}, versionField='revision', idempotencyField='requestId')
    routes['prefs'] = dict(method='POST', path='/api/action', defaults={'view': 'settings', 'kind': 'prefs', 'csrf': '$token:csrf'}, versionField='revision', idempotencyField='requestId')
    routes['logout'] = dict(method='POST', path='/api/logout', defaults={'csrf': '$token:csrf'})
    routes['rotate'] = dict(method='POST', path='/api/rotate', defaults={'csrf': '$token:csrf'})
    routes['js-only'] = dict(method='GET', path='/api/state?view=js-only')
    routes['redirect'] = dict(method='GET', path='/api/redirect')
    routes['scoped-redirect'] = dict(method='GET', path='/api/scoped-redirect')
    routes['scoped-check'] = dict(method='GET', path='/api/scoped-check')
    routes['secret-echo'] = dict(method='GET', path='/api/secret-echo')
    routes['short-cookie'] = dict(method='GET', path='/api/short-cookie')
    routes['short-flag'] = dict(method='GET', path='/api/short-flag')
    return dict(origin=origin, browserPaths=['/app/js-only', '/app/inbox'], routes=routes)
