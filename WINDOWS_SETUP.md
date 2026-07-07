# Windows PC Setup Guide — You Rock Club

This guide walks You Rock Club members through setting up a Windows Mini PC as a dedicated, always-on YRVI trading machine. Follow these steps in order from initial Windows setup through a verified reboot test.

> 💡 **Tip:** Open each download/reference link in a **new tab** — **Ctrl+click** (or middle-click) the link — so you don't lose your place in these instructions.

---

## Recommended Hardware

| Component | Spec | Notes |
|-----------|------|-------|
| Computer | GEEKOM A5 Mini PC | AMD Ryzen 7 5825U · 16GB RAM · 512GB SSD |
| Network | Ethernet (recommended) | More reliable than WiFi |
| UPS | Battery backup ~$75 | Highly recommended — prevents power-outage reboots |

> 💡 The GEEKOM A5 ships with Windows 11 Pro pre-installed — no OS purchase needed. It handles YRVI with headroom to spare.

---

## One-Time Setup Hardware

You will need the following physical hardware to complete the initial setup. After setup is complete, you can manage the GEEKOM entirely via **Remote Desktop** from another Windows PC or Mac — none of this hardware is needed day-to-day.

| Item | Notes |
|------|-------|
| Monitor with HDMI input | The GEEKOM A5 has two HDMI ports. A cheap 1080p monitor works fine. |
| HDMI cable | Usually included with monitors, otherwise ~$10 |
| USB keyboard | Any USB keyboard |
| USB mouse | Any USB mouse |

> 💡 Once Remote Desktop is configured (Phase 2), you can disconnect all of this and control the GEEKOM remotely forever after — from any Windows PC via the built-in Remote Desktop app, or from a Mac using **Microsoft Remote Desktop** (free on the App Store).

---

## Phase 1 — Windows Initial Setup

When you first power on the GEEKOM, follow these decisions at each setup screen:

### Microsoft Account
- **Create a local account instead** — on the "Sign in with Microsoft" screen, look for **"Sign-in options"** → **"Domain join instead"** (Windows 11 Pro). The exact wording varies by build — you may instead see "Other ways to sign in," or the screen may push hard toward a Microsoft account. The goal is simply: **do not sign in with a Microsoft account.**
- Choose a short username (e.g. `yrvi`) and a strong password
- This machine is a dedicated server and doesn't need OneDrive, Microsoft 365, or any cloud sync

> ⚠️ **If you accidentally created a Microsoft account** (or Windows wouldn't let you skip it), you can convert it to local afterward: **Settings → Accounts → Your info → "Sign in with a local account instead"** → set username `yrvi` + a password. A local account avoids Microsoft-account 2FA prompts and makes the automatic-login step (Phase 2) work cleanly.

### Privacy Settings
- **Turn everything off** — location, diagnostic data, inking, tailored experience, advertising ID
- None of this is needed for a trading server

### Time Zone
- Set to **Pacific Time (US & Canada)** — the YRVI scheduler uses Pacific time for its weekly job windows

### Automatic Updates
- Leave Windows Update **on** — Windows will manage updates in the background
- Avoid manually triggering large updates during market hours (Monday 9:55AM–10:30AM PST)

### BitLocker
- **Do NOT enable BitLocker drive encryption**
- BitLocker can require a PIN or recovery key after a reboot, which prevents YRVI from restarting unattended after a power outage
- IBKR credentials are stored as encrypted Docker secrets regardless of BitLocker

---

## Phase 2 — Windows System Settings

### Enable Remote Desktop
1. Start → Settings → System → Remote Desktop
2. Toggle **Remote Desktop** → On
3. Note the PC name shown (e.g. `GEEKOM-A5`) — you'll use this to connect from another machine
4. You can also connect by IP address: open Command Prompt and run `ipconfig` to find it

To connect from another Windows PC: Start → search "Remote Desktop Connection" → enter the IP or PC name.
To connect from a Mac: download **Microsoft Remote Desktop** (free, App Store) → add the PC by IP.

> 💡 Connect over your local network (Ethernet on both machines is most reliable) or via your router's VPN if accessing from outside your home.

### Enable Automatic Login
This ensures the GEEKOM logs itself in automatically after a power outage or reboot — no one needs to be present. **Set this up now, before installing Docker** (the Docker install in Phase 3 forces a reboot — see the note there).

**Method 1 — `netplwiz` (try this first):**
1. Press **Win + R**, type `netplwiz`, press Enter
2. In the User Accounts dialog, select your user account
3. **Uncheck** "Users must enter a user name and password to use this computer"
4. Click **Apply** — enter your password twice to confirm
5. Click **OK**

> ⚠️ **The checkbox is often missing** on Windows 11 (common with Microsoft / passwordless accounts). If you don't see "Users must enter a user name and password," try toggling **Settings → Accounts → Sign-in options → "For improved security, only allow Windows Hello sign-in for Microsoft accounts on this device" → Off**, then reopen `netplwiz`. If it still doesn't appear, use Method 2.

**Method 2 — Sysinternals Autologon (reliable fallback):**
1. Download **Autologon** from [learn.microsoft.com/sysinternals/downloads/autologon](https://learn.microsoft.com/en-us/sysinternals/downloads/autologon)
2. Unzip and run **Autologon.exe** (no install needed)
3. Enter **Username** (`yrvi`), **Domain** (leave the prefilled PC name), and **Password**
4. Click **Enable** — it stores the password encrypted, not plaintext

> ⚠️ **PIN gotcha:** If the account has a Windows Hello **PIN**, the boot may stop at the PIN prompt even with auto-login set. If your reboot test (Phase 6) doesn't go straight to the desktop, remove the PIN: **Settings → Accounts → Sign-in options → PIN (Windows Hello) → Remove.**

### Optional: Prevent Display Sleep
- Settings → System → Power → Screen and sleep
- Set all sleep timers to **Never** (the GEEKOM runs fine headless after initial setup)

---

## Phase 3 — Developer Tools

### 1. Git for Windows
Download and install from [git-scm.com/download/win](https://git-scm.com/download/win).

During install, **accept all the defaults** — click Next through every screen. The one screen worth a glance is **"Adjusting your PATH environment"**: confirm **"Git from the command line and also from 3rd-party software"** is selected (it's the default — you usually don't need to change anything). Click **Next → Install**.

Git for Windows includes **Git Bash**, the terminal you'll use for all YRVI commands.

### 2. GitHub CLI (gh) — *optional, you can skip this*
The YRVI repo is **public**, so cloning it needs **no GitHub account and no authentication**. You only need the GitHub CLI if you plan to push changes back (a dedicated trading box normally won't). **Most people can skip straight to Docker Desktop below.**

If you do want it: download the installer from [cli.github.com](https://cli.github.com) and run it, then **close and reopen Git Bash** (so it picks up the new `gh` command) and authenticate:
```bash
gh auth login
```
Choose: **GitHub.com → HTTPS → Yes → Login with a web browser**

> 💡 A terminal opened *before* you install a tool won't see it (`command not found`). After installing **anything** — git, gh, Docker — **close and reopen Git Bash** so its PATH refreshes.

### 3. Docker Desktop
Download from [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/) and run the installer.

On first launch:
- Accept the license agreement
- Select **Use recommended settings** → Finish
- Skip the Docker account sign-in (click Skip)

> ⚠️ **Docker will reboot the PC** to finish setting up its WSL2 backend. This is expected. After the reboot, the machine should boot straight to the desktop **if you completed the auto-login step in Phase 2** — if it stops at a login prompt instead, log in manually and revisit Phase 2 (Autologon) before continuing.

#### Configure Docker Desktop to Auto-Start
Docker Desktop → Settings (⚙️) → General:
- ✅ **Start Docker Desktop when you sign in to your computer**

Click **Apply & Restart**.

#### Verify Docker Works
Wait for the Docker Desktop whale icon / engine indicator to go **green ("Engine running")**, then open Git Bash and run:
```bash
docker run hello-world
```
Should print "Hello from Docker!"

> ⚠️ **If you get "Docker Desktop is unable to start"** — the WSL2 kernel is usually out of date. Fix it:
> ```bash
> wsl --update
> ```
> (If that errors on permissions, run it in **PowerShell as Administrator**.) Then fully **Quit Docker Desktop** (right-click the tray whale → Quit) and reopen it. If it still won't start, try `wsl --shutdown`, and check that **virtualization (AMD-V/SVM) is enabled** in the BIOS (Task Manager → Performance → CPU → "Virtualization: Enabled").

---

## Phase 4 — Clone YRVI and Run Setup

Use **Git Bash** for all commands (not PowerShell, not Command Prompt). Clone to your Windows home directory — **not** a WSL2 path.

### Clone the Repo
```bash
cd ~
git clone https://github.com/controllinghand/you_rock_fund.git
cd you_rock_fund
```

### Configure `.env.compose`
```bash
cp .env.compose.example .env.compose
```

You don't need to edit anything — the `--paper` flag in the next step sets `TRADING_MODE` for you (and points IBKR at the paper port). The paper account is the safety net for a new setup, so orders route to your paper account (not real money). Dry Run defaults to **off**; enable it in Settings only if you want to simulate without any account fills.

### Run Paper Trading Setup
```bash
bash setup_docker.sh --paper
```

The script runs the same 6-step flow as the Mac setup:

**Step 1 — Docker check**: verifies Docker is running.

**Step 2 — Configure secrets**: starts the secrets container and opens `http://localhost:8001` in your browser automatically. Enter at minimum:
- Your **IBKR paper trading username and password**
- Your **Render screener API secret** (provided in onboarding)

> 📖 **Where do these come from?** See [IBKR_SETUP_GUIDE.md](./IBKR_SETUP_GUIDE.md) → "Find Your Credentials for YRVI" for exactly where to find your paper username, paper password, and account number.

Setup waits at this step until you click **Save** in the browser.

> 💡 **If the browser doesn't open on its own**, just open it manually and go to **http://localhost:8001** — the page is served the whole time setup is waiting. Enter your secrets there and click Save, and setup continues automatically.

**Step 3 — Validate config**: verifies `.env.compose` and required secrets.

**Step 4 — Start containers**: builds and starts all 5 containers (ib_gateway, api, scheduler, web, secrets).

**Step 5 — Auto-start on login**: creates a `.bat` file and registers a Task Scheduler job (`YRVI_Docker_AutoStart`) that runs `docker compose up -d` at every login — **no manual Task Scheduler setup needed**.

> ⚠️ If Step 5 fails with a permissions error, close Git Bash, reopen it **as Administrator** (right-click → "Run as administrator"), navigate back to the repo, and rerun `bash setup_docker.sh --paper`. The Task Scheduler registration requires elevated rights.

**Step 6 — Desktop app**: skipped on Windows (macOS only). Access the dashboard at `http://localhost:3000`.

### Confirm All 5 Containers Are Running
Before going further, verify the stack actually started. In Git Bash, from the repo folder:
```bash
docker compose --env-file .env.compose ps
```
You should see **5 containers** with status `Up` / `running`: **ib_gateway**, **api**, **scheduler**, **web**, **secrets**.

- If you see **only `secrets`** → setup is still waiting on the secrets page. Open **http://localhost:8001**, enter your credentials, click **Save**, then rerun `bash setup_docker.sh --paper`.
- If a `docker compose ... logs` command **returns instantly with no output**, that container isn't running — check `ps` first.

> Run all `docker compose` commands from `~/you_rock_fund` in **Git Bash** (not Command Prompt), or compose won't find the project.

### Watch IB Gateway Log In
```bash
docker compose --env-file .env.compose logs -f ib_gateway
```
Wait for **"Login has completed"** — takes 30–90 seconds. Press Ctrl+C when you see it.

### Open the Dashboard
```
http://localhost:3000
```
You should see all status indicators green: **Gateway • Scheduler • IBKR**

---

## Phase 5 — Switch to Live Trading

Once you've validated paper trading works for a full week:

```bash
bash setup_docker.sh --live
```

> ⚠️ Only one YRVI instance can connect to an IBKR account at a time. If you have YRVI running on another machine (Mac Mini, laptop), shut it down before starting live trading on the GEEKOM. The GEEKOM should be your permanent home for live trading going forward.

---

## Phase 6 — Reboot Test

Always test a full reboot before considering setup complete:

1. Start → Power → **Restart**
2. After the GEEKOM restarts, it should boot straight to the desktop — confirms automatic login works
3. Docker Desktop should open automatically
4. YRVI containers should start within 60 seconds (via the Task Scheduler job)
5. Open the dashboard at `http://localhost:3000` — all indicators should show green

If containers haven't started yet, check Docker Desktop is fully running (engine indicator green), then from Git Bash:
```bash
cd ~/you_rock_fund
docker compose --env-file .env.compose logs -f ib_gateway
```
Wait for "Login has completed".

---

## Ongoing Operations

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

### Start / Restart Containers
```bash
cd ~/you_rock_fund
docker compose --env-file .env.compose up -d
```

### Stop Everything
```bash
docker compose --env-file .env.compose down
```

### Re-run Setup (after reboot, if needed)
```bash
cd ~/you_rock_fund && bash setup_docker.sh --paper
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

### Upgrading YRVI
The dashboard Upgrade button is macOS-only. Upgrade manually from Git Bash:
```bash
cd ~/you_rock_fund
git pull
docker compose --env-file .env.compose up -d --build
```

---

## Troubleshooting

> ❓ For general setup issues and fixes, see [FAQ.md](./FAQ.md).

| Problem | Fix |
|---------|-----|
| "Existing session detected" in gateway log | Another machine is connected to the same IBKR account. Shut it down first. |
| Containers don't start after reboot | Open Docker Desktop and wait for "Engine running", then run `bash setup_docker.sh --paper` |
| Dashboard shows Gateway red | Run `docker compose --env-file .env.compose logs -f ib_gateway` and check for errors |
| Task Scheduler job not registered | Rerun `bash setup_docker.sh --paper` from Git Bash opened as Administrator |
| `docker` not found in Git Bash | Docker Desktop isn't running. Open it and wait for the engine indicator to go green. |
| IB Gateway needs 2FA or shows a login dialog | Connect via TigerVNC (see below) |

### Connecting via VNC (2FA / login dialogs)

The IB Gateway runs headless inside Docker but exposes a VNC session on `127.0.0.1:5900`. Windows does not have a built-in VNC viewer — use **TigerVNC** (free, open-source, no account):

1. Download the Windows installer from [tigervnc.org](https://tigervnc.org/) (or the [GitHub releases](https://github.com/TigerVNC/tigervnc/releases)) and run `vncviewer64.exe`.
2. Connect to: **`127.0.0.1:5900`** (leave Username blank).
3. Password: `ibgateway123!test` (default — its first 8 chars, `ibgatewa`, are what count; change it via `http://localhost:8001`, keeping it **≤ 8 characters**).

You'll see the IB Gateway GUI and can dismiss any dialog that's blocking login.

> We switched from RealVNC because its current viewer forces you to create an account before it will connect. TigerVNC connects directly.

---

## Total Setup Cost

| Item | Cost |
|------|------|
| GEEKOM A5 Mini PC (Ryzen 7 5825U, 16GB/512GB) | ~$459 |
| UPS battery backup | ~$75 |
| Ethernet cable | ~$10 |
| **Total** | **~$544 one time** |

vs $3,500+/week potential income = **ROI in first week** 💰

---

*You Rock Club — YRVI Windows Setup Guide*
