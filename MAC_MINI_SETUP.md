# Mac Mini Setup Guide — You Rock Club

This guide walks You Rock Club members through setting up a Mac Mini as a dedicated, always-on YRVI trading machine. Follow these steps in order from initial macOS setup through a verified reboot test.

---

## Recommended Hardware

| Component | Spec | Notes |
|-----------|------|-------|
| Computer | Mac Mini M4 (Apple Silicon) | M4 Pro (48GB) is ideal but base M4 works fine. Intel minis (macOS 15+) also work — see Intel notes in Phases 3–4. All images build locally on the device, so amd64 builds natively. |
| RAM | 16GB minimum | Base config is fine for YRVI |
| Storage | 256GB SSD | Base config is fine |
| Network | Ethernet (recommended) | More reliable than WiFi |
| UPS | Battery backup ~$75 | Highly recommended — prevents power-outage reboots |

> 💡 Check Amazon weekly — the M4 Mac Mini regularly goes on sale for $469–499. MicroCenter sometimes has it for $399 in store.

---

## One-Time Setup Hardware

You will need the following physical hardware to complete the initial setup. After setup is complete, you can manage the Mac Mini entirely via Screen Sharing (remote desktop) from another Mac, or via a VNC client from a Windows PC — none of this hardware is needed day-to-day.

| Item | Notes |
|------|-------|
| Monitor with HDMI input | The Mac Mini has an HDMI port. If your monitor only has DisplayPort or VGA, you'll need an adapter. A cheap 1080p monitor works fine. |
| HDMI cable | Usually included with monitors, otherwise ~$10 |
| USB keyboard | Any USB or Bluetooth keyboard |
| USB mouse | Any USB or Bluetooth mouse |

> 💡 Once Screen Sharing is configured (Phase 2), you can disconnect all of this and control the Mac Mini remotely forever after — from another Mac via Finder/Screen Sharing, or from a Windows PC using a free VNC client like TigerVNC.

---

## Phase 1 — macOS Initial Setup

When you first power on the Mac Mini, follow these decisions at each setup screen:

### Apple Account
- **Skip signing in to Apple ID** — select "Set Up Later" or "Skip"
- This machine is a dedicated server and doesn't need iCloud, App Store sync, or any Apple services

### Location Services
- **Turn off** — not needed for a trading server

### Time Zone
- Select **closest city** (Los Angeles for Pacific time)

### Mac Analytics
- **Uncheck everything** and skip

### FileVault
- **Do NOT enable FileVault**
- FileVault requires a manual password entry after every reboot or power outage, which means YRVI won't auto-restart unattended
- Your IBKR credentials are stored encrypted in the secrets container regardless of FileVault

### Automatic Software Updates
- Turn on **Download new updates when available**
- Turn on **Install Security Responses and system files**
- Leave **Install macOS updates** OFF — a major OS update should not reboot your machine mid-trading day

---

## Phase 2 — macOS System Settings

### Remote Access — SSH + Screen Sharing

Enable both:
- **SSH (Remote Login)** for terminal access: **System Settings → General → Sharing → Remote Login → On**
- **Screen Sharing** (optional but recommended) to view the Mac's full desktop from another Mac: **System Settings → General → Sharing → Screen Sharing → On**

```bash
ssh [your-user]@[MAC_MINI_IP]
```

> 💡 Connect over your local network (Ethernet on both machines is most reliable) or via your router's remote access / VPN if accessing from outside your home.

> ⚠️ **VNC port note (important).** The IB Gateway container serves its own VNC on **`127.0.0.1:5900`** for 2FA/dialogs. macOS Screen Sharing also uses port 5900, but on different addresses (your LAN IP and IPv6 `::1`), so the two **coexist fine**. The catch: when you point a VNC client at the gateway, **always use `127.0.0.1:5900` (literal IPv4) — never `localhost:5900`.** On macOS `localhost` resolves to IPv6 `::1` first, which macOS Screen Sharing answers, so `localhost` sends you to the wrong server and you get an "authentication failed". If `docker compose up` ever fails with *"address already in use"* on 5900, either turn Screen Sharing off or set `IB_GATEWAY_VNC_PORT` to a free port in `.env.compose`.

### Enable Automatic Login
1. System Settings → Users & Groups (search "automatic" in Settings search bar)
2. Set **Automatically log in as: [your admin user]**
3. Enter your password to confirm

This ensures the Mac Mini logs itself in automatically after a power outage or reboot — no one needs to be present.

> ⚠️ Automatic Login is only available when FileVault is disabled. This is why we skip FileVault above.

### Prevent Sleep (important — keeps trading running 24/7)

The Mac Mini runs headless and must never **system-sleep**, or macOS freezes the Docker VM and the scheduler misses trade windows. The key setting is *computer* sleep, not display sleep.

- **System Settings → Energy:**
  - ✅ **"Prevent automatic sleeping when the display is off"** — this is the one that matters (stops *system* sleep, which is what freezes Docker). Set via the GUI, it **persists across reboots**.
  - ✅ **"Start up automatically after a power failure"**
  - Turn **Power Nap OFF** if shown (avoids maintenance wake/sleep cycles).
- Display sleep itself is harmless on a headless box, but you can set it to **Never** too.

**Or set it all from the terminal (equivalent, also persistent):**
```bash
sudo pmset -a sleep 0          # never system-sleep (the one that matters)
sudo pmset -a autorestart 1    # power back on automatically after an outage
sudo pmset -a powernap 0       # no maintenance wake/sleep cycles
```
Verify: `pmset -g | grep -iE "^ *sleep |autorestart|powernap"` → expect `sleep 0`, `autorestart 1`, `powernap 0`.

> **Mac mini vs. a lid-closed MacBook — do you need `pmset disablesleep 1`?**
> **No, not on a mini.** A Mac mini has no lid, so the GUI "prevent sleep" setting above is sufficient and persistent. The `pmset disablesleep 1` flag exists for **laptops running lid-closed**, where macOS clamshell/"maintenance sleep" can freeze Docker even on AC. If you ever run the live stack on a **MacBook** with the lid shut, run `sudo pmset -a disablesleep 1` — and **reapply it after every reboot**, because (unlike the GUI setting) it does **not** persist.

---

## Phase 3 — Developer Tools

Open Terminal and run these commands in order.

### 1. Xcode Command Line Tools
```bash
xcode-select --install
```
A popup will appear — click **Install** (not "Get Xcode"). Takes a few minutes.

### 2. Homebrew
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

### 3. Add Homebrew to PATH (Apple Silicon only)
```bash
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile && eval "$(/opt/homebrew/bin/brew shellenv)"
```

> **⚠️ Intel Macs:** Homebrew installs to `/usr/local`, **not** `/opt/homebrew`. The
> line above will silently fail on an Intel mini. On Intel, `/usr/local/bin` is
> already on the default PATH, so you usually don't need a shellenv line at all —
> just run `brew --version` (Step below) and if it works, skip this step. If it
> reports "command not found," use the Intel path instead:
> ```bash
> echo 'eval "$(/usr/local/bin/brew shellenv)"' >> ~/.zprofile && eval "$(/usr/local/bin/brew shellenv)"
> ```

Verify:
```bash
brew --version
```

### 4. Install Core Tools
```bash
brew install git gh python node
```

### 5. (Optional — developers only) Set Up SSH Key and GitHub Auth

> **Skip this step for a normal operator install.** The repo is **public**, so you
> can clone it anonymously over HTTPS (see Phase 5) — no SSH key or GitHub login
> required, and the dashboard upgrade button pulls updates anonymously too.
>
> Only do this if you'll be **pushing code changes back** to GitHub (i.e., setting
> up a dev machine).

```bash
ssh-keygen -t ed25519 -C "your@email.com"
```
Hit Enter through all prompts (default location, no passphrase).

Then authenticate with GitHub:
```bash
gh auth login
```
Choose: **GitHub.com → SSH → Yes → Login with a web browser** — this uploads your SSH key to GitHub automatically.

---

## Phase 4 — Docker Desktop

1. Download **Docker Desktop for Mac** from [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/) — choose the **Apple Silicon** build for M-series minis, or the **Intel chip** build for an Intel mini
2. Drag Docker to Applications and open it
3. Accept the license agreement
4. At setup: select **Use recommended settings** → Finish
5. Skip the Docker account sign-in (click Skip)

### Configure Auto-Start
Docker Desktop → Settings (⚙️) → General:
- ✅ **Start Docker Desktop when you sign in to your computer**
- ✅ **Open Docker Dashboard when Docker Desktop starts**

### Verify Docker Works
```bash
docker run hello-world
```
Should print "Hello from Docker!"

---

## Phase 5 — Clone YRVI and Run Setup

### Clone the Repo
```bash
cd ~ && git clone https://github.com/controllinghand/you_rock_fund.git
cd you_rock_fund
```

> The repo is public, so this HTTPS clone needs no authentication. If you set up
> SSH in Step 5 (developers), you can use `git clone git@github.com:controllinghand/you_rock_fund.git` instead.

### Register the Upgrade URL Scheme (one-time)
```bash
bash scripts/yrvi-register-url-scheme.sh
```
This registers `yrvi://upgrade` so the dashboard Upgrade button can open Terminal directly for one-click upgrades. Run it once after cloning; re-run it if you move the repo.

### Configure `.env.compose`
```bash
cp .env.compose.example .env.compose
```

`.env.compose` only contains non-secret settings (ports, trading mode, timezone) — no editing required for a default paper-trading setup. Account credentials are entered later via the secrets container UI at `http://localhost:8001` when `setup_docker.sh` runs.

Leave `TRADING_MODE=paper` — the paper account is the safety net for a new setup, so orders route to your paper account (not real money). Dry Run defaults to **off**; enable it in Settings only if you want to simulate without any account fills.

### Run Paper Trading Setup
```bash
./setup_docker.sh --paper
```

On first run, the script opens `http://localhost:8001` in your browser where you'll enter:
- Your **IBKR paper trading password**
- Your **Render screener API secret**

Secrets are stored encrypted in a persistent Docker volume. On subsequent runs, the script detects existing secrets and skips this step.

### What Setup Does
The script runs 6 steps automatically:
1. Verifies Docker is running
2. Starts the secrets container and opens `http://localhost:8001` for credential entry
3. Validates your `.env.compose` config
4. Builds and starts all 5 containers (secrets, ib_gateway, api, scheduler, web)
5. Installs a launchd login item so containers restart automatically after every reboot
6. Installs the YRVI Startup desktop app

### Watch IB Gateway Log In
```bash
docker compose --env-file .env.compose logs -f ib_gateway
```
Wait for **"Login has completed"** — takes 30–90 seconds.

### Open the Dashboard
```
http://localhost:3000
```

You should see all status indicators green: **Gateway • Scheduler • IBKR**

---

## Phase 6 — Switch to Live Trading

Once you've validated paper trading works for a full week:

```bash
./setup_docker.sh --live
```

> ⚠️ Only one YRVI instance can connect to an IBKR account at a time. If you have YRVI running on another machine (laptop, Windows PC), you must shut it down before starting live trading on the Mac Mini. The Mac Mini should be your permanent home for live trading going forward.

---

## Phase 7 — Reboot Test

Always test a full reboot before considering setup complete:

```bash
sudo reboot
```

After the Mac Mini restarts:
1. It should boot straight to the desktop (no password prompt) — confirms automatic login works
2. Docker Desktop should open automatically
3. YRVI containers should start within 60 seconds
4. Dashboard at `http://localhost:3000` should show all green

If IB Gateway needs a moment, run:
```bash
docker compose --env-file .env.compose logs -f ib_gateway
```
And wait for "Login has completed".

---

## Ongoing Operations

### Pre-flight Check (anytime)
```bash
bash ~/you_rock_fund/startup.sh
```
Or double-click **YRVI Startup** on your Desktop.

### Container Status
```bash
docker compose --env-file .env.compose ps
```

### View Logs
```bash
docker compose --env-file .env.compose logs -f scheduler   # trades
docker compose --env-file .env.compose logs -f ib_gateway  # gateway login
docker compose --env-file .env.compose logs -f api         # dashboard API
```

### Re-run Setup (after reboot, if needed)
```bash
cd ~/you_rock_fund && ./setup_docker.sh --paper
```
Existing secrets are detected automatically — no re-entry needed.

### Rotating a Secret (e.g. changed IBKR password)
1. Open `http://localhost:8001` in your browser (or visit **Secrets** in the dashboard)
2. Click **Update** next to the secret you want to change
3. Enter the new value and click **Save**

To apply a new IBKR password, restart the gateway after saving:
```bash
docker compose --env-file .env.compose restart ib_gateway
```

---

## Troubleshooting

> ❓ For common setup issues and fixes, see [FAQ.md](./FAQ.md).

| Problem | Fix |
|---------|-----|
| "Existing session detected" in gateway log | Another machine is connected to the same IBKR paper account. Shut it down first. |
| Containers don't start after reboot | Open Docker Desktop manually and wait for "Engine running", then run `./setup_docker.sh --paper` |
| Dashboard shows Gateway red | Run `docker compose --env-file .env.compose logs -f ib_gateway` and check for errors |
| Secret files missing error | Run `./setup_docker.sh --paper` — secrets are re-fetched from the secrets container |
| IB Gateway needs 2FA | Open the built-in **View Gateway** viewer — dashboard **Help → System Diagnostics → View Gateway** (see below). No VNC client needed. |

### Viewing the IB Gateway Screen (built-in View Gateway)

To reach the login/2FA screen, use the **View Gateway** viewer built into the dashboard —
there's nothing to install.

1. Open the dashboard (**http://localhost:3000** on the mini) → **Help** (left nav).
2. Under **System Diagnostics**, click **View Gateway** → **👁 Open viewer (view-only)**,
   or **⚠️ Enable keyboard / mouse control** to complete an IB Key 2FA (live) or confirm the
   auto-login (paper).

The password auto-fills from the `vnc_server_password` secret — nothing to type. See
[docs/view-gateway.md](docs/view-gateway.md).

**From another machine (LAN/VPN):** open an SSH tunnel to the dashboard and browse locally —
```bash
ssh -L 3000:127.0.0.1:3000 -L 6080:127.0.0.1:6080 <user>@<mini-ip>
```
then open `http://localhost:3000` and use View Gateway as above (it opens the viewer on
`localhost:6080`, which the tunnel forwards).

> We used to recommend installing RealVNC/TigerVNC and connecting an external client to
> `127.0.0.1:5900`. That's **no longer needed** — View Gateway is built in. The raw VNC port
> is still exposed on the container at `127.0.0.1:5900` if you ever want an external client as
> a fallback; on macOS use the literal `127.0.0.1:5900`, **never `localhost:5900`** (`localhost`
> resolves to IPv6 `::1`, which macOS Screen Sharing answers → auth fails against the wrong
> server).

---

## Total Setup Cost

| Item | Cost |
|------|------|
| Mac Mini M4 (16GB/256GB) | ~$499 |
| UPS battery backup | ~$75 |
| Ethernet cable | ~$10 |
| **Total** | **~$584 one time** |

vs $3,500+/week potential income = **ROI in first week** 💰

---

*You Rock Club — YRVI Mac Mini Setup Guide*
