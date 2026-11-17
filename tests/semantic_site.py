"""Signed-in-like semantic-read extension; keeps the earlier fixture unchanged."""
import html
import urllib.parse
from hybrid_site import Handler, make_server as base_server, contract as base_contract

LINKS = """
<script>
function detailLinks(){
 for(const card of document.querySelectorAll('#items article')){
  if(card.querySelector('a'))continue;
  const a=document.createElement('a');a.href='/app/js-only?q=urgent&unread=1&detail='+card.dataset.id;
  a.textContent='Open '+card.dataset.id;card.append(document.createTextNode(' '),a);
 }
}
new MutationObserver(detailLinks).observe(document.querySelector('#items'),{childList:true,subtree:true});
detailLinks();
</script>
"""

class SemanticHandler(Handler):
    def document(self, title, body, mainattrs=''):
        out = super().document(title, body, mainattrs)
        if 'id="items"' in body:
            out = out.replace('</body>', LINKS+'</body>')
        return out

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        self.server.requests.append(('GET', path))
        if path == '/semantic':
            if not self.session():
                return self.send(401, '<p id="signed-out">Signed out</p>', 'text/html')
            body = '''<h1>Read-only notes</h1><main id="notes"><p>Hello <b>world</b> &amp; café 😀</p>
<p class="css-hidden">CSS HIDDEN CONTENT</p><style>.css-hidden{display:none}</style>
<input type="password" value="fixture-private-password"><textarea>fixture-private-textarea</textarea>
<svg><script>const svgPrivate="fixture-private-svg"</script><style>.svgPrivate{}</style><text>SVG note</text></svg><script>const ignored='fixture-private-script'</script><template>fixture-private-template</template>
<a href="/app/inbox">Inbox</a><a href="/app/inbox">duplicate</a>
<a href="https://example.invalid/read">External</a><a href="javascript:void(0)">Unsafe protocol</a>
<a href="http://user:fixture-credential@127.0.0.1/">Credential URL</a>
<iframe srcdoc="<p>FRAME ONLY TEXT</p>"></iframe><div id="shadow"></div></main>
<script>shadow.attachShadow({mode:'open'}).innerHTML='<p>SHADOW ONLY TEXT</p>';
window.ready=true;window.guard='retained';localStorage.setItem('marker','local');sessionStorage.setItem('marker','session');
</script>'''
            return self.send(200, '<!doctype html><meta charset="utf-8">'+body, 'text/html')
        if path == '/semantic-frame':
            body = '<p id="phase">pending</p><canvas id="pixels" width="100" height="80"></canvas><script>window.ready=true;requestAnimationFrame(()=>{phase.textContent="frame-ready";pixels.getContext("2d").fillRect(0,0,100,80)})</script>'
            return self.send(200, '<!doctype html>'+body, 'text/html')
        return super().do_GET()

    def do_HEAD(self):
        path = urllib.parse.urlparse(self.path).path
        self.server.requests.append(('HEAD', path))
        return self.send(204 if self.session() else 401)

    def do_POST(self):
        self.server.requests.append(('POST', urllib.parse.urlparse(self.path).path))
        return super().do_POST()


def make_server():
    server = base_server()
    server.RequestHandlerClass = SemanticHandler
    server.requests = []
    return server


def contract(origin):
    out = base_contract(origin)
    out['browserPaths'] += ['/semantic', '/semantic-frame']
    out['routes']['head'] = dict(method='HEAD', path='/semantic/head')
    out['routes']['headCapture'] = dict(method='HEAD', path='/semantic/head', capture=dict(token='token'))
    return out
