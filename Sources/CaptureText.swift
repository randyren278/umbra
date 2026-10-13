// The page as text: innerText, markdown (whole page or just the main content), HTML and links.
// (Capture lane.)
import WebKit

let targetParam = Param(name: "target", kind: .string, help: "One element: an e<N> ref from `snapshot`, text=…, or a CSS selector (default: the whole page)")

/// HTML to markdown, in umbraWorld. Block elements become paragraphs, headings, lists, fenced
/// code, quotes and GFM tables; links and images get absolute URLs. With `main`, only the main
/// content: <main> or <article> when the page has them, else the densest block of prose, without
/// navigation, sidebars and the site's header and footer.
let markdownJS = #"""
const BLOCK = new Set('html address article aside blockquote body caption center dd details dialog dir div dl dt fieldset figcaption figure footer form h1 h2 h3 h4 h5 h6 header hgroup hr legend li main menu nav ol p pre section summary table tbody td tfoot th thead tr ul'.split(' '));
const SKIP = new Set('script style noscript template svg canvas iframe object embed video audio select textarea input button head link meta map'.split(' '));
const chrome = el => {
  const tag = el.localName, role = el.getAttribute('role');
  if (tag === 'nav' || tag === 'aside' || ['navigation', 'complementary', 'search', 'banner', 'contentinfo'].includes(role)) return true;
  if (tag !== 'header' && tag !== 'footer') return false;
  return !el.parentElement?.closest('article, main, section, [role=main]') && !(tag === 'header' && el.querySelector('h1'));
};
const skip = el => {
  if (SKIP.has(el.localName) || el.hidden || (main && chrome(el))) return true;
  const style = getComputedStyle(el);
  return style.display === 'none' || style.visibility === 'hidden';
};
const clean = s => s.replace(/[ \t ]+/g, ' ').replace(/ ?\n ?/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
const url = s => s.replace(/ /g, '%20').replace(/\(/g, '%28').replace(/\)/g, '%29');
const wrap = (s, mark) => {
  const t = s.trim();
  return t ? (/^\s/.test(s) ? ' ' : '') + mark + t + mark + (/\s$/.test(s) ? ' ' : '') : s;
};

function inline(node) {
  let s = '';
  for (const c of node.childNodes) {
    if (c.nodeType === 3) s += c.data.replace(/\s+/g, ' ');
    else if (c.nodeType === 1 && !skip(c)) s += BLOCK.has(c.localName) ? ' ' + inline(c) + ' ' : element(c);
  }
  return s;
}

function element(el) {
  const tag = el.localName;
  if (tag === 'br') return '\n';
  if (tag === 'img') {
    if (el.complete && el.naturalWidth === 1 && el.naturalHeight === 1) return '';  // tracking pixel
    const alt = (el.alt || el.title || '').replace(/\s+/g, ' ').trim();
    const src = el.currentSrc || el.src || '';
    if (src.startsWith('data:') || !src) return alt ? `![${alt}]()` : '';
    return `![${alt}](${url(src)})`;
  }
  if (tag === 'a') {
    const text = clean(inline(el)).replace(/\n+/g, ' ');
    if (!el.hasAttribute('href') || /^javascript:/i.test(el.href) || !text) return text;
    return `[${text}](${url(el.href)})`;
  }
  if (['code', 'kbd', 'samp', 'tt'].includes(tag)) {
    const t = el.textContent.replace(/\s+/g, ' ');
    const tick = t.includes('`') ? '``' : '`';
    return t.trim() ? tick + t + tick : t;
  }
  const mark = {strong: '**', b: '**', em: '*', i: '*', del: '~~', s: '~~', strike: '~~'}[tag];
  return mark ? wrap(inline(el), mark) : inline(el);
}

function blocks(node, out) {
  let buf = '';
  const flush = () => { const t = clean(buf); if (t) out.push(t); buf = ''; };
  for (const c of node.childNodes) {
    if (c.nodeType === 3) buf += c.data.replace(/\s+/g, ' ');
    else if (c.nodeType === 1 && !skip(c)) {
      if (BLOCK.has(c.localName)) { flush(); block(c, out); } else buf += element(c);
    }
  }
  flush();
}

function block(el, out) {
  const tag = el.localName;
  if (/^h[1-6]$/.test(tag)) {
    const t = clean(inline(el)).replace(/\n+/g, ' ');
    if (t) out.push('#'.repeat(+tag[1]) + ' ' + t);
  } else if (tag === 'pre') {
    const code = el.querySelector('code');
    const lang = (/(?:lang|language)-([\w+#.-]+)/.exec((code?.className || '') + ' ' + el.className) || [])[1] || '';
    const text = el.textContent.replace(/\n$/, '');
    const fence = text.includes('```') ? '````' : '```';
    out.push(fence + lang + '\n' + text + '\n' + fence);
  } else if (tag === 'blockquote') {
    const inner = [];
    blocks(el, inner);
    if (inner.length) out.push(inner.join('\n\n').replace(/^/gm, '> ').replace(/^> $/gm, '>'));
  } else if (tag === 'ul' || tag === 'ol') {
    const t = list(el);
    if (t) out.push(t);
  } else if (tag === 'table' && !el.querySelector('table') && el.getAttribute('role') !== 'presentation') {
    const t = table(el);
    if (t === null) blocks(el, out); else if (t) out.push(t);
  } else if (tag === 'hr') {
    out.push('---');
  } else if (tag === 'dt') {
    const t = clean(inline(el));
    if (t) out.push('**' + t + '**');
  } else {
    blocks(el, out);
  }
}

function list(el) {
  let n = el.localName === 'ol' ? (el.start || 1) : 0;
  const items = [];
  for (const li of el.children) {
    if (skip(li)) continue;
    const parts = [];
    if (li.localName === 'li') blocks(li, parts); else block(li, parts);
    const body = parts.join('\n');
    if (!body) continue;
    const marker = li.localName !== 'li' ? '  ' : n ? `${n++}. ` : '- ';
    items.push(marker + body.replace(/\n/g, '\n' + ' '.repeat(marker.length)));
  }
  return items.join('\n');
}

// A data table as GFM; null for a one-column layout table, which reads better as paragraphs.
function table(el) {
  const grid = [...el.rows].filter(r => !skip(r)).map(r => {
    const cells = [];
    for (const c of r.cells) {
      if (skip(c)) continue;
      cells.push(clean(inline(c)).replace(/\n+/g, ' ').replace(/\|/g, '\\|'));
      for (let i = 1; i < c.colSpan; i++) cells.push('');
    }
    return cells;
  }).filter(r => r.length);
  const width = Math.max(0, ...grid.map(r => r.length));
  if (width < 2) return width ? null : '';
  const line = r => '| ' + Array.from({length: width}, (_, i) => r[i] ?? '').join(' | ') + ' |';
  const caption = el.caption ? clean(inline(el.caption)) : '';
  return (caption ? caption + '\n\n' : '') + [line(grid[0]), '|' + ' --- |'.repeat(width), ...grid.slice(1).map(line)].join('\n');
}

// Readability's idea in brief: prose paragraphs score their parent and grandparent; the block with
// the best score, discounted by how much of its text is links, is the content.
function mainContent(scope) {
  for (const selector of ['main, [role=main]', 'article']) {
    const best = [...scope.querySelectorAll(selector)].filter(e => !skip(e))
      .map(e => [e, e.innerText.length]).sort((a, b) => b[1] - a[1])[0];
    if (best && best[1] >= 200) return best[0];
  }
  const scores = new Map();
  for (const p of scope.querySelectorAll('p, pre, td, blockquote')) {
    const t = p.textContent.trim();
    if (t.length < 25) continue;
    const score = 1 + t.split(',').length + Math.min(Math.floor(t.length / 100), 3);
    let node = p.parentElement;
    for (let level = 1; node && level <= 3; level++, node = node.parentElement) {
      scores.set(node, (scores.get(node) || 0) + score / (level === 1 ? 1 : level === 2 ? 2 : 9));
      if (node === scope) break;
    }
  }
  let best = scope, top = 0;
  for (const [node, score] of scores) {
    let links = 0;
    for (const a of node.querySelectorAll('a')) links += a.textContent.length;
    const adjusted = score * (1 - links / (node.textContent.length || 1));
    if (adjusted > top) { top = adjusted; best = node; }
  }
  return best;
}

const scope = el || document.body || document.documentElement;
const root = main ? mainContent(scope) : scope;
const out = [];
if (BLOCK.has(root.localName)) block(root, out); else { const t = clean(element(root)); if (t) out.push(t); }
return out.join('\n\n').replace(/\n{3,}/g, '\n\n');
"""#

let linksJS = """
    const seen = new Map();
    for (const a of document.querySelectorAll('a[href], area[href]')) {
      const href = a.href;
      if (!href || /^javascript:/i.test(href)) continue;
      const text = (a.innerText || a.getAttribute('aria-label') || a.title || a.querySelector('img[alt]')?.alt || '').replace(/\\s+/g, ' ').trim();
      if (!seen.get(href)) seen.set(href, text);
    }
    const f = (filter || '').toLowerCase();
    return [...seen].map(([href, text]) => ({text, href}))
      .filter(l => !f || l.href.toLowerCase().includes(f) || l.text.toLowerCase().includes(f));
    """

/// Replies with a page-reading result: `value` holds it and `text` repeats it, since that is what
/// the CLI prints.
func readout(_ tab: Tab, _ args: Args, _ body: String, _ values: [String: Any], _ reply: @escaping Reply) {
    examine(tab, args, body, values, reply) { value in
        let text = value as? String ?? ""
        reply(.success(["value": text, "chars": text.count, "url": tab.web.url?.absoluteString ?? "", "text": text]))
    }
}

let readingCommands: [Command] = [
    Command(name: "text",
            help: "The visible text of the page, or of one element with --target, as the browser lays it out (innerText): hidden elements are left out and blocks are separated by line breaks. The cheapest way to read a page.",
            params: [targetParam]) { args, engine, reply in
        guard let tab = pick(args, engine, reply) else { return }
        readout(tab, args, "return (el || document.body || document.documentElement).innerText", [:], reply)
    },
    Command(name: "markdown",
            help: "The page as markdown: headings, paragraphs, lists, links and images with absolute URLs, GFM tables, fenced code blocks, quotes and emphasis. --main keeps only the main content (the article, without navigation, sidebars, header and footer), which is usually what an agent wants to read. --target converts one element.",
            params: [targetParam, Param(name: "main", kind: .boolean, help: "Only the main content, without navigation and page chrome")],
            timeout: 60) { args, engine, reply in
        guard let tab = pick(args, engine, reply) else { return }
        readout(tab, args, markdownJS, ["main": args.bool("main")], reply)
    },
    Command(name: "html",
            help: "The page's current HTML (the live DOM, after scripts ran). With --target, that element's inner HTML, or its outer HTML with --outer.",
            params: [targetParam, Param(name: "outer", kind: .boolean, help: "Include the element's own tag")]) { args, engine, reply in
        guard let tab = pick(args, engine, reply) else { return }
        readout(tab, args, "return el ? (outer ? el.outerHTML : el.innerHTML) : document.documentElement.outerHTML", ["outer": args.bool("outer")], reply)
    },
    Command(name: "links",
            help: "Every link on the page as {text, href}: absolute URLs, each once, in page order. --filter keeps links whose text or URL contains a substring (case-insensitive).",
            params: [Param(name: "filter", kind: .string, help: "Substring the link text or URL must contain")]) { args, engine, reply in
        guard let tab = pick(args, engine, reply) else { return }
        tab.run(linksJS, ["filter": args.string("filter") ?? ""], in: umbraWorld) { result in
            switch result {
            case .failure(let error): reply(.failure(error))
            case .success(let value):
                let links = value as? [[String: Any]] ?? []
                let lines = links.map { link -> String in
                    let text = link["text"] as? String ?? "", href = link["href"] as? String ?? ""
                    return text.isEmpty ? href : "\(text)\t\(href)"
                }
                reply(.success(["links": links, "count": links.count, "text": lines.isEmpty ? "no links" : lines.joined(separator: "\n")]))
            }
        }
    },
]
