# Run several agents without losing track of their work

Umbra’s runtime schedules requests, keeps tab ownership explicit, and exposes the state needed to diagnose a stalled task. CLI, MCP and the [persistent Python, TypeScript and Swift clients](../../sdk/README.md) use the same profile daemon. These controls do not require a model or a cloud service.

Use a separate profile for a different signed-in identity. Within one profile, give each task a named tab and an agent/task lease. Leases coordinate cooperating clients; they are process-local labels, not authentication or a security boundary between agents running as the same macOS user.

## Give work an owner

These examples assume a local server is already running at `127.0.0.1:8765`. For development, use a throwaway profile and the built binary; never test against your signed-in default profile.

```sh
export UMBRA_PROFILE=demo-runtime
umbra open http://127.0.0.1:8765/ --tab research --agent researcher --task lookup
umbra lease --tab research --agent researcher --task lookup --seconds 300
umbra observe --tab research --agent researcher --task lookup --max 4000 --json
```

Every command on a leased tab must carry its matching `agent` and `task`. Commands without ownership remain compatible on unleased tabs. Mutating and observing commands on the same tab run in arrival order; independent tabs can progress concurrently. A lease expires once its duration has passed and the tab has no active or queued work. Pop-ups inherit their parent tab’s lease.

A profile shares its cookies, current-tab pointer and daemon. A named tab prevents accidental current-tab navigation; a lease prevents a cooperating client from addressing that tab under another owner. Use explicit tab names throughout a task.

## See queued work and cancel it

Choose a unique request ID when you may need to cancel or correlate work:

```sh
umbra wait --selector '#ready' --tab research --agent researcher --task lookup \
  --request wait-ready --timeout 30
# From another terminal using the same UMBRA_PROFILE:
umbra tasks --json
umbra cancel wait-ready --agent researcher --task lookup
umbra runtime --json
```

`tasks` returns pending jobs and the last 128 completion records. `runtime` adds limits, queue bytes, connection counts, memory-pressure state and lease metadata. Histories retain command names and labels, not command arguments or page contents. Labels themselves are supplied by clients; keep secrets out of them.

`--deadline` is an absolute Unix timestamp in **seconds**. The runtime uses the earlier of that deadline and the command’s timeout measured from admission; queue time counts. SDKs enforce the same absolute deadline locally. Explicit CLI/MCP budgets cover daemon connection retries and readiness; read/fetch/download share their original budget with store acquisition and one-shot fallback. After the execution deadline, CLI/MCP reply reads allow at most 100 ms to receive the daemon’s failure and cancellation details. This does not extend execution: a complete success received after the execution deadline is rejected, and an incomplete or later reply yields a local deadline error. Blocking request writes are unchanged, so this is a bound on connection/reply waiting rather than a hard whole-call wall-clock guarantee. Refused/full UNIX connections retry for at most one second within that budget; a missing socket keeps immediate cold startup. `request` is the runtime cancellation identifier; the raw socket envelope’s `id` only correlates a reply. SDKs set both consistently unless you explicitly override `args.request`.

Cancellation interrupts queued work and supported continuations, including idle-wait timers and DOM watches. It does not terminate arbitrary JavaScript already dispatched; that script and its later side effects may continue after cancellation is acknowledged. It cannot undo an already-dispatched click, submission or download. A timeout or socket closure alone does not prove that an action failed. Inspect the result before retrying consequential work. This runtime does not provide rollback.

## Observe changes without sending the whole page again

```sh
umbra observe --tab research --agent researcher --task lookup --interactive --max 4000 --json > first.json
baseline=$(python3 -c 'import json; print(json.load(open("first.json"))["baseline"])')
umbra observe --tab research --agent researcher --task lookup --interactive --max 4000 --since "$baseline" --json
```

Pass ownership on these calls if the tab is leased. A first observation returns `mode: "full"`, page text and a `baseline` token. With a compatible retained baseline, `mode: "delta"` returns `patch.start`, `patch.delete` and `patch.lines`. Replace that range in the previous snapshot’s line array; the `text` field is a human-readable patch, not a reconstructed full snapshot. Keep the newly returned baseline together with its reconstructed state. Delta fields `refs`, `url`, `title`, `truncated`, `depthLimited` and `doc` inherit from the **requested baseline** when omitted; emitted values replace them, including `false`. Keep state per token when callers interleave observations. Full responses, including resets, replace the state and contain all metadata. `changed` describes outline changes only; a title or URL can change without changing outline lines.

Baselines are document-local: navigation, eviction or a different scope/budget returns full content with a reset reason. Keep `interactive`, `scope`, `max` and `depth` identical for deltas. Each document retains at most eight baselines with a 512 KiB estimated serialized UTF-16 budget, including baseline tokens, options, line arrays and metadata. `max` bounds outline characters (1–100000), not model tokens or UTF-8 bytes; `depth` bounds nesting (0–100).

Read a narrower region with `--scope main`, a selector, text target or fresh ref. Use a screenshot only when layout adds information.

## Extract records and wait for events

```sh
umbra extract table --kind table --limit 25 --text-max 300 --json --tab research --agent researcher --task lookup
umbra extract form --kind form --schema '{"type":"array","items":{"type":"object"}}' --json --tab research --agent researcher --task lookup
umbra watch '#status' --state text --equals Saved --duration 10 --json --tab research --agent researcher --task lookup
```

Extraction is deterministic DOM traversal, not model interpretation. It returns typed form values or table/list records with source refs and document provenance; password values are omitted. `limit` caps rows/fields/items (1–1000), and `text-max` caps characters per value (1–10000). Check `truncated` before treating the result as complete.

The JSON Schema subset supports types, properties, required fields, additional properties, array items, item/string/numeric bounds, enum and const. Unsupported constraints fail explicitly; this is not a full JSON Schema implementation.

`watch` uses DOM mutation and input/change/load events rather than repeated agent calls. It returns `matched: false` when its duration expires; durations are 0–25 seconds, with at most 16 watches per document. A programmatic value change that emits no observed event can be missed. Use ordinary `wait --js` when the condition needs polling or JavaScript computation. This is a one-result wait, not a durable event stream. General `wait --js` conditions react to DOM/input/load/visibility events too, retaining a 100 ms fallback for globals/time or unobserved changes. Predicates may run repeatedly; keep them free of side effects. Resources remain on demand and are removed on completion, cancellation, timeout or navigation.

## Batch known steps and checkpoint a workflow

```sh
umbra batch '[
  {"command":"fill","args":{"target":"#name","value":"Jordan","tab":"research"}},
  {"command":"click","args":{"target":"#save","tab":"research"},"before":{"selector":"#save"},"after":{"text":"Saved"}}
]' --agent researcher --task lookup --json
```

A batch runs 1–64 supported steps sequentially and stops at the first error. Each successful step returns a receipt. Failures retain indexed completed-step results and a failed-step record through CLI JSON errors, SDK errors and MCP structured content; exit codes and MCP error status remain failures. A failed step marked dispatched may already have changed the site. `before` and `after` use string conditions from `wait`: `js`, `url`, `text`, `selector` or `gone`. Steps name their target tab in `args` and use the parent’s agent/task and deadline. `batch` and `workflow` are coordinators, so they do not take a top-level CLI `--tab`. They are individual scheduler operations, not an atomic transaction across agents. Definitions are limited to 256 KiB and collected results to 4 MiB. Large outputs should go to files or use smaller observation budgets.

For a reusable workflow, save this JSON as `workflow.json`:

```json
{
  "steps": [
    {"command":"fill","args":{"target":"#name","value":"${name}","tab":"research"}},
    {"command":"click","args":{"target":"#save","tab":"research"},"after":{"text":"Saved"}}
  ]
}
```

```sh
umbra workflow workflow.json --vars '{"name":"Jordan"}' --checkpoint progress.json \
  --agent researcher --task lookup
umbra workflow workflow.json --vars '{"name":"Jordan"}' --checkpoint progress.json \
  --agent researcher --task lookup --resume
```

The checkpoint records only a resolved-plan digest, step index, phase and timestamps, at mode 0600. Variables and page results are not written to it. The definition file and arguments can still contain sensitive data; protect those separately. A lock prevents concurrent use of the same checkpoint. Atomic replacement protects against partial file writes; it is not an fsync or power-loss durability guarantee.

`pending` is written before dispatch; `ready` follows command success and its postcondition. Resuming a pending, non-repeatable step refuses to guess whether its side effect happened. After inspecting the actual state, use `--resolve verified` to check its postcondition and advance, or `--resolve retry` to explicitly repeat it. `repeatable: true` opts a step into repetition on resume; use it only when repeating is safe. There is no automatic model retry.

The SDKs’ optional `propose_repair` / `proposeRepair` helper obtains a fresh observation and invokes your callback for replacement arguments. It returns an undispatched proposal. It does not select a model, execute the replacement or establish whether an earlier failed action was dispatched. [Client examples and cancellation](../../sdk/README.md).

## Choose a lighter execution path

```sh
umbra resources lean --tab research --agent researcher --task lookup
umbra resources balanced --tab research --agent researcher --task lookup --reload --until 'document.querySelector("main")'
umbra resources visual --tab research --agent researcher --task lookup --reload
umbra http http://127.0.0.1:8765/data.json --max 1048576 --json
```

`lean` blocks images, fonts, media and trackers; `balanced` blocks trackers; `visual` blocks nothing. Presets keep scripts and stylesheets. They replace the tab’s blocked-resource rules and persist until changed, including across navigation; finishing a task or releasing a lease does not restore earlier rules. With `--reload --until`, a failed readiness condition restores the previous rules and reloads to test again. A reload failure, cancellation or deadline can leave the applied rules in place. A preset is not a compatibility or speed guarantee.

`http` uses bounded ephemeral HTTP for UTF-8 HTML/text/JSON. It creates no WebKit tab, runs no JavaScript and sends no profile cookies. Its byte limit is 1–4194304. Use signed-in `fetch` or `open` when authentication or page execution matters. This is an HTTP shortcut, not a second DOM browser engine.

## Reach frames and canvas carefully

```sh
umbra frames --tab research --agent researcher --task lookup --json
# Replace f2 with an ID from that result:
umbra frame f2 snapshot --args '{"interactive":true,"max":2000}' --tab research --agent researcher --task lookup --json
umbra frame f2 eval --args '{"script":"return document.title"}' --tab research --agent researcher --task lookup --json
umbra pointer click --x 120 --y 80 --tab research --agent researcher --task lookup --json
```

`frames` lists registered main and cross-origin documents. Frame IDs change on navigation; refresh them after document changes. A `frame` operation takes explicit JSON arguments and does not change a hidden current-frame selection. Supported operations are snapshot, observe, extract, get, watch, eval, click, fill, check, uncheck and select. Frame arguments are limited to 64 KiB.

Native frame actions map through ancestor geometry and refuse hidden, covered, transformed, zoomed or offscreen targets. Scroll the parent first when needed. This capability does not disable the page’s same-origin policy. Closed shadow roots remain unsupported.

`pointer` sends trusted left-button click/move/down/up input at CSS viewport coordinates, including canvas pixels. It infers no DOM or canvas semantics and does not verify the application effect. Check a fresh observation or screenshot afterward. `capabilities --json` reports supported paths and explicit limits.

## Inspect only when needed

```sh
umbra inspector
# Open the returned loopback URL, including its token fragment, in your browser.
umbra record start --tab research --agent researcher --task lookup --path ./capture --fps 1 --seconds 20 --max 16777216
umbra record status --tab research --agent researcher --task lookup
umbra record stop --tab research --agent researcher --task lookup
umbra processes --json
umbra diagnose --path ./diagnostic.json
umbra diagnose --path ./failure.json --target research --observations --agent researcher --task lookup
umbra inspector --stop
```

The inspector is a token-protected IPv4-loopback dashboard for queue/ownership metadata, on-demand screenshots and human takeover. Its URL grants access to the profile inspector; keep it private. Optional live preview captures at one frame per second while enabled and visible; capture is off by default. `record` explicitly writes a PNG sequence and JSON manifest, bounded by duration (1–600 seconds), rate (0.1–5 fps), 600 frames and a 64 MiB maximum byte budget. Captures can contain signed-in page content. There is no video encoder or always-on recording service.

`diagnose` defaults to a metadata-only JSON bundle at mode 0600, at most 512 KiB, with recent failure codes and queue/ownership evidence. Explicit `--observations --target TAB` adds a bounded redacted outline under matching agent/task ownership. Field values, their mirrored text, URLs and recognized token patterns are removed before capture; labels and other visible content can remain sensitive. Cookies, headers, raw arguments and screenshots are excluded. Choose non-sensitive agent/task/tab/request labels. A screenshot or recording is a separate, content-bearing artifact.

`processes` and the inspector show native physical footprint and cumulative CPU by unique PID, with tracked tabs mapped to their WebContent process. Shared process memory and CPU are counted once and are not allocated among tabs or tasks. CPU seconds are cumulative process time, not utilization or the cost of a single command; values can be cached for up to two seconds and are null if native time conversion is unavailable. OS responsibility can omit shared or differently attributed GPU/network helpers; coverage gaps are shown explicitly. The runtime’s original `memory` field remains daemon-only.

## Capacity and memory pressure

Set these before starting a profile’s daemon; restarting is required to change its environment:

| Variable | Default | Valid range | Meaning |
|---|---:|---:|---|
| `UMBRA_ACTIVE` | 32 | 1–256 | Concurrent active non-coordinator jobs |
| `UMBRA_PENDING` | 256 | 1–4096 | Total admitted running/queued jobs |
| `UMBRA_TABS` | 128 | 1–1024 | Admitted tabs, including scratch tabs/pop-ups |
| `UMBRA_MIN_AVAILABLE_MB` | 512 | 0–16384 | Estimated system memory headroom reserved before starting more work |
| `UMBRA_JOB_MB` | 128 | 1–4096 | Headroom estimate per active job; limits admission, not an allocation guarantee |
| `UMBRA_CONNECTIONS` | 64 | 2–1024 | Accepted Unix-socket connections |
| `UMBRA_QUEUE_BYTES` | 33554432 | 1024–268435456 | Admitted argument bytes |
| `UMBRA_REQUEST_BYTES` | 8388608 | 1024–67108864 | Buffered unread socket input bytes |

Replies have a 64 MiB server cap; SDK clients default to a smaller 16 MiB cap. Idle socket reads/writes have a 30-second timeout. Admission failures are explicit errors; retry only when appropriate, rather than opening unlimited connections. Connection handling still uses one thread per accepted connection, bounded by the configured limit.

The scheduler also samples system free pages (which already include speculative pages) plus inactive pages once per second as an estimate of reclaimable headroom. It caps new active work using the reserve and per-job estimate, pauses new tab admission below the reserve, and resumes queued work when headroom recovers. This is not a per-profile physical-memory ceiling; the OS pressure signal remains authoritative.

Warning memory pressure reduces active capacity; critical pressure pauses new work and tab creation while allowing control requests and tab closure. Reclamation targets clean abandoned tabs; active jobs, queued tab work, leases, downloads, handoffs and tracked unsaved form input protect tabs. Successful explicit fill/type/select/check actions also protect form edits made through setter fallbacks, including frames; read-only page scripts do not mark tabs dirty. Programmatic page changes are not a general dirty-state detector. `runtime --reclaim` applies the critical reclamation policy manually.

Runtime memory counters describe the **daemon only**, excluding WebKit/GPU/network helpers. They are not whole-browser memory measurements. See [evaluation methods and retained samples](../evaluation/README.md) for measured workloads; performance targets are not achieved results.

When the task is finished, release its lease and close its tab:

```sh
umbra lease --release --tab research --agent researcher --task lookup
umbra tab close research
umbra stop
```
