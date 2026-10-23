# Umbra agent runtime

Build a native browser runtime that completes more agent work per GB of RAM while preserving invisible operation, trusted input, authenticated profiles, and CLI/MCP parity. The source brief is the user-supplied `pasted-text-1.txt` from 2026-10-22. Proposed targets are acceptance gates, not achieved measurements.

## Integration sequence

| Chunk | Deliverables | Evidence required |
|---|---|---|
| 1. Resource ownership | Per-tab ordering, leases, bounded admission, cancellation, deadlines, memory-pressure reclamation, runtime metrics | Public command tests for ownership, ordering, independent progress, overload, cancellation and protected reclamation |
| 2. Observations | Bounded incremental snapshots, action receipts, event-driven conditions, schema-checked extraction | DOM changes reconstructed from deltas; navigation invalidation; observer cleanup; schema errors; no secret field leakage |
| 3. Execution | Batches, parameterized workflows, checkpoints, repair adapter and recovery | Stop at failing step; detect uncertain side effects after interruption; verify postconditions before advancing |
| 4. Integrations | Persistent Python, TypeScript and Swift clients; task resource profiles; cross-origin frames and visual targeting | Live fixture journeys using each client and command through CLI/MCP |
| 5. Operations | On-demand local inspector, bounded redacted failure bundles, optional recording | Inspector reflects live queues and ownership; idle capture off; bundle content and retention checks |
| 6. Proof and presentation | Frozen baseline comparisons, concurrency matrix, bounded lifecycle validation, docs and cool-color desktop/mobile SVGs | Raw measurements, honest target assessment, full regression suite, rendered visual inspection |

## Requirement ledger

Implementation, passing tests and a performance result are distinct evidence. Final dispositions are recorded in `STATUS.md`; the 24-hour soak requirement was explicitly removed by the user on 2026-10-23.

| Brief requirement | Planned proof |
|---|---|
| Native invisible runtime, trusted input, persistent authentication, shared CLI/MCP | Existing full suite plus new command MCP conformance |
| Memory-aware scheduling | Heavy fixture admission/queue tests and full responsible-process memory samples |
| Agent/task tab ownership and serial mutations | Two owners contend for one tab; distinct tabs progress independently |
| Pressure reclamation protects active commands, downloads, unsaved work and handoffs | Public pressure/configuration scenarios for each protected state |
| Bounded connections, queues and retained output | Resource-limit rejection, fair drain, bounded recent events, oversized input tests |
| Batched commands | Ordered execution with indexed receipts and early stop |
| Action receipts | Click/fill receipts match visible outcomes on native-input fixtures |
| Event subscriptions | URL/DOM/dialog/download/network conditions without unbounded polling or retained listeners |
| Observation budgets and deltas | Full/delta reconstruction, byte/token accounting, stale baseline rejection |
| Cross-origin frames, virtualized interfaces, canvas | Separate fixture journeys; report unsupported capabilities explicitly until implemented |
| Workflow replay, model assistance and recovery | Deterministic replay, explicit optional repair hook, checkpoint interruption tests |
| Task resource profiles and fallback | Tab preset persists on success until explicitly changed; readiness-failure fallback restores prior rules, with no automatic task-end restore |
| On-demand inspector and takeover | Live task/queue/resource view plus existing handoff journey |
| Persistent SDKs with cancellation and deadlines | Python, TypeScript, Swift live socket tests |
| Bounded redacted failure bundles | Bundle allowlist, size limit and injected secret-field checks |
| Optional streaming/recording/tracing | Explicit start/stop and no capture when unused |
| Lightweight extraction path exploration | Bounded experiment comparing suitable HTTP/DOM work with full WebKit, compatibility documented |
| 2× completed tasks/minute in same RAM budget | Identical mixed-workload baseline/candidate samples, failures included |
| 30% lower active total physical footprint | Whole responsible-process family, matched workload/cache state, repeated samples |
| 50% fewer observation tokens | Same tasks and reconstruction quality, explicit tokenizer or byte-only qualification |
| p95 control latency regression no greater than 10% | Alternated baseline/candidate runs with sample distributions |
| 1, 4, 8, 16, 32 clients | Isolation, fairness, cancellation, success and resource accounting at each level |
| 24-hour localhost soak | Removed as a release requirement at the user’s explicit request on 2026-10-23; no 24-hour stability claim. Optional runner retained for future use. |
| No task-success regression | Frozen task suite with denominator and baseline/candidate failure details |
| Relevant competitor comparison | Separate frozen candidate/Playwright-Chromium campaign on the same external tasks, seeds and model; report interface differences and retain every outcome |
| Docs/SVGs and prominent stats | XML/link/data checks and inspected desktop/mobile renders; no green/orange metric accents |
| Commit in chunks | Lead-owned focused commits; preserve the initial uncommitted patch |

## State and safety

Development and tests use only throwaway named profiles, local fixture sites and build outputs. Installed binaries and the default authenticated store remain untouched. The initial worktree patch is preserved outside the repository and must not be absorbed into an unrelated commit. Workers use isolated worktrees and send patches; the lead reviews and integrates them.

The runtime design compares one daemon per task with a shared profile daemon plus explicit tab ownership and a bounded per-tab scheduler. The shared daemon retains authenticated state and avoids duplicating browser infrastructure; WebKit work stays on the main thread, while asynchronous operations on independent tabs may overlap. Performance measurements decide whether transport changes are justified.
