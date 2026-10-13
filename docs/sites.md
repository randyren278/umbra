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
    "keepSession": true
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
| `keepSession` | no | `true` keeps this domain's session-only cookies between runs. Defaults to `false`. |

If a site has both `cookie` and `probe`, it counts as signed in when the cookie is set and the probe passes: the cookie answers "signed out" without a request, and the probe catches a session the site ended while its cookie stayed. With neither, its login page still works for signing in, but Umbra can't tell when you're done: you click Done.

## Signed-in checks: `cookie` or `probe`

The sign-in window checks each site when it lists it, and checks the site you're signing in to before each page it would show; once the site passes, that page is never loaded. A site passes when:

- **`cookie`:** a cookie with that name is set on `domain` or one of its subdomains.
- **`probe`:** a request for the probe path on the site answers 2xx without a redirect. Umbra makes it in a view that is never shown, on the login page's origin when that is on `domain`, otherwise `https://<domain>`. API endpoints that need a session work well here. Canvas's `/api/v1/users/self` answers 401 until you sign in.

Use `cookie` when the site has one clearly named session cookie. Use `probe` when cookie names are generic or change, or when you'd rather ask the site directly. Use both when the site can end a session without clearing its cookie: a page that answers 200 when you're signed in and redirects to the login page when you're not (Instagram's `/accounts/edit/`, LinkedIn's `/feed/`) makes a good probe. When the window opens a login page for such a site, it first removes the old session cookie, so only a fresh sign-in counts.

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

## Using sites from the command line

```sh
umbra sign-in                      # the window, listing every site; returns at once
umbra sign-in canvas linkedin      # chosen sites, by name: returns once both are signed in
umbra sign-in https://example.com/login   # a one-off page, named after the host, with no check
umbra                              # the same as `umbra sign-in`, and so is opening Umbra.app
umbra sign-out canvas              # delete the site's cookies from the store
umbra sign-in https://github.com/login --save GitHub   # add an account (see below)
```

Named sites that are already signed in return at once (exit 0) and open nothing. Otherwise the window opens each login page in turn and the command returns once all are signed in; it exits 3 if the window is closed first and 2 after `--timeout` (default 900 seconds). The profile's daemon shows the window, so other commands keep working meanwhile. An unknown name exits 64 and lists the names it knows. With no sites file and no arguments, `umbra sign-in` exits 64 and explains how to name a URL instead.

## Adding an account

The window's **Add Account** button takes a link to a site's login page and an optional name, and `umbra sign-in <login URL> --save NAME` does the same from a shell. Umbra adds the site to the sites file (domain from the link's host without `www.`, the name from the host unless you give one), opens its login page, and you sign in and click Done. Agents can use the account from then on, like any other.

You don't need to know the site's cookie. During that first sign-in Umbra notes the domain's cookies once the login page has loaded, and when you click Done (or close the window) it saves as the site's `cookie` the one the sign-in set: a new cookie whose name looks like a session (`sess`, `auth`, `token`, `sid`, `login`, `user`, `jwt`), ignoring ones like `csrftoken` or analytics, or else the only new HttpOnly cookie. After that the site is checked like the others: its status shows in the window, and its login page closes the moment you're signed in. If no cookie can be told apart, the site stays a link you sign in to with Done; add a `cookie` or `probe` by hand.

Umbra rewrites the sites file to add the entry, one site per line, keeping every other entry and field. A name or domain that's already listed is refused.

## Separate files and profiles

`UMBRA_SITES` points one run at another file, and `UMBRA_PROFILE` gives it another cookie store. Together they keep a second account fully separate:

```sh
UMBRA_PROFILE=alt UMBRA_SITES=~/.config/umbra/alt-sites.json umbra sign-in
```
