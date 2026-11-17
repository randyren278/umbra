# Explicit reads without a render host

Use declared API reads when a supported endpoint provides the required state. They use no WebKit view. When the task needs the current DOM, a new nonvisual tab retains WebKit and page JavaScript while avoiding a visual host:

    umbra api read work inbox --query '{"q":"urgent","unread":1}' --json
    umbra open https://site.example/notes --new --nonvisual --tab notes
    umbra dom read --tab notes --selector main
    umbra dom find --tab notes --query deadline
    umbra dom links --tab notes --selector nav --json

API sessions/contracts are described in [the API guide](api.md). Read accepts only declared GET/HEAD routes, rejects request data, and rejects HEAD response captures before dispatch. It reuses authorization, origin, cookie isolation, redaction, freshness and cancellation handling. A GET/HEAD restriction does not certify that a server endpoint has no side effects. Authentication and mutations still require an explicit supported call; no mutation is retried or moved to the browser automatically.

## DOM contract

DOM reads are opt-in structural reads of ordinary text nodes under one main-document light-DOM root. CDATA and accessible-name synthesis are outside this subset. They do not ask for CSS, layout, visibility, occlusion, hit testing or coordinates; they emit no input events, scrolls or clicks. They do not call the outline/action library; normal tab setup still installs the usual scripts. Existing text, snapshot, links and eval contracts remain available.

- Read returns whitespace-normalized descendant text.
- Find returns matching individual text nodes, in document order, using a case-insensitive substring. It cannot match a phrase split across separate text nodes.
- Links returns unique absolute HTTP(S) hrefs and descendant-text labels, in document order (not computed accessible names). It skips credentials, unsupported protocols and oversized links. It fetches and follows nothing.

The selector identifies the first matching root. Only a single tag, ID, class or compound of those is supported, such as body, main, #items or article.unread. Attributes, combinators, pseudo-classes, escapes and other selector syntax are refused. Use ordinary rendered browser commands for unsupported requirements.

Scripts, styles, templates, noscript, head content, form-control values and frame subtrees are excluded, including corresponding SVG/MathML names. Shadow trees are not entered. CSS-hidden text can still be included: these results do not mean visible or actionable content. Closed shadows, canvas pixels, virtualized/unloaded records, browser-native find and server-side search are outside this contract. Use a supported API query or the full browser path for those tasks.

Results contain records, text, doc, url, title, count, truncated, visited, scannedCharacters, skippedLinks, metadataTruncated, source=live-dom and backend=webkit-dom. No action refs or geometry are invented. The doc token is stable within a document; navigation changes it. URL/title are refreshed on every read even when text is unchanged. This command has no delta reconstruction or retained snapshot cache.

Default limits are 50 find/link records and 16,000 output text/URL UTF-16 units. Limit is 1–200; max is 1–100,000. Traversal stops at 50,000 counted node visits, one million scanned text units or about 100 ms; individual platform calls and serialization are not a hard real-time guarantee. Rejected and comment nodes count toward traversal bounds. URL metadata is capped at 8,192 units, title at 4,096 and tag names at 128; truncation is reported, so do not treat a truncated URL as navigable. Link hrefs are capped at 4,096. Missing roots and limited scopes must not be interpreted as whole-site absence.

Freshness means the current DOM, not independent server revalidation. A session can expire on the server while old content remains in an unrefreshed page. Use an authoritative declared read, explicit URL navigation or the application's normal refresh flow before relying on current server/auth state. A structural read does not promise a pending animation-frame callback has completed.

## Browser capabilities still promote

Native/trusted input, screenshot/PDF, explicit render/handoff, viewport mutation and recording start require rendering. Geometry get, frame operations/listing and visibility waits also promote opted-in views. The same view, document and private session are retained; promotion is sticky. No host is detached afterward.

    umbra render --tab notes
    umbra get '#submit' --tab notes --attr box
    umbra click '#submit' --tab notes
    umbra screenshot /tmp/notes.png --tab notes

Ordinary open of a discovered URL can navigate an existing nonvisual tab without making a window. It is explicit URL navigation, not a trusted link click and not a substitute for click handlers. Any task needing native actions, pixels, frame-dependent completion or actionable geometry must use those browser capabilities. Unsupported DOM selectors are refused rather than guessed. Advanced eval keeps its existing caller-controlled semantics; the new read route never accepts an arbitrary program or silently classifies it as safe.

C3 could already perform equivalent nonvisual reads with carefully written scripts and supported GET calls. These commands provide bounded, discoverable CLI/MCP contracts. Do not claim an intrinsic RAM advantage over identical C3 scripts without measurement.
