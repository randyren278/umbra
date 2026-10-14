# Security model

- **Cookies stay in the store.** Like any browser, Umbra sends a cookie only to the site it belongs to. No command or tool prints, returns or exports a cookie value; `cookies` lists names and expiry dates only.
- **The agent acts as you.** On any site a profile is signed into, an agent can read, click and submit as you would. The [skill](../skill/SKILL.md) tells agents to do what the request asks and to ask first before any consequential action that's their own idea. Give agents a profile signed in only where you'd trust them.
- **People stay in charge of identity.** Signing in, two-factor prompts and CAPTCHAs happen in `sign-in` and `handoff` windows, by you.
- **Only your user reaches the daemon.** Its socket is created with owner-only permissions in `~/Library/Application Support/Umbra/`.
- **Pages can't see Umbra's machinery.** Snapshot and action code runs in an isolated world.
- **No telemetry.** Umbra's own requests are the URLs you or your agent give it; what those pages load is up to them, as in any browser.
- **The `keepSession` trade-off.** It re-saves a site's session-only cookies with a 30-day expiry so sign-ins survive between runs. They outlive the browser session the site had in mind, so turn it on only where it's needed and only on a Mac you trust.
- **Your account protects the store.** As with Safari, anyone who can read your home folder can reuse your sessions.


## Limitations

- macOS 14 or later only. Prebuilt zips are Apple Silicon; build from source on Intel.
- Closed shadow roots and cross-origin iframes can't be reached. `snapshot` marks cross-origin iframes so an agent knows something is there; open shadow roots and same-origin iframes work.
- `fill` and `type` insert text the way an input method does, so a page that listens for a `keydown` per character won't see one. Use `press` for those keys.
- HTML5 drag and drop is dispatched as page events, which are untrusted, because a native drag session would act on the real desktop. Mouse-driven drags (sliders, sortable lists, canvases) are native.
- No passkeys or Touch ID in the sign-in window. Use a password with a push approval or a code.
- One daemon per profile. Runs of the one-shot commands without a daemon take turns on the profile's lock.
- It's not a scraper or an anti-bot tool. It's for your own accounts at a human pace. It doesn't hide automation, rotate identities or solve CAPTCHAs, and site terms still apply.
