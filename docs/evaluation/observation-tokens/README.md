# Observation tokens against Playwright MCP

This study compares the MCP results an agent receives from Umbra with Playwright MCP running Chromium and WebKit. It counts browser observations, including their tool-result metadata. It does not measure total agent usage, model billing, task success or browser speed.

The candidate uses Umbra's explicit `observe --since` baseline protocol plus a complete `text` read on **every** state. Playwright MCP's `browser_snapshot` returns full snapshots; the pinned server exposes full or no snapshot output, without an incremental snapshot mode. Initial observations and navigation resets are charged in full. Clients must retain Umbra's baseline state to use the incremental result.

Complete text reads are included because the pilot caught shortened paragraphs in Umbra's outline. Counting only the shorter outline would fail the information-equivalence check. The measured bundle retains the task prose, table cells, headings, form values, checked and expanded states, dynamic outcomes and actionable controls. The full-text supplement is charged even on pages whose outline already contains all the task prose.

## Results and evidence

The [frozen protocol](protocol.json) defines the workloads, rotated browser order, tokenizer, quality checks and minimum headline threshold before the measured run. The [raw results](results.json) retain every observation response, complete-observation diagnostics, reconstructed text, checked DOM state, token counts, environment and cleanup receipts. [Pilot attempts](pilots.json) retain failures and the changes that preceded measurement.

The scenarios cover an order queue, a customer support record and an article review. Each moves through entry, checkbox, saved receipt, expanded actions, reviewer assignment, an unchanged read, a verification receipt and a navigation reset. Fixture-owned JavaScript sets identical deterministic page states in every engine; this isolates observation representation from input policy. Setup and action responses are excluded consistently across all arms. It is not an agent-driven task evaluation.

Umbra deltas are reconstructed and compared with a fresh full observation at every state, including refs and document metadata. All engines must expose every required piece of task information. The harness compares actual DOM-state objects across engines and retains every repeat. The result also records the cost of Umbra's full observation bundles as a diagnostic, so the effect of retaining client state is visible.

Umbra reuses unchanged outline lines and groups table cells into compact rows. Playwright's delivered snapshot repeats the full accessible tree and its element references. Full-text reads are still charged on every Umbra state; the diagnostic totals show how much of the saving depends on retaining the delta baseline.

This is a small localhost workload. Results depend on page structure and the amount of content that remains unchanged. The comparison is against Playwright MCP's delivered snapshots, including its wrappers and element references, rather than a hypothetical custom compact Playwright client or native WebKit token usage. The pinned Playwright driver is a prerelease; its exact package and browser versions are retained in the raw environment record. No paid model was called.

## Reproduce

Use the recorded app build or verify its binary digest against the raw results. Build locally with `make build`; do not install over a live Umbra app. The harness creates disposable profiles, uses localhost pages, stops its daemons and deletes their profile stores. The output directory must be new.

```sh
task=$(mktemp -d /tmp/umbra-token-study.XXXXXX)
python3 -m venv "$task/env"
"$task/env/bin/pip" install tiktoken==0.12.0
npm install --prefix "$task/tools" @playwright/mcp@0.0.83 \
  playwright@1.64.0-alpha-1790635538000 \
  playwright-core@1.64.0-alpha-1790635538000
export PLAYWRIGHT_BROWSERS_PATH="$task/browsers"
node "$task/tools/node_modules/@playwright/mcp/cli.js" install-browser chromium webkit
"$task/env/bin/python" bench/observation_tokens.py \
  --playwright-mcp "$task/tools/node_modules/@playwright/mcp/cli.js" \
  --out "$task/results"
```

Audit the retained repository evidence with the same tokenizer environment:

```sh
"$task/env/bin/python" bench/verify_observation_tokens.py
```

The [harness](../../../bench/observation_tokens.py), [evidence validator](../../../bench/verify_observation_tokens.py), [verification receipt](verification.json) and [page fixture](../../../bench/observation_tokens.html) are versioned together. The validator also checks that removing required prose, actionable refs or checked state fails the quality gate. Pilot mode is never eligible for a headline. A missing dependency, information mismatch, truncated observation, reconstruction mismatch, failed arm or failed cleanup prevents a completion claim.
