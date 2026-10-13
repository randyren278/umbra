# How Umbra works

Umbra is a small Swift program, in [Sources/](../Sources/), compiled into a macOS app bundle. This page covers what happens during a run, where state lives, and why it's built this way.

## The app

`make build` compiles it with `swiftc` into `build/Umbra.app` and signs it ad hoc. `Info.plist` is linked into the binary itself (`-sectcreate __TEXT __info_plist`), so the bundle identifier `com.randyren.umbra` holds however you launch it: from the app, through the `~/.local/bin/umbra` symlink, or from a copy of the binary. That matters because the identifier decides where WebKit keeps the cookie store.

A headless command sets the app's activation policy to *prohibited*. The process then has no Dock icon and no menu bar, and it can never become the active app. `umbra sign-in` uses the *accessory* policy instead, which allows a window but still shows no Dock icon and no Command-Tab entry.

## The cookie store

Umbra uses a persistent `WKWebsiteDataStore(forIdentifier:)`. That API arrived in macOS 14, which sets the minimum version. The store holds cookies, local storage and caches, and WebKit keeps it at:

```
~/Library/WebKit/com.randyren.umbra/WebsiteDataStore/<identifier>/
```

The default store's identifier is the fixed UUID `6F1C7A52-3D0B-4E8F-9A61-F1A57C0DE001`. It was carried over from FIRST's earlier helper so that tool's signed-in store moved to Umbra intact. Safari's data lives elsewhere, so neither browser can see the other's cookies.

Changing either the bundle identifier or the store identifier gives you a new, empty store, which signs every site out. If you fork Umbra under your own identifier, expect to sign in again.

## Profiles

`UMBRA_PROFILE=<name>` selects a different store. Its identifier is derived from the name: the first 32 hex digits of SHA-256 of `umbra profile <name>`, grouped 8-4-4-4-12 as a UUID. The same name always maps to the same store, so a profile persists between runs.

To find a profile's folder, for instance to delete it:

```sh
python3 -c 'import hashlib, sys; h = hashlib.sha256(("umbra profile " + sys.argv[1]).encode()).hexdigest()[:32]; print("-".join([h[:8], h[8:12], h[12:16], h[16:20], h[20:]]))' work
# then remove ~/Library/WebKit/com.randyren.umbra/WebsiteDataStore/<that id>
```

## The lock

Before touching the store, every command takes an exclusive `flock` on a lock file:

```
~/Library/Application Support/Umbra/store.lock          # the default store
~/Library/Application Support/Umbra/store-<name>.lock   # UMBRA_PROFILE=<name>
```

Two WebKit processes writing one store at the same time is untested ground, so runs on the same profile take turns. A second run blocks until the first one exits, because the kernel releases the lock when the process ends, even if it crashes. Different profiles have different locks and run in parallel.

Waiting for the lock doesn't count toward `--timeout`. The timer starts once the run holds the lock. `umbra sign-in` holds the lock while its window is open, so background runs on that profile wait until you close it.

## The life of a `read`

1. Parse the arguments. If stdin isn't a terminal, read it as the script.
2. Become a prohibited (headless) app, take the lock, and start the timeout timer.
3. Load the URL in a 1280×900 `WKWebView` that is never put in a window.
4. Wait for the first finished navigation. Later ones, from redirects or single-page apps, are ignored. A load that fails before that point exits 4. A navigation cancelled by a script redirect is not treated as a failure.
5. Sleep for `--wait` seconds, if given. Then, with `--until JS`, evaluate `Boolean(JS)` in whatever document is current every quarter second until it's true. An error during a navigation counts as not yet. The timeout still applies.
6. With `--cookie NAME`, look for a cookie with that name that would be sent to the page's current host, and exit 3 if there is none.
7. Run the script with `callAsyncJavaScript` in the page's own JavaScript world, as the body of an async function. An empty script becomes `return document.documentElement.outerHTML`.
8. Print the result. A string prints as is, an object or array prints as JSON with sorted keys, a number or boolean prints as text, and `null` or `undefined` prints nothing. A thrown error exits 2 with the error's message.
9. Save the session cookies of `keepSession` sites (see [sites.md](sites.md#keepsession)) and exit. If saving takes longer than 5 seconds, the process exits anyway.

## `fetch`

`fetch` loads the `--from` page first. By default that's `/robots.txt` on the URL's own origin, which is small and same-origin. It still counts as loaded when the site has no robots.txt. Then, inside that page, Umbra runs `fetch(url, {credentials: 'include'})` and prints the response body as text, exactly as received. A non-2xx status prints the body anyway and exits 5. Because the body is read as text, use `download` for binary files.

The request comes from the page, so the browser's normal rules apply. The right cookies are attached, and a cross-origin URL needs the target to allow it (CORS). That's why `fetch` is meant to be same-origin with its `--from` page.

## `download`

`download` loads the URL as a top-level navigation and turns the response into a download, the same thing that happens when you click a download link in Safari. Going through a top-level navigation makes the site the request's first party, so its cookies are sent. Redirects are followed, including to storage hosts on other domains.

A non-2xx response cancels the download and exits 5 without writing anything. Otherwise the bytes go to `.<name>.umbra-part` beside the target and are renamed over it with `rename(2)` once complete. Readers see either the old file or the whole new one, never half of either. A run that times out removes its partial file.

## The user agent

Umbra sets WebKit's application name to `Version/<Safari version> Safari/605.1.15`, reading the version from `/Applications/Safari.app`. WebKit fills in the rest, so sites see the same user agent the installed Safari sends. On a Mac with Safari 27.0.1:

```
Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/27.0.1 Safari/605.1.15
```

## The sign-in window

`umbra sign-in` opens one window, *Sign in to Umbra*. It has a segmented control with one tab per site and a WebKit view for each tab, and every view shares the profile's store. Umbra watches the store for cookie changes and re-checks each site when a cookie changes or a page finishes loading. A tab gets a green check once its site passes, and after a fresh sign-in the window moves on to the next site that still needs one.

The app has no visible menu bar, but it installs a hidden Edit menu so that Command-V can paste a password and Command-W can close the window. Closing the window saves the session cookies of `keepSession` sites and quits.

Passkeys and Touch ID prompts don't work in this window. Sign in with a password and a push approval or a one-time code.

## Timeouts and exit codes

| Command | Default `--timeout` |
|---|---|
| `read` | 90 s, plus `--wait`; `--until` waits within it |
| `fetch` | 90 s |
| `download` | 300 s |

When the timer fires, the run exits 2 with `no answer in N seconds`. The full list of exit codes is in the [README](../README.md#exit-codes).
