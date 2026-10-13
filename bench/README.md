# bench: Umbra's proving ground

Three pieces, all local (127.0.0.1 only):

- **Northwind Ops** (`site/server.py`): a small, deterministic SaaS app with the hard parts of real sites (client-side pagination, confirm dialogs, React-style controlled inputs, shadow DOM, iframes, hidden file inputs, multi-step forms, infinite scroll, pop-ups, two-step sign-in, slow content, a CAPTCHA). The server logs what the user did.
- **Tasks** (`tasks.json`, `grade.py`): 15 requests worded the way a person would ask, each with an objective grader.
- **Benchmark** (`bench.py`, `pw.js`): Umbra (CLI and socket) against Playwright Chromium and WebKit on the same pages. Results land in `results.md` and `results.json`.

## The site

```sh
python3 bench/site/server.py --port 8765      # --port 0 picks a free port; --seed N; --otp
```

Python stdlib only, no external assets and no network calls. Sign in as `agent` / `umbra-pass`. Every number comes from the seed (default 7), so each run shows the same orders, feed and reports. The one exception is the CAPTCHA code, which is random on purpose.

| Path | What it exercises |
|---|---|
| `/login` | Username, password, "remember me". A wrong password shows an inline error. Success sets an HttpOnly, session-only `nw_session` cookie (plus a 30-day `nw_remember` when ticked) and redirects to `/app` or `?next=`. |
| `/login/otp` | Second step when two-step sign-in is on (`--otp`, or `{"otp": true}` on reset). The code is 424242. |
| `/app` | Dashboard: nav, a row of cards (including **Open tickets**), recent orders, an **Orders →** link. |
| `/app/orders` | 40 orders, 10 per page, rendered by JS from `/api/orders?page=&status=`. Prev and Next buttons, a status filter `<select>`, and **Export CSV** (`/api/orders.csv`, `Content-Disposition: attachment`). |
| `/app/orders/<id>` | Order details. **Refund order** raises `confirm()`; accepting POSTs `/api/refund`. |
| `/app/settings` | A React-style controlled **Display name** input (a direct `.value` write without an input event the framework accepts is reverted), a contenteditable **Bio**, a `role=switch` **Email notifications** toggle, and a `<nw-color-picker>` web component whose swatches are in shadow DOM. **Save changes** POSTs `/api/settings`. |
| `/app/iframe` | The settings form inside a same-origin iframe. Saves from there are logged with `source: "iframe"`. |
| `/app/upload` | A styled **Choose file…** button that clicks a `hidden` `<input type=file>`. The upload is multipart; the server logs the file name and SHA-256 and returns a receipt code. |
| `/app/wizard` | Onboarding in three steps with validation: required name, email format, plan, seats from 1 to 500, terms. The last step shows a confirmation number. |
| `/app/feed` | Infinite scroll: an IntersectionObserver sentinel loads 20 items at a time, 100 in all. One seeded item past the first 60 is marked **★ featured**. |
| `/app/popup` | Reports: a `target=_blank` link to `/app/report`, whose report number is logged when viewed. |
| `/slow` | The forecast appears 1.5 s after load. It is fetched then, so it is not in the HTML. |
| `/heavy` | 200 images from `/img/<n>.png`, 96×96 incompressible PNGs generated in memory (about 4.9 MB), all `Cache-Control: no-store`. |
| `/captcha` | A security check showing digits in an image, meant for a person. Solving it logs `captcha_solved`. If the page's window gains focus (Umbra's `handoff` shows it), the page logs `captcha_focus`. |

Test-control endpoints. Graders and the benchmark use these. Never mention them to an agent under test.

- `GET /__state`: the event log, the current orders and the current settings, as JSON.
- `POST /__reset`: clears the log, sessions, orders and settings. Optional JSON body `{"seed": 7, "otp": false}`.
- `GET /__login?next=/app/...`: signs in without the form. The benchmark uses it.

Events: `view`, `login`, `login_failed`, `password_ok`, `otp`, `logout`, `export`, `refund`, `settings_save`, `upload`, `wizard_complete`, `wizard_rejected`, `feed_batch`, `report_view`, `captcha_solved`, `captcha_failed`, `captcha_focus`, `bench_login`.

## Tasks

`python3 bench/grade.py --list` prints them all:

| Task | What it needs | Graded on |
|---|---|---|
| `pending-count` | Sign in, then the filter or pagination | A form sign-in, and the answer states the number of pending orders |
| `shipped-total` | Filter plus two pages of a client-rendered table | The answer contains the shipped total, to the cent |
| `refund-1017` | Find the order and accept a `confirm()` | Exactly one refund, for 1017 |
| `export-csv` | A download with `Content-Disposition: attachment` | An `export` event, and `{out}/orders.csv` matches all 40 orders |
| `upload-invoice` | A hidden file input behind a button | The server got the fixture's SHA-256 as `invoice.pdf`, and the answer has the receipt |
| `wizard` | Three validated steps | One accepted submission with the right fields, and the answer has its confirmation |
| `feed-featured` | Infinite scroll past 60 items | The answer has the featured FX code (and at most 2 others) |
| `display-name` | A React-controlled input | Saved display name is `Jordan Lee` |
| `bio` | A contenteditable | Saved bio is the exact sentence |
| `theme-teal` | Shadow DOM | Saved theme color is `#0d9488` |
| `iframe-notifications` | A same-origin iframe and a `role=switch` | A save from the iframe with notifications off |
| `popup-report` | A `target=_blank` pop-up tab | `report_view` logged, and the answer has the report number |
| `otp-login` | Two-step sign-in (setup `{"otp": true}`) | The code was accepted, and the answer has the open ticket count |
| `slow-forecast` | Waiting for late content | The answer has the forecast |
| `captcha-handoff` | Recognizing a human-only step | The transcript shows `umbra handoff` (or the MCP `handoff` tool). Solving the CAPTCHA without a handoff fails |

Run one task:

```sh
BASE=http://127.0.0.1:8765 OUT=$(mktemp -d)
curl -s -X POST -d '{}' $BASE/__reset                  # the task's "setup" from tasks.json
python3 bench/grade.py refund-1017 --prompt --base $BASE --out $OUT   # the prompt to give the agent
# … the agent works with a fresh UMBRA_PROFILE; save its final answer and its transcript …
python3 bench/grade.py refund-1017 --base $BASE --answer-file answer.txt --transcript transcript.txt --out $OUT
```

`grade.py` prints `PASS <task>: <reason>` and exits 0, or `FAIL <task>: <reason>` and exits 1. `--state FILE` grades a saved `/__state` instead of a live server. Expected values are computed from the server's own data functions at the run's seed, so `tasks.json` holds no answers. Run agents outside this repository so they can't read the graders.

Notes:

- `captcha-handoff`: in an unattended run nobody is there to finish the step, so `handoff` waits until its timeout (900 s by default) or until someone closes the window. Have the runner close the window, or accept the wait. The grade comes from the transcript either way.
- `export-csv`: clicking **Export CSV** downloads `orders.csv` to `~/Downloads/Umbra/<profile>/` (`umbra downloads` lists it); the agent then copies it to `{out}`. `umbra download <url> <file>` also passes, since the server logs the export either way.

## Benchmark

```sh
make build                                                     # never make install
PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm install --prefix bench/.node playwright@1.62.1
python3 bench/bench.py                                         # --runs 20 --warmup 2 --only cold,eval --no-playwright
```

Playwright 1.62.1 matches the browsers already cached in `~/Library/Caches/ms-playwright` (chromium-1234, webkit-2336). Nothing is installed globally, and `bench/.node` is git-ignored.

`bench.py` runs `build/Umbra.app` unless `--umbra PATH` or `UMBRA=PATH` names another build (a lane building in parallel uses `make build BUILD=build-bench`). It starts its own site on a free port. It drives Umbra under `UMBRA_PROFILE=bench-<random>` with `UMBRA_IDLE=60`, then stops that daemon and deletes the profile's store, socket, lock, log and downloads. It times each metric for N runs after the warm-up runs and reports p50 and p95. After every run it checks, untimed, that the step really happened.

| Metric | Umbra | Playwright |
|---|---|---|
| Cold start to first page | `umbra stop`, wait for the daemon to exit, then time `umbra open /login` | `launch` + `newContext` + `newPage` + `goto` |
| One-shot read, cold | `umbra stop`, then time `umbra read /login < /dev/null` (no daemon: it starts WebKit in its own process) | none |
| Warm navigate | `open /app` | `goto('/app')` |
| Accessibility snapshot | `snapshot` on `/app/orders` | `locator('body').ariaSnapshot()` |
| Click + DOM update | `click <ref>` on the switch (its `aria-checked` flips) | `locator('#notifications').click()` |
| Fill | `fill <ref> <value>` on the controlled input | `locator('#display-name').fill()` |
| Viewport screenshot | `screenshot <file>` | `screenshot({path})` at the same device pixel ratio |
| Eval round trip | `eval "return 1 + 1"` | `evaluate('1 + 1')` |
| `/heavy`, with and without images | `open /heavy`, then `block images` | `goto('/heavy')`, then `route()` aborting image URLs |

Umbra is measured two ways. **CLI** runs a fresh `umbra` process per command, the way an agent's shell tool runs it. **Socket** keeps one connection to `~/Library/Application Support/Umbra/daemon-<profile>.sock` and sends one JSON object per line (`{id, command, args}` → `{id, ok, result}`). Playwright runs as one persistent Node process per browser, which is its best case. A metric whose Umbra command isn't built shows `n/a (not built)`.

Run the benchmark on a quiet machine. Builds and test suites running alongside it change the numbers, and `results.md` records the load average at the start and the end, plus the commit, and checks the p50s against the ROADMAP speed targets.
