# Sign-in and handoff

<p align="center">
  <picture>
    <source media="(max-width: 600px)" srcset="../assets/sign-in-mobile.svg">
    <img src="../assets/sign-in.svg" width="100%" alt="Umbra's sign-in window lists each account (for example Canvas and LinkedIn signed in, GitHub signed out) with Sign In, Sign Out and Add Account. It checks each site without loading it, shows a site's page only to sign in, and closes that page the moment the sign-in lands; agents then use the session in the background.">
  </picture>
</p>

Umbra never types your passwords. You sign in once, in a window:

```sh
umbra sign-in                                    # every site in sites.json (or open Umbra.app)
umbra sign-in Canvas                             # one site; returns once you're signed in
umbra sign-in https://canvas.example.edu/login   # any site, as a one-off page
umbra sign-in https://github.com/login --save GitHub   # add it to your sites
```

The window lists your sites and whether each is signed in, without loading any of them. For a site Umbra can check (a cookie or probe in `sites.json`), it shows only the login page: a site you're signed in to is never loaded, and the page closes the moment the sign-in lands. A site with neither is asked by its login page, loaded where you don't see it: if the site moves it on into itself, you're signed in and nothing opens; if not, the page opens and closes once the site lets you in (or click Done). An added account's first sign-in ends with Done, which is when Umbra learns its cookie. Umbra is a way to sign in, not a browser for you: a site you're signed in to can't become another place to scroll Instagram. While the window is open, Umbra has a Dock icon; Quit closes the window, and agents keep working throughout. Each row shows the site's own logo, and its status is checked again every time the window is shown. A row's **…** menu has **Keep Signed In**, **Check Again** and **Sign Out…**; the command line has `umbra sign-out Canvas` too, for a sign-in the site has dropped. **Add Account** takes a link to any site's login page: you sign in once and click Done, Umbra learns which cookie marks you as signed in, and from then on the account is checked like the others and agents can use it.

List your regular sites in [`sites.json`](sites.md) with a cookie or probe that proves you're signed in. Mark `keepSession` on the ones whose sign-in rests on session cookies, such as university single sign-on, so the sign-in survives between runs. A site that still signs you out when you leave it alone for a while, as Canvas does, needs more: choose **Keep Signed In** from its **…** menu in the window, and Umbra visits it every 10 minutes so the session never goes idle (see [keepSignedIn](sites.md#keepsignedin)).

<p align="center">
  <picture>
    <source media="(max-width: 600px)" srcset="../assets/human-handoff-mobile.svg">
    <img src="../assets/human-handoff.svg" width="100%" alt="You sign in through a visible window, an agent uses that profile off screen, and handoff briefly reveals the agent's tab when a person must complete a step before it hides again.">
  </picture>
</p>

Mid-task, an agent may hit something only you can do: a two-factor prompt, a CAPTCHA or a consent screen. `handoff` shows you that tab in a window with a message, and returns when you close it or when a condition holds:

```sh
umbra handoff "Approve the Duo push" --until "location.pathname.startsWith('/app')"
```

The window hides again afterward, and the tab carries on where you left it. If the condition already holds, no window appears at all.


## Profiles

A profile is a separate browser identity: its own cookie store, daemon, socket and download folder. The default profile is your main one. Choose another with `--profile NAME` or `UMBRA_PROFILE=NAME`:

<p align="center">
  <picture>
    <source media="(max-width: 600px)" srcset="../assets/profile-lanes-mobile.svg">
    <img src="../assets/profile-lanes.svg" width="100%" alt="Example research and work profiles form separate lanes: each agent has its own daemon and tabs, cookie store, and downloads, so parallel work does not disturb the other profile.">
  </picture>
</p>

```sh
umbra --profile research open https://news.example.com/
UMBRA_PROFILE=research umbra snapshot --interactive
```

Give agents and experiments their own profiles, signed in only to the sites they need, so they can't disturb the store your scheduled jobs use. The same goes for agents working in parallel. The current tab and the cookies belong to the profile, so two agents on one profile change things under each other. Give each its own profile, or at least its own `--tab`. Cookies on `127.0.0.1` are shared across ports, so local test servers don't keep agents apart either. Each profile's files live in `~/Library/WebKit/com.randyren.umbra/WebsiteDataStore/`, `~/Library/Application Support/Umbra/` and `~/Downloads/Umbra/<profile>/`; [docs/how-it-works.md](how-it-works.md#profiles) shows how to find and delete one.
