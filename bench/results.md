# Umbra vs Playwright on Northwind Ops

Measured 2026-10-12 23:16 on a MacBookPro18,3 (Apple M1 Pro, 8 cores, 16 GB), macOS 27.0.1 (26A434), load average 5.63 at the start.
umbra 0.1.0 at ce34007 + uncommitted Sources changes; Playwright 1.62.1 chromium 151.0.7922.34 and webkit 26.5.
Each cell is p50 / p95 in milliseconds over 20 timed runs, after 2 untimed warm-up runs (cold start has none: every run is cold). Every run is checked afterward (untimed): the page loaded, the switch flipped, the field holds the value, the images loaded or were blocked.

| Metric | Umbra CLI | Umbra socket | Playwright Chromium | Playwright WebKit |
|---|---:|---:|---:|---:|
| Cold start to first page loaded | 409 / 421 | n/a (CLI only) | 179 / 194 | 753 / 825 |
| Warm navigate to a local page | 22 / 24 | 7.1 / 8.9 | 6.5 / 8.2 | 6.7 / 10 |
| Accessibility snapshot (orders table) | 15 / 17 | 1.0 / 1.5 | 3.8 / 4.8 | 5.2 / 6.1 |
| Click + DOM update (role=switch) | 16 / 17 | 1.3 / 1.8 | 16 / 17 | 66 / 67 |
| Fill a field (React-controlled input) | 16 / 18 | 1.9 / 3.7 | 2.6 / 3.4 | 3.5 / 6.2 |
| Viewport screenshot to a file | 66 / 68 | 51 / 73 | 90 / 93 | 58 / 59 |
| Eval round trip | 14 / 15 | 0.2 / 0.4 | 0.6 / 1.0 | 0.3 / 0.4 |
| /heavy load, 200 images | 188 / 260 | 165 / 302 | 98 / 109 | 157 / 166 |
| /heavy load, images blocked | 38 / 42 | 24 / 26 | 86 / 89 | 65 / 78 |

## What each path measures

- **Umbra CLI**: wall time of `umbra <command>` as a fresh process, as an agent's shell tool runs it (process start, connecting to the daemon, the command, printing). Cold start runs `umbra stop` first and waits for the daemon to exit, so it includes spawning the daemon and starting WebKit.
- **Umbra socket**: one persistent connection to the daemon's unix socket, one JSON line per command (what `umbra mcp` and other long-lived clients pay).
- **Playwright**: one persistent Node process per browser (`bench/pw.js`), headless, its best case. Cold start is `launch()` + `newContext()` + `newPage()` + `goto()` inside that process; Node's own start-up is not counted. Each context is non-persistent, so every launch uses a fresh temporary profile.

## Notes

- Accessibility snapshot: Umbra `snapshot` against Playwright `page.locator('body').ariaSnapshot()` on /app/orders with 10 rows loaded.
- Click: the settings page's `role=switch` button, whose handler flips `aria-checked` synchronously. Playwright's click includes its actionability checks.
- Fill: the settings page's React-style controlled input, which ignores `.value` writes the framework never sees.
- Screenshots are the 1280×900 viewport at device pixel ratio 2 for every browser, written to a PNG file.
- Image blocking: Umbra `block images`; Playwright `page.route()` aborting image URLs (a glob, so other requests are not intercepted). The page serves 200 incompressible 96×96 PNGs (about 4.9 MB) with `Cache-Control: no-store`.
- The site is `bench/site/server.py` (Python, threaded, HTTP/1.1 keep-alive) on 127.0.0.1.
- "n/a (not built)" means the Umbra build lacks a command that metric needs; rerun once it lands.
