# Runtime delivery status

Closeout and push are authorized by the user on 2026-10-23. The user explicitly removed the 24-hour soak requirement. Performance targets remain measured gates, not assumed results; the earlier failed campaign stays retained. Finish runnable validation, report unavailable checks precisely, and publish the verified implementation without claiming unmeasured long-duration stability.

## Committed implementation

- `1fa9e88`: preserves the pre-existing session-attendance and invisible-verification patch separately.
- `4c731b5`: cool-palette SVGs and qualified historical benchmark highlights. All 24 SVGs parse; 16 generated figures match their data. Desktop/mobile renders were inspected.
- `c45b12d`, `e5c8243`: persistent Python/TypeScript/Swift SDKs and owned cancellation.
- `7193dcd`: frozen mixed-workload, loaded-control, observation-token and soak protocols.
- `29a6191`: bounded runtime, leases, private operation generations, incremental observations, extraction, watches/events, cross-origin frames, public HTTP/resource presets and native process metrics. Independent core build passed 23 runtime/identity checks; preceding checkpoint passed 78 feature checks.
- `569f8b2`: checkpointed workflows, partial failure receipts, inspector, bounded recording and redacted optional failure observations. Four final failure-bundle tests and desktop/mobile inspector journeys pass.
- `bee3a1b`: runtime guides/examples/contracts. 52 local documentation links and all SVG checks pass.
- `fb3a71b`: paired external capability harness and explicitly labelled calibration/plumbing evidence.
- `40bd72a`: canonical launch-agent paths and short-idle attendance cadence correction. Five unchanged attendance/resting checks and the unchanged launch-agent check pass. Controlled scheduling-delay before/after evidence is retained in `docs/evaluation/runtime/verification/`. Default maintenance interval remains 15 seconds.

## Current candidate and measured findings

Current combined binary SHA-256 `0f2db45988f472004708411947026319678e6d26f500cabe913f91a9646dcfc9`; product source `1a1d850` includes the general event-sensitive wait, transport deadline fixes and bounded failure delivery. Candidate app/SDK: task directory `frozen-candidate-v5/`; manifest `candidate-freeze-v5.json`. Frozen Python SDK SHA remains `1de49bf4f21951045660ce112fa94dc888fba1ffaba166c94974b882f88acfdb`. Root benchmark harness SHA is `5d019177f6bb75c84b89f97c4f6b79c4eaa8f37d0d5393dfeeaba30d4fb07a49`. Workload and three JSON method specifications are unchanged.

Full combined regression v6 PASSED with exit 0 after 853 seconds: 416 tests across 22 suites, one expected skip, and all 17 smoke checks. Exact log and hashes are retained in `verification/full-v6/`. Full-v5’s three sign-in diagnostic failures remain in `verification/full-v5/`; the original assertions pass after the bounded-delivery fix.

`1a1d850` fixes that deadline race with a uniform 100 ms failure-delivery window, without extending execution deadlines or accepting late successes. Focused baseline failures and passing checks remain in `verification/reply-delivery/`; blocking request writes are unchanged.

A model usage limit stopped the performance worker before any new matrix launched. Root verified the complete regression, unchanged binary/SDK/source/harness hashes, absent previous runners/outputs and a quiet interval, then started primary-v2 directly on 2026-10-23 at approximately 18:06:54 UTC. Runner PID 9824, session `98949`, log `/tmp/umbra-primary-official-v2.log`, lane output `runtime/docs/evaluation/runtime/primary-official-v2/`. The first baseline and candidate cells are running. No builds/tests/model episodes overlap the matrix. Secondary-v1 remains pending after primary inspection.

Both external campaigns are prepared: 1,250 initial-Umbra/candidate cases and a separate 1,250 candidate/Playwright-Chromium cases with matching tasks, seeds and model. Zero final episodes have launched. External execution remains held until quiet measurements finish. The 24-hour soak was removed as a release requirement; it will not be started.

The earlier b3 candidate passed 385 tests / 1 skip + 17 smoke (retained `verification/FULL-SUITE.md`) but failed the first complete performance campaign. The full 60 cells and all raw evidence are retained in `docs/evaluation/runtime/primary-v1/`: 35 budget aborts, 14 cell errors, 40,254 planned/measured failures; only 3/15 eligible pairs per route. All 66 cleanup receipts passed. No complete throughput/RAM/control target was proved.

That trial exposed a cross-agent ownership/lifetime bug. An action mistook unrelated scratch tabs for pop-ups and retained them to a 60-second deadline. `32bad68` binds native opener UUIDs, snapshots IDs and clears follow timers. The paired four-iteration/two-tab probe showed 8 closed WebContent processes retained before, 0 after; fixed peak 170.68 MiB under a 1 GiB guard. Evidence is in `verification/popup-lifetime/`.

The old journey reconstructed all 16 states, but token reductions were SDK 52.27%, CLI 41.62%, MCP 43.35%. SDK’s duplicated full text inflates its denominator. `e05ac3e` now inherits only unchanged metadata from the explicitly requested baseline, preserving full/reset results and bounding metadata cache storage.37 focused checks plus 8 integrated checks passed; actual fresh journey tokens remain unmeasured.

The initial semantic grid had a verifier schema mistake after its actions. Corrected separate diagnostics passed all 1/4/8/16/32 levels with ownership, fairness, cancellation, drain and connection bounds; peak 32-worker family 1231 MiB. The corrected harness reads tasks separately from runtime counters and strictly reconstructs all 6 metadata fields against forced-full. It does not drop payloads from token counts or weaken reset fidelity.

Loaded-control p95 also missed the target. Retained-history attribution showed 95–99% queue time behind same-tab waits with a 100 ms condition poll interval; return 1 JS service was about 1 ms. `7553315` implements a general event-sensitive wait with a bounded 100 ms fallback, original predicate semantics and cancellation cleanup. Fifty focused checks passed. Tab ordering and the frozen eval gate are unchanged; fresh comparative latency proof is pending.

`9425a3a` bounds connection-pressure retries and connected reply reads with the caller's explicit deadline. Read/fetch/download share that budget across readiness and fallback, and MCP preserves arrival-time budgets. The transport lane reports 20 latest-source checks and 15 repeat checks passing; earlier compatibility receipts are distinguished. Exact evidence is retained in `verification/condition-waits/` and `verification/transport-pressure/`.

## Remaining work in order

1. The corrected frozen candidate’s 416-test full regression is complete and retained; preserve its immutable binary and source hashes.
2. Finish root-owned primary-v2, inspect all rows and cleanup, then run secondary-v1 unchanged. Retain the earlier failed campaign. No builds/tests/model episodes during matrices.
3. Rerun the 16-state actual SDK/CLI/MCP observation journey and all 1/4/8/16/32 semantics with corrected strict metadata reconstruction. Report all target misses and denominators, not a passing subset.
4. Only after final quiet measurements, give `/root/external_campaign` the confirmed hash and explicit GO for 125 tasks x 5 seeds x 2 arms = 1,250 episodes. The corrected initial-Umbra comparison is prepared in `external-final-v2/`; old d316 preflight is superseded, zero episodes launched. The separate candidate/Playwright-Chromium campaign is frozen in `external-competitor-v1/` before outcomes. Execute the initial-Umbra pair first, competitor pair second, with at most four external workers total. Original handoff: task directory `external-final/continue.md`.
5. Run bounded lifecycle/cleanup validation as appropriate. The user removed the 24-hour soak requirement; retain the optional tool and make no long-duration stability claim.
6. Publish final measured results, validate docs/SVGs, document all deferred or unavailable checks and target misses, then push the verified commits and confirm the remote revision.

## Resume and safety

Task directory currently `/private/var/folders/l0/f73q0dln26v1fk40dxfc_w9h0000gn/T/umbra-runtime-k_tqurzx`. Baseline app: `baseline-app/Umbra.app/Contents/MacOS/umbra`, SHA-256 `1a71d178d1899b5610adf065128167efadae7e054b27e18d7b06cc6681b8dd47`; baseline source tree `4e07d2462a46c5b8fe71837cb0ddcf7677cec18e`.

Performance lane is task directory `runtime`; exact commands and fixed harness hashes are in `performance-result.md` and `performance-handoff.json`. The older handoff describes commands; its original harness hash is superseded by the hash above and `performance-final-v2-freeze-check.json`. Root and lane harness files match the current freeze. Workload SHA-256 `bd536999c3efca63a69c02160bf2122597a05970f9a711d912d8c6cd936d8318`. Use task directory `tokenizer-venv/bin/python` with actual pinned tiktoken.

External dependencies: `/private/tmp/umbra-runtime-external.SXH9wg`; preparation in `docs/evaluation/runtime/external/PREPARATION.md`. Final worker owns its prepared `external-final` directory; do not restart an active campaign or substitute calibration for full proof.

All test browsers use named throwaway profiles and localhost fixtures. Stop/delete only run-owned profiles. Never install, touch the default profile, run Safari/AppleScript, or print cookie values. No product-source changes are planned unless a concrete failure requires one.
