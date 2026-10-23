# Persistent clients

Small Python, TypeScript and Swift clients speak Umbra’s existing JSON-lines Unix-socket protocol. Each client reuses one connection, requires an explicit profile, and serializes its commands. Use separate clients for independent concurrent work; the profile determines which daemon, tabs and signed-in store they share.

There are no third-party runtime dependencies. Copy the source into your project or import it by path. Python needs 3.10+, TypeScript runs directly on a Node version supporting `--experimental-strip-types` (verified here on Node 24), and Swift targets macOS 14+.

## Python

```python
# PYTHONPATH=/path/to/umbra/sdk/python python3 script.py
from umbra import Umbra, UmbraError
import time

with Umbra("agent-research", binary="/absolute/path/to/umbra") as browser:
    browser.start()  # explicit; waits for status, using this profile
    browser.call("open", {"url": "https://example.com"})
    page = browser.call("snapshot", {"interactive": True},
                        deadline=time.time() + 10, request_id="read-1")
    print(page["text"])
```

Without `start()`, the client connects to an already-running daemon. Supply the binary only if you want the client to start it; the SDK never discovers or installs a browser binary. `start()` first probes for the existing daemon, and only spawns when absent. It inherits your environment, overrides `UMBRA_PROFILE`, and defaults to a 20-second startup deadline. It returns a child process handle only when it starts one (`None` / `undefined` / `nil` when the daemon already exists).

## TypeScript

```typescript
import { Umbra, UmbraError } from './umbra.ts';

const browser = new Umbra('agent-research', { binary: '/absolute/path/to/umbra' });
try {
  await browser.start();
  await browser.call('open', { url: 'https://example.com' });
  const page = await browser.call('snapshot', { interactive: true }, {
    deadline: Date.now() / 1000 + 10,
    requestId: 'read-1',
  });
  console.log(page.text);
} finally {
  browser.close();
}
```

Run a TypeScript entry point with `node --experimental-strip-types script.ts`. These sources use Node’s built-in modules, not browser JavaScript.

An `AbortSignal` rejects the active call, closes its data connection and asks the daemon to cancel it over a separate connection. Cancellation is best effort; await an explicit cancellation receipt when you need to know the server accepted it.

```typescript
const controller = new AbortController();
const result = browser.call('wait', { selector: '#ready' }, {
  requestId: 'wait-1', signal: controller.signal,
});
controller.abort();
await result; // rejects with UmbraError.code === 'cancelled'
```

## Swift

Compile `swift/Umbra.swift` alongside your app or command’s sources:

```swift
import Foundation

let browser = try Umbra(profile: "agent-research",
                        binary: URL(fileURLWithPath: "/absolute/path/to/umbra"))
defer { browser.close() }
try browser.start()
try browser.call("open", ["url": "https://example.com"])
let page = try browser.call("snapshot", ["interactive": true],
                            deadline: Date().addingTimeInterval(10), requestID: "read-1")
print(page["text"] ?? "")
```

Swift calls block the calling thread. Use a background queue in a UI app. The client’s serial lock includes deadline accounting.

## Deadlines, cancellation and errors

Python and TypeScript deadlines are absolute Unix timestamps in **seconds**, not milliseconds; Swift accepts an absolute `Date`. They cover the client queue, connection and reply. The SDK forwards the same deadline as `args.deadline` so the runtime can expire work too. Defaults are 30 seconds per call. Closing a client closes its socket; it never sends `stop` or deletes the profile.

To cancel an in-flight request from another thread/task, assign its ID and use the same client’s control method. It opens its own connection, so a blocked data command cannot prevent cancellation:

```python
browser.cancel("wait-1", agent="researcher", task="lookup", deadline=time.time() + 5)
```

```typescript
await browser.cancel('wait-1', { agent: 'researcher', task: 'lookup', deadline: Date.now() / 1000 + 5 });
```

```swift
try browser.cancel("wait-1", agent: "researcher", task: "lookup", deadline: Date().addingTimeInterval(5))
```

Pass the same `agent` and `task` used by the original call when cancelling owned work; omit them for an unowned call. TypeScript AbortSignal forwards the original call’s ownership automatically. The SDK sets `args.request` to its wire request ID by default, so caller-selected IDs identify cancellable runtime work. If you explicitly override `args.request`, cancel that runtime ID instead.

Cancellation requires a runtime with the `cancel` command; repair proposals require `observe`. On older builds, its unknown-command error is preserved. A local timeout or closed socket alone does not prove a submitted action was cancelled. The clients never automatically replay a command after disconnect, timeout, mismatched reply or uncertain outcome.

`UmbraError.code` preserves the daemon’s structured code, and `details` preserves its complete error object. Transport/protocol failures use code `1`; local deadlines use `2`; TypeScript local aborts use `'cancelled'`. Successful calls return the structured `result` object.

Replies default to a **16 MiB bound**, including the JSON-line framing. Configure `max_reply_bytes` (Python) / `maxReplyBytes` (TypeScript and Swift) for larger screenshots or downloads. Oversized or malformed replies close the socket; a later explicit command can establish another connection. This is reconnection, not replay of the failed command.

`socket_path` / `socketPath` overrides are for test fixtures or custom transports. Normal clients derive the profile’s actual socket path, including Umbra’s existing long-profile hashing rule.

## Optional repair proposals

`propose_repair` / `proposeRepair` asks the runtime for a **fresh `observe` result**, then calls your provider callback with that observation and the failed step. The callback returns a replacement args object. The helper validates the proposal and returns `{step, observation, dispatched: false}`; it never executes or retries the step. It does not select a model, retain provider credentials, or persist callback inputs.

```python
proposal = browser.propose_repair(
    {"command": "click", "args": {"target": "#old"}},
    lambda observation, step: {"target": "#replacement"},
    observe_args={"tab": "work"},
)
```

Supply the relevant tab/observation options explicitly. The caller must establish that the failed action was not dispatched before choosing to run any replacement, and use appropriate preconditions and postconditions. An uncertain submission or timeout must not be automatically repeated. The proposal’s `dispatched: false` describes the proposal helper, not proof about an earlier failed action. User callbacks run in the caller; an elapsed deadline is checked when a synchronous callback returns.

## Verification

```sh
python3 tests/test_sdk.py -v
```

The local Unix-socket fixture checks persistent reuse, fragmented replies, structured errors, bounded buffers, response IDs, absolute deadlines and cancellation over a second connection. It compiles and runs Swift and runs TypeScript when their toolchains are available. To also verify all three clients against a shared real WebKit page on localhost:

```sh
UMBRA_SDK_BINARY="$PWD/build/Umbra.app/Contents/MacOS/umbra" python3 tests/test_sdk.py -v
# Include real owned-request cancellation and observe-only repair on the new runtime:
UMBRA_SDK_BINARY="$PWD/build/Umbra.app/Contents/MacOS/umbra" UMBRA_SDK_RUNTIME=1 python3 tests/test_sdk.py -v
```

That opt-in test creates an isolated profile, sets `UMBRA_IDLE=60`, `UMBRA_UNSEEN=1` and a temporary launch-agent directory, and stops the daemon and removes its profile files afterward. It verifies Python → TypeScript → Swift shared page state and that closing a client leaves the same daemon alive. It does not use the owner’s default profile.
