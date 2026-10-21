# sites.json

`sites.json` tells Umbra which sites you sign in to, how to tell that a session is alive, and which session cookies to keep between runs. It's optional. `read`, `fetch` and `download` work without it, and `umbra sign-in <url>` can open any site as a one-off tab.

**Location:** `~/.config/umbra/sites.json`, or whatever path `UMBRA_SITES` names.

Every command reads the file at startup. A file that doesn't parse stops every command with exit 78, and the message names the missing key or bad value. A missing file just means no sites.

## Format

A JSON array with one object per site. [examples/sites.example.json](../examples/sites.example.json) is a working starting point:

```json
[
  {
    "name": "Canvas",
    "domain": "canvas.example.edu",
    "login": "https://canvas.example.edu/login",
    "probe": "/api/v1/users/self",
    "keepSession": true,
    "keepSignedIn": true
  },
  {
    "name": "LinkedIn",
    "domain": "linkedin.com",
    "login": "https://www.linkedin.com/login",
    "cookie": "li_at",
    "probe": "/feed/"
  },
  {
    "name": "Instagram",
    "domain": "instagram.com",
    "login": "https://www.instagram.com/accounts/login/",
    "cookie": "sessionid",
    "probe": "/accounts/edit/"
  }
]
```

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | The name the window shows, and the one you pass to `umbra sign-in` (matched without regard to case). |
| `domain` | yes | The site's domain, such as `linkedin.com`. It covers subdomains, so `www.linkedin.com` counts. |
| `login` | yes | The login page the window opens. |
| `cookie` | one or both | The name of a cookie that exists only while you're signed in. HttpOnly cookies count. |
| `probe` | one or both | A path on the site that answers 2xx, without a redirect, only while you're signed in. |
| `probeScript` | no | For a JavaScript app: render `probe` (or `login`), then run this async JavaScript body in the page. Return `true` only after verifying the authenticated session, or `false` when signed out. Overrides cookie and HTTP-status checks. |
| `keepSession` | no | `true` keeps this domain's session-only cookies between runs. Defaults to `false`. |
| `keepSignedIn` | no | `true` visits the site every 10 minutes so its session never sits idle, and implies `keepSession`. Defaults to `false`. |

If a site has both `cookie` and `probe`, it counts as signed in when the cookie is set and the probe passes: the cookie answers "signed out" without a request, and the probe catches a session the site ended while its cookie stayed. With neither, Umbra asks the site itself, by its login page (below).

## Signed-in checks: `cookie` or `probe`

The sign-in window checks each site when it lists it, and checks the site you're signing in to before each page it would show; once the site passes, that page is never loaded. A site passes when:

- **`cookie`:** a cookie with that name is set on `domain` or one of its subdomains.
- **Neither:** the site moves its login page on into itself. Umbra loads the login page in a view that is never shown and watches it: a site you're signed in to takes you off its login page, onto one of its own pages that isn't a sign-in step (no `login`, `sso`, `auth`, `verify`, `2fa` and the like in the path) and asks for no password, and the page stays there for a second. A site you're not signed in to keeps you at a password, on a sign-in path or on another host, for two and a half seconds. This works for single-page apps that answer every path with the same page, which a `probe` can't tell apart, such as Wealthsimple's. Umbra never counts a site as signed in unless it moved you off the login page, so a site whose login form lives at `/` still opens for you to sign in. While you sign in to such a site in the window, the same watch closes its page once the site lets you in; Done works too.
- **`probe`:** a request for the probe path on the site answers 2xx without a redirect. Umbra makes it in a view that is never shown, on the login page's origin when that is on `domain`, otherwise `https://<domain>`. API endpoints that need a session work well here. Canvas's `/api/v1/users/self` answers 401 until you sign in.
- **`probeScript`:** Umbra fully loads `probe` (or `login`) so the app can refresh its tokens, then awaits the script once. The script can wait for app initialization and make a read-only authenticated request, returning a boolean. Tokens stay in the page; script exceptions, a non-boolean result or no answer within 30 seconds mean "couldn't check". A public app shell returning 200 or a stale cookie alone proves nothing. For example, `"probeScript": "return (await fetch('/api/me')).ok;"` checks a cookie-authenticated app. Bearer-token apps need their own authenticated request. Keep script code and site URLs trusted, and never return credentials.

Use `cookie` when the site has one clearly named session cookie. Use `probe` when cookie names are generic or change, or when you'd rather ask the site directly. Use both when the site can end a session without clearing its cookie: a page that answers 200 when you're signed in and redirects to the login page when you're not (Instagram's `/accounts/edit/`, LinkedIn's `/feed/`) makes a good probe. When the window opens a login page for such a site, the old cookie stays (the check may only have failed for a moment), and a sign-in counts once the cookie gets a new value. If the probe answers anything other than 2xx, a redirect, 401 or 403 (a 503, a 429, a bot check), Umbra can't tell: `umbra sign-in <site>` exits 4 and opens nothing.

Scripts get the same kind of check through `umbra read --cookie NAME`. That exits 3 when no cookie with that name is sent to the page's final host.

### Finding the cookie name

1. Open the site in a one-off page, sign in, and click Done:
   ```sh
   umbra sign-in https://www.example.com/login
   ```
2. List the cookie names and expiry dates for the domain (values are never shown):
   ```sh
   umbra cookies example.com
   ```
3. Sign out (`umbra sign-out example.com`), sign in again, list again, and pick a cookie that appeared with the sign-in. Names like `session`, `sessionid`, `auth` or `user_session` are good bets.

`umbra cookies` prints one cookie per line as name, domain and expiry. The expiry is `session` for cookies that die with the browser.

## keepSession

WebKit holds session-only cookies (those without an expiry date) in memory and drops them when the process ends. That process is a one-shot run or the profile's daemon (which also shows the sign-in window), which exits after 15 idle minutes. For a site whose sign-in rests on session cookies, which is common with university single sign-on and Canvas, you'd be signed in during `umbra sign-in` and signed out once that process ends.

With `"keepSession": true`, Umbra rewrites that site's session-only cookies as ordinary cookies that expire 30 days later. It does this when a one-shot run ends, when the daemon stops, and at each sign-in in the window. Because each of those renews them, the session lasts as long as you use it at least once a month, or until the site ends it on its side.

**Signs you need it:** the sign-in window says you're signed in, but the next `read --cookie` exits 3 or the next API call gets 401. `umbra cookies <domain>` shows `session` in the expiry column for the cookies that matter.

**The trade-off:** the site meant those cookies to vanish when the browser quit. With `keepSession` they sit on disk in Umbra's store for up to 30 days, much like ticking "keep me signed in". Anyone who can read your home folder could reuse them. Turn it on only for the sites that need it, and only on a Mac you trust.

**Turning it off** stops the renewal. Cookies that were already rewritten keep their 30-day expiry. To drop them now, `umbra sign-out <site>`.

**Matching:** a cookie is kept when its domain is the site's `domain` or a subdomain of it. If your sign-in passes through a separate identity provider (say `login.example.edu` for `canvas.example.edu`) and you also want that provider's session kept, add it as its own entry with `keepSession: true`.

## keepSignedIn

`keepSession` keeps the cookies, but the site also keeps the session on its own side, and many sites end a session that goes unused for a while. Canvas does. Between your uses nothing visits the site, so its server times the session out and you're signed out, though the cookie is still in the store.

With `"keepSignedIn": true`, Umbra visits the site every 10 minutes, so the session is never idle long enough to end. It implies `keepSession`: the site's session-only cookies are kept for 30 days whether or not `keepSession` is set.

**Signs you need it:** `keepSession` is on and `umbra cookies <domain>` shows the cookies with 30-day expiry dates, yet the site still signs you out after some time without use.

**What a visit does:** Umbra goes to the site in a view that is never shown, on the same origin as the signed-in checks:

- **`probe`:** a request for the probe path. 2xx without a redirect means signed in; a redirect, 401 or 403 means signed out; anything else, Umbra can't tell. If the site also has a `cookie` and it's missing, that counts as signed out, with no request.
- **`probeScript`:** render the app and await its signed-in script, as above. This runs the app's JavaScript session refresh instead of canceling the page as soon as its response headers arrive.
- **`cookie` only:** the site's root page (`/`), then the cookie decides.
- **Neither:** the login page, watched as in [the signed-in checks](#signed-in-checks-cookie-or-probe): loading it is the visit, and where it lands says whether you're signed in.

Cookies the site sets in its answer are kept, so a session cookie the site renews on each request stays current. After the visits, Umbra rewrites the session-only cookies of `keepSession` and `keepSignedIn` sites to expire 30 days later and writes everything to disk.

**The launch agent:** the visits come from a launchd job in your login session (a LaunchAgent). It runs `umbra keep-signed-in` once when it's installed, at each login, and every 10 minutes by the clock (on the hour, ten past, and so on).

- **Label:** `com.randyren.umbra.keep-signed-in`, or `com.randyren.umbra.keep-signed-in.<profile>` for a profile.
- **Job file:** `~/Library/LaunchAgents/<label>.plist`.
- **Log:** `~/Library/Application Support/Umbra/keep-signed-in.log`, or `keep-signed-in-<profile>.log` for a profile.

The job file names the Umbra binary (with symlinks resolved), the sites file and the profile. Each profile has one agent, and it reads the sites file it was installed with. If you move Umbra, run `umbra keep-signed-in <site>` again to point the agent at the new copy. To see that the agent is loaded and how its last run exited (add `.<profile>` for a profile):

```sh
launchctl print gui/$(id -u)/com.randyren.umbra.keep-signed-in
```

Each run goes through the profile's daemon when one is running, and otherwise works on its own, with no window and no Dock icon. It never starts the daemon.

**Turning it on:** choose **Keep Signed In** from the site's **…** menu in the sign-in window, or run `umbra keep-signed-in <site>`. Either one sets the field in the sites file and installs the launch agent; the command also visits the site at once and prints the result. The agent visits every site marked in the file, so a field you add by hand counts too, but only the menu item and `keep-signed-in <site>` install the agent.

**Turning it off:** choose **Keep Signed In** again, or run `umbra keep-signed-in <site> --off`. That removes the field from the site's entry and, once no site in the file has it, removes the launch agent. Cookies that were already rewritten keep their 30-day expiry. To drop them now, `umbra sign-out <site>`.

**Reading the log:** each run adds one line per visited site, in the order of the sites file, with the time in UTC:

```
2026-10-18T14:20:03Z Canvas: signed in
2026-10-18T14:30:03Z Canvas: couldn't check (the site didn't answer as expected)
2026-10-18T14:40:04Z Canvas: signed out; `umbra sign-in Canvas` signs in again
```

`signed in` means the visit found the session alive, which keeps it going. `signed out` means the session had already ended. A visit can't sign you in, so run `umbra sign-in <site>`, and the next visit finds you signed in. `couldn't check` means the site answered something else, such as a 503 or a bot check; the next visit tries again. To watch it:

```sh
tail -f ~/Library/Application\ Support/Umbra/keep-signed-in.log
```

**The command:** `umbra keep-signed-in` with no sites visits every marked site once and prints those lines. That's what the agent runs. With no site marked it says so and does nothing. It exits 0 when every site is signed in, 3 when any is signed out, and otherwise 4 when any couldn't be checked. On exit 0 the lines go to stdout, otherwise to stderr; the agent sends both to its log. `--json` gives the time and each site's state. Sites are named as in the sites file, never by URL.

**The trade-off:** every 10 minutes Umbra sends the site a request with your cookies, for as long as the agent runs, and the site sees a session that is never idle. The cookies sit on disk as they do with `keepSession`. Turn it on only for the sites that sign you out too soon, and only on a Mac you trust.

**Limits:**

- It can't outlast a session the site caps. Some sites end every session after a fixed time, however busy it is; then you sign in again.
- It visits only while you're logged in and the Mac is awake. A visit that fell due while the Mac slept runs as soon as it wakes (`StartCalendarInterval` in `man launchd.plist`), but if the Mac slept longer than the site lets a session sit idle, the session has already ended.
- Signing out of the site yourself, in any browser, can end every session the site has for you, Umbra's included. Canvas does this.

## Using sites from the command line

```sh
umbra sign-in                      # the window, listing every site; returns at once
umbra sign-in canvas linkedin      # chosen sites, by name: returns once both are signed in
umbra sign-in https://example.com/login   # a one-off page, named after the host, with no check
umbra                              # the same as `umbra sign-in`, and so is opening Umbra.app
umbra sign-out canvas              # delete the site's cookies from the store
umbra keep-signed-in canvas        # visit it every 10 minutes from now on (see keepSignedIn)
umbra keep-signed-in               # visit every kept site now and report
umbra keep-signed-in canvas --off  # stop visiting it
umbra sign-in https://github.com/login --save GitHub   # add an account (see below)
```

Named sites that are already signed in return at once (exit 0) and open nothing. Otherwise the window opens each login page in turn and the command returns once all are signed in; it exits 3 if the window is closed first and 2 after `--timeout` (default 900 seconds). The profile's daemon shows the window, so other commands keep working meanwhile. An unknown name exits 64 and lists the names it knows. With no sites file and no arguments, `umbra sign-in` exits 64 and explains how to name a URL instead.

## Adding an account

The window's **Add Account** button takes a link to a site's login page and an optional name, and `umbra sign-in <login URL> --save NAME` does the same from a shell. Umbra adds the site to the sites file (domain from the link's host without `www.`, the name from the host unless you give one), opens its login page, and you sign in and click Done. Agents can use the account from then on, like any other.

You don't need to know the site's cookie. During that first sign-in Umbra notes the domain's cookie names once the login page has loaded. When you click Done, and a page has loaded since, it saves as the site's `cookie` the one the sign-in set: a new cookie whose name looks like a session (`sess`, `auth`, `token`, `sid`, `login`, `user`, `jwt`), ignoring ones like `csrftoken` or analytics, or else the only new HttpOnly cookie. Closing the window instead of clicking Done learns nothing, because a login page can set session-like cookies of its own (even across a redirect) while nobody signs in. After that the site is checked like the others: its status shows in the window, and its login page closes the moment you're signed in. If you close the window, or no cookie can be told apart, `umbra sign-in <url> --save` exits 3 and the site stays listed without a check; sign in again, or add a `cookie` or `probe` by hand.

Umbra rewrites the sites file to add the entry, one site per line, keeping every other entry and field. A name or domain that's already listed is refused.

## Separate files and profiles

`UMBRA_SITES` points one run at another file, and `UMBRA_PROFILE` gives it another cookie store. Together they keep a second account fully separate:

```sh
UMBRA_PROFILE=alt UMBRA_SITES=~/.config/umbra/alt-sites.json umbra sign-in
```
