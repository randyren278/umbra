// The library the interaction commands call in umbraWorld: the snapshot, refs, target resolution,
// actionability checks and event observers. Installed at document start in every main frame;
// refs live as long as the document does.
let interactJS = #"""
(() => {
if (globalThis.__umbra) return;
const refOf = new WeakMap(), refs = new Map();
let counter = 0, styles = new Map(), labelMaps = new Map(), obs = null;
const doc = Math.random().toString(36).slice(2);  // tells a new document from this one after a navigation
// Computed styles and label lookups are cached for the length of one call.
const fresh = () => { styles = new Map(); labelMaps = new Map(); };
// The page world forwards its navigate events (they never reach an isolated world) under this name.
document.addEventListener('NAV_EVENT', e => { if (obs) obs.nav = JSON.parse(e.detail); });

const ref = el => {
  let r = refOf.get(el);
  if (!r) { r = 'e' + ++counter; refOf.set(el, r); refs.set(r, new WeakRef(el)); }
  return r;
};
const deref = r => { const el = refs.get(r)?.deref(); return el && el.isConnected ? el : null; };
const norm = s => s ? String(s).replace(/[\s\u200b]+/g, ' ').trim() : '';
const clip = (s, n) => s.length > n ? s.slice(0, n - 1).trimEnd() + '…' : s;
const quote = s => '"' + s.replace(/"/g, '\\"') + '"';
const win = n => (n.ownerDocument || n).defaultView || window;
const style = el => { let s = styles.get(el); if (!s) styles.set(el, s = win(el).getComputedStyle(el)); return s; };
const isFrame = el => el.localName === 'iframe' || el.localName === 'frame';
const docOf = el => { try { return el.contentDocument; } catch { return null; } };
const SKIP = new Set(['script', 'style', 'noscript', 'template', 'head', 'meta', 'link', 'title', 'base', 'datalist', 'param', 'source', 'track']);

// The flat tree: shadow roots instead of light children, slots replaced by what they show.
const kids = n => {
  if (n.shadowRoot) return n.shadowRoot.childNodes;
  if (n.localName === 'details' && !n.open) return [...n.children].filter(c => c.localName === 'summary').slice(0, 1);
  if (n.localName === 'slot') { const a = n.assignedNodes({ flatten: true }); return a.length ? a : n.childNodes; }
  return n.childNodes;
};
const up = n => n.assignedSlot || (n.parentNode && n.parentNode.nodeType === 11 ? n.parentNode.host : n.parentNode)
  || (n.nodeType === 9 && n.defaultView ? n.defaultView.frameElement : null);

// MARK: Roles, names, states

const INPUT_ROLE = { button: 'button', submit: 'button', reset: 'button', image: 'button', file: 'button', color: 'button',
  checkbox: 'checkbox', radio: 'radio', range: 'slider', number: 'spinbutton', search: 'searchbox' };
const LEAF = new Set(['link', 'button', 'textbox', 'searchbox', 'checkbox', 'radio', 'switch', 'combobox', 'slider',
  'spinbutton', 'option', 'menuitem', 'menuitemcheckbox', 'menuitemradio', 'tab']);
const ACTIVE = new Set([...LEAF, 'listbox', 'treeitem', 'clickable', 'draggable']);
const CONTENT = new Set(['button', 'link', 'heading', 'tab', 'menuitem', 'menuitemcheckbox', 'menuitemradio', 'option',
  'checkbox', 'radio', 'switch', 'treeitem', 'cell', 'columnheader', 'rowheader', 'tooltip']);
const CONTAINER = new Set(['navigation', 'main', 'banner', 'contentinfo', 'complementary', 'form', 'search', 'region',
  'dialog', 'alertdialog', 'list', 'table', 'grid', 'tablist', 'tabpanel', 'menu', 'menubar', 'toolbar', 'radiogroup',
  'tree', 'group', 'listbox']);
const TEXTROLE = new Set(['paragraph', 'listitem', 'cell', 'columnheader', 'rowheader', 'blockquote', 'code', 'caption',
  'label', 'legend', 'term', 'definition', 'alert', 'status', 'note', 'log', 'tooltip']);
const SCOPED = 'article, aside, main, nav, section';

function roleOf(el) {
  const explicit = el.getAttribute('role');
  if (explicit) {
    const r = explicit.trim().split(/\s+/)[0].toLowerCase();
    if (r) return r === 'none' || r === 'presentation' ? '' : r;
  }
  switch (el.localName) {
    case 'a': case 'area': return el.hasAttribute('href') ? 'link' : '';
    case 'button': case 'summary': return 'button';
    case 'input': return el.type === 'hidden' ? '' : INPUT_ROLE[el.type] || (el.list ? 'combobox' : 'textbox');
    case 'select': return el.multiple || el.size > 1 ? 'listbox' : 'combobox';
    case 'textarea': return 'textbox';
    case 'h1': case 'h2': case 'h3': case 'h4': case 'h5': case 'h6': return 'heading';
    case 'img': return el.getAttribute('alt') === '' ? '' : 'img';
    case 'svg': return el.getAttribute('aria-label') || el.querySelector(':scope > title') ? 'img' : '';
    case 'nav': return 'navigation';
    case 'main': return 'main';
    case 'aside': return 'complementary';
    case 'header': return el.closest(SCOPED) ? '' : 'banner';
    case 'footer': return el.closest(SCOPED) ? '' : 'contentinfo';
    case 'form': return 'form';
    case 'search': return 'search';
    case 'section': return el.hasAttribute('aria-label') || el.hasAttribute('aria-labelledby') ? 'region' : '';
    case 'dialog': return 'dialog';
    case 'ul': case 'ol': case 'menu': return 'list';
    case 'li': return 'listitem';
    case 'table': return 'table';
    case 'tr': return 'row';
    case 'td': return 'cell';
    case 'th': return el.getAttribute('scope') === 'row' ? 'rowheader' : 'columnheader';
    case 'p': return 'paragraph';
    case 'blockquote': return 'blockquote';
    case 'pre': return 'code';
    case 'fieldset': case 'details': return 'group';
    case 'option': return 'option';
    case 'progress': return 'progressbar';
    case 'meter': return 'meter';
    case 'iframe': case 'frame': return 'iframe';
    case 'label': return 'label';
    case 'legend': return 'legend';
    case 'caption': case 'figcaption': return 'caption';
    case 'dt': return 'term';
    case 'dd': return 'definition';
  }
  return el.isContentEditable && !(el.parentElement && el.parentElement.isContentEditable) ? 'textbox' : '';
}

// A control's labels, from one pass over the labels of its tree (el.labels walks the document each time).
function labelsOf(el) {
  if (!/^(input|select|textarea|button|meter|output|progress)$/.test(el.localName)) return [];
  const root = el.getRootNode();
  let map = labelMaps.get(root);
  if (!map) {
    labelMaps.set(root, map = new Map());
    for (const l of root.querySelectorAll('label')) { const c = l.control; if (c) map.set(c, [...(map.get(c) || []), l]); }
  }
  return map.get(el) || [];
}

function labelText(n) {
  let s = '';
  for (const c of n.childNodes) {
    if (c.nodeType === 3) s += c.data;
    else if (c.nodeType === 1 && !/^(input|select|textarea|script|style)$/.test(c.localName) && !c.hidden && c.getAttribute('aria-hidden') !== 'true') s += ' ' + labelText(c);
  }
  return s;
}

function contentName(el) {
  let n = norm(el.innerText);
  if (!n) {
    const i = el.querySelector('img[alt]:not([alt=""]), [aria-label], svg title');
    if (i) n = norm(i.getAttribute('alt') || i.getAttribute('aria-label') || i.textContent);
  }
  return n;
}

function nameOf(el, role) {
  let n = '';
  const by = el.getAttribute('aria-labelledby');
  if (by) {
    const root = el.getRootNode();
    n = norm(by.split(/\s+/).map(id => { const t = root.getElementById ? root.getElementById(id) : null; return t ? t.textContent : ''; }).join(' '));
  }
  n = n || norm(el.getAttribute('aria-label'));
  if (!n) n = norm(labelsOf(el).map(labelText).join(' '));
  if (!n) {
    const t = el.localName;
    if (t === 'img' || t === 'area' || t === 'input' && el.type === 'image') n = norm(el.getAttribute('alt'));
    else if (t === 'input' && /^(submit|reset|button)$/.test(el.type)) n = norm(el.value) || (el.type === 'submit' ? 'Submit' : el.type === 'reset' ? 'Reset' : '');
    else if (t === 'fieldset') n = norm(el.querySelector(':scope > legend')?.textContent);
    else if (t === 'table') n = norm(el.querySelector(':scope > caption')?.textContent);
    else if (t === 'figure') n = norm(el.querySelector(':scope > figcaption')?.textContent);
    else if (t === 'svg') n = norm(el.querySelector(':scope > title')?.textContent);
    else if (t === 'dialog' || role === 'dialog' || role === 'alertdialog') n = norm(el.querySelector('h1, h2, h3, h4, h5, h6')?.textContent);
  }
  if (!n && (role === 'clickable' || role === 'draggable')) n = norm(inlineText(kids(el))[0]);
  else if (!n && CONTENT.has(role)) n = contentName(el);
  if (!n) n = norm(el.getAttribute('title') || el.getAttribute('placeholder') || el.getAttribute('aria-placeholder'));
  return clip(n, 100);
}

function checkedOf(el) {
  if (el.localName === 'input') return el.indeterminate ? 'mixed' : el.checked;
  const c = el.getAttribute('aria-checked');
  return c === 'mixed' ? 'mixed' : c === 'true';
}

const disabledOf = el => (el.matches && el.matches(':disabled')) || !!(el.closest && el.closest('[aria-disabled="true"]'));
const area = el => { const r = el.getBoundingClientRect(); return r.width * r.height; };

function deepActive() {
  let a = document.activeElement;
  for (;;) {
    if (a && a.shadowRoot && a.shadowRoot.activeElement) a = a.shadowRoot.activeElement;
    else if (a && isFrame(a) && docOf(a) && docOf(a).activeElement && docOf(a).activeElement !== docOf(a).body) a = docOf(a).activeElement;
    else break;
  }
  return a === document.body ? null : a;
}

function states(el, role, active) {
  const s = [];
  if (role === 'heading') s.push(`[level=${/^h[1-6]$/.test(el.localName) ? el.localName[1] : el.getAttribute('aria-level') || 2}]`);
  if (/^(checkbox|radio|switch|menuitemcheckbox|menuitemradio)$/.test(role)) {
    const c = checkedOf(el);
    if (c === 'mixed') s.push('[checked=mixed]'); else if (c) s.push('[checked]');
  }
  if (disabledOf(el)) s.push('[disabled]');
  let ex = el.getAttribute('aria-expanded');
  if (ex == null && el.localName === 'summary' && el.parentElement && el.parentElement.localName === 'details') ex = String(el.parentElement.open);
  if (ex === 'true') s.push('[expanded]'); else if (ex === 'false') s.push('[collapsed]');
  if (el.getAttribute('aria-selected') === 'true' || el.localName === 'option' && el.selected) s.push('[selected]');
  const pressed = el.getAttribute('aria-pressed');
  if (pressed === 'true') s.push('[pressed]'); else if (pressed === 'mixed') s.push('[pressed=mixed]');
  if (el.required || el.getAttribute('aria-required') === 'true') s.push('[required]');
  if (el.readOnly && /^(input|textarea)$/.test(el.localName) || el.getAttribute('aria-readonly') === 'true') s.push('[readonly]');
  if (el.getAttribute('aria-invalid') === 'true') s.push('[invalid]');
  if (el.localName === 'input' && !/^(text|checkbox|radio|submit|button|reset|image|search|range|number)$/.test(el.type)) s.push(`[type=${el.type}]`);
  if (el.localName === 'dialog' && el.matches(':modal') || el.getAttribute('aria-modal') === 'true') s.push('[modal]');
  if (active && el === active) s.push('[focused]');
  return s.join(' ');
}

function valueOf(el, role) {
  const t = el.localName;
  if (t === 'input') {
    if (/^(checkbox|radio|button|submit|reset|image|hidden)$/.test(el.type)) return '';
    if (el.type === 'password') return el.value ? '••••••' : '';
    if (el.type === 'file') return [...el.files].map(f => f.name).join(', ');
    return el.value;
  }
  if (t === 'textarea') return el.value;
  if (t === 'select') return [...el.selectedOptions].map(o => norm(o.label)).join(', ');
  if (el.isContentEditable) return el.innerText;
  if (/^(slider|spinbutton|progressbar|meter|scrollbar)$/.test(role)) return el.getAttribute('aria-valuetext') || el.getAttribute('aria-valuenow') || (el.value != null ? String(el.value) : '');
  return '';
}

function hrefOf(el) {
  const h = el.getAttribute('href');
  if (!h || /^\s*javascript:/i.test(h)) return '';
  try {
    const u = new URL(h, el.baseURI);
    return u.origin === location.origin ? u.pathname + u.search + u.hash : u.href;
  } catch { return h; }
}

// What the snapshot calls an element with no interactive role but a pointer cursor, an onclick
// or draggable=true; only the outermost element of a pointer region counts.
function clickableRole(el) {
  if (/^(html|body)$/.test(el.localName)) return '';
  if (el.getAttribute('draggable') === 'true') return 'draggable';
  const p = up(el);
  return el.hasAttribute('onclick') || style(el).cursor === 'pointer' && !(p && p.nodeType === 1 && style(p).cursor === 'pointer') ? 'clickable' : '';
}

function describe(el) {
  if (!el || el.nodeType !== 1) return 'nothing';
  const role = roleOf(el) || clickableRole(el);
  const name = nameOf(el, role) || (ACTIVE.has(role) ? '' : clip(norm(el.innerText || el.textContent), 50));
  if (role) return role + (name ? ' ' + quote(name) : el.id ? '#' + el.id : '');
  const tag = el.localName + (el.id ? '#' + el.id : '') + (el.classList && el.classList.length ? '.' + [...el.classList].slice(0, 2).join('.') : '');
  return tag + (name ? ' ' + quote(name) : '');
}

// MARK: Visibility and geometry

function visible(el) {
  if (!el || !el.isConnected) return false;
  const s = style(el);
  if (s.display === 'contents') return [...kids(el)].some(n => n.nodeType === 1 && visible(n));
  if (s.visibility !== 'visible' || el.checkVisibility && !el.checkVisibility()) return false;
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}

// A control drawn by its label instead (display: none, zero size or a 1-pixel "sr-only" box).
const proxied = el => /^(checkbox|radio|file)$/.test(el.type) && el.localName === 'input' && (!visible(el) || area(el) <= 4)
  && labelsOf(el).some(visible);

// Where an element's frame sits in the top viewport.
function frameOffset(el) {
  let x = 0, y = 0;
  for (let w = win(el); w !== window && w.frameElement; w = win(w.frameElement)) {
    const fe = w.frameElement, r = fe.getBoundingClientRect(), s = style(fe);
    x += r.left + parseFloat(s.borderLeftWidth) + parseFloat(s.paddingLeft);
    y += r.top + parseFloat(s.borderTopWidth) + parseFloat(s.paddingTop);
  }
  return { x, y };
}

// The middle of the element's largest visible box, in the top viewport's CSS pixels.
function point(el) {
  const rects = [...el.getClientRects()];
  if (!rects.length) rects.push(el.getBoundingClientRect());
  let best = null;
  for (const r of rects) {
    let x1 = r.left, y1 = r.top, x2 = r.right, y2 = r.bottom, w = win(el), full = true;
    for (;;) {
      const v = w.visualViewport, vw = v ? v.width : w.innerWidth, vh = v ? v.height : w.innerHeight;
      if (x1 < 0 || y1 < 0 || x2 > vw || y2 > vh) full = false;
      x1 = Math.max(x1, 0); y1 = Math.max(y1, 0); x2 = Math.min(x2, vw); y2 = Math.min(y2, vh);
      const fe = w === window ? null : w.frameElement;
      if (!fe) break;
      const fr = fe.getBoundingClientRect(), fs = style(fe);
      const ox = fr.left + parseFloat(fs.borderLeftWidth) + parseFloat(fs.paddingLeft), oy = fr.top + parseFloat(fs.borderTopWidth) + parseFloat(fs.paddingTop);
      x1 += ox; x2 += ox; y1 += oy; y2 += oy;
      w = win(fe);
    }
    const a = Math.max(0, x2 - x1) * Math.max(0, y2 - y1);
    if (a >= 1 && (!best || a > best.a)) best = { x: (x1 + x2) / 2, y: (y1 + y2) / 2, a, full };
  }
  return best;
}

// The element really under a point: through open shadow roots and same-origin frames.
function deepHit(x, y) {
  let doc = document;
  for (;;) {
    let el = doc.elementFromPoint(x, y);
    while (el && el.shadowRoot) { const inner = el.shadowRoot.elementFromPoint(x, y); if (!inner || inner === el) break; el = inner; }
    const d = el && isFrame(el) ? docOf(el) : null;
    if (!d) return el;
    const r = el.getBoundingClientRect(), s = style(el);
    x -= r.left + parseFloat(s.borderLeftWidth) + parseFloat(s.paddingLeft);
    y -= r.top + parseFloat(s.borderTopWidth) + parseFloat(s.paddingTop);
    doc = d;
  }
}

function hits(target, el, h) {
  for (let n = h; n; n = up(n)) if (n === target || n === el) return true;
  const label = h && h.closest ? h.closest('label') : null;
  return !!label && (label.control === el || label.control === target);
}

// MARK: Snapshot

// The text of inline content, and the part of it no interactive element already names.
function inlineText(nodes) {
  let full = '', own = '';
  for (const n of nodes) {
    if (n.nodeType === 3) { full += n.data; own += n.data; continue; }
    if (n.nodeType !== 1 || SKIP.has(n.localName) || n.getAttribute('aria-hidden') === 'true') continue;
    if (n.localName === 'br') { full += ' '; own += ' '; continue; }
    const s = style(n);
    if (s.display === 'none' || /^(select|textarea|iframe|frame)$/.test(n.localName)) continue;
    if (!inlineLevel(n)) { full += ' '; own += ' '; continue; }
    if (s.visibility !== 'visible') continue;
    if (n.localName === 'img') { const alt = n.getAttribute('alt') || ''; full += alt; own += alt; continue; }
    const [f, o] = inlineText(kids(n)), pad = s.display === 'inline' ? '' : ' ';  // an inline-block is a word of its own
    full += pad + f + pad;
    own += ACTIVE.has(roleOf(n)) || n.hasAttribute('onclick') || n.localName === 'label' && n.control ? ' ' : pad + o + pad;
  }
  return [full, own];
}

const inlineLevel = n => { const d = style(n).display; return d.startsWith('inline') || d === 'contents'; };
const wordy = s => /[\p{L}\p{N}]/u.test(s);

// A block's content in order: runs of inline content (one text line each, when they say
// something no control already says) between its block-level children.
function flow(el, depth, cx, ia, pc, inside, role) {
  let seen;
  const shown = () => seen ??= area(el) > 0 && el.checkVisibility();
  const parts = [];
  let run = null;
  for (const n of kids(el)) {
    if (n.nodeType === 3 || n.nodeType === 1 && (n.localName === 'br' || inlineLevel(n))) (run || parts.push(run = []) && run).push(n);
    else if (n.nodeType === 1) { run = null; parts.push(n); }
  }
  let d = depth, first = true;
  for (const part of parts) {
    if (!Array.isArray(part)) { visit(part, d, cx, ia, false, pc, inside); first = false; continue; }
    const [full, own] = inlineText(part);
    const els = part.filter(n => n.nodeType === 1);
    if (wordy(own) && shown()) {
      const text = clip(norm(full), 160);
      if (first && TEXTROLE.has(role)) { push(cx, depth, `- ${role}: ${text}`); d = depth + 1; }
      else push(cx, d, `- text: ${text}`);
      for (const n of els) visit(n, first && TEXTROLE.has(role) ? d : d + 1, cx, ia, true, pc, inside);
    } else for (const n of els) visit(n, d, cx, ia, true, pc, inside);
    first = false;
  }
}

function push(cx, depth, s) {
  const line = '  '.repeat(depth) + s;
  if (cx.size + line.length + 1 > cx.max) { cx.full = true; return false; }
  cx.lines.push(line);
  cx.size += line.length + 1;
  return true;
}

function emit(cx, depth, el, role, proxy) {
  const c = proxy || el;
  const name = proxy ? nameOf(el, 'label') || nameOf(proxy, role) : nameOf(el, role);
  const r = ref(el);
  let s = `- ${role}${name ? ' ' + quote(name) : ''} [ref=${r}]`;
  const st = states(c, role, cx.active);
  if (st) s += ' ' + st;
  const v = norm(valueOf(c, role));
  if (v && v !== name) s += ' value=' + quote(clip(v, 100));
  if (role === 'link') { const h = hrefOf(el); if (h) s += ' → ' + clip(h, 120); }
  if (push(cx, depth, s)) cx.refs++;
}

function visit(el, depth, cx, ia, inl, pc, inside) {
  if (cx.full) return;
  const tag = el.localName;
  if (SKIP.has(tag) || el.getAttribute('aria-hidden') === 'true' || el.inert) return;
  const st = style(el), disp = st.display;
  if (disp === 'none') {
    if (tag === 'input' && el.type === 'file' && !proxied(el)) push(cx, depth, `- button${nameOf(el, 'button') ? ' ' + quote(nameOf(el, 'button')) : ''} [ref=${ref(el)}] [type=file] [hidden]`) && cx.refs++;
    return;
  }
  if (inl && !inlineLevel(el)) inl = false;
  const shown = st.visibility === 'visible', cursor = st.cursor;
  const box = () => { const r = el.getBoundingClientRect(); return (r.width > 0 && r.height > 0 || disp === 'contents') && el.checkVisibility(); };
  let role = roleOf(el), proxy = null;
  if (tag === 'label' && el.control) {
    if (!proxied(el.control)) return children(el, depth, cx, ia, inl, cursor, inside);
    proxy = el.control;
    role = roleOf(proxy);
  } else if (proxied(el)) return;
  if (!ACTIVE.has(role) && !inside && tag !== 'body' && tag !== 'html') {
    if (el.getAttribute('draggable') === 'true') role = 'draggable';
    else if (el.hasAttribute('onclick') || cursor === 'pointer' && pc !== 'pointer') role = 'clickable';
  }

  if (ACTIVE.has(role)) {
    const leaf = LEAF.has(role) || tag === 'select';
    if (shown && box()) {
      emit(cx, depth, el, role, proxy);
      if (tag === 'select') {
        const opts = [...el.options], n = Math.min(opts.length, 25);
        for (let i = 0; i < n; i++) push(cx, depth + 1, `- option ${quote(clip(norm(opts[i].label), 80))}${opts[i].selected ? ' [selected]' : ''}${opts[i].disabled ? ' [disabled]' : ''}`);
        if (opts.length > n) push(cx, depth + 1, `- … ${opts.length - n} more options`);
      }
      if (!leaf) children(el, depth + 1, cx, ia, role === 'clickable' || role === 'draggable', cursor, true);
    } else if (!leaf) children(el, depth, cx, ia, inl, cursor, inside);
    return;
  }
  if (role === 'heading') {
    if (shown && box()) {
      push(cx, depth, `- heading ${quote(nameOf(el, role))}${refOf.has(el) ? ` [ref=${ref(el)}]` : ''} ${states(el, role)}`);
      children(el, depth + 1, cx, true, true, cursor, inside);
    }
    return;
  }
  if (role === 'iframe') return frame(el, depth, cx, ia);
  if (ia) return children(el, depth, cx, ia, inl, cursor, inside);
  if (role === 'img') {
    const n = shown && box() ? nameOf(el, role) : '';
    if (n) push(cx, depth, `- img ${quote(n)}`);
    return;
  }
  if (role === 'row') return row(el, depth, cx, cursor, inside);
  if (inl || !shown) return children(el, depth, cx, ia, inl, cursor, inside);
  const name = CONTAINER.has(role) ? nameOf(el, role) : '';
  if (CONTAINER.has(role) && (name || !/^(group|region)$/.test(role))) {
    const at = cx.lines.length;
    if (!push(cx, depth, `- ${role}${name ? ' ' + quote(name) : ''}${states(el, role) ? ' ' + states(el, role) : ''}:`)) return;
    flow(el, depth + 1, cx, ia, cursor, inside, '');
    if (cx.lines.length === at + 1 && !/^(dialog|alertdialog)$/.test(role)) cx.lines.length = at;
    return;
  }
  flow(el, depth, cx, ia, cursor, inside, role);
}

function children(el, depth, cx, ia, inl, pc, inside) {
  for (const n of kids(el)) if (n.nodeType === 1) visit(n, depth, cx, ia, inl, pc, inside);
}

function frame(el, depth, cx, ia) {
  const r = el.getBoundingClientRect();
  if (!r.width || !r.height || style(el).visibility !== 'visible') return;
  const doc = docOf(el), name = nameOf(el, 'iframe');
  let src = el.src || '';
  try { const u = new URL(src); if (u.origin === location.origin) src = u.pathname + u.search; } catch {}
  if (!ia) push(cx, depth, `- iframe${name ? ' ' + quote(name) : ''}${doc ? '' : ' [cross-origin]'}${src && src !== 'about:blank' ? ' → ' + clip(src, 120) : ''}${doc && doc.body ? ':' : ''}`);
  if (doc && doc.body) visit(doc.body, ia ? depth : depth + 1, cx, ia, false, '', false);
}

function row(el, depth, cx, pc, inside) {
  const r = el.getBoundingClientRect();
  if (!r.width || !r.height) return;
  const cells = [...kids(el)].filter(c => c.nodeType === 1 && (/^(td|th)$/.test(c.localName) || /cell|header/.test(c.getAttribute('role') || '')));
  const text = cells.map(c => clip(norm(c.innerText), 80)).join(' | ');
  const shown = /[\p{L}\p{N}]/u.test(text) && push(cx, depth, `- row: ${clip(text, 300)}`);
  children(el, shown ? depth + 1 : depth, cx, true, true, pc, inside);
}

function snapshot(a) {
  fresh();
  let root = document.body || document.documentElement, note = '';
  if (a.scope) {
    const f = find(a.scope);
    if (!f.el) return { error: f.error };
    root = f.el;
  } else {
    // Behind a modal dialog the page is inert: show the dialog alone.
    const modal = [...document.querySelectorAll('dialog, [aria-modal="true"]')].reverse().find(d => (d.localName !== 'dialog' || d.matches(':modal')) && visible(d));
    if (modal) { root = modal; note = `- note: a modal dialog is open, so only it is shown (--scope body shows the page behind it)\n`; }
  }
  const cx = { lines: [], size: 0, max: a.max || 25000, full: false, refs: 0, active: deepActive() };
  if (root) visit(root, 0, cx, !!a.interactive, false, '', false);
  let text = note + (cx.lines.join('\n') || (a.interactive ? '(nothing interactive is visible)' : '(nothing visible)'));
  if (cx.full) text += `\n… cut at ${cx.max} characters; narrow it with --scope <css>${a.interactive ? '' : ' or --interactive'}, or raise --max`;
  return { text, refs: cx.refs, url: location.href, title: document.title, truncated: cx.full };
}

// MARK: Targets

// Every document and open shadow root, so CSS reaches into components and same-origin frames.
function scopes(root, out = []) {
  out.push(root);
  for (const el of root.querySelectorAll('*')) {
    if (el.shadowRoot) scopes(el.shadowRoot, out);
    if (isFrame(el) && docOf(el)) scopes(docOf(el), out);
  }
  return out;
}

function byText(raw) {
  let want = norm(raw), exact = false;
  const m = /^(["'])(.*)\1$/.exec(want);
  if (m) { want = norm(m[2]); exact = true; }
  if (!want) return { error: 'text= needs some text', fatal: true };
  const low = want.toLowerCase();
  let best = null, score = 9, length = Infinity, count = 0;
  for (const root of scopes(document)) for (const el of root.querySelectorAll('*')) {
    if (SKIP.has(el.localName)) continue;
    const role = roleOf(el), active = ACTIVE.has(role);
    // Cheap first: the text must be in the subtree, unless an attribute names the element.
    if (!(el.textContent || '').toLowerCase().includes(low) && !(active && (el.attributes.length || labelsOf(el).length))) continue;
    let text;
    if (active) text = nameOf(el, role);
    else if ([...el.childNodes].some(n => n.nodeType === 3 && n.data.trim())) text = norm(el.innerText);
    else continue;
    let s;
    if (exact ? text === want : text.toLowerCase() === low) s = active ? 0 : 1;
    else if (!exact && text.toLowerCase().includes(low)) s = active ? 2 : 3;
    else continue;
    if (s > score) continue;
    if (s === score && text.length >= length) { count++; continue; }
    if (!visible(el)) continue;
    if (s < score) count = 0;
    best = el; score = s; length = text.length; count++;
  }
  if (!best) return { error: `no visible element has the text ${quote(want)}` };
  for (let n = best; n; n = up(n)) if (n.nodeType === 1 && (ACTIVE.has(roleOf(n)) || clickableRole(n))) return { el: n, count };
  return { el: best, count };
}

function find(target) {
  const t = String(target || '').trim();
  if (!t) return { fatal: true, error: 'no target given: pass a ref like e5, text=Some text, or a CSS selector' };
  const m = /^@?(e\d+)$/.exec(t);
  if (m) {
    const el = deref(m[1]);
    if (el) return { el, count: 1 };
    return { fatal: true, error: refs.has(m[1]) ? `${m[1]} is no longer on the page; take a new snapshot` : `no element has ref ${m[1]} on this page; take a snapshot first (refs reset when the page changes)` };
  }
  if (/^text=/i.test(t)) return byText(t.slice(5));
  let list;
  try { list = [...document.querySelectorAll(t)]; }
  catch { return { fatal: true, error: `not a valid CSS selector: ${t} (a target is a ref like e5, text=Some text, or a CSS selector)` }; }
  if (!list.some(visible)) list = scopes(document).flatMap(r => [...r.querySelectorAll(t)]);
  if (!list.length) return { error: `nothing matches ${t}` };
  return { el: list.find(visible) || list[0], count: list.length };
}

// MARK: Actions

const TEXTY = /^(text|search|email|password|tel|url|number)$/;
const SETTER = /^(date|time|datetime-local|month|week|color|range)$/;
const fillKind = el => el.localName === 'textarea' || el.isContentEditable || el.localName === 'input' && TEXTY.test(el.type) ? 'text'
  : el.localName === 'input' && SETTER.test(el.type) ? 'setter' : '';

function arm(el, kind) {
  if (obs) obs.off();
  const o = { seen: {}, nav: null, link: '', submit: false, unloading: false, prevented: null, files: null, subs: [] };
  const on = (t, type, fn, capture = true) => { t.addEventListener(type, fn, capture); o.subs.push(() => t.removeEventListener(type, fn, capture)); };
  const log = e => { if (!(e.type in o.seen)) o.seen[e.type] = e.isTrusted; };
  for (const w of new Set([window, el ? win(el) : window])) {
    for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click', 'dblclick', 'auxclick', 'contextmenu', 'mousemove', 'mouseover',
      'keydown', 'keyup', 'beforeinput', 'input', 'change', 'wheel']) on(w, type, log);
    on(w, 'click', e => {
      o.prevented = e.defaultPrevented;
      const t = e.composedPath()[0], a = t && t.closest ? t.closest('a[href], area[href]') : null;
      if (!a || win(a) !== window || /^\s*javascript:/i.test(a.getAttribute('href'))) return;
      if (a.hasAttribute('download')) { o.download = true; return; }
      const u = new URL(a.href, location.href);
      if (!u.hash || u.href.split('#')[0] !== location.href.split('#')[0]) o.link = u.href;
    }, false);
    on(w, 'keydown', e => { o.keyPrevented = e.defaultPrevented; }, false);
    on(w, 'change', e => { const t = e.composedPath()[0]; if (t && t.type === 'file') o.files = [...t.files].map(f => ({ name: f.name, size: f.size })); });
    if (kind === 'right') on(w, 'contextmenu', e => e.preventDefault());  // a native menu would block the daemon
  }
  // Only the top document's navigations matter: a frame navigating leaves the tab where it is.
  on(window, 'submit', e => { if (!e.defaultPrevented) o.submit = true; }, false);
  on(window, 'beforeunload', () => { o.unloading = true; });
  o.off = () => { o.subs.forEach(f => f()); o.subs = []; if (obs === o) obs = null; };
  setTimeout(o.off, 10000);
  obs = o;
}

function observed() {
  const o = obs;
  const out = { url: location.href, title: document.title };
  if (!o) return out;
  o.off();
  out.events = o.seen;
  out.files = o.files;
  out.download = !!o.download || !!(o.nav && o.nav.download);
  // A cross-document navigate event, unless it was intercepted (the URL then changes at once); failing
  // that, a followed link, a submitted form or beforeunload.
  const n = o.nav;
  out.navigating = n ? !n.same && !n.download && location.href !== n.url : o.unloading || o.submit || !!o.link && !o.prevented && location.href !== o.link;
  return out;
}

// Finds the target and checks it can take the action: attached, visible, enabled, editable,
// scrolled into view and not covered. The caller retries until its timeout.
function prepare(a) {
  fresh();
  const f = find(a.target), act = a.action;
  if (!f.el) return { status: f.fatal ? 'error' : 'missing', message: f.error };
  let el = f.el;
  if (/^(fill|type|check|select|upload)$/.test(act) && el.localName === 'label' && el.control) el = el.control;
  if ((act === 'fill' || act === 'type') && !fillKind(el)) {
    const inner = [...el.querySelectorAll('input, textarea, [contenteditable]')].filter(fillKind);
    if (inner.length === 1) el = inner[0];
  }
  if (act === 'select' && el.localName !== 'select') {
    const inner = el.querySelectorAll('select');
    if (inner.length === 1) el = inner[0];
  }
  const info = { ref: ref(el), desc: describe(el), count: f.count, url: location.href, doc };
  const fail = (status, why) => Object.assign(info, { status, message: `${info.desc} (${info.ref}) ${why}` });
  const role = roleOf(el);
  if (act === 'fill' || act === 'type') {
    if (!fillKind(el)) return fail('error', `can't be filled; target a text field, textarea or contenteditable${el.localName === 'select' ? ' (use select for a <select>)' : ''}`);
    info.kind = fillKind(el);
    if (el.type === 'password') info.password = true;
  }
  if (act === 'select' && el.localName !== 'select') return fail('error', /combobox|listbox/.test(role)
    ? 'is a custom combobox, not a <select>: click it to open the list, then click the option (take a snapshot to see the options)'
    : 'is not a <select>');
  if ((act === 'check' || act === 'uncheck') && !/^(checkbox|radio|switch|menuitemcheckbox|menuitemradio)$/.test(role)) return fail('error', 'is not a checkbox, radio button or switch');
  if (act === 'check' || act === 'uncheck') info.checked = checkedOf(el) === true;
  if (act === 'upload' && !(el.localName === 'input' && el.type === 'file')) {
    const inner = el.querySelectorAll('input[type=file]');
    if (inner.length === 1) info.input = ref(inner[0]);
  } else if (act === 'upload') info.input = info.ref;
  if (info.input) deref(info.input).value = '';  // so choosing the same file again still fires change
  if (act === 'focus') {
    el.focus();
    info.focused = deepActive() === el;
    if (a.arm) arm(el, a.arm);
    return Object.assign(info, { status: 'ok' });
  }
  let target = el;
  if (proxied(el)) target = labelsOf(el).find(visible);
  if (!visible(target)) {
    if (act === 'upload' && info.input) return Object.assign(info, { status: 'ok', hidden: true });
    return fail('hidden', 'is not visible');
  }
  if (/^(click|fill|type|check|uncheck|select|drag)$/.test(act) && disabledOf(el)) return fail('disabled', 'is disabled');
  if ((act === 'fill' || act === 'type') && (el.readOnly || el.getAttribute('aria-readonly') === 'true')) return fail('readonly', 'is read-only');
  if (act === 'select') return Object.assign(info, { status: 'ok' });
  let p = point(target);
  if (!p || !p.full) {
    target.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' });
    p = point(target);
  }
  if (!p) return fail('hidden', 'is outside the viewport');
  Object.assign(info, { x: p.x, y: p.y, focused: deepActive() === el, draggable: !!(target.closest && target.closest('a[href], img, [draggable=true]')) && target.draggable !== false });
  if (a.hitTest !== false) {
    let h = deepHit(p.x, p.y);
    // A sticky header or a banner may cover it where it is: try it centred, then at either edge.
    for (const block of ['center', 'start', 'end', 'center']) {
      if (hits(target, el, h)) break;
      target.scrollIntoView({ block, inline: 'center', behavior: 'instant' });
      p = point(target);
      if (!p) return fail('hidden', 'is outside the viewport');
      h = deepHit(p.x, p.y);
    }
    info.x = p.x;
    info.y = p.y;
    if (!hits(target, el, h)) {
      if (style(target).pointerEvents === 'none') return fail('covered', 'does not take pointer events (pointer-events: none)');
      return Object.assign(fail('covered', `is covered by ${describe(h)}${h ? ` (${ref(h)})` : ''}`), { cover: h ? ref(h) : null });
    }
    const sel = h.closest && h.closest('select');
    if (/^(click|check|uncheck|drag)$/.test(act) && sel && !sel.multiple && sel.size <= 1) return fail('error', `is a <select>: choose an option with \`select ${ref(sel)} <option>\` (a click would open a native menu)`);
    if (/^(click|drag)$/.test(act) && h.localName === 'input' && SETTER.test(h.type) && h.type !== 'range') return fail('error', `is a ${h.type} input: set it with \`fill ${ref(h)} <value>\` (a click would open a native picker)`);
  }
  if (a.arm) arm(el, a.arm);
  return Object.assign(info, { status: 'ok' });
}

function current(el) { return el.isContentEditable ? el.innerText.replace(/\n$/, '') : el.value; }

// Gets a field ready for typing: focused, and either all selected (fill) or caret at the end.
function fillStart(a) {
  const el = deref(a.ref);
  if (!el) return { error: 'the field left the page' };
  if (deepActive() !== el) el.focus();
  if (a.clear) {
    if (el.isContentEditable) win(el).getSelection().selectAllChildren(el); else el.select();
  } else if (a.end) {
    if (el.isContentEditable) { const s = win(el).getSelection(); s.selectAllChildren(el); s.collapseToEnd(); }
    else try { el.setSelectionRange(el.value.length, el.value.length); } catch {}
  }
  arm(el, 'input');
  return { focused: deepActive() === el, value: current(el) };
}

function fillCheck(a) {
  const el = deref(a.ref), o = obs;
  if (!el) return { error: 'the field left the page' };
  const seen = o ? o.seen : {};
  return { value: current(el), input: 'input' in seen || 'beforeinput' in seen, trusted: seen.input === true || seen.beforeinput === true };
}

function setValue(a) {
  const el = deref(a.ref);
  if (!el) return { error: 'the field left the page' };
  el.focus();
  if (el.isContentEditable) el.textContent = a.value; else el.value = a.value;
  el.dispatchEvent(new InputEvent('input', { bubbles: true, composed: true, inputType: 'insertText', data: a.value }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  if (obs) obs.off();
  return { value: current(el) };
}

// A control whose keyboard activation opens a native popup (a <select> menu, a date or color picker).
const picker = el => !!el && (el.localName === 'select' && !el.multiple && el.size <= 1
  || el.localName === 'input' && SETTER.test(el.type) && el.type !== 'range');

function focused() {
  fresh();
  const el = deepActive();
  return el ? { ref: ref(el), desc: describe(el), editable: !!fillKind(el) && !el.readOnly, picker: picker(el), url: location.href, doc }
    : { url: location.href, doc };
}

function keyPrevented() { return { prevented: !!(obs && obs.keyPrevented) }; }

function armKeys() {
  arm(deepActive(), 'keys');
  return focused();
}

function selectOptions(a) {
  const el = deref(a.ref);
  const opts = [...el.options], chosen = new Set(), missing = [];
  for (const v of a.values) {
    const o = opts.find(o => o.value === v) || opts.find(o => norm(o.label) === norm(v)) || opts.find(o => norm(o.label).toLowerCase() === norm(v).toLowerCase());
    if (o) chosen.add(o); else missing.push(v);
  }
  if (missing.length) return { error: `no option ${missing.map(quote).join(', ')} in ${describe(el)}; options: ${opts.slice(0, 30).map(o => quote(norm(o.label))).join(', ')}` };
  if (!el.multiple && chosen.size > 1) return { error: `${describe(el)} takes one option, not ${chosen.size}` };
  const off = [...chosen].find(o => o.disabled);
  if (off) return { error: `option ${quote(norm(off.label))} in ${describe(el)} is disabled` };
  el.focus();
  for (const o of opts) o.selected = chosen.has(o);
  el.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  return { selected: [...el.selectedOptions].map(o => norm(o.label)), values: [...el.selectedOptions].map(o => o.value) };
}

function stateOf(a) {
  const el = deref(a.ref);
  return el ? { checked: checkedOf(el) === true } : { error: 'the element left the page' };
}

function scroller(el) {
  for (let n = el; n; n = up(n)) {
    if (n.nodeType !== 1) continue;
    if (n === n.ownerDocument.documentElement || n === n.ownerDocument.body) break;
    const s = style(n);
    if (/(auto|scroll|overlay)/.test(s.overflowY + s.overflowX) && (n.scrollHeight > n.clientHeight || n.scrollWidth > n.clientWidth)) return n;
  }
  return (el ? el.ownerDocument : document).scrollingElement;
}

function scrollState(a) {
  fresh();
  const el = a.ref ? deref(a.ref) : null;
  const s = scroller(el), page = s === s.ownerDocument.scrollingElement;
  if (a.to) {
    const top = a.to === 'top' ? 0 : s.scrollHeight;
    if (page) win(s).scrollTo({ top, left: s.scrollLeft, behavior: 'instant' }); else s.scrollTo({ top, left: s.scrollLeft, behavior: 'instant' });
  } else if (a.reveal && el) el.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' });
  const w = win(s);
  return { x: Math.round(page ? w.scrollX : s.scrollLeft), y: Math.round(page ? w.scrollY : s.scrollTop),
    height: s.scrollHeight, width: s.scrollWidth, view: page ? w.innerHeight : s.clientHeight,
    what: page ? 'page' : describe(s) + ` (${ref(s)})` };
}

function syntheticDrag(a) {
  const src = deref(a.from), dst = deref(a.to);
  if (!src || !dst) return { error: 'an element left the page' };
  const dt = new DataTransfer();
  const fire = (el, type, p) => el.dispatchEvent(new DragEvent(type, { bubbles: true, cancelable: true, composed: true, dataTransfer: dt, clientX: p.x, clientY: p.y }));
  const ps = point(src) || { x: 0, y: 0 }, pd = point(dst) || { x: 0, y: 0 };
  fire(src, 'dragstart', ps);
  fire(src, 'drag', ps);
  fire(dst, 'dragenter', pd);
  const accepted = !fire(dst, 'dragover', pd);
  if (accepted) fire(dst, 'drop', pd); else fire(dst, 'dragleave', pd);
  fire(src, 'dragend', pd);
  return { accepted };
}

function setFiles(a) {
  const el = deref(a.ref);
  if (!el) return { error: 'the file input left the page' };
  const dt = new DataTransfer();
  for (const f of a.files) dt.items.add(new File([Uint8Array.fromBase64(f.data)], f.name, { type: f.type }));
  el.files = dt.files;
  el.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  return { files: [...el.files].map(f => ({ name: f.name, size: f.size })) };
}

function filesOf(a) {
  const el = deref(a.ref);
  return { files: el && el.files ? [...el.files].map(f => ({ name: f.name, size: f.size })) : [], seen: obs ? obs.files : null };
}

function get(a) {
  fresh();
  const f = find(a.target);
  if (!f.el) return { status: f.fatal ? 'error' : 'missing', message: f.error };
  const el = f.el, role = roleOf(el), r = el.getBoundingClientRect(), o = frameOffset(el);
  const out = { status: 'ok', ref: ref(el), desc: describe(el), role, name: nameOf(el, role), visible: visible(el), enabled: !disabledOf(el),
    text: norm(el.innerText ?? el.textContent), box: { x: Math.round(r.left + o.x), y: Math.round(r.top + o.y), width: Math.round(r.width), height: Math.round(r.height) } };
  if (fillKind(el) || el.localName === 'select') out.value = el.type === 'password' ? '' : current(el);
  if (/^(checkbox|radio|switch)$/.test(role)) out.checked = checkedOf(el);
  if (a.attr) out.attr = el.getAttribute(a.attr);
  return out;
}

// find(target) → {el, count} or {error, fatal}: the Capture lane resolves --target with it too.
globalThis.__umbra = { find: t => { fresh(); return find(t); }, where: () => ({ title: document.title, url: location.href, doc }), snapshot, prepare, observed, fillStart, fillCheck, setValue, focused, armKeys, keyPrevented, selectOptions, stateOf,
  scrollState, syntheticDrag, setFiles, filesOf, get };
})();
"""#
