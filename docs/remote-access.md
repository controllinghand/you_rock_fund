# Remote access — your dashboard on your phone

Optional. Get the YRVI dashboard on your phone from anywhere, without opening a
single inbound port and without adding a login to the app.

The approach: put your box and your phone on a **private overlay network**
([Tailscale](https://tailscale.com) is the easy button), then publish *only* the
dashboard port onto that network. Your devices can reach it. Nothing else on the
internet can — there is no port forward, no public IP, and no DNS record pointing
at your house.

---

## Read this first

**This is yours to run and yours to own.** Every YRVI deployment is standalone by
design — its own IBKR account, its own secrets, its own state, its own money.
Nobody else can see your box, and that includes whoever gave you this software.
The flip side of that isolation is that **nobody else can debug your box either.**
If your overlay network breaks, you fix it. This document is the help you get.

**You will use your own account.** Your own Tailscale login, your own private
network with only your own devices on it. Do not join someone else's network and
do not invite anyone onto yours. A shared network across operators' boxes is
explicitly not a thing YRVI does.

**Why there's no password on the dashboard:** because the network is the boundary.
The dashboard binds to `127.0.0.1` and is not reachable off the box. An overlay
network preserves that — it adds your phone as a private peer rather than opening
a door to the internet. See [dashboard-auth.md](dashboard-auth.md) for the full
reasoning. This matters: **the setup below is safe precisely because it does not
expose anything publicly. If you deviate from it — see the warnings — you are
putting an unauthenticated dashboard on the open internet.**

---

## What you get

- The dashboard at `https://your-box.your-tailnet.ts.net` from anywhere — cell
  data, coffee shop, wherever — with a real, valid TLS certificate.
- Zero inbound ports. Nothing forwarded on your router. Nothing to attack.
- No YRVI code changes. Nothing touches Docker, IBKR, or the trading stack.
- About 15 minutes.

## What you don't get

- Access to anyone else's box, or them to yours.
- A route to the rest of your machine. You publish one port, not the box.
- Support. See above.

---

## Setup (macOS)

Verified on macOS 15 (Intel) and macOS 26 (Apple Silicon). Windows should work
the same way via the Tailscale installer, but is untested — see [Windows](#windows).

### 1. Install the daemon on the box

```bash
# Apple Silicon
/opt/homebrew/bin/brew install tailscale

# Intel
/usr/local/bin/brew install tailscale
```

> **Use the Homebrew CLI, not the Tailscale GUI app, on the box.** The GUI app
> runs inside your logged-in user session — it stops when you log out and does not
> reliably come back after a reboot. Your YRVI box runs headless and unattended,
> which is exactly the case the GUI app handles worst.
>
> On your *laptop*, the GUI app (`brew install --cask tailscale`) is the better
> choice — you want the menu-bar toggle there.

### 2. Start it as a root daemon

```bash
sudo brew services start tailscale
```

> **The `sudo` is not optional.** With `sudo`, Homebrew installs a *LaunchDaemon*
> in `/Library/LaunchDaemons` that runs as root from boot, with nobody logged in.
> Without it, you get a *LaunchAgent* that dies when you log out — and your
> "always available" dashboard silently isn't, exactly when you need it.
>
> Homebrew will print `` `tailscale` must be run as non-root to start at user
> login! `` — **ignore that.** It is advising the setup you do not want.

### 3. Join your network

```bash
sudo tailscale up
```

This prints a URL. Open it in any browser and sign in (Google or GitHub SSO). The
free tier covers this comfortably. The command waits until you approve.

### 4. Disable key expiry ⚠️

**Do not skip this step.**

Tailscale device keys expire after about 180 days by default. On a headless box
that means it silently drops off your network six months from now, and you find
out at the exact moment you need it — probably during a problem, probably from
somewhere that isn't home.

In the [admin console](https://login.tailscale.com/admin/machines): **Machines →
your box → the `···` menu → Disable key expiry.**

Verify it took:

```bash
tailscale status --json | grep -i keyexpiry     # should return nothing
```

### 5. Publish the dashboard

```bash
sudo tailscale serve --bg 3000
```

This proxies `https://your-box.your-tailnet.ts.net` → `http://127.0.0.1:3000`. The
loopback binding stays exactly as it is; Serve reaches it from inside the box.

**The first time you run this, Tailscale will ask you to enable Serve on your
network.** That page has a trap — see immediately below.

> ### ⚠️ UNCHECK "Tailscale Funnel"
>
> The Serve enablement page offers two checkboxes and **pre-checks both**:
>
> - **HTTPS certificates** — required. Leave it checked.
> - **Tailscale Funnel** — **UNCHECK THIS.**
>
> Funnel *"lets you route traffic from the internet to services running on your
> Tailscale devices."* That is public internet ingress: the exact opposite of the
> point of this entire document. YRVI's dashboard has no login, and the box it
> runs on holds your IBKR credentials and a live brokerage session. It must never
> be on the public internet.
>
> The button keeps reading **"Enable HTTPS and Funnel"** even after you uncheck
> the box. That label is stale — the checkbox is what counts. To confirm Funnel
> was not enabled:
>
> ```bash
> tailscale status --json | grep -i funnel      # should return nothing
> ```
>
> Note that enabling HTTPS certificates writes your box's `.ts.net` hostname into
> public Certificate Transparency logs, permanently. That is unavoidable with any
> real certificate. It publishes a *name*, not a route — there is nothing behind
> it to connect to — and it reveals nothing about your account.

### 6. Your phone

Install Tailscale from the App Store or Play Store and sign in with the **same**
account. Then open:

```
https://your-box.your-tailnet.ts.net
```

> ### Type `https://`
>
> **Serve listens on 443 only.** `http://` hits port 80, where nothing is
> listening, and you get `ERR_CONNECTION_FAILED` — which looks like a broken setup
> but is just the wrong scheme.
>
> This is worse than it sounds: once your browser has the `http://` URL in its
> history, typing the hostname *autocompletes back to the broken one*, so every
> "retry" repeats the mistake. The tell is **"Not Secure"** in the address bar.
>
> If you get stuck in that loop, use a different browser for the first visit.
>
> Also: `http://<tailnet-ip>:3000` will not work either. The dashboard binds to
> `127.0.0.1`, not the network interface — which is exactly why Serve exists.

Turn WiFi off and load it over cell data. That is the real test.

---

## Verifying

From the box:

```bash
tailscale status                 # your devices, and whether they're online
tailscale serve status           # should say "(tailnet only)"
tailscale funnel status          # must NOT show anything served publicly
```

`(tailnet only)` is the phrase that matters. If you ever see the dashboard
described as available on the internet, stop and turn Funnel off.

> **A quirk that will confuse you:** the Homebrew `tailscaled` on macOS does not
> fully register itself as the system DNS resolver, so **the box cannot resolve
> its own `.ts.net` name** — `curl` from the box returns `Could not resolve host`
> even though everything is working. Your phone and laptop resolve it fine, since
> their apps handle DNS properly. To test the endpoint from the box itself, skip
> DNS:
>
> ```bash
> curl --resolve your-box.your-tailnet.ts.net:443:<tailnet-ip> \
>      https://your-box.your-tailnet.ts.net/
> ```

---

## Rules

**Never enable Funnel.** Covered above. It is the one action that turns this from
a private network into a public exposure.

**Never rebind YRVI services to `0.0.0.0`.** Every published port in
`docker-compose.yml` starts with `127.0.0.1:` and must stay that way. That loopback
binding is what makes a login-less dashboard safe. Serve does not need you to
change it — it reaches loopback from inside the box.

**Keep phone use operational.** Restarting a wedged gateway or triggering a run
from your phone is fine. Changing capital settings or credentials from a phone on
a coffee shop network is a bad habit; do that at the box.

---

## Windows

Untested, but the shape is the same: install Tailscale from
[tailscale.com/download](https://tailscale.com/download), sign in, disable key
expiry in the admin console, then:

```
tailscale serve --bg 3000
```

The Funnel warning, the `https://`-only rule, and the never-rebind-to-`0.0.0.0`
rule all apply identically. If you work through it, corrections to this section
are welcome.

## Undoing it

```bash
sudo tailscale serve --https=443 off    # stop publishing the dashboard
sudo tailscale down                     # leave the network
sudo brew services stop tailscale       # stop the daemon
```

Then delete the machine from the admin console. Nothing in YRVI is affected —
this was never part of the trading stack.
