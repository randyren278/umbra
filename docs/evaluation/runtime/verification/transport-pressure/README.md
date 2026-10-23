# Connection pressure and explicit reply budgets

A controlled Darwin UNIX listener with a full accept backlog returns ECONNREFUSED, the same error as a stale endpoint. The old client could spawn a replacement daemon and return “no open tab” instead of the existing endpoint’s response. This reproduces the full-suite failure’s exact symptom; that original stress event did not record errno.

The client retries refused/full connections for up to one second within a valid supplied budget. Missing sockets keep immediate cold startup. Existing stale recovery, worker-capacity and cancellation assertions remain. Read/fetch/download preserve live-daemon routing and share their remaining budget with store acquisition and one-shot fallback. MCP readiness and dispatch share one explicit absolute deadline.

A second controlled test held partial replies beyond a 0.12-second budget; the old client returned late success after 0.5 seconds. Explicit budgets now poll reply reads against the original deadline, including partial replies and EINTR. No-budget calls retain their blocking-read behavior.

The latest isolated source passed 20 unique checks and 15 repeats with explicit profile cleanup. Previous pre-poll compatibility checks are distinguished in the JSON receipt. The combined build passed 25 focused checks; its full regression is the next gate.
