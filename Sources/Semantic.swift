import Foundation
import WebKit

// Structural reads deliberately do not ask WebKit for style, layout or actionability.
let semanticJS = #"""
const action = a.action, limit = a.limit, max = a.max;
const doc = globalThis.__umbraSemanticDoc ||= (globalThis.crypto?.randomUUID?.() || String(Date.now()) + Math.random());
if (a.selector.includes(':') || a.selector.includes('\\')) return { error: 'semantic selectors do not support colons, escapes or pseudo-classes; use a rendered browser command', kind: 'needs_browser' };
if (!/^(?:[A-Za-z][\w-]*|\*)?(?:[.#][\w-]+)*$/.test(a.selector)) return { error: 'semantic selector must be a single tag, id, class or compound of those', kind: 'invalid_selector' };
let visited = 0, scanned = 0, used = 0, truncated = false, skippedLinks = 0;
const started = performance.now(), records = [], seen = new Set(), stop = {};
const blocked = el => { const name = el.localName || ''; return name.length <= 8 && /^(script|style|noscript|template|head|input|textarea|select|option|iframe|frame)$/.test(name.toLowerCase()); };
const tag = el => { const name = el?.localName || ''; if (name.length > 128) truncated = true; return name.slice(0, 128); };
function visit() {
  if (visited >= 50000 || performance.now() - started > 100) { truncated = true; throw stop; }
  visited++;
}
const walker = el => document.createTreeWalker(el, NodeFilter.SHOW_ALL, {
  acceptNode(node) { visit(); return node.nodeType === 1 && blocked(node) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT; }
});
const chunk = node => {
  if (scanned >= 1000000) { truncated = true; throw stop; }
  const take = Math.min(node.length, 1000000 - scanned, 100000);
  if (take < node.length) truncated = true;
  scanned += take;
  return node.substringData(0, take).replace(/\s+/g, ' ').trim();
};
function textOf(el, cap) {
  const w = walker(el), parts = []; let node, chars = 0;
  try {
    while ((node = w.nextNode())) {
      if (node.nodeType !== 3) continue;
      const text = chunk(node);
      if (!text) continue;
      const left = cap - chars;
      if (text.length > left) { parts.push(text.slice(0, Math.max(0, left))); truncated = true; break; }
      parts.push(text); chars += text.length + 1;
      if (chars >= cap) { truncated = true; break; }
    }
  } catch (error) { if (error !== stop) throw error; }
  return parts.join(' ').slice(0, Math.max(0, cap));
}
function add(record) {
  const left = max - used - (record.href?.length || 0);
  if (left < 0 || records.length >= limit) { truncated = true; return false; }
  if (record.text.length > left) { record.text = record.text.slice(0, left); truncated = true; }
  used += record.text.length + (record.href?.length || 0);
  records.push(record);
  if (used >= max || records.length >= limit) { truncated = true; return false; }
  return true;
}
try {
  let root = null, candidate = document.documentElement;
  if (candidate) {
    visit();
    if (candidate.matches(a.selector)) root = candidate;
    else {
      const roots = walker(candidate); let node;
      while ((node = roots.nextNode())) if (node.nodeType === 1 && node.matches(a.selector)) { root = node; break; }
    }
  }
  if (root && !blocked(root)) {
    if (action === 'read') records.push({ tag: tag(root), text: textOf(root, max) });
    else {
      const w = walker(root); let node = root;
      do {
        if (action === 'find' && node.nodeType === 3) {
          const text = chunk(node);
          if (text.toLocaleLowerCase().includes(a.query.toLocaleLowerCase()) && !add({ tag: tag(node.parentElement), text })) break;
        } else if (action === 'links' && node.nodeType === 1 && node.localName === 'a' && node.hasAttribute('href')) {
          const raw = node.getAttribute('href'), base = document.baseURI;
          if (raw.length > 4096 || base.length > 8192) { skippedLinks++; continue; }
          let url;
          try { url = new URL(raw, base); } catch { skippedLinks++; continue; }
          if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.href.length > 4096) { skippedLinks++; continue; }
          if (seen.has(url.href)) continue;
          seen.add(url.href);
          if (!add({ tag: 'a', text: textOf(node, Math.max(0, max - used - url.href.length)), href: url.href, sameOrigin: url.origin === location.origin })) break;
        }
      } while ((node = w.nextNode()));
    }
  }
} catch (error) {
  if (error !== stop) return { error: 'invalid semantic selector', kind: 'invalid_selector' };
}
const url = location.href, title = document.title;
const metadataTruncated = { url: url.length > 8192, title: title.length > 4096 };
return { backend: 'webkit-dom', source: 'live-dom', action, doc, url: url.slice(0, 8192), title: title.slice(0, 4096),
  records, count: records.length, truncated: truncated || metadataTruncated.url || metadataTruncated.title,
  visited, scannedCharacters: scanned, skippedLinks, metadataTruncated,
  scope: 'main-document-light-dom', visibilityChecked: false, layoutChecked: false,
  text: records.map(r => r.href ? r.text + '\t' + r.href : r.text).join('\n') };
"""#

func semanticCommands() -> [Command] {
    [Command(name: "dom", help: "Explicit structural live-DOM read, find or links without style/layout, hit testing, scrolling or input. Uses a retained WebKit context; does not create a render host on a new nonvisual tab. CSS-hidden text may be included. Scripts, form values, frames and shadow trees are excluded. Results are not visible/actionable state or proof of server freshness. No synthetic actions or automatic navigation.",
             params: [Param(name: "action", kind: .string, help: "read, find or links", required: true, positional: true),
                      Param(name: "selector", kind: .string, help: "One tag/id/class compound root (default body); no attributes, combinators, colons or escapes"),
                      Param(name: "query", kind: .string, help: "Case-insensitive substring for find; matches individual text nodes"),
                      Param(name: "limit", kind: .number, help: "Maximum find/link records, 1–200 (default 50)"),
                      Param(name: "max", kind: .number, help: "Total output text/URL character budget, 1–100000 (default 16000)")]) { args, engine, reply in
        let action = args.string("action") ?? ""
        guard ["read", "find", "links"].contains(action) else { return reply(.failure(UmbraError(64, "dom action must be read, find or links"))) }
        let selector = args.string("selector") ?? "body", query = args.string("query") ?? ""
        guard !selector.isEmpty, selector.utf8.count <= 2048, query.utf8.count <= 2048,
              action != "find" || !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return reply(.failure(UmbraError(64, "dom needs a selector/query of at most 2048 bytes and a nonempty query for find")))
        }
        guard action == "find" || args.values["query"] == nil else { return reply(.failure(UmbraError(64, "query is only supported by dom find"))) }
        var options: [String: Any] = ["action": action, "selector": selector, "query": query]
        for (key, fallback, high) in [("limit", 50.0, 200.0), ("max", 16000.0, 100000.0)] {
            let value = args.number(key) ?? fallback
            guard value.isFinite, value.rounded() == value, value >= 1, value <= high else { return reply(.failure(UmbraError(64, "\(key) must be an integer from 1 to \(Int(high))"))) }
            options[key] = Int(value)
        }
        guard let tab = pick(args, engine, reply) else { return }
        tab.run(semanticJS, ["a": options], in: umbraWorld) { result in
            switch result {
            case .failure(let error): reply(.failure(error))
            case .success(let value):
                guard var out = value as? [String: Any] else { return reply(.failure(UmbraError(5, "could not represent semantic DOM read"))) }
                if let error = out["error"] as? String { return reply(.failure(UmbraError(64, error, details: ["kind": out["kind"] ?? "invalid_selector"]))) }
                out["rendered"] = tab.rendered; out["windowCreated"] = tab.windowCreated
                reply(.success(out))
            }
        }
    }]
}
