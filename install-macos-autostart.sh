#!/bin/sh
# Install VoicePill autostart on macOS.
#
# Why this is not just a plist pointing at python: under launchd a process is its
# own TCC subject, and Homebrew/python.org interpreters are ad-hoc signed with an
# identifier like "Python-555549445dca..." that carries no stable designated
# requirement. Accessibility silently refuses to match it, so the pynput listener
# starts, logs "This process is not trusted!", and the hotkey never fires - even
# though the very same interpreter reads as trusted when you run it from a
# terminal, because there it inherits the terminal's own grant.
#
# So we copy the framework interpreter into a real bundle with a stable
# CFBundleIdentifier, ad-hoc sign that, and let launchd run the bundle. TCC then
# has something to match, and the grant covers VoicePill alone instead of every
# python script on the machine.
#
# usage: GROQ_API_KEY=gsk_... ./install-macos-autostart.sh [/path/to/python]
set -e

PY=${1:-$(command -v python3)}
[ -x "$PY" ] || { echo "no python found - pass one as \$1"; exit 1; }
[ -n "$GROQ_API_KEY" ] || { echo "GROQ_API_KEY is not set"; exit 1; }

HERE=$(cd "$(dirname "$0")" && pwd)
APP=$HERE/VoicePill.app
PLIST=$HOME/Library/LaunchAgents/com.voicepill.plist

# sys.base_prefix, not sys.prefix: inside a venv the framework lives at the base
HOME_PY=$("$PY" -c 'import sys; print(sys.base_prefix)')
SITE=$("$PY" -c 'import site; print(site.getsitepackages()[0])')
SRC=$HOME_PY/Resources/Python.app/Contents/MacOS/Python
# non-framework builds have no Python.app; their bin/python3 is already the real
# Mach-O, and copying it works the same way
[ -f "$SRC" ] || SRC=$HOME_PY/bin/python3
[ -f "$SRC" ] || { echo "cannot find the interpreter binary under $HOME_PY"; exit 1; }

# Leave a bundle that already carries the right identity alone. Rebuilding from
# unchanged inputs reproduces the same cdhash and the Accessibility grant does
# survive - measured, including a move to a different directory. But a rebuild
# after the interpreter changes underneath (a python upgrade, a different
# install) produces a different binary, and there the grant is not guaranteed to
# carry over. Skipping the rebuild costs nothing and removes the question.
if codesign -dv "$APP" 2>&1 | grep -q "Identifier=com.voicepill.app"; then
    echo "VoicePill.app already built and signed - keeping it (and its TCC grant)"
else

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp "$SRC" "$APP/Contents/MacOS/VoicePill"

cat > "$APP/Contents/Info.plist" <<'PLIST_EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>VoicePill</string>
  <key>CFBundleDisplayName</key><string>VoicePill</string>
  <key>CFBundleExecutable</key><string>VoicePill</string>
  <key>CFBundleIdentifier</key><string>com.voicepill.app</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0.0</string>
  <key>CFBundleVersion</key><string>1.0.0</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <!-- agent app: no Dock icon, no menu bar, but still a real GUI session member
       so tkinter can draw the pill -->
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>VoicePill records your voice to transcribe it.</string>
</dict>
</plist>
PLIST_EOF

# --identifier pins the signature to the bundle id rather than the copied
# binary's name, so the TCC grant survives rebuilds of this bundle
codesign --force --sign - --identifier com.voicepill.app "$APP"

fi

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.voicepill</string>
  <key>ProgramArguments</key>
  <array>
    <string>$APP/Contents/MacOS/VoicePill</string>
    <string>$HERE/voicepill.py</string>
  </array>
  <key>WorkingDirectory</key><string>$HERE</string>
  <key>EnvironmentVariables</key>
  <dict>
    <!-- a LaunchAgent does not read your shell rc files -->
    <key>GROQ_API_KEY</key><string>$GROQ_API_KEY</string>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <!-- the copied interpreter has no framework around it: point it back at the
         stdlib, and at wherever pynput was installed -->
    <key>PYTHONHOME</key><string>$HOME_PY</string>
    <key>PYTHONPATH</key><string>$SITE</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><false/>
  <key>StandardOutPath</key><string>$HERE/voicepill.log</string>
  <key>StandardErrorPath</key><string>$HERE/voicepill.err</string>
</dict>
</plist>
PLIST_EOF
chmod 600 "$PLIST"   # it holds the API key

launchctl bootout "gui/$(id -u)/com.voicepill" 2>/dev/null || true
# bootout is asynchronous; bootstrapping over a job still shutting down fails
# with "Bootstrap failed: 5: Input/output error"
i=0
while launchctl print "gui/$(id -u)/com.voicepill" >/dev/null 2>&1 && [ $i -lt 10 ]; do
    sleep 1
    i=$((i + 1))
done
: > "$HERE/voicepill.err"
launchctl bootstrap "gui/$(id -u)" "$PLIST"
sleep 4

echo
echo "installed: $APP"
echo "launchagent: $PLIST"

PID=$(launchctl print "gui/$(id -u)/com.voicepill" 2>/dev/null | awk '/pid = /{print $3}')
if grep -q "not trusted" "$HERE/voicepill.err" 2>/dev/null; then
    echo
    echo "NEXT: grant Accessibility to VoicePill, or the hotkey will never fire."
    echo "  System Settings -> Privacy & Security -> Accessibility -> +"
    echo "  then Cmd-Shift-G and paste:  $APP"
    echo "Remove any older Python / python3.14 rows - those never applied."
    echo "Then re-run this script to restart and re-check."
elif [ -z "$PID" ]; then
    # an empty error log is not success on its own: the single-instance guard
    # and a missing ffmpeg both exit quietly enough to look like a clean start
    echo
    echo "WARNING: it did not stay running. Error log says:"
    sed 's/^/  /' "$HERE/voicepill.err" 2>/dev/null
    exit 1
else
    echo "Accessibility OK - listener is live (pid $PID)."
fi
