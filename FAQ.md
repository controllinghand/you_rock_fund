# YRVI Setup FAQ

Common issues and fixes for You Rock Club members setting up YRVI on a Mac Mini.

---

### Q: Docker fails with "address already in use" on port 5900

**A:** macOS Screen Sharing uses port 5900, which conflicts with IB Gateway's VNC port.

Turn it off before starting YRVI:

**System Settings → General → Sharing → Screen Sharing → toggle OFF**

Then restart the stack:
```bash
docker compose --env-file .env.compose down
./setup_docker.sh --paper
```

> Use SSH for remote terminal access instead — Screen Sharing cannot run alongside YRVI.
>
> Enable SSH: **System Settings → General → Sharing → Remote Login → On**
>
> Then connect with: `ssh [your-user]@[MAC_MINI_IP]`

---

### Q: I need to check the IB Gateway screen (e.g. it's stuck on a 2FA or confirmation dialog)

**A:** The IB Gateway runs headless inside Docker but exposes a VNC session on port 5900. macOS's built-in Screen Sharing won't work for this — it refuses to connect to localhost. Use **RealVNC Viewer** (free) instead:

1. Download: https://www.realvnc.com/en/connect/download/viewer/
2. Open RealVNC Viewer and connect to: `127.0.0.1:5900`
3. Password: `ibgateway123!test` (unless you set a custom VNC Password in secrets)

You'll see the IB Gateway GUI and can dismiss whatever dialog is blocking it.

---

### Q: setup_docker.sh fails or containers won't start — I forgot to configure secrets

**A:** Since v1.4.0, account credentials are entered via the secrets container UI, not `.env.compose`. Re-run `setup_docker.sh --paper` — it will open `http://localhost:8001` in your browser. Enter at minimum:

| Field | What to enter |
|---|---|
| IBKR Paper Account ID | Your IBKR paper account ID (e.g. `DU1234567`) |
| IBKR Paper Username | Your IBKR paper username |
| IBKR Paper Trading Password | Your IBKR paper password |
| IBKR Live Trading Password | Your IBKR live password |
| Render Screener API Secret | Provided in onboarding |

Optional fields (only needed if you flip the stack to live mode or want a custom VNC password):

| Field | What to enter |
|---|---|
| IBKR Live Account ID | Your IBKR live account ID |
| IBKR Live Username | Your IBKR live username |
| VNC Password | Defaults to `ibgateway123!test` if unset |

If your browser doesn't open, paste `http://localhost:8001` into it directly. If the browser flow times out, the script falls back to terminal prompts.

---

### Q: Discord test notification fails — "webhook not configured"

**A:** The Discord webhook URL lives in a secret file, not in `.env.compose`. Create it manually:

```bash
echo "https://discord.com/api/webhooks/xxx/yyy" > ~/you_rock_fund/docker/secrets/discord_webhook_url
```

Get your webhook URL from: **Discord → Edit Channel → Integrations → Webhooks**

Then restart the scheduler to pick it up:
```bash
cd ~/you_rock_fund
docker compose --env-file .env.compose restart scheduler
```

See `docker/secrets/README.md` for the full list of secret files.

---

### Q: Docker doesn't start automatically after a reboot

**A:** Docker Desktop needs to be configured to launch at login:

**Docker Desktop → Settings (⚙️) → General → ✅ Start Docker Desktop when you log in**

Then reboot and confirm YRVI comes back up on its own.

---

### Q: The Mac Mini asks for a password on every reboot instead of auto-logging in

**A:** Automatic Login requires FileVault to be **off**. Check your status:

```bash
fdesetup status
```

If FileVault is on, turn it off:

**System Settings → Privacy & Security → FileVault → Turn Off**

This takes 30–60 minutes to decrypt. After it finishes, enable auto-login:

**System Settings → Users & Groups → Automatically log in as → [your user]**

> Note: Keeping your Apple ID is fine — FileVault is a separate toggle and can be disabled without removing your Apple ID.

---

### Q: Scheduler fails to restart with "no such file or directory: render_secret"

**A:** The `render_secret` file is missing from `docker/secrets/`. Create it:

```bash
echo "your_render_secret_here" > ~/you_rock_fund/docker/secrets/render_secret
```

Contact the fund operator for the Render API secret value if you don't have it.

---

### Q: I get "couldn't find env file" when running docker compose commands

**A:** You're running the command from the wrong directory. Always run Docker commands from the repo root:

```bash
cd ~/you_rock_fund
docker compose --env-file .env.compose <command>
```

---

### Q: Where do I find all the secret files I need to create?

**A:** See [`docker/secrets/README.md`](docker/secrets/README.md) for the complete list of secret files, what each contains, and how to get the values.

---

*Have a question not covered here? Post in the You Rock Club Discord and we'll add it.*
