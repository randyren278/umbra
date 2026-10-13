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
    "cookie": "li_at"
  },
  {
    "name": "Instagram",
    "domain": "instagram.com",
    "login": "https://www.instagram.com/accounts/login/",
    "cookie": "sessionid"
  }
]
```

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | The tab label, and the name you pass to `umbra sign-in` (matched without regard to case). |
| `domain` | yes | The site's domain, such as `linkedin.com`. It covers subdomains, so `www.linkedin.com` counts. |
| `login` | yes | The URL the sign-in tab opens. |
| `cookie` | one of these | The name of a cookie that exists only while you're signed in. HttpOnly cookies count. |
| `probe` | one of these | A same-origin path that answers with a 2xx status only while you're signed in. |
| `keepSession` | no | `true` keeps this domain's session-only cookies between runs. Defaults to `false`. |

If a site has both `cookie` and `probe`, Umbra checks `cookie` and ignores `probe`. With neither, the tab still works for signing in, but it never shows a check.

## Signed-in checks: `cookie` or `probe`

The sign-in window re-checks every site whenever a cookie changes or a page finishes loading. A site passes when:

- **`cookie`:** a cookie with that name is set on `domain` or one of its subdomains.
- **`probe`:** the tab is showing a page on `domain`, and a `fetch()` of the probe path from that page returns 2xx. API endpoints that need a session work well here. Canvas's `/api/v1/users/self` answers 401 until you sign in.

Use `cookie` when the site has one clearly named session cookie. Use `probe` when cookie names are generic or change, or when you'd rather ask the site directly.

Scripts get the same kind of check through `umbra read --cookie NAME`. That exits 3 when no cookie with that name is sent to the page's final host.

### Finding the cookie name

1. Open the site in a one-off tab, sign in, and close the window:
   ```sh
   umbra sign-in https://www.example.com/login
   ```
2. List the cookie names and expiry dates for the domain (values are never shown):
   ```sh
   umbra cookies example.com
   ```
3. Sign out on the site (`umbra sign-in https://www.example.com/`), list again, and pick a cookie that disappeared. Names like `session`, `sessionid`, `auth` or `user_session` are good bets.

`umbra cookies` prints one cookie per line as name, domain and expiry. The expiry is `session` for cookies that die with the browser.

## keepSession

WebKit holds session-only cookies (those without an expiry date) in memory and drops them when the process ends. Each Umbra run is its own process. For a site whose sign-in rests on session cookies, which is common with university single sign-on and Canvas, that means you're signed in during `umbra sign-in` and signed out on the very next run.

With `"keepSession": true`, Umbra rewrites that site's session-only cookies at the end of every run (and while the sign-in window is open) as ordinary cookies that expire 30 days later. Because every run renews them, the session lasts as long as you use it at least once a month, or until the site ends it on its side.

**Signs you need it:** sign-in shows a green check, but the next `read --cookie` exits 3 or the next API call gets 401. `umbra cookies <domain>` shows `session` in the expiry column for the cookies that matter.

**The trade-off:** the site meant those cookies to vanish when the browser quit. With `keepSession` they sit on disk in Umbra's store for up to 30 days, much like ticking "keep me signed in". Anyone who can read your home folder could reuse them. Turn it on only for the sites that need it, and only on a Mac you trust.

**Turning it off** stops the renewal. Cookies that were already rewritten keep their 30-day expiry. To drop them now, sign out on the site with `umbra sign-in <url>`.

**Matching:** a cookie is kept when its domain is the site's `domain` or a subdomain of it. If your sign-in passes through a separate identity provider (say `login.example.edu` for `canvas.example.edu`) and you also want that provider's session kept, add it as its own entry with `keepSession: true`.

## Using sites from the command line

```sh
umbra sign-in                      # every site, one tab each
umbra sign-in canvas linkedin      # chosen sites, by name
umbra sign-in https://example.com/login   # a one-off tab, named after the host, with no check
umbra                              # the same as `umbra sign-in`
```

An unknown name exits 64 and lists the names it knows. With no sites file and no arguments, `umbra sign-in` exits 64 and explains how to name a URL instead.

## Separate files and profiles

`UMBRA_SITES` points one run at another file, and `UMBRA_PROFILE` gives it another cookie store. Together they keep a second account fully separate:

```sh
UMBRA_PROFILE=alt UMBRA_SITES=~/.config/umbra/alt-sites.json umbra sign-in
```
