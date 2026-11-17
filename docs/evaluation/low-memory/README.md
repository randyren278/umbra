# Active low-memory sessions

These measurements use synthetic signed-in-like localhost applications, not real accounts or live sites. Supported API tasks can avoid WebKit entirely. A JS-only surface keeps a real WebKit view and JavaScript state, initially windowless; native input, geometry and pixels promote that same view. No active view is discarded to obtain the reported saving. Ordinary browsing defaults remain unchanged. See [API sessions](../../api.md) and [bounded DOM reads](../../semantic.md).

## Matched stock / hybrid comparison

Three reordered independent sessions per mode on an Apple M1 Pro, 16 GiB, macOS 27.0.1 (26A434), Swift 6.4.2, optimized repository build. Stock is revision `364b29a448d8668fef44e177871794649b494b3a`; C3 is the accepted API/nonvisual/store-lifecycle implementation. The tested binary and fixture hashes are in [public numeric results](results.json).

Every arm uses the same four-surface, 2,000-message / 200-task synthetic fixture, with login, inbox/dashboard/settings, a JS-only surface, filtering, verified writes, freshness and expiry. Ten task mixes precede one trusted native promotion/action and ten warm native actions. API and browser modes must return equivalent required task results. All logical views remain usable and retained.

Ranges below are across three per-session stage medians, in **MiB**. SVG bar lengths use the arithmetic mean of those three medians on a shared 0–450 MiB scale; labels show rounded ranges:

- Four surfaces ready: stock **362–367**, four windowless WebKit views **122–123**, three API views plus one windowless WebKit view **67–68**.
- After ten task mixes: stock **371–379**, four windowless views **140–143**, hybrid **75–76**.
- Immediately after ten native actions: stock **438–443**, four initially windowless views with one promoted **285–290**, hybrid **221–222**. The paired stock-to-hybrid reduction is **49.3–50.1%**.
- Candidate using unchanged stock defaults: **437–440 MiB** immediately after the same native burst. This is a compatibility control, not an additional saving.

A separate three-pair settling cohort retained every view and measured **157.1–157.5 MiB hybrid versus 385–389 MiB stock at ten seconds idle**. That is a different measurement stage/cohort, not a lower active/burst ceiling. First-promotion and delayed native allocations vary; sampled peaks are not bounded by stage medians. The later read cohort below sampled totals up to roughly 288–298 MiB across recorded stages. Footprint growth alone does not establish a leak.

## Explicit read contract comparison

The later feature adds `api read` for declared GET/HEAD routes and `dom read/find/links` for bounded main-document light-DOM reads. The API path creates no WebKit engine. DOM reads retain WebKit and the page's JS, without style/layout/input calls or creating a render host on a never-promoted nonvisual view. Existing instrumentation remains installed. CSS-hidden text may be returned; results do not certify visibility, actionability, server freshness or signed-in status.

Three reordered sessions each compare ordinary C3 text/link tools, equivalent generic C3 DOM scripts/GET calls, and the new explicit commands on identical fixture bytes and topology (three API views plus one browser view). Each run has 30 API reads, 30 structural read/find/link tasks, three explicit URL navigations, freshness injection, one explicit API write, one trusted native write and a screenshot, ten native read actions, and ten seconds idle. Only the loaded 100 DOM rows are searched; API search can query the authoritative full dataset.

- New commands: **20.1–20.2 MiB API-only**, **68.2–71.4 MiB before graphics**, **225.2–228.0 MiB after the native burst**, **150.7–153.2 MiB at ten seconds idle**.
- C3 ordinary reads: **19.9–20.0**, **67.2–69.2**, **224.7–230.6**, **149.7–152.6 MiB**, respectively.
- C3 equivalent scripts: **19.9–20.0**, **69.7–71.9**, **228.5–240.3**, **152.1–153.6 MiB**, respectively.

All three methods already made **zero read-phase host promotions**. Across three candidate sessions, all **90 API reads and 90 structural tasks** passed, with 30 unchanged read-state blocks and nine explicit navigations. Native/pixel work promoted the same view exactly once per session. Authoritative server receipts show exactly two intended writes per run, with no replay. Screenshots were pixel-identical within each matched block. Changed-query held-out runs also passed.

This expands explicit no-render task coverage; **no additional RAM reduction over efficient C3 reads is demonstrated**. It is not a comparison to Playwright, nor an autonomous agent task-selection evaluation. No external performance result is inferred.

New structural read/find/links median / p95 responses were **1.90/6.40**, **1.57/2.32**, **3.30/4.80 ms**, respectively, over 30 calls each across three sessions. C3 equivalent script medians were 2.11, 1.61 and 3.78 ms. These are descriptive socket/tool timings, excluding CLI/MCP startup. Native-write medians were about 26/32/45 ms for ordinary/script/new with only three observations; this does not establish a universal latency regression-free result. Navigation/native timing excludes subsequent fixture-ready waits; screenshot timing includes completion. Real networking, TLS and SSO are unmeasured.

## Accounting and limitations

Memory uses native `proc_pid_rusage` physical footprint over unique PIDs attributed by macOS responsibility, including daemon, WebContent, GPU, networking and ancillary helpers. Resident memory is recorded separately. This is **not summed RSS, globally deduplicated DRAM, or all shared system services**. Fixture server/controller are excluded consistently. Five within-stage samples and repeated tasks are correlated; three processes/sessions, not 30 independent trials, underlie a pooled 30-call distribution. The native sampler records brief peaks separately from stage medians.

The fixture has synthetic accounts, private declared-session cookie/token state, session rotation/expiry, revisions and explicit action verification. It cannot establish arbitrary-site compatibility, OAuth grants, browser authorization for a separate API, virtualization coverage, full accessibility semantics, frame/closed-shadow reads or real-site latency. Declared GET/HEAD routes must be supported/authorized by the application; the HTTP method alone cannot prove server purity. No endpoint inference, synthetic replacement of trusted input, automatic mutation retry or host detachment is used.

The initial read benchmark had an output-folder failure. Another C3 reference run reported one read-state mismatch before detailed before/after state logging existed; its cause remains unknown. A diagnostic reference rerun and all final state-logged comparisons passed without weakening the state equality gate. The public results describe the frozen final cohort, not a pooled selection of initial and final runs. Native source review and CLI/MCP synthetic watcher tests cover unchanged/new-reply observations, URL/title changes with identical text, navigation/expiry and genuinely frame-dependent fallback; they do not establish a delta/ref reconstruction contract.

## Reproduce safely on macOS

Use a separate checkout and build output. Never use an owner's signed-in/default profile or installed app. The harness creates throwaway profiles, sites, agents and logo caches and removes only its own profile files and child processes. Tests load only `127.0.0.1` fixtures. Access-dependent GUI checks skip without permission prompts. Do not run benchmark arms concurrently.

```sh
# Build current and stock in separate checkouts/output directories.
# Set your own throwaway UMBRA_PROFILE, UMBRA_AGENTS and cache paths when building/testing.
make build BUILD=/tmp/umbra-candidate-build

python3 bench/hybrid.py --umbra /path/to/stock/umbra --mode stock --runs 3 --cycles 10 --json /tmp/stock.json
python3 bench/hybrid.py --umbra /path/to/candidate/umbra --mode hybrid --runs 3 --cycles 10 --json /tmp/hybrid.json

python3 bench/semantic.py --umbra /path/to/candidate/umbra --mode dom --cycles 10 --json /tmp/dom.json
python3 bench/semantic.py --umbra /path/to/reference/umbra --mode script --cycles 10 --json /tmp/script.json
python3 bench/semantic.py --umbra /path/to/reference/umbra --mode layout --cycles 10 --json /tmp/layout.json
```

Alternate arms across three blocks: stock/default/nonvisual/hybrid for the original experiment; layout/dom/script, script/dom/layout, dom/layout/script for the read experiment. The read protocol requires at least seven cycles to include all mutation/navigation gates. Add `--held-out --cycles 8` to script/dom for changed-query/expiry/native fallback. Use the same checked-out fixture/harness for each arm and record binary, source, fixture and benchmark hashes. The original C3 binary predates the explicit DOM command; its SHA is retained as historical provenance. A current reference using script/layout modes can rerun the interface comparison, but is not that historical binary. Report every failed run, helper attribution, native peak, immediate post-burst and settled stage separately.

Public `results.json` preserves per-run stage aggregates, descriptive timings, checks, transitions and binary/fixture hashes while omitting local usernames, profile names, paths, cookies and PIDs. It is not a full raw process trace. Benchmark source is in [hybrid.py](../../../bench/hybrid.py) and [semantic.py](../../../bench/semantic.py); fixtures and correctness gates are in `tests/`. Regenerate the four repo-native SVGs with `python3 assets/low_memory.py`. The image values derive from that JSON; exact values and scope remain available as text here.
