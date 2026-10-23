# Umbra runtime: external MiniWoB comparison

PLUMBING DIAGNOSTIC ONLY. This is a selected task subset and/or both arms use the same binary; it cannot establish runtime non-regression.

Status: COMPLETE. 4/4 frozen episodes finished.

Unchanged pinned BrowserGym setup/validation, 125 task types in a full run, five seeded repeats per arm. Both arms use Umbra/WebKit through the same filtered MCP broker. Every frozen episode stays in the denominator.

| Arm | Positive reward | Full reward | Finished | Cleanup failures | Invalid traces |
|---|---:|---:|---:|---:|---:|
| baseline | 2/2 | 2/2 | 2/2 | 0 | 0 |
| candidate | 2/2 | 2/2 | 2/2 | 0 | 0 |

| Metric | Candidate minus baseline | 95% task-cluster interval |
|---|---:|---:|
| accepted_success | 0.00 pp | 0.00 to 0.00 pp |
| strict_success | 0.00 pp | 0.00 to 0.00 pp |

Observed aggregate score comparison only. A nonnegative difference does not prove statistical noninferiority, per-task equivalence or reliability outside this frozen suite.

Bootstrap: 10,000 paired task-type samples, RandomState(42); all repetitions stay grouped. Public tasks may be present in model training. This adapted MCP protocol is not an official BrowserGym leaderboard submission.

Model: `gpt-6.1-sol`, `high` effort, `codex-cli 0.163.0`. Task types: 2; repetitions: 1; episode cap: 120 seconds. Ten interaction requests and forty observations per episode; a native press-array counts once. Wall time includes model inference and shared load. Broker time includes validation and is not pure engine latency.

Native copy/paste uses the shared macOS clipboard. Profiles isolate browser data, but do not isolate that clipboard. No arbitrary scripts, filesystem tools, source, reward or upstream internals are exposed to the model. The trusted controller alone calls upstream setup and validate. Exact binary, harness, source and raw-file hashes are retained.

| Arm | Binary SHA-256 |
|---|---|
| baseline | `1a71d178d1899b5610adf065128167efadae7e054b27e18d7b06cc6681b8dd47` |
| candidate | `1a71d178d1899b5610adf065128167efadae7e054b27e18d7b06cc6681b8dd47` |

| Failure | Baseline | Candidate |
|---|---:|---:|

| Task | Split | Baseline | Candidate |
|---|---|---:|---:|
| miniwob.click-test-2 | train | 1/1 | 1/1 |
| miniwob.enter-text | train | 1/1 | 1/1 |

Raw evidence: `/private/var/folders/l0/f73q0dln26v1fk40dxfc_w9h0000gn/T/umbra-runtime-k_tqurzx/external-plumbing-pilot-v2`.
