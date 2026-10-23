# Runtime comparison on external MiniWoB tasks

Status: deferred research at the authorized release closeout. Zero episodes launched in either new final campaign. Prepared manifests name the earlier `0f2db45988f472004708411947026319678e6d26f500cabe913f91a9646dcfc9` candidate; any future execution must freeze a fresh manifest against the intended binary (the verified closeout product is `bace278786dd13901a428728c8dbc1e0ef542f78feaf7ba6c26df38ab212ad1e`). Preparation is not a passing score or capability-preservation result.

The historical October 13 campaign is a different artifact and does not prove capability preservation for this runtime change. The new comparison uses the frozen initial Umbra binary and final candidate, unchanged upstream BrowserGym setup/validation, 125 task types, five original seeded repeats, ten interaction requests, forty observations and a 120-second episode cap. Every frozen episode remains in the denominator. Both arms use the same filtered Umbra MCP surface and model configuration. Arm order is deterministically shuffled per task/repeat.

## Prepared artifacts

- Isolated harness worktree: `/var/folders/l0/f73q0dln26v1fk40dxfc_w9h0000gn/T/umbra-runtime-k_tqurzx/external-eval`.
- Frozen initial binary: `/var/folders/l0/f73q0dln26v1fk40dxfc_w9h0000gn/T/umbra-runtime-k_tqurzx/baseline-app/Umbra.app/Contents/MacOS/umbra`.
- Disposable dependencies: `/private/tmp/umbra-runtime-external.SXH9wg`; Python is `env/bin/python` beneath it.
- BrowserGym revision `e71419aef5759a11cb7896a26c36e5f7e92d82b2`; MiniWoB revision `33c3b4ddef8c6eb67c57a29663d844b1eda7e614`.
- BrowserGym core/miniwob 0.14.3, NumPy 2.5.4, Playwright Python 1.63.0 and Playwright MCP 0.0.83. The pinned source adapter overrides BrowserGym's legacy Playwright dependency; it does not execute the Gym browser environment.
- Codex CLI 0.163.0; the plumbing manifest records `gpt-6.1-sol` at high effort. Final manifest must freeze the same model for both arms.

The existing harness only selected a mutable repository build and compared it with Chromium. `bench/external/run.py` now accepts explicit `--umbra-binary` and `--baseline-binary` paths, records and verifies both hashes, selects the right binary inside each trusted broker, and retains the existing default Umbra/Playwright mode. It also rejects changed upstream repositories or grader source. `compare_report.py` reports this two-Umbra comparison without assigning Playwright labels or treating a selected subset/same-binary pilot as non-regression evidence.

## Current checks

`baseline-grader-calibration.json` retains six upstream controls: unfinished, correct and incorrect actions for `click-test-2` in initial Umbra and Chromium. All six matched upstream expectations. This is a narrow transport/grader calibration, not broad task-capability evidence.

`baseline-plumbing-pilot/` reports four model episodes across `click-test-2` and `enter-text`, using the same initial binary in both named arms to check binary selection and broker plumbing. All four reached full reward, all profiles were removed, and transcript audit found no forbidden tools. Serial episode durations were 17.0–22.8 seconds. These two easy tasks do not predict full-suite speed or success. The raw pilot is `/var/folders/l0/f73q0dln26v1fk40dxfc_w9h0000gn/T/umbra-runtime-k_tqurzx/external-plumbing-pilot-v2`; hashes identify every retained raw file.

An earlier configuration pilot was aborted before any browser action because disabling Codex's `code_mode_host` made CLI 0.163 fail closed. Its exact profile was cleaned, and its failure receipt remains under `external-plumbing-pilot/pilot-aborted.json` in the task directory. The working invocation retains Codex's tool host and disables shell, applications/plugins, unrelated browsers/computer use, skills, memories/hooks, goals and web search. The only configured MCP server is the filtered browser broker. Browser calls in the successful transcript were direct MCP calls.

The controller can read pinned source and run `setup()`/`validate()`; the model cannot. Models receive only visible-page observations, supported input actions, and a goal tool. Navigation, arbitrary scripts, file paths, reward, answer keys and raw source are withheld. The local MiniWoB HTML uses local resources; generated external-looking search URLs are displayed text rather than external navigation. Throwaway profiles isolate browser data; the native macOS clipboard remains shared. No default profile, installed Umbra app or installed CLI was modified.

## Final run

After the lead supplies the candidate hash, verify it, then freeze a new directory with `--baseline-binary INITIAL --umbra-binary CANDIDATE --repeats 5 --timeout 120` and all dependency paths. Confirm exactly 1,250 cases, 125 unique task types and 625 cases per arm before execution. Run at four workers only after the quiet performance matrix has completed. The hard episode-cap budget alone is about 10.4 hours (1,250 × 120 / 4); setup/cleanup and scheduling add overhead. The pilot is not a valid full-suite runtime estimate.

Run `compare_report.py --root RAW --out REPORT` after the campaign; preserve unfinished episodes and failures, verify all cleanup receipts, and inspect task-level paired outcomes as well as aggregate scores. Report positive reward and full reward separately, with paired task-cluster intervals. A nonnegative observed score difference is not proof of statistical noninferiority or per-task equivalence. Do not claim the capability acceptance gate until this final campaign is complete and audited.

## Separate competitor campaign

The initial-Umbra comparison above remains unchanged at 1,250 episodes. A second, separate 1,250-episode campaign uses the harness’s original candidate-Umbra versus Playwright/Chromium arms, preserving the same 125 task types, five seeds, model `gpt-6.1-sol` at high effort, ten interaction requests, forty observations and 120-second episode cap. This supplies the competitor comparison required by the brief; it cannot replace the initial-Umbra capability-preservation comparison.

Prepared task artifacts are `external-final-v2/{preflight.json,continue.md}` for initial/candidate Umbra (manifest SHA-256 `d2ef2d98ff525e405b785bd24e7c4f6e6172fe0e56e197c8fe5cc3459bb28dad`) and `external-competitor-v1/{preflight.json,continue.md}` for candidate/Playwright (manifest SHA-256 `b3d2db1400a9d12906580140a64f924a2795ab489ade435020f0703e612d9700`). Both freeze arm order before outcomes. These are preparation receipts, not measured results.

The competitor freeze records Playwright MCP 0.0.83, its Node Playwright 1.64.0-alpha-1790635538000 dependency, controller Python Playwright 1.63.0, and Chromium 151.0.7922.34 with package and binary hashes. Recheck all hashes before execution; do not silently update dependencies. Browser tools expose different action/observation affordances, and controller, inference, shared clipboard, scheduling and any soak load affect results. This is a browser-agent task comparison, not an isolated rendering-engine speed test.

Execute initial-Umbra/candidate first, then candidate/Playwright, with no more than four external workers total. Each campaign retains all 1,250 frozen cases, failures, incomplete episodes, cleanup and forbidden-tool audits. The initial comparison uses `compare_report.py`; the competitor comparison uses the original `report.py` with its proper Umbra/Playwright labels. Report positive and full reward separately, task-cluster uncertainty, timing limits and actual overlap with soak. Neither campaign may run during quiet performance matrices.
