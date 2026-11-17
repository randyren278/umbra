# Declared API sessions

API sessions are opt-in, same-origin JSON transports. They create no WebView until an explicit browser fallback. Existing browser commands keep their defaults. This is a declared-contract client, not a website-to-API translator.

```sh
umbra api open work --contract contract.json
umbra api call work handshake --json
umbra api call work login --data '{"username":"agent","password":"fixture-password"}' --json
umbra api read work inbox --query '{"q":"urgent","unread":1}' --json
umbra api call work mark --data '{"id":7,"revision":1,"requestId":"one-intended-write"}' --json
umbra api browser work --url http://127.0.0.1:PORT/app/js-only --tab work-ui
umbra render --tab work-ui
umbra api close work
```

The commands above use synthetic fixture credentials. Real API access needs its own supported authorization; a browser sign-in does not imply an API grant.

## Opt-in nonvisual browser tabs

Normal `open` keeps the existing browser behavior. Create a new windowless tab explicitly when its work can run without visibility-dependent rendering:

```sh
umbra open example.com --new --tab reading --nonvisual
umbra snapshot --tab reading --interactive
umbra render --tab reading
```

`--nonvisual` is chosen only when a tab is created. The WebKit view and JavaScript state stay alive; read/eval/outline commands can run without a window. Native input, pixel capture and other rendering-dependent commands promote the existing view automatically. `render` promotes it explicitly without reloading; its offscreen window stays invisible to the person.

Promotion is sticky: a promoted view is not automatically returned to windowless mode. Animation frames and visibility-dependent timers can pause before promotion, so use `render` when a page needs them. This mode is opt-in; it does not promise every site works without rendering. `api browser` opts its fallback into this mode by default, while `api browser --render` promotes immediately.

For bounded structural DOM text/search/link reads, see [explicit semantic reads](semantic.md). They retain WebKit; declared API reads avoid it entirely.

## Route contract

A contract names an origin, routes and optional browser paths:

```json
{
  "origin": "http://127.0.0.1:PORT",
  "browserPaths": ["/app/js-only"],
  "routes": {
    "handshake": {
      "method": "GET", "path": "/api/session",
      "capture": {"preCsrf": "csrf"}
    },
    "login": {
      "method": "POST", "path": "/api/session",
      "defaults": {"csrf": "$token:preCsrf"},
      "capture": {"csrf": "csrf"}, "authentication": true
    },
    "inbox": {
      "method": "GET", "path": "/api/state?view=inbox",
      "capture": {"csrf": "csrf"}
    },
    "mark": {
      "method": "POST", "path": "/api/action",
      "defaults": {"view": "inbox", "kind": "mark", "csrf": "$token:csrf"},
      "versionField": "revision", "idempotencyField": "requestId"
    }
  }
}
```

`defaults` are fixed declaration values and win over caller data. A whole string `$token:name` resolves a previously captured scalar; nested objects/arrays may contain such values. `capture` maps private token names to dotted JSON object paths. Captured fields and configured `redact` field names are removed from returned application data. If a private cookie/captured string unexpectedly appears as another JSON key or value, the entire response is refused with `kind: redaction_conflict`, `needsBrowser: true` and no payload/text. Values shorter than eight characters match only as whole strings; longer private strings also match when embedded. Numeric business IDs and versions are unchanged. This avoids silently returning damaged application data. Tokens are not general scripts, and paths are not request templates. Query fields are scalar and cannot replace fixed query fields in the route.

`versionField` requires that request-body field, or a captured token with the same name. It does not implement application-specific verification. `idempotencyField` uses an explicit string from caller data, otherwise the runtime request identifier. The server must actually implement idempotency; the client never automatically retries mutations or replays them during browser upgrade. A successful mutation reports `outcome: acknowledged`, not generic business verification. Check its receipt and fresh read according to the contract.

A 401 clears captured tokens and marks `needsAuth`. A successful route explicitly declared `authentication: true` clears that state. Conflicts and refusals return typed errors. A cancelled, transport-failed, malformed, oversized or server-failed dispatched mutation can have `kind: unknown_outcome`, `outcome: unknown`, `causeKind` and a request identifier; a mutating redaction conflict has the same uncertain-outcome rule, since its write may already have committed; reconcile state or safely reuse a server-supported idempotency key before another write. There is no automatic fallback on an error.

The session owns a private, memory-only cookie jar and ephemeral URLSession, with cache and ambient credential/cookie storage disabled. Only declared same-origin routes run. Safe GET/HEAD redirects stay within that origin and recompute cookies for the redirected path; mutating redirects and cross-origin redirects are not followed. Cookie values, Set-Cookie headers and captured token values are never returned. A contract is bounded to 256 KiB/128 routes; request objects to 1 MiB and responses to 4 MiB. There are at most 32 sessions, 64 captured tokens/64 KiB per session and 256 cookies/128 KiB per jar.

`api browser` is explicit. Its URL path must exactly match `browserPaths` on the declared origin. It creates or selects an owned, nonvisual tab with a per-session nonpersistent WKWebsiteDataStore; it never imports from or writes into the ordinary profile cookie store. Native browser commands work on that tab and promote rendering when needed. `--render` creates/promotes the fallback immediately. Repeat fallback to the same loaded URL does not reload it. Caller intent such as filters may be encoded in the URL; arbitrary drafts, JS/private storage and pending writes are not converted automatically.

Before an API call, cookie changes from that session's browser store are synchronized into its jar. Responses publish updated jar cookies back before replying. Await browser-specific asynchronous completion before switching backends. Cookies alone do not transfer localStorage, IndexedDB, service workers, OAuth grants or passkeys. Browser tabs and API calls share their agent/task ownership lane.

`api close` closes only the API transport. Retained fallback tabs keep their agent/task protection and private browser store; their commands use independent tab lanes after detaching from the API session. It does not discard those views or publish their authentication to other agents. Reopening the same API session name creates a fresh transport/store. API declarations, cookies and tokens are daemon-memory state, not a persistent account manager. Unknown schemas, HTML, unavailable routes or additional browser capabilities need the normal WebKit path. No arbitrary authentication headers, HTML-form parser or automatic JS-only API discovery is implemented.
