# Umbra vs Playwright on Northwind Ops

Measured 2026-10-13 02:33 on a MacBookPro18,3 (Apple M1 Pro, 8 cores, 16 GB), macOS 27.0.1 (26A434). One-minute load average 2.66 at the start and 9.72 at the end (five-minute 4.81): no other lane's suites were running at the start, but something else started in roughly the last minute, when the Playwright WebKit rows (run last) were running. Against the earlier run (load 5.6 to 10.8), those rows are the same or faster (eval aside: 0.3 to 0.4 ms), except warm navigate, which went from 6.7 to 13 ms. Umbra's socket navigate made the same jump (7.1 to 13 ms) while load was still low, so the WebKit navigate change isn't the load spike; Chromium stayed at 6.5 ms.
umbra 0.2.0 built from 4f879a5 (HEAD moved to e0b33a3 during the run; that commit changes only docs, so Sources, Makefile and Info.plist are identical); Playwright 1.62.1 with chromium 151.0.7922.34 and webkit 26.5.
Each cell is p50 / p95 in milliseconds over 20 timed runs, after 2 untimed warm-up runs (the two cold rows have none: every run is cold). Every run is checked afterward (untimed): the page loaded, the switch flipped, the field holds the value, the images loaded or were blocked.

| Metric | Umbra CLI | Umbra socket | Playwright Chromium | Playwright WebKit |
|---|---:|---:|---:|---:|
| Cold start to first page loaded | 408 / 413 | n/a (CLI only) | 153 / 163 | 718 / 727 |
| One-shot read (no daemon), cold | 385 / 391 | n/a (CLI only) | — | — |
| Warm navigate to a local page | 27 / 28 | 13 / 14 | 6.5 / 7.2 | 13 / 17 |
| Accessibility snapshot (orders table) | 14 / 14 | 0.9 / 1.3 | 3.6 / 4.7 | 4.4 / 5.4 |
| Click + DOM update (role=switch) | 15 / 16 | 1.5 / 3.4 | 16 / 16 | 66 / 67 |
| Fill a field (React-controlled input) | 15 / 18 | 1.3 / 2.6 | 2.1 / 2.6 | 3.0 / 3.5 |
| Viewport screenshot to a file | 64 / 66 | 50 / 51 | 85 / 97 | 57 / 58 |
| Eval round trip | 12 / 14 | 0.2 / 0.2 | 0.3 / 0.5 | 0.4 / 0.5 |
| /heavy load, 200 images | 175 / 178 | 163 / 169 | 83 / 87 | 147 / 151 |
| /heavy load, images blocked | 42 / 45 | 29 / 31 | 76 / 78 | 60 / 70 |

## ROADMAP speed targets

p50 against the target; p95 in parentheses.

| Target | Umbra CLI | Umbra socket |
|---|---:|---:|
| Cold start to first page loaded < 700 ms | 408 (413) met | n/a (CLI only) |
| One-shot read (no daemon), cold < 500 ms | 385 (391) met | n/a (CLI only) |
| Warm navigate to a local page < 120 ms | 27 (28) met | 13 (14) met |
| Accessibility snapshot (orders table) < 25 ms | 14 (14) met | 0.9 (1.3) met |
| Click + DOM update (role=switch) < 40 ms | 15 (16) met | 1.5 (3.4) met |

## What each path measures

- **Umbra CLI**: wall time of `umbra <command>` as a fresh process, as an agent's shell tool runs it (process start, connecting to the daemon, the command, printing). Cold start runs `umbra stop` first and waits for the daemon to exit, so it includes spawning the daemon and starting WebKit.
- **Umbra socket**: one persistent connection to the daemon's unix socket, one JSON line per command (what `umbra mcp` and other long-lived clients pay).
- **Playwright**: one persistent Node process per browser (`bench/pw.js`), headless, its best case. Cold start is `launch()` + `newContext()` + `newPage()` + `goto()` inside that process; Node's own start-up is not counted. Each context is non-persistent, so every launch uses a fresh temporary profile.

## Notes

- One-shot read: `umbra read <url> < /dev/null` with no daemon running, so it starts WebKit in its own process, loads /login and prints the HTML. Each run stops the daemon first and checks that none was started. Playwright has no counterpart row; its cold start row is the nearest comparison.
- Accessibility snapshot: Umbra `snapshot` against Playwright `page.locator('body').ariaSnapshot()` on /app/orders with 10 rows loaded.
- Click: the settings page's `role=switch` button, whose handler flips `aria-checked` synchronously. Playwright's click includes its actionability checks.
- Fill: the settings page's React-style controlled input, which ignores `.value` writes the framework never sees.
- Screenshots are the 1280×900 viewport at device pixel ratio 2 for every browser, written to a PNG file.
- Image blocking: Umbra `block images`; Playwright `page.route()` aborting image URLs (a glob, so other requests are not intercepted). The page serves 200 incompressible 96×96 PNGs (about 4.9 MB) with `Cache-Control: no-store`.
- The site is `bench/site/server.py` (Python, threaded, HTTP/1.1 keep-alive) on 127.0.0.1.
- "n/a (not built)" means the Umbra build lacks a command that metric needs; rerun once it lands.
