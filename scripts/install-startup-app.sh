#!/bin/bash
# ─────────────────────────────────────────────────────────────
#  Install / update the "YRVI Startup" desktop app.
#
#  Repoints the app at the headless launcher (scripts/yrvi-launch.sh) so a
#  double-click brings the whole stack up in the BACKGROUND — no Terminal
#  window, and the browser shows the result.
#
#  Replaces the old behavior, whose MacOS executable did:
#      osascript … tell application "Terminal" … do script "bash yrvi_startup.command"
#  i.e. it force-opened a Terminal (the window that "pops up"), collided with
#  macOS session-restore (the "two terminals"), and hardcoded an absolute
#  /Users/<name> path so it broke on every other machine.
#
#  Updates the app in place if it already exists (keeps its icon), otherwise
#  builds the bundle from scratch. Uses $HOME (not a hardcoded user) so it works
#  on any box — Sean's, David's, Scott's.
#
#  Usage:  bash scripts/install-startup-app.sh
# ─────────────────────────────────────────────────────────────
set -euo pipefail

APP="/Applications/YRVI Startup.app"
# Honor YRVI_PROJ (setup_docker.sh passes its own $PROJ) so this works even if
# the repo isn't at ~/you_rock_fund.
PROJ="${YRVI_PROJ:-$HOME/you_rock_fund}"
ICON_SRC="$PROJ/assets/YRVI.icns"

mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# ── 1. The bundle executable: launch the headless launcher, detached ──
# nohup + background + fd redirection so the launcher survives this process
# exiting (the app quits immediately, no Dock bouncing, no Terminal).
# Bake the resolved project path in (unquoted heredoc → $PROJ expands now) so the
# app finds the launcher regardless of $HOME quirks or repo location.
cat > "$APP/Contents/MacOS/yrvi_startup" <<EXEC
#!/bin/bash
# YRVI Startup — runs the headless launcher in the background (no Terminal).
export YRVI_PROJ="$PROJ"
nohup /bin/bash "$PROJ/scripts/yrvi-launch.sh" </dev/null >/dev/null 2>&1 &
EXEC
chmod +x "$APP/Contents/MacOS/yrvi_startup"

# ── 2. Info.plist (only written if missing — don't clobber an existing one) ──
if [ ! -f "$APP/Contents/Info.plist" ]; then
    cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key><string>yrvi_startup</string>
    <key>CFBundleIconFile</key><string>YRVI</string>
    <key>CFBundleName</key><string>YRVI Startup</string>
    <key>CFBundleIdentifier</key><string>com.yourockfund.startup</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>LSUIElement</key><true/>
</dict>
</plist>
PLIST
fi
[ -f "$APP/Contents/PkgInfo" ] || printf 'APPL????' > "$APP/Contents/PkgInfo"

# ── 3. Icon: copy into the bundle AND set a custom Finder icon (reliable) ──
if [ -f "$ICON_SRC" ]; then
    cp "$ICON_SRC" "$APP/Contents/Resources/YRVI.icns"
    /usr/bin/osascript -l JavaScript \
        -e 'function run(a){ObjC.import("AppKit"); var i=$.NSImage.alloc.initWithContentsOfFile(a[0]); return $.NSWorkspace.sharedWorkspace.setIconForFileOptions(i,a[1],0);}' \
        "$ICON_SRC" "$APP" >/dev/null 2>&1 || true
fi

# ── 4. Refresh Finder/LaunchServices so the change shows immediately ──
touch "$APP"
xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true

echo "✅ Updated: $APP"
echo "   Double-click 'YRVI Startup' — the dashboard opens in your browser, no Terminal."
