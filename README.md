# VoicePill

Push-a-key dictation with a visible recording pill. Windows, macOS, Linux.
Mic → Groq `whisper-large-v3` → `gpt-oss-120b` structurizer → your cursor.

Talk for a minute and you get back a structured prompt, not a wall of transcript.

    ctrl+alt+space    tap to start, tap again to transcribe + paste at the cursor
    ctrl+shift+space  same, but only copies to the clipboard
    ctrl+alt+p        re-paste the last transcript
    esc               cancel and discard the take
    ctrl+alt+q        quit

## Setup with Claude Code (easiest)

This repo is a Claude Code plugin. In any Claude Code session:

    /plugin marketplace add I-am-sayantan/voicepill
    /plugin install voicepill@voicepill-marketplace
    /voicepill:setup

`/voicepill:setup` (`commands/setup.md`) tells Claude to detect the OS, install
ffmpeg and the right hotkey backend, ask for a Groq key, **measure every microphone
and pick one that is not dead**, choose a hotkey that is free on that OS, install
autostart the platform's way, and run the selftest. Everything below is that same
process by hand.

## Platform support

| | capture | hotkey backend | suppresses the key | autostart |
|---|---|---|---|---|
| Windows | ffmpeg dshow | `keyboard` | capable, off by default | Startup shortcut |
| macOS | ffmpeg avfoundation | `pynput` | no | LaunchAgent |
| Linux | ffmpeg pulse | `pynput` | no | systemd user unit |

Only Windows is tested. The macOS and Linux paths are written but unverified - the
capture format, device enumeration, paste key (`command+v` on macOS) and state
directory all branch on `sys.platform`. macOS needs Accessibility **and** Microphone
permission for whatever runs Python, or the hotkey listener silently never fires.
Because `pynput` cannot suppress a keystroke, pick a combo nothing else owns on
macOS/Linux - `ctrl+space` is the IME switcher on many setups.

## Setup by hand

1. **Python 3.10+** (3.12 tested) with tkinter — the python.org installer includes it.
   The Microsoft Store build of Python also works.
2. **ffmpeg** on PATH: `winget install Gyan.FFmpeg`, then open a new terminal.
   `curl.exe` ships with Windows 10/11 already.
3. `pip install -r requirements.txt` — one dependency, chosen by platform
   (`keyboard` on Windows, `pynput` elsewhere).
4. **Groq API key** (free, no card): create one at <https://console.groq.com/keys>, then

       setx GROQ_API_KEY "gsk_..."

   Open a new terminal afterwards. The app also reads it from `HKCU\Environment`, so it
   works when launched by a shell whose environment predates the `setx`.
5. **Pick a microphone.** `DEVICE` at the top of `voicepill.py` is pinned to the author's
   earbuds. Set it to `""` to auto-pick the first input, or run

       python voicepill.py --devices    # lists inputs, prints a DEVICE line to paste
       python voicepill.py --levels     # measures each input for 3s

   and paste the right one in. A pinned device that isn't plugged in falls back to
   whatever is present, so putting earbuds away doesn't break it.
6. `python voicepill.py --check` should print all green, then `python voicepill.py`.

To start it at login, put a shortcut to `pythonw.exe voicepill.py` in `shell:startup`
(working directory = the repo). `pythonw` runs it with no console window.

To ship it as an exe: `pyinstaller --onefile --noconsole --name VoicePill voicepill.py`.
ffmpeg stays external — bundling it adds ~80 MB — and the exe reads the key from the
environment.

## Diagnostics

    python voicepill.py --check      # ffmpeg, curl, key, mic level, groq auth
    python voicepill.py --selftest   # records 2s and round-trips it through Groq
    python voicepill.py --devices    # list inputs
    python voicepill.py --levels     # measure every input

**The level meter in the pill is the microphone diagnostic.** Flat grey bars while you
speak = wrong or unrouted capture device, and no amount of API debugging will fix it.
Run `--levels`: anything reading near -90 dB is a device Windows has stopped routing to.
That is what a connected headset does to a laptop's built-in array.

Whisper returns `"Thank you."` for a silent recording, so VoicePill treats that (and any
sub-5KB file) as `mic silent` rather than pasting it.

## How it works

    mic ──ffmpeg──> take.mp3 ──> POST /audio/transcriptions   whisper-large-v3
                       │                                      prompt = vocab.txt
                       └──> raw PCM on stdout ──> level meter
                                                      │
                            POST /chat/completions  gpt-oss-120b  (structurizer)
                                                      │
                                            clipboard ──> ctrl+alt+space paste

- **`vocab.txt`** is a bias prompt, not an instruction. Whisper conditions on it so
  jargon comes out spelled right. Add any term it mangles — that is the fix, because
  the structurizer is told never to invent and will faithfully carry an error through.
- **The structurizer reorganises, never answers.** Self-corrections collapse to your
  final version; a short instruction stays one plain sentence. Set `STRUCTURE = False`
  to paste whisper's output verbatim.
- Any API failure falls back to the raw transcript. A dictation is never lost.
- The transcript stays on the clipboard after pasting, so plain `ctrl+v` still works.

## Notes for anyone changing the code

- `-flush_packets 1` in the ffmpeg args is **mandatory**. Without it, killing ffmpeg
  leaves a 0-byte file because the ~32KB avio buffer never reaches disk. `--selftest`
  is the regression test.
- Levels come from a second ffmpeg output writing raw PCM to stdout, *not* from
  `astats` + `ametadata=print`. That filter's text stream is block-buffered and only
  flushes at process exit, which never happens here — we stop by killing ffmpeg.
- `SUPPRESS` is **False** by default. Setting it True lets you use a combo another app
  owns (it swallows the key before Warp sees it), but every keystroke on the machine
  then routes through this process, and unrelated keys - the numpad - start dropping.
  Prefer a free combo. `esc` and the quit key are never suppressed either way.
- Hotkey callbacks run on the keyboard hook thread. They only spawn threads — doing
  work there stalls every keystroke on the system.
- tkinter owns the main thread. Workers talk to it through one `queue.Queue` drained by
  `root.after`, and clipboard access happens only on the main thread.

MIT. Groq's free tier has rate limits, not billing.
