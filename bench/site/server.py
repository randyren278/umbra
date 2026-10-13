#!/usr/bin/env python3
"""Northwind Ops: a small, deterministic SaaS app that Umbra's agent tasks and benchmarks run on.

    python3 bench/site/server.py --port 8765          (--port 0 picks a free port and prints it)

Stdlib only, no external assets, no network calls. Every number comes from a seed (default 7), so
each run shows the same orders, feed and reports. The server logs what the user did:

    GET  /__state   the event log, orders and settings, as JSON (for graders)
    POST /__reset   clear the log, sessions, orders and settings; optional JSON {"seed": 7, "otp": false}
    GET  /__login   sign in without the form (?next=/app/...), for the benchmark

Sign in as agent / umbra-pass. With otp on, a second step asks for the code 424242.
Agents under test must not be told about the /__ endpoints.
"""
import argparse
import csv
import datetime
import hashlib
import html
import io
import json
import random
import re
import secrets
import struct
import threading
import time
import urllib.parse
import zlib
from functools import lru_cache
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

USER, PASSWORD, OTP = 'agent', 'umbra-pass', '424242'
SEED = 7
PER_PAGE = 10
FEED_TOTAL, FEED_BATCH = 100, 20

CUSTOMERS = ['Acme Freight', 'Blue Harbor Foods', 'Cedar & Pine', 'Delta Outfitters', 'Evergreen Clinic',
             'Foxglove Studio', 'Granite Works', 'Harbor Light Co.', 'Iris Analytics', 'Juniper Bakery',
             'Kestrel Air', 'Lumen Labs', 'Maple Logistics', 'Northbeam Media', 'Oakridge Dental', 'Pioneer Tools']
PRODUCTS = ['Pallet wrap (case of 4)', 'Thermal labels 4×6 (2,000)', 'Corrugated boxes 18″ (bundle)',
            'Packing tape (36 rolls)', 'Handheld barcode scanner', 'Folding hand truck', 'Bench shipping scale']
CITIES = ['Rotterdam', 'Oakland', 'Savannah', 'Vancouver', 'Hamburg', 'Busan', 'Valencia', 'Santos']
EVENTS = ['Shipment {n} cleared customs in {city}', 'Invoice {n} paid by {cust}', 'Driver checked in at the {city} depot',
          'Return {n} received from {cust}', 'Stock alert: SKU {n} is below its reorder point',
          'New customer onboarded: {cust}', 'Route {n} re-planned, saving {km} km']
PLANS = {'starter': 'Starter', 'team': 'Team', 'enterprise': 'Enterprise'}
SWATCHES = [('Blue', '#2563eb'), ('Teal', '#0d9488'), ('Violet', '#7c3aed'), ('Rose', '#e11d48'),
            ('Amber', '#d97706'), ('Slate', '#475569')]
DEFAULT_SETTINGS = {'display_name': 'Alex Rivera', 'bio': 'Operations lead at Northwind.',
                    'notifications': True, 'theme_color': '#2563eb'}
EMAIL = re.compile(r'[^\s@]+@[^\s@]+\.[^\s@]{2,}')


# ---------------------------------------------------------------- data (pure functions of the seed)

def make_orders(seed=SEED):
    r = random.Random(seed)
    orders = []
    for i in range(40):
        oid = 1001 + i
        status = r.choices(('pending', 'paid', 'shipped', 'refunded'), (3, 3, 4, 1))[0]
        if oid == 1017:
            status = 'paid'  # the refund task needs a refundable order at every seed
        orders.append({'id': oid, 'customer': r.choice(CUSTOMERS), 'total': round(r.uniform(38, 2400), 2),
                       'status': status, 'date': (datetime.date(2026, 8, 24) + datetime.timedelta(days=i)).isoformat()})
    return orders


def order_lines(order, seed=SEED):
    r = random.Random(seed * 10000 + order['id'])
    names = r.sample(PRODUCTS, r.randint(1, 3))
    cents = round(order['total'] * 100)
    cuts = sorted(r.sample(range(1, cents), len(names) - 1)) if len(names) > 1 else []
    amounts = [b - a for a, b in zip([0] + cuts, cuts + [cents])]
    return [(name, amount / 100) for name, amount in zip(names, amounts)]


def feed_items(seed=SEED):
    r = random.Random(seed + 1)
    codes = r.sample(range(1000, 10000), FEED_TOTAL)
    featured = r.randrange(60, FEED_TOTAL)
    items = []
    for i in range(FEED_TOTAL):
        title = r.choice(EVENTS).format(n=r.randint(2000, 9999), city=r.choice(CITIES), cust=r.choice(CUSTOMERS),
                                        km=r.randint(4, 90))
        minutes = 4 + i * 13
        ago = f'{minutes} min ago' if minutes < 60 else f'{minutes // 60} h ago'
        items.append({'n': i + 1, 'code': f'FX-{codes[i]}', 'title': title, 'ago': ago, 'featured': i == featured})
    return items


def report_number(seed=SEED):
    return f'RPT-{random.Random(seed + 2).randint(100000, 999999)}'


def open_tickets(seed=SEED):
    return random.Random(seed + 3).randint(5, 29)


def forecast(seed=SEED):
    return '${:,}'.format(random.Random(seed + 4).randint(900, 1900) * 1000)


def confirmation(name, email, plan, seats):
    digest = hashlib.sha256(f'{name.strip()}|{email.strip()}|{plan}|{seats}'.lower().encode()).hexdigest()
    return f'NW-{int(digest, 16) % 900000 + 100000}'


def receipt(sha256):
    return 'RC-' + sha256[:8].upper()


def money(value):
    return '${:,.2f}'.format(value)


# ---------------------------------------------------------------- images (PNG, generated in memory)

def png(width, height, rows, color_type):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    raw = b''.join(b'\0' + row for row in rows)
    header = struct.pack('>IIBBBBB', width, height, 8, color_type, 0, 0, 0)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b'')


@lru_cache(maxsize=256)
def product_image(n):
    """96×96 tinted noise, about 20 KB and barely compressible, so 200 of them are real work to load."""
    r = random.Random(n)
    size = 96
    base = [r.randint(40, 190) for _ in range(3)]
    pixels = bytearray(r.randbytes(size * size * 3))
    for channel in range(3):
        table = bytes(min(255, base[channel] + (x >> 2)) for x in range(256))
        pixels[channel::3] = bytes(pixels[channel::3]).translate(table)
    stride = size * 3
    return png(size, size, [bytes(pixels[y * stride:(y + 1) * stride]) for y in range(size)], 2)


DIGITS = {'0': '111101101101111', '1': '010110010010111', '2': '111001111100111', '3': '111001111001111',
          '4': '101101111001001', '5': '111100111001111', '6': '111100111101111', '7': '111001010010010',
          '8': '111101111101111', '9': '111101111001111'}


def captcha_image(code):
    """The code in jittered block digits over speckle and stray lines: meant for human eyes."""
    r = random.Random(code)
    width, height, scale = 240, 80, 7
    canvas = [bytearray(r.randint(200, 255) for _ in range(width)) for _ in range(height)]
    for i, digit in enumerate(code):
        x0, y0 = 18 + i * 36 + r.randint(-4, 4), 16 + r.randint(-8, 8)
        for k, bit in enumerate(DIGITS[digit]):
            if bit == '1':
                for y in range(scale):
                    for x in range(scale):
                        px, py = x0 + (k % 3) * scale + x + y // 4, y0 + (k // 3) * scale + y
                        if 0 <= px < width and 0 <= py < height:
                            canvas[py][px] = r.randint(20, 90)
    for _ in range(5):
        y, slope = r.randint(0, height - 1), r.uniform(-0.3, 0.3)
        for x in range(width):
            py = int(y + slope * x)
            if 0 <= py < height:
                canvas[py][x] = r.randint(40, 120)
    return png(width, height, [bytes(row) for row in canvas], 0)


# ---------------------------------------------------------------- state

class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self, seed=SEED, otp=False):
        with self.lock:
            self.seed, self.otp = seed, otp
            self.started = time.monotonic()
            self.events = []
            self.sessions = {}    # session token -> user
            self.pending = set()  # tokens that passed the password and wait for the OTP
            self.orders = {o['id']: o for o in make_orders(seed)}
            self.settings = dict(DEFAULT_SETTINGS)
            self.captcha = ''.join(secrets.choice('0123456789') for _ in range(6))  # not seeded: unguessable

    def log(self, kind, **fields):
        with self.lock:
            self.events.append({'seq': len(self.events) + 1, 'ms': round((time.monotonic() - self.started) * 1000),
                                'type': kind, **fields})

    def snapshot(self):
        with self.lock:
            return {'seed': self.seed, 'otp': self.otp, 'events': list(self.events),
                    'orders': [dict(o) for o in self.orders.values()], 'settings': dict(self.settings)}


STATE = State()


# ---------------------------------------------------------------- page chrome

CSS = """
:root{--bg:#f5f6f8;--panel:#fff;--ink:#161a22;--muted:#5d6472;--line:#e3e6eb;--accent:#2563eb;--danger:#c42b2b;--radius:10px}
*{box-sizing:border-box}
body{margin:0;font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;color:var(--ink);background:var(--bg)}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.top{display:flex;align-items:center;gap:16px;height:56px;padding:0 20px;background:var(--panel);border-bottom:1px solid var(--line)}
.brand{display:flex;align-items:center;gap:10px;font-weight:650;color:var(--ink);font-size:15px}
.mark{width:28px;height:28px;border-radius:7px;background:linear-gradient(135deg,#1e3a8a,var(--accent) 60%,#38bdf8);color:#fff;display:grid;place-items:center;font-weight:700}
.who{margin-left:auto;color:var(--muted);display:flex;gap:14px;align-items:center}
.avatar{width:28px;height:28px;border-radius:50%;background:#dbe3f4;color:#1e3a8a;display:grid;place-items:center;font-weight:700;font-size:12px}
.shell{display:grid;grid-template-columns:216px 1fr;min-height:calc(100vh - 56px)}
nav.side{padding:16px 12px;border-right:1px solid var(--line);background:var(--panel)}
nav.side a{display:block;padding:8px 12px;border-radius:8px;color:var(--ink);margin-bottom:2px}
nav.side a:hover{background:#f1f3f6;text-decoration:none}
nav.side a[aria-current=page]{background:#eaf0ff;color:#1e40af;font-weight:600}
main{padding:28px 32px;max-width:1080px;width:100%}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:0 0 12px}
.sub{color:var(--muted);margin:0 0 20px}.muted{color:var(--muted)}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:24px}
.card,.panel{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius)}
.card{padding:16px 18px}.panel{padding:20px;margin-bottom:20px}
.card .label{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.card .value{font-size:26px;font-weight:650;margin-top:4px;font-variant-numeric:tabular-nums}
.card .delta{font-size:12px;color:#16653a}
table{width:100%;border-collapse:collapse}
th,td{padding:10px 12px;text-align:left;border-bottom:1px solid var(--line)}
th{font-size:12px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}td.empty{color:var(--muted);text-align:center;padding:28px}
tbody tr:hover{background:#fafbfc}
.badge{display:inline-block;padding:2px 9px;border-radius:999px;font-size:12px;font-weight:600}
.badge.pending{background:#fff3d1;color:#7f5300}.badge.paid{background:#e0eaff;color:#1d4ed8}
.badge.shipped{background:#dcf4e6;color:#14603a}.badge.refunded{background:#eef0f3;color:#5d6472}
.btn{appearance:none;border:1px solid transparent;background:var(--accent);color:#fff;padding:8px 14px;border-radius:8px;font:inherit;font-weight:600;cursor:pointer}
.btn.secondary{background:var(--panel);color:var(--ink);border-color:#cfd4dc}.btn.danger{background:var(--danger)}
.btn:disabled{opacity:.45;cursor:default}
.toolbar{display:flex;gap:12px;align-items:center;margin-bottom:16px}.spacer{flex:1}
.pager{display:flex;gap:12px;align-items:center;justify-content:flex-end;margin-top:14px}
label,.label-text{display:block;font-weight:600;margin:0 0 6px}label.inline{display:inline;margin:0}
input[type=text],input[type=email],input[type=password],input[type=number],select,.editable{width:100%;padding:9px 11px;border:1px solid #cfd4dc;border-radius:8px;font:inherit;background:#fff;color:inherit}
select{width:auto}.editable{min-height:72px}
input:focus,select:focus,.editable:focus{outline:2px solid color-mix(in srgb,var(--accent) 35%,transparent);border-color:var(--accent)}
input[aria-invalid=true]{border-color:var(--danger)}
.field{margin-bottom:16px}.error{color:var(--danger);font-size:13px;margin-top:4px;min-height:1px}
.alert{background:#fdecec;color:#8f1d1d;border:1px solid #f5c2c2;padding:10px 12px;border-radius:8px;margin-bottom:16px}
.notice{background:#e8f6ee;color:#14532d;border:1px solid #b7e2c8;padding:10px 12px;border-radius:8px;margin-top:16px}
.row{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:12px 0;border-top:1px solid var(--line)}
.switch{width:44px;height:24px;border-radius:999px;border:0;background:#c5ccd6;position:relative;cursor:pointer;padding:0;flex:none}
.switch::after{content:"";position:absolute;top:3px;left:3px;width:18px;height:18px;border-radius:50%;background:#fff;transition:left .15s}
.switch[aria-checked=true]{background:var(--accent)}.switch[aria-checked=true]::after{left:23px}
.auth{min-height:100vh;display:grid;place-items:center;padding:24px}
.auth-card{width:380px;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:28px;box-shadow:0 8px 30px rgba(20,30,60,.06)}
.auth-card .brand{margin-bottom:18px}.check{display:flex;gap:8px;align-items:center;font-weight:400;margin:4px 0 18px}
.check input{margin:0}.wide{width:100%}
.steps{display:flex;gap:8px;margin:0 0 20px;padding:0;list-style:none}
.steps li{flex:1;padding:8px 10px;border-radius:8px;background:#eef0f3;color:var(--muted);font-weight:600;font-size:13px}
.steps li[aria-current=step]{background:#eaf0ff;color:#1e40af}.steps li.done{background:#dcf4e6;color:#14603a}
fieldset{border:0;padding:0;margin:0 0 16px}legend{font-weight:600;margin-bottom:6px;padding:0}
.choice{display:flex;gap:10px;align-items:flex-start;border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin-bottom:8px;font-weight:400}
.choice strong{display:block}.actions{display:flex;gap:10px;justify-content:flex-end;margin-top:8px}
dl.review{display:grid;grid-template-columns:140px 1fr;gap:6px 12px;margin:0 0 16px}dl.review dt{color:var(--muted)}dl.review dd{margin:0}
.feed{list-style:none;margin:0;padding:0}
.item{display:flex;gap:12px;align-items:flex-start;padding:14px 16px;border-bottom:1px solid var(--line)}
.item .dot{width:10px;height:10px;border-radius:50%;background:#c5ccd6;margin-top:6px;flex:none}
.item p{margin:0}.item .meta{color:var(--muted);font-size:12px}.item .star{margin-left:auto;color:#a15c00;font-weight:700;white-space:nowrap}
.item.featured{background:#fffaf0}.item.featured .dot{background:#d97706}
.sentinel{padding:18px;text-align:center;color:var(--muted)}
.dropzone{border:2px dashed #cfd4dc;border-radius:12px;padding:36px;text-align:center;background:#fafbfc}
.spinner{width:28px;height:28px;border-radius:50%;border:3px solid #dbe1ea;border-top-color:var(--accent);animation:spin 1s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.big{font-size:40px;font-weight:700;margin:8px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(110px,1fr));gap:12px}
.grid figure{margin:0;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:6px;text-align:center;font-size:12px;color:var(--muted)}
.grid img{display:block;width:96px;height:96px;margin:0 auto 4px;border-radius:4px;background:#eef0f3}
iframe{width:100%;height:640px;border:1px solid var(--line);border-radius:var(--radius);background:var(--panel)}
.kv{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
"""

NAV = [('/app', 'Dashboard'), ('/app/orders', 'Orders'), ('/app/feed', 'Activity'), ('/app/wizard', 'Onboarding'),
       ('/app/upload', 'Upload invoice'), ('/app/popup', 'Reports'), ('/app/settings', 'Settings'),
       ('/app/iframe', 'Embedded widget')]


def fill(template, **values):
    for key, value in values.items():
        template = template.replace('{{' + key + '}}', str(value))
    return template


def document(title, body, script='', accent=None):
    style = f' style="--accent:{accent}"' if accent else ''
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{html.escape(title)} · Northwind Ops</title><link rel="icon" href="data:,"><style>{CSS}</style></head>'
            f'<body{style}>{body}' + (f'<script>{script}</script>' if script else '') + '</body></html>')


def app_page(path, title, content, script=''):
    settings = STATE.settings
    name = settings['display_name']
    initials = ''.join(part[0] for part in name.split()[:2]).upper() or '?'
    links = ''.join(f'<a href="{href}"' + (' aria-current="page"' if href == path else '') + f'>{label}</a>'
                    for href, label in NAV)
    body = (f'<header class="top"><a class="brand" href="/app"><span class="mark">N</span>Northwind Ops</a>'
            f'<div class="who"><span class="avatar" aria-hidden="true">{html.escape(initials)}</span>'
            f'<span>Signed in as <strong id="who">{html.escape(name)}</strong></span><a href="/logout">Sign out</a></div></header>'
            f'<div class="shell"><nav class="side" aria-label="Main">{links}</nav><main>{content}</main></div>')
    return document(title, body, script, settings['theme_color'])


# ---------------------------------------------------------------- pages

LOGIN = """<div class="auth"><div class="auth-card">
<div class="brand"><span class="mark">N</span>Northwind Ops</div>
<h1>Sign in</h1><p class="sub">Use your Northwind account.</p>
{{error}}
<form method="post" action="/login">
<input type="hidden" name="next" value="{{next}}">
<div class="field"><label for="username">Username</label><input type="text" id="username" name="username" autocomplete="username" value="{{username}}" required></div>
<div class="field"><label for="password">Password</label><input type="password" id="password" name="password" autocomplete="current-password" required></div>
<label class="check"><input type="checkbox" id="remember" name="remember" value="1"{{remember}}> Remember me on this device</label>
<button class="btn wide" type="submit">Sign in</button>
</form></div></div>"""

OTP_PAGE = """<div class="auth"><div class="auth-card">
<div class="brand"><span class="mark">N</span>Northwind Ops</div>
<h1>Two-step verification</h1><p class="sub">Enter the 6-digit code from your authenticator app.</p>
{{error}}
<form method="post" action="/login/otp">
<input type="hidden" name="next" value="{{next}}">
<div class="field"><label for="otp">Verification code</label><input type="text" id="otp" name="otp" inputmode="numeric" autocomplete="one-time-code" maxlength="6" pattern="[0-9]{6}" required></div>
<button class="btn wide" type="submit">Verify</button>
</form></div></div>"""

DASHBOARD = """<h1>Dashboard</h1><p class="sub">Operations at a glance for September 2026.</p>
<section class="cards" aria-label="Key figures">
<div class="card"><div class="label">Revenue (MTD)</div><div class="value">{{revenue}}</div><div class="delta">▲ 8.2% vs August</div></div>
<div class="card"><div class="label">Orders</div><div class="value">{{orders}}</div><div class="delta">▲ 5 vs August</div></div>
<div class="card"><div class="label">Open tickets</div><div class="value" id="open-tickets">{{tickets}}</div><div class="delta">3 due today</div></div>
<div class="card"><div class="label">On-time delivery</div><div class="value">96.4%</div><div class="delta">▲ 1.1 pts</div></div>
</section>
<section class="panel"><div class="toolbar"><h2 style="margin:0">Recent orders</h2><span class="spacer"></span><a href="/app/orders">Orders →</a></div>
<table><thead><tr><th>Order</th><th>Customer</th><th class="num">Total</th><th>Status</th></tr></thead><tbody>{{recent}}</tbody></table></section>"""

ORDERS = """<h1>Orders</h1><p class="sub">Every order placed through Northwind, newest first.</p>
<div class="panel">
<div class="toolbar"><label class="inline" for="status-filter">Status</label>
<select id="status-filter"><option value="">All statuses</option><option value="pending">Pending</option><option value="paid">Paid</option><option value="shipped">Shipped</option><option value="refunded">Refunded</option></select>
<span class="muted" id="count" aria-live="polite">Loading…</span><span class="spacer"></span>
<button class="btn secondary" type="button" id="export">Export CSV</button></div>
<table id="orders" aria-busy="true"><thead><tr><th>Order</th><th>Customer</th><th class="num">Total</th><th>Status</th><th>Date</th></tr></thead><tbody id="rows"></tbody></table>
<div class="pager"><span class="muted" id="page-label"></span><button class="btn secondary" type="button" id="prev" disabled>Prev</button><button class="btn secondary" type="button" id="next" disabled>Next</button></div>
</div>"""

ORDERS_JS = r"""
const rows = document.getElementById('rows'), count = document.getElementById('count'), label = document.getElementById('page-label');
const prev = document.getElementById('prev'), next = document.getElementById('next'), filter = document.getElementById('status-filter');
const table = document.getElementById('orders');
let page = 1;
const money = n => '$' + n.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'})[c]);
async function load() {
  table.setAttribute('aria-busy', 'true');
  const response = await fetch('/api/orders?page=' + page + '&status=' + encodeURIComponent(filter.value));
  const data = await response.json();
  rows.innerHTML = data.orders.map(o => `<tr><td><a href="/app/orders/${o.id}">#${o.id}</a></td><td>${esc(o.customer)}</td><td class="num">${money(o.total)}</td><td><span class="badge ${o.status}">${o.status[0].toUpperCase() + o.status.slice(1)}</span></td><td>${o.date}</td></tr>`).join('')
    || '<tr><td colspan="5" class="empty">No orders match this filter.</td></tr>';
  const first = (data.page - 1) * data.per_page + 1, last = Math.min(data.page * data.per_page, data.total);
  count.textContent = data.total ? `Showing ${first}–${last} of ${data.total} orders` : 'No orders';
  label.textContent = `Page ${data.page} of ${data.pages}`;
  prev.disabled = data.page <= 1;
  next.disabled = data.page >= data.pages;
  table.setAttribute('aria-busy', 'false');
}
prev.addEventListener('click', () => { page--; load(); });
next.addEventListener('click', () => { page++; load(); });
filter.addEventListener('change', () => { page = 1; load(); });
document.getElementById('export').addEventListener('click', () => { location.href = '/api/orders.csv'; });
load();
"""

ORDER = """<p><a href="/app/orders">← Orders</a></p>
<h1>Order #{{id}}</h1><p class="sub">Placed {{date}} by {{customer}}</p>
<div class="panel"><div class="kv">
<div><div class="label-text">Status</div><span id="status" class="badge {{status}}">{{status_label}}</span></div>
<div><div class="label-text">Total</div><span id="total">{{total}}</span></div>
<div><div class="label-text">Ship to</div>{{customer}}, {{city}}</div></div></div>
<div class="panel"><h2>Items</h2><table><thead><tr><th>Item</th><th class="num">Amount</th></tr></thead><tbody>{{lines}}</tbody></table></div>
<div class="panel"><h2>Refund</h2>{{refund}}<div id="refund-result" role="status"></div></div>"""

ORDER_JS = r"""
const button = document.getElementById('refund');
if (button) button.addEventListener('click', async () => {
  if (!confirm(button.dataset.confirm)) return;
  button.disabled = true;
  const response = await fetch('/api/refund', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id: Number(button.dataset.id)})});
  const data = await response.json();
  const note = document.getElementById('refund-result');
  if (response.ok) {
    note.className = 'notice';
    note.textContent = `Refund ${data.refund} issued for ${data.amount}.`;
    const status = document.getElementById('status');
    status.className = 'badge refunded';
    status.textContent = 'Refunded';
  } else {
    note.className = 'alert';
    note.textContent = data.error;
    button.disabled = false;
  }
});
"""

SETTINGS = """{{heading}}
<form id="settings" class="panel" novalidate data-source="{{source}}">
<div class="field"><label for="display-name">Display name</label><input type="text" id="display-name" name="display_name" autocomplete="name"></div>
<div class="field"><span class="label-text" id="bio-label">Bio</span><div id="bio" class="editable" contenteditable="true" role="textbox" aria-multiline="true" aria-labelledby="bio-label">{{bio}}</div></div>
<div class="row"><div><span class="label-text" id="notifications-label" style="margin:0">Email notifications</span><span class="muted">Order updates and weekly summaries.</span></div>
<button type="button" class="switch" id="notifications" role="switch" aria-checked="{{notifications}}" aria-labelledby="notifications-label"></button></div>
<div class="row"><div><span class="label-text" style="margin:0">Theme color</span><span class="muted">Used for buttons and highlights.</span></div>
<nw-color-picker id="theme" value="{{theme}}"></nw-color-picker></div>
<div class="actions"><button class="btn" type="submit" id="save">Save changes</button></div>
<div id="saved" role="status"></div>
</form>"""

SETTINGS_JS = r"""
// A tiny React-style controlled input: the field shows `state`, and only input events that really
// change the value (as React's value tracker sees it) update `state`. Writing .value directly
// without the native setter is ignored and reverted, exactly as in a React app.
const form = document.getElementById('settings');
const nameInput = document.getElementById('display-name');
const native = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
let tracked = '';
Object.defineProperty(nameInput, 'value', {
  configurable: true,
  get() { return native.get.call(this); },
  set(v) { tracked = String(v); native.set.call(this, v); },
});
const state = {display_name: SAVED.display_name};
function render() { if (native.get.call(nameInput) !== state.display_name) nameInput.value = state.display_name; }
nameInput.addEventListener('input', () => {
  const v = native.get.call(nameInput);
  if (v !== tracked) { tracked = v; state.display_name = v; }
  render();
});
nameInput.addEventListener('blur', render);
render();

const toggle = document.getElementById('notifications');
toggle.addEventListener('click', () => toggle.setAttribute('aria-checked', toggle.getAttribute('aria-checked') === 'true' ? 'false' : 'true'));

class ColorPicker extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode: 'open'}).innerHTML = `<style>
      :host{display:inline-flex;align-items:center;gap:12px}
      .swatches{display:flex;gap:8px}
      button{width:26px;height:26px;border-radius:50%;border:2px solid #fff;box-shadow:0 0 0 1px #cfd4dc;background:var(--c);cursor:pointer;padding:0}
      button[aria-checked=true]{box-shadow:0 0 0 2px var(--c)}
      input{width:92px;padding:6px 8px;border:1px solid #cfd4dc;border-radius:6px;font:13px ui-monospace,Menlo,monospace}
    </style><div class="swatches" role="radiogroup" aria-label="Theme color">${SWATCHES.map(([name, hex]) =>
      `<button type="button" role="radio" aria-checked="false" aria-label="${name}" title="${name}" data-hex="${hex}" style="--c:${hex}"></button>`).join('')}</div>
      <input aria-label="Custom hex color" spellcheck="false">`;
    this.shadowRoot.querySelectorAll('button').forEach(b => b.addEventListener('click', () => this.choose(b.dataset.hex)));
    const hex = this.shadowRoot.querySelector('input');
    hex.addEventListener('change', () => {
      if (/^#[0-9a-f]{6}$/i.test(hex.value.trim())) this.choose(hex.value.trim().toLowerCase()); else hex.value = this.value;
    });
  }
  connectedCallback() { this.value = this.getAttribute('value') || '#2563eb'; }
  get value() { return this._value; }
  set value(v) {
    this._value = v;
    this.shadowRoot.querySelectorAll('button').forEach(b => b.setAttribute('aria-checked', String(b.dataset.hex === v)));
    this.shadowRoot.querySelector('input').value = v;
  }
  choose(v) {
    this.value = v;
    document.body.style.setProperty('--accent', v);
    this.dispatchEvent(new Event('change', {bubbles: true, composed: true}));
  }
}
customElements.define('nw-color-picker', ColorPicker);

form.addEventListener('submit', async event => {
  event.preventDefault();
  const body = {display_name: state.display_name, bio: document.getElementById('bio').innerText.trim(),
                notifications: toggle.getAttribute('aria-checked') === 'true', theme_color: document.getElementById('theme').value,
                source: form.dataset.source};
  const saved = document.getElementById('saved');
  const response = await fetch('/api/settings', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  saved.className = response.ok ? 'notice' : 'alert';
  saved.textContent = response.ok ? 'Settings saved.' : 'Could not save your settings.';
  const who = window.top.document.getElementById('who');
  if (response.ok && who) who.textContent = body.display_name;
});
"""

UPLOAD = """<h1>Upload invoice</h1><p class="sub">Attach a supplier invoice to reconcile it against open orders.</p>
<div class="panel"><div class="dropzone">
<p><strong>PDF or image, up to 10 MB</strong></p>
<button class="btn" type="button" id="choose">Choose file…</button>
<p class="muted" id="chosen">No file chosen</p>
<input type="file" id="file" name="invoice" accept=".pdf,image/*" hidden>
</div><div id="receipt" role="status"></div></div>"""

UPLOAD_JS = r"""
const input = document.getElementById('file');
document.getElementById('choose').addEventListener('click', () => input.click());
input.addEventListener('change', async () => {
  const file = input.files[0];
  if (!file) return;
  document.getElementById('chosen').textContent = file.name;
  const body = new FormData();
  body.append('invoice', file);
  const receipt = document.getElementById('receipt');
  receipt.className = 'muted';
  receipt.textContent = 'Uploading…';
  const response = await fetch('/api/upload', {method: 'POST', body});
  const data = await response.json();
  receipt.className = response.ok ? 'notice' : 'alert';
  receipt.innerHTML = response.ok
    ? `Uploaded <strong></strong> (${data.size.toLocaleString()} bytes). Receipt code: <strong id="receipt-code">${data.receipt}</strong>`
    : 'Upload failed: ' + data.error;
  if (response.ok) receipt.querySelector('strong').textContent = data.filename;
});
"""

WIZARD = """<h1>Onboarding</h1><p class="sub">Set up a new customer workspace in three steps.</p>
<div class="panel" id="wizard">
<ol class="steps"><li aria-current="step">1. Account</li><li>2. Plan</li><li>3. Review</li></ol>
<form id="wizard-form" novalidate>
<section class="step" data-step="0">
<div class="field"><label for="full-name">Full name</label><input type="text" id="full-name" autocomplete="name" aria-describedby="full-name-error"><div class="error" id="full-name-error"></div></div>
<div class="field"><label for="email">Work email</label><input type="email" id="email" autocomplete="email" aria-describedby="email-error"><div class="error" id="email-error"></div></div>
<div class="field"><label for="company">Company <span class="muted">(optional)</span></label><input type="text" id="company" autocomplete="organization"></div>
</section>
<section class="step" data-step="1" hidden>
<fieldset id="plan" aria-describedby="plan-error"><legend>Plan</legend>
<label class="choice"><input type="radio" name="plan" value="starter"><span><strong>Starter</strong><span class="muted">Up to 5 seats, email support</span></span></label>
<label class="choice"><input type="radio" name="plan" value="team"><span><strong>Team</strong><span class="muted">Shared inboxes, SSO, priority support</span></span></label>
<label class="choice"><input type="radio" name="plan" value="enterprise"><span><strong>Enterprise</strong><span class="muted">Audit log, custom contracts</span></span></label>
<div class="error" id="plan-error"></div></fieldset>
<div class="field"><label for="seats">Seats</label><input type="number" id="seats" min="1" max="500" value="1" aria-describedby="seats-error"><div class="error" id="seats-error"></div></div>
</section>
<section class="step" data-step="2" hidden>
<dl class="review" id="review"></dl>
<label class="check"><input type="checkbox" id="terms" aria-describedby="terms-error"> I agree to the terms of service</label>
<div class="error" id="terms-error"></div>
</section>
<div class="actions"><button class="btn secondary" type="button" id="back" hidden>Back</button><button class="btn" type="submit" id="continue">Continue</button></div>
</form></div>"""

WIZARD_JS = r"""
const form = document.getElementById('wizard-form'), steps = [...form.querySelectorAll('.step')];
const markers = [...document.querySelectorAll('.steps li')];
const back = document.getElementById('back'), next = document.getElementById('continue');
const value = id => document.getElementById(id).value.trim();
let at = 0;
function show(i) {
  at = i;
  steps.forEach((s, j) => s.hidden = j !== i);
  markers.forEach((m, j) => { m.className = j < i ? 'done' : ''; j === i ? m.setAttribute('aria-current', 'step') : m.removeAttribute('aria-current'); });
  back.hidden = i === 0;
  next.textContent = i === 2 ? 'Submit' : 'Continue';
  if (i === 2) {
    const plan = form.querySelector('input[name=plan]:checked').parentElement.querySelector('strong').textContent;
    const review = document.getElementById('review');
    review.replaceChildren();
    for (const [term, detail] of [['Name', value('full-name')], ['Email', value('email')], ['Company', value('company') || '—'],
                                  ['Plan', plan], ['Seats', value('seats')]]) {
      review.append(Object.assign(document.createElement('dt'), {textContent: term}),
                    Object.assign(document.createElement('dd'), {textContent: detail}));
    }
  }
  steps[i].querySelector('input')?.focus();
}
function check(id, message) {
  const field = document.getElementById(id);
  field.setAttribute('aria-invalid', message ? 'true' : 'false');
  document.getElementById(id + '-error').textContent = message;
  return !message;
}
function validate(i) {
  if (i === 0) {
    const email = value('email');
    const a = check('full-name', value('full-name') ? '' : 'Enter your full name.');
    const b = check('email', !email ? 'Enter your work email.' : /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(email) ? '' : 'Enter a valid email address, like name@company.com.');
    return a && b;
  }
  if (i === 1) {
    const seats = Number(value('seats'));
    const a = check('plan', form.querySelector('input[name=plan]:checked') ? '' : 'Choose a plan.');
    const b = check('seats', Number.isInteger(seats) && seats >= 1 && seats <= 500 ? '' : 'Seats must be a whole number from 1 to 500.');
    return a && b;
  }
  return check('terms', document.getElementById('terms').checked ? '' : 'Accept the terms of service to continue.');
}
back.addEventListener('click', () => show(at - 1));
form.addEventListener('submit', async event => {
  event.preventDefault();
  if (!validate(at)) return;
  if (at < 2) return show(at + 1);
  next.disabled = true;
  const body = {name: value('full-name'), email: value('email'), company: value('company'),
                plan: form.querySelector('input[name=plan]:checked').value, seats: Number(value('seats'))};
  const response = await fetch('/api/wizard', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) { next.disabled = false; return check('terms', data.error); }
  markers.forEach(m => { m.className = 'done'; m.removeAttribute('aria-current'); });
  form.outerHTML = `<div class="notice" role="status"><strong>You're all set.</strong> The workspace is being created. Confirmation number <strong id="confirmation">${data.confirmation}</strong></div>`;
});
"""

FEED = """<h1>Activity</h1><p class="sub">Everything happening across your fleet, newest first.</p>
<div class="panel" style="padding:0"><ul class="feed" id="items"></ul><div class="sentinel" id="sentinel">Loading more…</div></div>"""

FEED_JS = r"""
const list = document.getElementById('items'), sentinel = document.getElementById('sentinel');
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'})[c]);
let offset = 0, busy = false, done = false;
function inView() { const r = sentinel.getBoundingClientRect(); return r.top < innerHeight && r.bottom > 0; }
async function more() {
  if (busy || done) return;
  busy = true;
  const data = await (await fetch('/api/feed?offset=' + offset)).json();
  list.insertAdjacentHTML('beforeend', data.items.map(item => `<li class="item${item.featured ? ' featured' : ''}" data-code="${item.code}">
    <span class="dot"></span><div><p class="title">${esc(item.title)}</p><p class="meta">${item.ago} · ${item.code}</p></div>
    ${item.featured ? '<span class="star">★ featured</span>' : ''}</li>`).join(''));
  offset += data.items.length;
  done = offset >= data.total;
  sentinel.textContent = done ? "You're all caught up." : 'Loading more…';
  busy = false;
  if (!done && inView()) more();  // the observer only fires on a change, so keep filling a tall window
}
new IntersectionObserver(entries => { if (entries.some(e => e.isIntersecting)) more(); }).observe(sentinel);
"""

REPORTS = """<h1>Reports</h1><p class="sub">Quarterly operations reports. Each opens in a new window.</p>
<div class="panel"><table><thead><tr><th>Report</th><th>Published</th></tr></thead><tbody>
<tr><td><a href="/app/report?quarter=2026-Q3" target="_blank" id="q3-report">Q3 2026 operations report ↗</a></td><td>Oct 3, 2026</td></tr>
<tr><td class="muted">Q2 2026 operations report (archived)</td><td class="muted">Jul 6, 2026</td></tr>
</tbody></table></div>"""

REPORT = """<main style="max-width:760px;margin:0 auto">
<header class="brand" style="margin:8px 0 24px"><span class="mark">N</span>Northwind Ops</header>
<h1>Q3 2026 operations report</h1><p class="sub">Report number <strong id="report-number">{{number}}</strong> · Published October 3, 2026</p>
<div class="cards" style="grid-template-columns:repeat(3,1fr)">
<div class="card"><div class="label">Revenue</div><div class="value">$1.42M</div></div>
<div class="card"><div class="label">Shipments</div><div class="value">3,218</div></div>
<div class="card"><div class="label">On-time</div><div class="value">95.8%</div></div></div>
<div class="panel"><h2>Summary</h2><p>Volume grew 6% quarter over quarter, led by the Rotterdam and Savannah lanes. Returns fell to 1.9% of orders.</p></div>
</main>"""

SLOW = """<main style="max-width:640px;margin:48px auto">
<div class="brand" style="margin-bottom:24px"><span class="mark">N</span>Northwind Ops</div>
<h1>Q4 revenue forecast</h1><p class="sub">Crunching the numbers…</p>
<div class="panel" id="result" aria-live="polite"><div class="spinner" role="progressbar" aria-label="Loading"></div></div></main>"""

SLOW_JS = r"""
setTimeout(async () => {
  const data = await (await fetch('/api/forecast')).json();
  document.querySelector('.sub').textContent = 'Updated just now.';
  document.getElementById('result').innerHTML = `<p class="muted">Projected Q4 revenue, all regions</p><p class="big" id="forecast">${data.forecast}</p>`;
}, 1500);
"""

CAPTCHA = """<div class="auth"><div class="auth-card">
<div class="brand"><span class="mark">N</span>Northwind Ops</div>
<h1>Security check</h1><p class="sub">We noticed unusual activity. Type the six digits in the image to see your account balance.</p>
<img src="/captcha.png?t={{nonce}}" alt="Security code (image)" width="240" height="80" style="display:block;border-radius:8px;margin-bottom:16px">
<form id="captcha-form"><div class="field"><label for="code">Digits</label><input type="text" id="code" autocomplete="off" inputmode="numeric" maxlength="6"></div>
<button class="btn wide" type="submit">Verify</button></form><div id="captcha-result" role="status"></div></div></div>"""

CAPTCHA_JS = r"""
document.getElementById('captcha-form').addEventListener('submit', async event => {
  event.preventDefault();
  const response = await fetch('/api/captcha', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({code: document.getElementById('code').value})});
  const data = await response.json();
  const result = document.getElementById('captcha-result');
  result.className = response.ok ? 'notice' : 'alert';
  result.textContent = response.ok ? `Verified. Account balance: ${data.balance}` : 'That was not right. Try again.';
});
window.addEventListener('focus', () => navigator.sendBeacon('/api/captcha/focus'));
if (document.hasFocus()) navigator.sendBeacon('/api/captcha/focus');
"""

HEAVY = """<header class="top"><span class="brand"><span class="mark">N</span>Northwind Ops</span><span class="who">Product catalog</span></header>
<main><h1>Catalog</h1><p class="sub">200 products with photos.</p><div class="grid">{{figures}}</div></main>"""


# ---------------------------------------------------------------- server

def session_cookie(name, value, max_age=None, http_only=True):
    parts = [f'{name}={value}', 'Path=/', 'SameSite=Lax']
    if http_only:
        parts.append('HttpOnly')
    if max_age is not None:
        parts.append(f'Max-Age={max_age}')
    return ('Set-Cookie', '; '.join(parts))


def safe_next(value, default='/app'):
    return value if value.startswith('/') and not value.startswith('//') else default


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'NorthwindOps/1.0'

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.dispatch('GET')

    def do_POST(self):
        self.dispatch('POST')

    def dispatch(self, method):
        url = urllib.parse.urlsplit(self.path)
        self.query = dict(urllib.parse.parse_qsl(url.query))
        length = int(self.headers.get('Content-Length') or 0)
        self.body = self.rfile.read(length) if length else b''
        try:
            jar = SimpleCookie(self.headers.get('Cookie', ''))
        except CookieError:
            jar = SimpleCookie()
        self.cookies = {name: morsel.value for name, morsel in jar.items()}
        self.user = STATE.sessions.get(self.cookies.get('nw_session'))
        try:
            for pattern, methods, route in ROUTES:
                match = re.fullmatch(pattern, url.path)
                if match and method in methods:
                    return route(self, *match.groups())
            if any(re.fullmatch(pattern, url.path) for pattern, _, _ in ROUTES):
                return self.send(405, 'method not allowed', 'text/plain')
            self.send(404, document('Not found', '<main><h1>Page not found</h1><p><a href="/app">Back to the dashboard</a></p></main>'))
        except (BrokenPipeError, ConnectionResetError):
            pass

    # replies

    def send(self, status=200, body=b'', ctype='text/html; charset=utf-8', headers=()):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, location, headers=()):
        self.send(303, b'', 'text/plain', [('Location', location), *headers])

    def json(self, value, status=200):
        self.send(status, json.dumps(value), 'application/json')

    def form(self):
        return dict(urllib.parse.parse_qsl(self.body.decode('utf-8', 'replace')))

    def payload(self):
        try:
            value = json.loads(self.body or b'{}')
            return value if isinstance(value, dict) else {}
        except ValueError:
            return {}

    def page(self, path, title, content, script=''):
        STATE.log('view', path=path)
        self.send(200, app_page(path, title, content, script))

    def signed_in(self):
        """True when signed in; otherwise answers (login redirect or 401) and returns False."""
        if self.user:
            return True
        if self.path.startswith('/api/'):
            self.json({'error': 'not signed in'}, 401)
        else:
            self.redirect('/login?next=' + urllib.parse.quote(self.path, safe='/?=&'))
        return False

    def start_session(self, remember):
        token = secrets.token_urlsafe(18)
        STATE.sessions[token] = USER
        headers = [session_cookie('nw_session', token)]
        if remember:
            headers.append(session_cookie('nw_remember', USER, max_age=30 * 86400, http_only=False))
        return headers

    # test control

    def state(self):
        self.json(STATE.snapshot())

    def reset(self):
        options = self.payload()
        STATE.reset(int(options.get('seed', SEED)), bool(options.get('otp', False)))
        self.json({'ok': True, 'seed': STATE.seed, 'otp': STATE.otp})

    def backdoor(self):
        headers = self.start_session(False)
        STATE.log('bench_login')
        self.redirect(safe_next(self.query.get('next', '/app')), headers)

    # sign-in

    def root(self):
        self.redirect('/app' if self.user else '/login')

    def login_page(self, error='', username=None, remember=False):
        if self.command == 'GET' and self.user:
            return self.redirect(safe_next(self.query.get('next', '/app')))
        STATE.log('view', path='/login')
        box = f'<div class="alert" role="alert">{error}</div>' if error else ''
        body = fill(LOGIN, error=box, next=html.escape(self.query.get('next', '/app')),
                    username=html.escape(username if username is not None else self.cookies.get('nw_remember', '')),
                    remember=' checked' if remember else '')
        self.send(200, document('Sign in', body))

    def login(self):
        form = self.form()
        username, remember = form.get('username', '').strip(), form.get('remember') == '1'
        self.query['next'] = form.get('next', '/app')
        if username != USER or form.get('password') != PASSWORD:
            STATE.log('login_failed', user=username)
            return self.login_page('Incorrect username or password.', username, remember)
        target = safe_next(form.get('next', '/app'))
        if STATE.otp:
            token = secrets.token_urlsafe(18)
            STATE.pending.add(token)
            STATE.log('password_ok', user=username, remember=remember)
            return self.redirect('/login/otp?next=' + urllib.parse.quote(target),
                                 [session_cookie('nw_pending', f'{token}.{int(remember)}')])
        STATE.log('login', user=username, remember=remember)
        self.redirect(target, self.start_session(remember))

    def otp_page(self, error=''):
        token = self.cookies.get('nw_pending', '').split('.')[0]
        if token not in STATE.pending:
            return self.redirect('/login')
        STATE.log('view', path='/login/otp')
        box = f'<div class="alert" role="alert">{error}</div>' if error else ''
        self.send(200, document('Two-step verification', fill(OTP_PAGE, error=box, next=html.escape(self.query.get('next', '/app')))))

    def otp(self):
        form = self.form()
        token, _, remember = self.cookies.get('nw_pending', '').partition('.')
        self.query['next'] = form.get('next', '/app')
        if token not in STATE.pending:
            return self.redirect('/login')
        if form.get('otp', '').strip() != OTP:
            STATE.log('otp', ok=False)
            return self.otp_page("That code didn't work. Check your authenticator and try again.")
        STATE.pending.discard(token)
        STATE.log('otp', ok=True)
        STATE.log('login', user=USER, remember=remember == '1')
        self.redirect(safe_next(form.get('next', '/app')),
                      self.start_session(remember == '1') + [session_cookie('nw_pending', '', max_age=0)])

    def logout(self):
        STATE.sessions.pop(self.cookies.get('nw_session'), None)
        STATE.log('logout')
        self.redirect('/login', [session_cookie('nw_session', '', max_age=0)])

    # app pages

    def dashboard(self):
        if not self.signed_in():
            return
        orders = sorted(STATE.orders.values(), key=lambda o: -o['id'])
        revenue = sum(o['total'] for o in orders if o['status'] in ('paid', 'shipped'))
        recent = ''.join(f'<tr><td><a href="/app/orders/{o["id"]}">#{o["id"]}</a></td><td>{html.escape(o["customer"])}</td>'
                         f'<td class="num">{money(o["total"])}</td><td><span class="badge {o["status"]}">{o["status"].title()}</span></td></tr>'
                         for o in orders[:5])
        self.page('/app', 'Dashboard', fill(DASHBOARD, revenue=money(revenue), orders=len(orders),
                                            tickets=open_tickets(STATE.seed), recent=recent))

    def orders_page(self):
        if self.signed_in():
            self.page('/app/orders', 'Orders', ORDERS, ORDERS_JS)

    def order_page(self, oid):
        if not self.signed_in():
            return
        order = STATE.orders.get(int(oid))
        if not order:
            return self.send(404, app_page('/app/orders', 'Order not found', '<h1>Order not found</h1><p><a href="/app/orders">← Orders</a></p>'))
        lines = ''.join(f'<tr><td>{html.escape(name)}</td><td class="num">{money(amount)}</td></tr>'
                        for name, amount in order_lines(order, STATE.seed))
        if order['status'] in ('paid', 'shipped'):
            prompt = f'Refund {money(order["total"])} to {order["customer"]}? This cannot be undone.'
            refund = (f'<p class="muted">Refunds go back to the original payment method within 5 business days.</p>'
                      f'<button class="btn danger" type="button" id="refund" data-id="{order["id"]}" data-confirm="{html.escape(prompt)}">Refund order</button>')
        elif order['status'] == 'refunded':
            refund = '<p class="muted">This order has been refunded.</p>'
        else:
            refund = '<p class="muted">Pending orders can be cancelled by support, not refunded.</p>'
        city = CITIES[order['id'] % len(CITIES)]
        content = fill(ORDER, id=order['id'], date=order['date'], customer=html.escape(order['customer']), city=city,
                       status=order['status'], status_label=order['status'].title(), total=money(order['total']),
                       lines=lines, refund=refund)
        self.page(f'/app/orders/{oid}', f'Order #{oid}', content, ORDER_JS)

    def settings_page(self):
        if not self.signed_in():
            return
        settings = STATE.settings
        embed = self.query.get('embed') == '1'
        heading = ('<h2>Profile settings</h2>' if embed else
                   '<h1>Settings</h1><p class="sub">Your profile and preferences.</p>')
        content = fill(SETTINGS, heading=heading, source='iframe' if embed else 'page', bio=html.escape(settings['bio']),
                       notifications='true' if settings['notifications'] else 'false', theme=settings['theme_color'])
        script = (f'const SAVED = {json.dumps(settings)};\nconst SWATCHES = {json.dumps(SWATCHES)};\n' + SETTINGS_JS)
        if embed:
            STATE.log('view', path='/app/settings?embed=1')
            return self.send(200, document('Settings', f'<main style="padding:20px">{content}</main>', script, settings['theme_color']))
        self.page('/app/settings', 'Settings', content, script)

    def iframe_page(self):
        if self.signed_in():
            self.page('/app/iframe', 'Embedded widget',
                      '<h1>Embedded widget</h1><p class="sub">The settings widget as partners see it inside their portal.</p>'
                      '<iframe src="/app/settings?embed=1" title="Settings widget" id="widget"></iframe>')

    def upload_page(self):
        if self.signed_in():
            self.page('/app/upload', 'Upload invoice', UPLOAD, UPLOAD_JS)

    def wizard_page(self):
        if self.signed_in():
            self.page('/app/wizard', 'Onboarding', WIZARD, WIZARD_JS)

    def feed_page(self):
        if self.signed_in():
            self.page('/app/feed', 'Activity', FEED, FEED_JS)

    def reports_page(self):
        if self.signed_in():
            self.page('/app/popup', 'Reports', REPORTS)

    def report_page(self):
        if not self.signed_in():
            return
        number = report_number(STATE.seed)
        STATE.log('report_view', report=number)
        self.send(200, document('Q3 2026 operations report', fill(REPORT, number=number), accent=STATE.settings['theme_color']))

    # public pages

    def slow_page(self):
        STATE.log('view', path='/slow')
        self.send(200, document('Q4 forecast', SLOW, SLOW_JS))

    def heavy_page(self):
        STATE.log('view', path='/heavy')
        figures = ''.join(f'<figure><img src="/img/{n}.png" alt="Product {n}" width="96" height="96">'
                          f'<figcaption>SKU-{4000 + n}</figcaption></figure>' for n in range(1, 201))
        self.send(200, document('Catalog', fill(HEAVY, figures=figures)))

    def image(self, n):
        n = int(n)
        if not 1 <= n <= 200:
            return self.send(404, b'', 'image/png')
        self.send(200, product_image(n), 'image/png')

    def captcha_page(self):
        STATE.log('view', path='/captcha')
        self.send(200, document('Security check', fill(CAPTCHA, nonce=secrets.token_hex(4)), CAPTCHA_JS))

    def captcha_png(self):
        self.send(200, captcha_image(STATE.captcha), 'image/png')

    def captcha_answer(self):
        if str(self.payload().get('code', '')).strip() == STATE.captcha:
            STATE.log('captcha_solved')
            return self.json({'ok': True, 'balance': '$12,480.17'})
        STATE.log('captcha_failed')
        self.json({'ok': False}, 403)

    def captcha_focus(self):
        STATE.log('captcha_focus')  # the page's window took focus: a person can see it (Umbra's handoff)
        self.send(204)

    # API

    def api_orders(self):
        if not self.signed_in():
            return
        status = self.query.get('status', '')
        orders = sorted((o for o in STATE.orders.values() if not status or o['status'] == status), key=lambda o: -o['id'])
        pages = max(1, -(-len(orders) // PER_PAGE))
        try:
            page = min(max(1, int(self.query.get('page', 1))), pages)
        except ValueError:
            page = 1
        self.json({'page': page, 'pages': pages, 'per_page': PER_PAGE, 'total': len(orders),
                   'orders': orders[(page - 1) * PER_PAGE:page * PER_PAGE]})

    def api_csv(self):
        if not self.signed_in():
            return
        out = io.StringIO()
        writer = csv.writer(out, lineterminator='\n')
        writer.writerow(['id', 'customer', 'total', 'status', 'date'])
        for o in sorted(STATE.orders.values(), key=lambda o: -o['id']):
            writer.writerow([o['id'], o['customer'], f'{o["total"]:.2f}', o['status'], o['date']])
        STATE.log('export', rows=len(STATE.orders))
        self.send(200, out.getvalue(), 'text/csv; charset=utf-8',
                  [('Content-Disposition', 'attachment; filename="orders.csv"')])

    def api_refund(self):
        if not self.signed_in():
            return
        try:
            order = STATE.orders.get(int(self.payload().get('id')))
        except (TypeError, ValueError):
            order = None
        if not order:
            return self.json({'error': 'No such order.'}, 404)
        if order['status'] not in ('paid', 'shipped'):
            return self.json({'error': f'Order #{order["id"]} is {order["status"]} and cannot be refunded.'}, 409)
        order['status'] = 'refunded'
        STATE.log('refund', order=order['id'], amount=order['total'])
        self.json({'ok': True, 'refund': f'RF-{order["id"]}', 'amount': money(order['total'])})

    def api_settings(self):
        if not self.signed_in():
            return
        data = self.payload()
        saved = {'display_name': str(data.get('display_name', '')).strip(), 'bio': str(data.get('bio', '')).strip(),
                 'notifications': bool(data.get('notifications')), 'theme_color': str(data.get('theme_color', '')).lower()}
        if not saved['display_name']:
            return self.json({'error': 'Display name is required.'}, 422)
        STATE.settings.update(saved)
        STATE.log('settings_save', source=str(data.get('source', 'page')), **saved)
        self.json({'ok': True})

    def api_upload(self):
        if not self.signed_in():
            return
        match = re.search(r'boundary="?([^";]+)"?', self.headers.get('Content-Type', ''))
        if not match:
            return self.json({'error': 'expected multipart/form-data'}, 400)
        for part in self.body.split(b'--' + match.group(1).encode()):
            head, _, data = part.partition(b'\r\n\r\n')
            name = re.search(rb'filename="([^"]*)"', head)
            if name:
                data = data[:-2] if data.endswith(b'\r\n') else data
                filename = name.group(1).decode('utf-8', 'replace')
                digest = hashlib.sha256(data).hexdigest()
                code = receipt(digest)
                STATE.log('upload', filename=filename, size=len(data), sha256=digest, receipt=code)
                return self.json({'ok': True, 'filename': filename, 'size': len(data), 'receipt': code})
        self.json({'error': 'no file in the upload'}, 400)

    def api_wizard(self):
        if not self.signed_in():
            return
        data = self.payload()
        name, email, plan = str(data.get('name', '')).strip(), str(data.get('email', '')).strip(), str(data.get('plan', ''))
        seats = data.get('seats')
        problem = ('Full name is required.' if not name else
                   'Enter a valid email address.' if not EMAIL.fullmatch(email) else
                   'Choose a plan.' if plan not in PLANS else
                   'Seats must be a whole number from 1 to 500.' if not (isinstance(seats, int) and 1 <= seats <= 500) else '')
        if problem:
            STATE.log('wizard_rejected', reason=problem)
            return self.json({'error': problem}, 422)
        number = confirmation(name, email, plan, seats)
        STATE.log('wizard_complete', name=name, email=email, company=str(data.get('company', '')).strip(), plan=plan,
                  seats=seats, confirmation=number)
        self.json({'ok': True, 'confirmation': number})

    def api_feed(self):
        if not self.signed_in():
            return
        try:
            offset = max(0, int(self.query.get('offset', 0)))
        except ValueError:
            offset = 0
        STATE.log('feed_batch', offset=offset)
        self.json({'total': FEED_TOTAL, 'items': feed_items(STATE.seed)[offset:offset + FEED_BATCH]})

    def api_forecast(self):
        self.json({'forecast': forecast(STATE.seed)})


ROUTES = [
    (r'/__state', ('GET',), Handler.state),
    (r'/__reset', ('POST',), Handler.reset),
    (r'/__login', ('GET',), Handler.backdoor),
    (r'/', ('GET',), Handler.root),
    (r'/login', ('GET',), Handler.login_page),
    (r'/login', ('POST',), Handler.login),
    (r'/login/otp', ('GET',), Handler.otp_page),
    (r'/login/otp', ('POST',), Handler.otp),
    (r'/logout', ('GET',), Handler.logout),
    (r'/app', ('GET',), Handler.dashboard),
    (r'/app/orders', ('GET',), Handler.orders_page),
    (r'/app/orders/(\d+)', ('GET',), Handler.order_page),
    (r'/app/settings', ('GET',), Handler.settings_page),
    (r'/app/iframe', ('GET',), Handler.iframe_page),
    (r'/app/upload', ('GET',), Handler.upload_page),
    (r'/app/wizard', ('GET',), Handler.wizard_page),
    (r'/app/feed', ('GET',), Handler.feed_page),
    (r'/app/popup', ('GET',), Handler.reports_page),
    (r'/app/report', ('GET',), Handler.report_page),
    (r'/slow', ('GET',), Handler.slow_page),
    (r'/heavy', ('GET',), Handler.heavy_page),
    (r'/img/(\d+)\.png', ('GET',), Handler.image),
    (r'/captcha', ('GET',), Handler.captcha_page),
    (r'/captcha\.png', ('GET',), Handler.captcha_png),
    (r'/api/captcha', ('POST',), Handler.captcha_answer),
    (r'/api/captcha/focus', ('POST',), Handler.captcha_focus),
    (r'/api/orders', ('GET',), Handler.api_orders),
    (r'/api/orders\.csv', ('GET',), Handler.api_csv),
    (r'/api/refund', ('POST',), Handler.api_refund),
    (r'/api/settings', ('POST',), Handler.api_settings),
    (r'/api/upload', ('POST',), Handler.api_upload),
    (r'/api/wizard', ('POST',), Handler.api_wizard),
    (r'/api/feed', ('GET',), Handler.api_feed),
    (r'/api/forecast', ('GET',), Handler.api_forecast),
]


def main():
    parser = argparse.ArgumentParser(description='Serve Northwind Ops, the Umbra proving ground.')
    parser.add_argument('--port', type=int, default=8765, help='port (0 picks a free one)')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--seed', type=int, default=SEED)
    parser.add_argument('--otp', action='store_true', help='start with two-step sign-in on')
    options = parser.parse_args()
    STATE.reset(options.seed, options.otp)
    server = ThreadingHTTPServer((options.host, options.port), Handler)
    server.daemon_threads = True
    print(f'Northwind Ops on http://{options.host}:{server.server_address[1]}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
