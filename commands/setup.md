---
description: Install and configure VoicePill dictation on this machine (Windows, macOS, or Linux)
---

# VoicePill setup

Set up dictation on the machine you are running on. Work through this in order and
report what you did. Do not ask the user to run things you can run yourself.

## 1. Detect the platform

Run `python -c "import sys; print(sys.platform)"`. Everything below branches on
`win32` / `darwin` / `linux`. If Python is missing or older than 3.10, install it
first (`winget install Python.Python.3.12` / `brew install python@3.12` / the distro
package) and say so.

## 2. Prerequisites

- **ffmpeg** must be on PATH. `winget install Gyan.FFmpeg` / `brew install ffmpeg` /
  `sudo apt install ffmpeg`. On Windows a new shell is needed afterwards for PATH.
- **curl** — already present on Windows 10+, macOS, and most Linux.
- **tkinter** — bundled with python.org and Windows builds; on Debian/Ubuntu it is a
  separate package: `sudo apt install python3-tk`.
- **Linux only**: `pactl` (package `pulseaudio-utils`) for device enumeration.
- Then `pip install -r requirements.txt` from the plugin directory. The requirements
  file picks the hotkey backend by platform, so do not install both by hand.

## 3. Groq API key

Ask the user for a key from <https://console.groq.com/keys> (free tier, no card) if
`GROQ_API_KEY` is not already set. Never print the key back, and never write it into
a file inside the repository.

- Windows: `setx GROQ_API_KEY "gsk_..."` — takes effect in new processes. The app also
  reads `HKCU\Environment` directly, so it works even when launched from a shell whose
  environment predates the `setx`.
- macOS/Linux: append `export GROQ_API_KEY="gsk_..."` to `~/.zshrc` or `~/.bashrc`.
  For a GUI/launchd start on macOS, put it in the LaunchAgent plist instead - a
  LaunchAgent does not read your shell rc files.

Verify with `python voicepill.py --check`. It must print `groq: HTTP 200`.

## 4. Pick the microphone - do not skip this

`DEVICE` in `voicepill.py` is pinned to the original author's earbuds and will not
exist on this machine. Run:

    python voicepill.py --levels

That measures every input for 3 seconds and prints its volume. Then:

- Set `DEVICE` to the printed string for the input the user actually speaks into, or
- set `DEVICE = ""` to auto-pick the first input.

**A device reading near -90 dB is dead or unrouted** - Windows does this to a laptop's
built-in array while a headset is connected. Picking such a device produces recordings
that transcribe as `"Thank you."`, which is what Whisper returns for silence. If every
device reads near -90 dB, stop and tell the user their microphone is muted at the OS
level; no code change will fix it.

## 5. Choose a hotkey that is actually free

Default is `ctrl+space`. On Windows the `keyboard` backend *suppresses* it, so the
focused app never sees it. On macOS and Linux `pynput` cannot suppress, so a combo
another app owns will trigger both - and `ctrl+space` is the IME switcher on many
setups. Suggest `ctrl+alt+space` there. Edit `HOTKEY_PASTE` at the top of the file.

macOS also needs **Accessibility** permission (System Settings -> Privacy & Security ->
Accessibility) for the terminal or app running Python, plus **Microphone** permission.
Without Accessibility the listener starts and silently never fires. Tell the user to
grant it; you cannot do it for them.

## 6. Start it, and start it at login

Foreground test first: `python voicepill.py`, then have the user press the hotkey,
speak, press it again. Confirm text appears at their cursor.

Then install autostart for the platform:

- **Windows**: a shortcut in `shell:startup` running `pythonw.exe voicepill.py` with
  the working directory set to the repo. `pythonw` means no console window. Set the
  shortcut's WindowStyle to **1 (normal)**, not 7 - 7 starts it minimised off-screen.
- **macOS**: a LaunchAgent at `~/Library/LaunchAgents/com.voicepill.plist` with
  `RunAtLoad`, `ProgramArguments` = the python binary plus the script path, and
  `EnvironmentVariables` carrying `GROQ_API_KEY` and a `PATH` that includes ffmpeg.
  `launchctl load` it.
- **Linux**: a `~/.config/systemd/user/voicepill.service` with
  `ExecStart=/usr/bin/python3 <path>/voicepill.py` and
  `Environment=GROQ_API_KEY=...`, then `systemctl --user enable --now voicepill`.
  On a headless or Wayland session, global hotkeys may not reach the app at all - say
  so rather than pretending it works.

## 7. Verify and report

Run `python voicepill.py --selftest`. It records 2 seconds, asserts the file grew past
5 KB, and round-trips it through Groq. Report:

- platform, python version, ffmpeg path
- chosen device and its measured dB
- hotkey and backend (`keyboard` = suppressing, `pynput` = not)
- autostart mechanism installed
- `--check` and `--selftest` output

If the selftest warns that Whisper heard silence, that is the microphone, not the
setup - return to step 4.
