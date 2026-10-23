# Runtime project closeout — 2026-10-23

The user authorized push and closeout, accepted scoped measured results, and explicitly removed the 24-hour soak requirement. This iteration is closed after final regression, independent review and live app checks. [Final results and limits](../evaluation/runtime/RESULTS.md).

## Final implementation

- Bounded adaptive admission, per-tab ordering, agent/task leases, generation-safe cancellation and explicit deadlines.
- Incremental observations, bounded metadata cache, typed DOM extraction, watches/events and event-sensitive waits.
- Checked batches, action receipts and checkpointed workflows with uncertainty refusal and optional repair proposals.
- Cross-origin frames/native input, canvas coordinates, public HTTP and persistent task resource presets.
- Persistent Python/TypeScript/Swift SDKs, on-demand inspector, bounded recording, redacted diagnostic bundles and process accounting.
- Cool-color SVGs and benchmark headlines linked to their recorded workloads and raw data.

Final product source: `9542ee5`. Built binary SHA-256: `bace278786dd13901a428728c8dbc1e0ef542f78feaf7ba6c26df38ab212ad1e`. Later commits only record documentation and evidence.

## Verification

418 tests across 22 suites passed with one expected skip; all 17 smoke checks passed. Three additional visible GUI tests passed. Manual HTTPS navigation returned 200; snapshots, painting, trusted native form input, native Accounts launch/close, and desktop/mobile inspector capture all worked. Images were inspected. The exact final binary passed all five 1/4/8/16/32-client semantic levels and exceeded 50% observation-token reduction independently through SDK, CLI and MCP, reconstructing all 16 states exactly.

Independent review found one malformed frame-select mutation bug; the boundary guard and public regression tests resolve it. No other confirmed P1/P2 was found within the recorded review scope. All run-owned daemons/profile files were cleaned up. Installed binaries and default authentication were preserved.

## Benchmarks and remaining limits

The single-worker matched fixture measured 1.32–1.47× throughput and about 82–84% lower sampled peak family footprint across three valid repeats. The complete 60-cell grid did not meet broad throughput/RAM/p95 gates, and heavy matched 32-worker cells exceeded 2 GiB. All raw failures and aborts are retained. Performance samples identify their earlier candidate hash; final-binary regression/semantic/token checks identify the shipped product hash.

Extended external campaigns and the secondary admission experiment were prepared but not executed for this release; no new score or capability-preservation claim is made. The optional 24-hour tool remains, with its release requirement removed and no long-duration claim. Remote CI workflows are manually disabled, so verification is local. These are documented limits, not hidden passing gates or active background jobs.

## Delivery

The authorized destination is `origin/main` at `github.com/randyren278/umbra`. Publication is verified by reading the remote commit after push. Source/evidence delivery does not install over the owner’s running app.
