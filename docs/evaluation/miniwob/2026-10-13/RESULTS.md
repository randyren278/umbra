# MiniWoB++ external evaluation

Status: COMPLETE.

Pinned upstream tasks and BrowserGym setup/validation; fresh Codex sessions with filtered browser MCP tools.

| Browser | BrowserGym successes | Full reward | Episode p50 / p95 | Broker call p50 / p95 |
|---|---:|---:|---:|---:|
| umbra | 524/625 (83.8%; task CI 78.2%–89.1%) | 517/625 | 21.0 / 83.7 s | 8.0 / 31.9 ms |
| playwright | 494/625 (79.0%; task CI 72.6%–85.1%) | 489/625 | 25.2 / 71.6 s | 47.2 / 5023.4 ms |

Paired success difference (Umbra minus Playwright): 4.8 percentage points, 95% task-cluster interval -0.3 to 9.9.

Full-reward paired difference: 4.5 percentage points, 95% task-cluster interval -0.6 to 9.6.


Both primary score intervals include zero: these outcomes do not establish superiority. The asymmetric action budget prevents attributing the observed gap solely to browser quality.

## Frozen setup

- Umbra revision: `a215d69297124329c80ba743f773def3c950b257`; binary SHA-256 `96850cf40c9685dd00b40c5bf8304992f3fd2405367092e274534fd8a039c979`.
- MiniWoB++ revision: `33c3b4ddef8c6eb67c57a29663d844b1eda7e614`.
- BrowserGym revision: `e71419aef5759a11cb7896a26c36e5f7e92d82b2`.
- Model: `gpt-6.1-sol`, `high` effort; `codex-cli 0.162.1`.
- Task types: 125; 5 of the standard five repetitions; two arms.
- Upstream task order and RandomState(42) seed schedule preserved. No task excluded after seeing results.
- Ten interaction units as implemented by the frozen broker, forty observations, 120 seconds per episode including setup and final response. A native press-array counts as one unit; fill_form counts each field separately. This is not ten unbatched BrowserGym actions.
- Loopback HTTP only; disposable Umbra profiles and Chromium contexts. 4 concurrent episodes.
- No arbitrary JavaScript, filesystem tools, browser navigation, other MCP servers or account access in either arm.
- BrowserGym success is its unchanged `RAW_REWARD_GLOBAL > 0` check on a terminated episode. Full reward requires exactly 1.0.
- Attempts with missing/malformed traces, forbidden filesystem/command tools or another MCP server are invalidated by transcript audit. Started actions are audited even if they never completed.

## Coverage and failures

| Browser | Finished | Train success | Test success | Cleanup failures | Trace violations |
|---|---:|---:|---:|---:|---:|
| umbra | 625/625 | 273/310 | 251/315 | 0 | 0 |
| playwright | 625/625 | 257/310 | 237/315 | 0 | 0 |

| Failure classification | Umbra | Playwright |
|---|---:|---:|
| action limit | 1 | 9 |
| agent stopped unfinished | 64 | 81 |
| infrastructure / setup | 0 | 1 |
| upstream rejected | 26 | 34 |
| wall time limit | 10 | 6 |

## Unbatched key-budget sensitivity

Post-hoc conservative diagnostic, not a replacement score: expand each key in a native press-array into its own interaction unit and exclude credit from outcomes reached after ten such units. Other interaction commands count once; fill_form counts fields. This does not rerun the agents with an enforced normalized budget, and counts individual character keys separately even where another typing API could express a string in one action.

| Browser | Positive reward within ten expanded units | Full reward within ten expanded units |
|---|---:|---:|
| umbra | 501/625 | 494/625 |
| playwright | 494/625 | 489/625 |

The primary score remains the frozen protocol score. Its batching asymmetry prevents attributing a score difference solely to browser quality. A prospective comparison with normalized actions is required for that claim.

## Serialized clipboard diagnostic

A separate post-hoc run repeated both clipboard task types with the original five seeds, one episode at a time. Umbra earned full reward on 10/10 cases; Playwright earned 10/10. These twenty episodes do not replace any primary result. Strategy and machine load also vary, so this does not identify clipboard contention as the sole cause of primary failures.

[Diagnostic report](clipboard-serial/RESULTS.md).

## Per-task results

| Task | Split | Umbra | Playwright |
|---|---|---:|---:|
| miniwob.ascending-numbers | test | 4/5 | 0/5 |
| miniwob.bisect-angle | test | 1/5 | 0/5 |
| miniwob.book-flight | train | 3/5 | 1/5 |
| miniwob.book-flight-nodelay | train | 3/5 | 0/5 |
| miniwob.buy-ticket | train | 5/5 | 5/5 |
| miniwob.choose-date | train | 5/5 | 5/5 |
| miniwob.choose-date-easy | train | 5/5 | 5/5 |
| miniwob.choose-date-medium | train | 5/5 | 5/5 |
| miniwob.choose-date-nodelay | train | 5/5 | 5/5 |
| miniwob.choose-list | test | 5/5 | 5/5 |
| miniwob.circle-center | train | 5/5 | 5/5 |
| miniwob.click-button | test | 5/5 | 5/5 |
| miniwob.click-button-sequence | test | 4/5 | 5/5 |
| miniwob.click-checkboxes | train | 5/5 | 5/5 |
| miniwob.click-checkboxes-large | train | 5/5 | 4/5 |
| miniwob.click-checkboxes-soft | train | 5/5 | 5/5 |
| miniwob.click-checkboxes-transfer | train | 5/5 | 5/5 |
| miniwob.click-collapsible | train | 5/5 | 5/5 |
| miniwob.click-collapsible-2 | train | 5/5 | 4/5 |
| miniwob.click-collapsible-2-nodelay | train | 5/5 | 3/5 |
| miniwob.click-collapsible-nodelay | train | 5/5 | 5/5 |
| miniwob.click-color | test | 5/5 | 3/5 |
| miniwob.click-dialog | train | 5/5 | 5/5 |
| miniwob.click-dialog-2 | train | 5/5 | 5/5 |
| miniwob.click-link | test | 5/5 | 5/5 |
| miniwob.click-menu | test | 2/5 | 5/5 |
| miniwob.click-menu-2 | test | 5/5 | 5/5 |
| miniwob.click-option | train | 5/5 | 5/5 |
| miniwob.click-pie | test | 5/5 | 5/5 |
| miniwob.click-pie-nodelay | test | 5/5 | 4/5 |
| miniwob.click-scroll-list | test | 5/5 | 5/5 |
| miniwob.click-shades | train | 5/5 | 4/5 |
| miniwob.click-shape | test | 5/5 | 5/5 |
| miniwob.click-tab | test | 5/5 | 5/5 |
| miniwob.click-tab-2 | test | 5/5 | 5/5 |
| miniwob.click-tab-2-easy | test | 5/5 | 4/5 |
| miniwob.click-tab-2-hard | test | 5/5 | 5/5 |
| miniwob.click-tab-2-medium | test | 5/5 | 3/5 |
| miniwob.click-test | test | 5/5 | 5/5 |
| miniwob.click-test-2 | train | 5/5 | 5/5 |
| miniwob.click-test-transfer | train | 5/5 | 5/5 |
| miniwob.click-widget | test | 5/5 | 5/5 |
| miniwob.copy-paste | test | 4/5 | 0/5 |
| miniwob.copy-paste-2 | test | 1/5 | 0/5 |
| miniwob.count-shape | train | 5/5 | 5/5 |
| miniwob.count-sides | test | 5/5 | 5/5 |
| miniwob.daily-calendar | train | 1/5 | 5/5 |
| miniwob.drag-box | train | 5/5 | 5/5 |
| miniwob.drag-circle | test | 2/5 | 1/5 |
| miniwob.drag-cube | test | 0/5 | 3/5 |
| miniwob.drag-items | train | 3/5 | 3/5 |
| miniwob.drag-items-grid | test | 1/5 | 3/5 |
| miniwob.drag-shapes | train | 5/5 | 3/5 |
| miniwob.drag-shapes-2 | train | 5/5 | 0/5 |
| miniwob.drag-single-shape | train | 2/5 | 2/5 |
| miniwob.drag-sort-numbers | train | 5/5 | 5/5 |
| miniwob.draw-circle | train | 0/5 | 0/5 |
| miniwob.draw-line | train | 1/5 | 0/5 |
| miniwob.email-inbox | test | 3/5 | 5/5 |
| miniwob.email-inbox-delete | test | 1/5 | 5/5 |
| miniwob.email-inbox-forward | test | 5/5 | 5/5 |
| miniwob.email-inbox-forward-nl | test | 4/5 | 5/5 |
| miniwob.email-inbox-forward-nl-turk | test | 5/5 | 5/5 |
| miniwob.email-inbox-important | test | 1/5 | 5/5 |
| miniwob.email-inbox-nl-turk | test | 5/5 | 5/5 |
| miniwob.email-inbox-noscroll | test | 3/5 | 5/5 |
| miniwob.email-inbox-reply | test | 5/5 | 5/5 |
| miniwob.email-inbox-star-reply | test | 5/5 | 5/5 |
| miniwob.enter-date | train | 5/5 | 5/5 |
| miniwob.enter-password | train | 5/5 | 5/5 |
| miniwob.enter-text | train | 5/5 | 5/5 |
| miniwob.enter-text-2 | train | 5/5 | 5/5 |
| miniwob.enter-text-dynamic | train | 5/5 | 5/5 |
| miniwob.enter-time | train | 5/5 | 5/5 |
| miniwob.find-greatest | test | 5/5 | 5/5 |
| miniwob.find-midpoint | train | 0/5 | 0/5 |
| miniwob.find-word | test | 5/5 | 5/5 |
| miniwob.focus-text | train | 5/5 | 5/5 |
| miniwob.focus-text-2 | train | 5/5 | 5/5 |
| miniwob.form-sequence | train | 5/5 | 5/5 |
| miniwob.form-sequence-2 | test | 5/5 | 5/5 |
| miniwob.form-sequence-3 | test | 5/5 | 5/5 |
| miniwob.generate-number | test | 5/5 | 5/5 |
| miniwob.grid-coordinate | test | 4/5 | 5/5 |
| miniwob.guess-number | test | 5/5 | 5/5 |
| miniwob.highlight-text | test | 4/5 | 0/5 |
| miniwob.highlight-text-2 | test | 5/5 | 0/5 |
| miniwob.hot-cold | test | 0/5 | 0/5 |
| miniwob.identify-shape | train | 5/5 | 5/5 |
| miniwob.login-user | train | 5/5 | 5/5 |
| miniwob.login-user-popup | train | 5/5 | 4/5 |
| miniwob.multi-layouts | train | 5/5 | 5/5 |
| miniwob.multi-orderings | train | 5/5 | 4/5 |
| miniwob.navigate-tree | train | 5/5 | 5/5 |
| miniwob.number-checkboxes | test | 5/5 | 0/5 |
| miniwob.odd-or-even | train | 5/5 | 5/5 |
| miniwob.order-food | test | 5/5 | 5/5 |
| miniwob.phone-book | test | 5/5 | 5/5 |
| miniwob.read-table | train | 5/5 | 5/5 |
| miniwob.read-table-2 | train | 5/5 | 5/5 |
| miniwob.resize-textarea | test | 0/5 | 0/5 |
| miniwob.right-angle | test | 1/5 | 0/5 |
| miniwob.scroll-text | test | 5/5 | 5/5 |
| miniwob.scroll-text-2 | test | 5/5 | 4/5 |
| miniwob.search-engine | train | 0/5 | 0/5 |
| miniwob.sign-agreement | test | 5/5 | 2/5 |
| miniwob.simple-algebra | test | 5/5 | 5/5 |
| miniwob.simple-arithmetic | test | 5/5 | 5/5 |
| miniwob.social-media | test | 2/5 | 3/5 |
| miniwob.social-media-all | test | 4/5 | 4/5 |
| miniwob.social-media-some | test | 5/5 | 5/5 |
| miniwob.stock-market | test | 1/5 | 1/5 |
| miniwob.terminal | test | 5/5 | 5/5 |
| miniwob.text-editor | train | 1/5 | 4/5 |
| miniwob.text-transform | train | 4/5 | 5/5 |
| miniwob.tic-tac-toe | test | 4/5 | 2/5 |
| miniwob.unicode-test | train | 5/5 | 5/5 |
| miniwob.use-autocomplete | train | 5/5 | 5/5 |
| miniwob.use-autocomplete-nodelay | train | 5/5 | 5/5 |
| miniwob.use-colorwheel | train | 5/5 | 5/5 |
| miniwob.use-colorwheel-2 | train | 5/5 | 5/5 |
| miniwob.use-slider | train | 5/5 | 3/5 |
| miniwob.use-slider-2 | train | 5/5 | 3/5 |
| miniwob.use-spinner | test | 5/5 | 5/5 |
| miniwob.visual-addition | train | 5/5 | 5/5 |

## Interpretation limits

This is a comparison on standard external tasks with their original grader. It uses an adapted MCP action/observation protocol, so these scores are not an official BrowserGym or MiniWoB leaderboard submission. Tool capabilities, browser engines and screenshots differ; the Playwright baseline uses Chromium. The 120-second cap is stricter than BrowserGym’s page timer. Scores support claims about this frozen agent/tool setup only.

The bootstrap groups all repetitions of each task together. Episode-level independence is not assumed. These intervals describe uncertainty over task types, not performance on every real website. Tasks are public and may be present in model training data.

Native copy/paste uses the Mac’s shared clipboard, which browser profiles do not isolate. Clipboard tasks ran concurrently; their outcomes cannot establish isolated clipboard reliability. They remain in the primary frozen denominator.

Wall times include model inference and run under shared machine load. Broker call times include MCP handling, action waits, two upstream validation calls and image hashing; they are not pure engine latency. The Python Playwright control connection uses the upstream 100 ms slow-motion setting; the Umbra control connection has no equivalent delay. Broker durations therefore do not support a browser speedup claim. Installation is outside the timing. Codex reports token usage but no dollar cost for this subscription run.

The disposable environment uses Playwright Python 1.63.0 with browsergym-core 0.14.3, overriding that package’s legacy Playwright 1.44 pin. The upstream task code is imported from the pinned source checkout and calibrated with positive, negative and unfinished controls; the Gym runtime is not used. This environment is an adapter execution, not an unmodified BrowserGym installation.

### Primary sources

- [MiniWoB++](https://github.com/Farama-Foundation/miniwob-plusplus): external HTML tasks.
- [BrowserGym MiniWoB adapter](https://github.com/ServiceNow/BrowserGym/blob/e71419aef5759a11cb7896a26c36e5f7e92d82b2/browsergym/miniwob/src/browsergym/miniwob/base.py): unchanged setup and reward validation.
- [BrowserGym benchmark configuration](https://github.com/ServiceNow/BrowserGym/blob/e71419aef5759a11cb7896a26c36e5f7e92d82b2/browsergym/experiments/src/browsergym/experiments/benchmark/configs.py): 125 tasks, five repeats and ten actions.
- [Playwright MCP](https://github.com/microsoft/playwright-mcp): comparator.
- [WebdriverIO benchmark](https://github.com/webdriverio/benchmark): Online-Mind2Web comparison; not executed because it requires live websites, gated tasks and paid judging.

Raw run directory: `/private/tmp/umbra-external-full-20261013`. `evidence-hashes.json` identifies the exact raw files. `results.json` retains every episode; `summary.json` holds aggregate measurements.
