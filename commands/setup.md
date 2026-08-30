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
Accessibility), plus **Microphone** permission. Without Accessibility the listener
starts and silently never fires. Tell the user to grant it; you cannot do it for them.

Which Accessibility entry depends on how it was started, and getting this wrong costs
an hour:

- **Started from a terminal**: grant the terminal app. The python process inherits
  that grant, because a child process is attributed to whoever is responsible for it.
- **Started by launchd** (step 6): the job is its own TCC subject and inherits
  nothing. Granting the interpreter does not work - Homebrew and python.org builds
  are ad-hoc signed with an identifier like `Python-555549445dca...`, which carries no
  stable designated requirement for TCC to match. The row appears in the list, the
  toggle turns on, and the process still logs `This process is not trusted!`. Use
  `install-macos-autostart.sh`, which gives it a bundle identity to match.

`AXIsProcessTrusted()` is the fast way to tell which situation you are in - run it
under launchd, not from your shell, or you will just measure the terminal's grant:

    python -c "import ctypes,ctypes.util; ax=ctypes.cdll.LoadLibrary(ctypes.util.find_library('ApplicationServices')); ax.AXIsProcessTrusted.restype=ctypes.c_bool; print(ax.AXIsProcessTrusted())"

## 6. Start it, and start it at login

Foreground test first: `python voicepill.py`, then have the user press the hotkey,
speak, press it again. Confirm text appears at their cursor.

Then install autostart for the platform:

- **Windows**: a shortcut in `shell:startup` running `pythonw.exe voicepill.py` with
  the working directory set to the repo. `pythonw` means no console window. Set the
  shortcut's WindowStyle to **1 (normal)**, not 7 - 7 starts it minimised off-screen.
- **macOS**: run `GROQ_API_KEY=gsk_... ./install-macos-autostart.sh <python>`, passing
  the interpreter that has `pynput` (the venv one, if you made a venv). It builds
  `VoicePill.app`, writes `~/Library/LaunchAgents/com.voicepill.plist` (chmod 600 - it
  holds the key, and a LaunchAgent does not read shell rc files), and bootstraps it.
  Re-running is safe: it leaves an already-signed bundle alone, so the Accessibility
  grant survives.

  Check where the checkout lives first. launchd jobs get no access to `~/Documents`,
  `~/Desktop` or `~/Downloads`, so a checkout under any of those cannot be
  autostarted at all - the job is blocked at spawn with no error written anywhere,
  and even reading the script from there hangs the process. Move the checkout
  somewhere else rather than granting Full Disk Access to work around it.

  Do not hand-write a plist pointing at `python3`. It bootstraps fine and then never
  fires - see the TCC note in step 5. The script copies the framework interpreter into
  a bundle with a stable `CFBundleIdentifier`, ad-hoc signs *that*, and points launchd
  at it, with `PYTHONHOME` aimed back at the stdlib and `PYTHONPATH` at site-packages.
  The user then grants Accessibility to `VoicePill.app` - narrower than granting the
  interpreter, which would cover every python script on the machine.
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
