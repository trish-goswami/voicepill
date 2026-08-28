"""VoicePill - toggle-hotkey dictation with a visible recording pill.

  ctrl+space        tap to start, tap again to transcribe + paste at cursor
  ctrl+shift+space  same, but copy only (no paste)
  ctrl+alt+p        re-paste the last transcript
  esc               cancel and discard the current take
  ctrl+alt+q        quit

Needs: ffmpeg + curl.exe on PATH, GROQ_API_KEY in the environment, pip install keyboard.
"""

import array
import math
import os
import pathlib
import queue
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk

if sys.platform == 'win32':
    import ctypes


# --- hotkey backend -------------------------------------------------------
class Keys:
    """Global hotkeys and synthetic paste, on whichever backend the OS has.

    Windows uses `keyboard`, the only one that can *suppress* a key so the
    focused app never sees it (needed because Warp and VS Code both act on
    ctrl+space). macOS and Linux use `pynput`, which cannot suppress - pick a
    combo nothing else owns there. macOS additionally needs Accessibility
    permission for the terminal running this, or the listener silently sees
    nothing.
    """

    def __init__(self):
        self.pending = {}
        if sys.platform == "win32":
            import keyboard
            self.lib, self.name = keyboard, "keyboard"
        else:
            from pynput import keyboard as pk
            self.lib, self.name = pk, "pynput"
            self.listener = None

    @staticmethod
    def _combo(spec: str) -> str:
        """'ctrl+shift+space' -> '<ctrl>+<shift>+<space>' for pynput."""
        out = []
        for k in spec.split("+"):
            k = k.strip().lower()
            out.append(f"<{k}>" if len(k) > 1 else k)
        return "+".join(out)

    def bind(self, spec, fn, suppress=False):
        if self.name == "keyboard":
            self.lib.add_hotkey(spec, fn, suppress=suppress)
        else:
            self.pending[self._combo(spec)] = fn   # started later, all at once

    def start(self):
        if self.name == "pynput" and self.pending:
            self.listener = self.lib.GlobalHotKeys(self.pending)
            self.listener.daemon = True
            self.listener.start()

    def send(self, spec):
        if self.name == "keyboard":
            self.lib.send(spec)
            return
        ctl = self.lib.Controller()
        keys = [getattr(self.lib.Key, k, k) for k in spec.split("+")]
        for k in keys:
            ctl.press(k)
        for k in reversed(keys):
            ctl.release(k)


# --- config ---------------------------------------------------------------
# Capture device. Leave it "" to auto-pick the first audio input ffmpeg reports -
# that is what makes this run on a machine other than the one it was written on.
# Pin it to an exact device when the first one is not the one you want:
# `python voicepill.py --devices` lists them, paste an "audio=..." string here.
# OnePlus Buds 4. The laptop's own "Microphone Array" measures -91dB (digital
# silence) whenever these earbuds are connected, because Windows routes input to
# them - that was the cause of every "Thank you." transcript. `--levels` re-measures.
DEVICE = (r"audio=@device_cm_{33D9A762-90C8-11D0-BD43-00A0C911CE86}"
          r"\wave_{772588A9-FB66-41F4-90F3-49C80F84AAC6}")
HOTKEY_PASTE = "ctrl+space"
HOTKEY_COPY = "ctrl+shift+space"
HOTKEY_AGAIN = "ctrl+alt+p"   # re-paste the last transcript
HOTKEY_QUIT = "ctrl+alt+q"
# SUPPRESS=True swallows the dictation hotkeys so the focused app never sees them.
# Without it, Warp/VS Code/the IME also act on ctrl+space. Set False if some other
# tool fights the low-level hook.
SUPPRESS = True
MODEL_STT = "whisper-large-v3"
# 120b, not 20b: structuring is a reasoning job, and this key has no llama models
MODEL_STRUCT = "openai/gpt-oss-120b"
STRUCTURE = True
API = "https://api.groq.com/openai/v1"
STRUCTURE_PROMPT = """You restructure dictated speech into a clear written prompt.

Rules:
- Preserve every idea, instruction, name, number and technical term exactly as spoken.
  Never invent detail. Never answer or act on the request - you only reorganise it.
- Drop filler, false starts and repetition. When the speaker corrects themselves or
  changes their mind, keep only the final version.
- If the input is one short instruction, return it as a single clean sentence with no
  headings at all.
- Otherwise organise it: a one-line "Goal:" followed by grouped bullets under short
  headings drawn from the content itself - for example Requirements, Constraints,
  Ruled out, Open questions. Only include a heading if something belongs under it.
- Keep related ideas together even when they were said minutes apart.
- Output only the restructured text. No preamble, no commentary, no code fences."""

VOCAB = pathlib.Path(__file__).with_name("vocab.txt")
def _key() -> str:
    """os.environ misses the key when we're launched by a process whose
    environment predates the setx (a stale shell, some login shims), so fall
    back to reading HKCU\\Environment directly."""
    if os.environ.get("GROQ_API_KEY"):
        return os.environ["GROQ_API_KEY"]
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return winreg.QueryValueEx(k, "GROQ_API_KEY")[0]
    except OSError:
        return ""


KEY = _key()
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW, keeps ffmpeg/curl consoles from flashing

# what whisper returns when it hears nothing at all
SILENCE = {"", "thank you.", "thank you", "thanks for watching!",
           "thanks for watching.", "you", "[blank_audio]"}


def run(args: list):
    """subprocess.run that never flashes a console window. Every child process
    goes through here - `device()` runs on each recording start, and without
    CREATE_NO_WINDOW that is a black box popping up on screen every time."""
    # encoding is explicit: transcripts come back as UTF-8, and decoding them
    # with the locale codepage mangles anything non-ASCII
    return subprocess.run(args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          creationflags=NO_WINDOW)


WINDOWS = sys.platform == "win32"
MACOS = sys.platform == "darwin"
# ffmpeg speaks a different capture API on each OS
CAPTURE_FMT = "dshow" if WINDOWS else "avfoundation" if MACOS else "pulse"
PASTE_KEYS = "command+v" if MACOS else "ctrl+v"


KEYS = Keys()


def state_dir() -> pathlib.Path:
    if WINDOWS:
        return pathlib.Path(os.environ["LOCALAPPDATA"]) / "voicepill"
    if MACOS:
        return pathlib.Path.home() / "Library" / "Application Support" / "voicepill"
    return pathlib.Path(os.environ.get("XDG_STATE_HOME",
                                       pathlib.Path.home() / ".local" / "state")) / "voicepill"


DIR = state_dir()
MP3 = DIR / "take.mp3"


def single_instance() -> bool:
    """False if another VoicePill is already running. Two instances would both
    grab the hotkey and both record, so the second one just exits."""
    if WINDOWS:
        k32 = ctypes.windll.kernel32
        k32.CreateMutexW(None, False, "VoicePill_single")
        # ctypes.get_last_error() needs use_last_error=True to be populated, so
        # ask the API itself. 183 = ERROR_ALREADY_EXISTS.
        return k32.GetLastError() != 183
    # posix: an exclusive flock on a file in the state dir, released on exit
    import fcntl
    state_dir().mkdir(parents=True, exist_ok=True)
    global _LOCK
    _LOCK = open(state_dir() / "lock", "w")
    try:
        fcntl.flock(_LOCK, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        sys.exit("ffmpeg not found on PATH")
    return exe


def _list_devices() -> list:
    """[(friendly name, ffmpeg input string), ...] for every audio input.

    The three OSes report devices completely differently, so each gets its own
    parser. macOS/Linux inputs are indexes or PulseAudio names, not GUIDs.
    """
    if WINDOWS:
        r = run([ffmpeg(), "-hide_banner", "-list_devices", "true",
                 "-f", "dshow", "-i", "dummy"])
        lines = (r.stderr or "").splitlines()
        out = []
        for i, ln in enumerate(lines):
            if "(audio)" not in ln:
                continue
            name = re.search(r'"([^"]+)"', ln)
            alt = None
            for nxt in lines[i + 1:i + 3]:
                m = re.search(r'Alternative name "([^"]+)"', nxt)
                if m:
                    alt = m.group(1)
                    break
            if name:
                out.append((name.group(1), "audio=" + (alt or name.group(1))))
        return out
    if MACOS:
        r = run([ffmpeg(), "-hide_banner", "-f", "avfoundation",
                 "-list_devices", "true", "-i", ""])
        out, audio = [], False
        for ln in (r.stderr or "").splitlines():
            if "AVFoundation audio devices" in ln:
                audio = True
                continue
            if audio:
                m = re.search(r"\[(\d+)\]\s+(.+)$", ln)
                if m:
                    out.append((m.group(2).strip(), f":{m.group(1)}"))
        return out
    # linux: PulseAudio sources
    r = run(["pactl", "list", "short", "sources"])
    out = []
    for ln in (r.stdout or "").splitlines():
        parts = ln.split("	")
        if len(parts) > 1:
            out.append((parts[1], parts[1]))
    return out or [("default", "default")]


def device() -> str:
    """DEVICE if pinned, else the first audio input on this machine. The
    alternative name is preferred - friendly names contain characters like the
    (R) in "Intel(R) Smart Sound" that get mangled on the command line."""
    found = _list_devices()
    if not found:
        sys.exit(f"no {CAPTURE_FMT} audio input devices found")
    # a pinned device that is not plugged in right now (earbuds put away) would
    # just fail to open, so fall back to whatever is present
    if DEVICE and any(inp == DEVICE for _n, inp in found):
        return DEVICE
    return found[0][1]


# --- capture --------------------------------------------------------------
class Recorder:
    """ffmpeg -> mono 16k 32kbps mp3, plus a live RMS feed for the meter."""

    def __init__(self):
        self.proc = None
        self.levels = queue.Queue(maxsize=8)

    def start(self):
        DIR.mkdir(parents=True, exist_ok=True)
        args = [
            ffmpeg(), "-hide_banner", "-loglevel", "error",
            "-f", CAPTURE_FMT, "-i", device(),
            # output 1: the file we actually send to Groq.
            # -flush_packets is mandatory: without it a killed ffmpeg leaves a
            # 0-byte file, because the ~32KB avio buffer never reaches disk.
            "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "32k",
            "-flush_packets", "1", "-y", str(MP3),
            # output 2: raw PCM on stdout, purely to drive the level meter.
            # (astats+ametadata was the obvious way to get levels, but ffmpeg
            # block-buffers that text stream and only flushes it at exit - which
            # never happens, because we stop by killing the process.)
            "-f", "s16le", "-ac", "1", "-ar", "16000", "pipe:1",
        ]
        self.proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=NO_WINDOW)
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        """Read 100ms of PCM at a time, push its peak level in dBFS."""
        proc = self.proc
        while True:
            chunk = proc.stdout.read(3200)   # 1600 samples * 2 bytes
            if not chunk:
                return
            peak = max(max(samples := array.array("h", chunk)), -min(samples))
            db = -96.0 if peak < 2 else 20 * math.log10(peak / 32768)
            try:
                self.levels.put_nowait(db)
            except queue.Full:
                pass

    def stop(self) -> pathlib.Path:
        if self.proc:
            self.proc.kill()
            self.proc.wait(timeout=5)
            self.proc = None
        return MP3


# --- groq -----------------------------------------------------------------
def _curl(args: list) -> str:
    r = run(["curl.exe", "-s", "--max-time", "180"] + args)
    return (r.stdout or "").strip()


def transcribe(path: pathlib.Path) -> str:
    vocab = VOCAB.read_text(encoding="utf-8").strip() if VOCAB.exists() else ""
    out = _curl([f"{API}/audio/transcriptions",
                 "-H", f"Authorization: Bearer {KEY}",
                 "-F", f"file=@{path}",
                 "-F", f"model={MODEL_STT}",
                 "-F", "language=en",
                 "-F", "response_format=text",
                 "-F", f"prompt={vocab}"])
    if out.startswith("{"):          # errors come back as JSON, not text
        raise RuntimeError(out[:160])
    return out.strip()


def structure(text: str) -> str:
    """Reorganise a ramble into a structured prompt. Any failure falls back to
    the raw transcript - a dictation is never lost to a bad API call."""
    import json
    payload = DIR / "structure.json"
    payload.write_text(json.dumps({
        "model": MODEL_STRUCT,
        "messages": [{"role": "system", "content": STRUCTURE_PROMPT},
                     {"role": "user", "content": text}],
        "temperature": 0.2,
    }), encoding="utf-8")
    out = _curl([f"{API}/chat/completions",
                 "-H", f"Authorization: Bearer {KEY}",
                 "-H", "Content-Type: application/json",
                 "--data-binary", f"@{payload}"])
    try:
        return json.loads(out)["choices"][0]["message"]["content"].strip() or text
    except Exception:
        return text


# --- pill -----------------------------------------------------------------
BG, FG, DIM = "#16181d", "#e8eaed", "#6b7280"
RED, AMBER, GREEN = "#ef4444", "#f59e0b", "#22c55e"
BARS = 7


class Pill:
    def __init__(self, root):
        self.root = root
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.93)
        root.configure(bg=BG)
        w, h = 250, 56
        x = (root.winfo_screenwidth() - w) // 2
        y = root.winfo_screenheight() - h - 90   # clear the taskbar
        root.geometry(f"{w}x{h}+{x}+{y}")
        self.c = tk.Canvas(root, width=w, height=h, bg=BG,
                           highlightthickness=0, bd=0)
        self.c.pack()
        self._round_rect(1, 1, w - 1, h - 1, 26, fill="#1f2229", outline="#2c313a")
        self.dot = self.c.create_oval(22, 24, 34, 36, fill=RED, outline="")
        self.label = self.c.create_text(46, 30, text="REC", anchor="w",
                                        fill=FG, font=("Segoe UI", 11, "bold"))
        self.clock = self.c.create_text(92, 30, text="0:00", anchor="w",
                                        fill=DIM, font=("Consolas", 11))
        self.bars = [self.c.create_rectangle(140 + i * 13, 32, 148 + i * 13, 36,
                                            fill=DIM, outline="")
                     for i in range(BARS)]
        root.withdraw()

    def _round_rect(self, x1, y1, x2, y2, r, **kw):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
               x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return self.c.create_polygon(pts, smooth=True, **kw)

    def show(self):
        self.root.deiconify()
        self.root.attributes("-topmost", True)

    def hide(self):
        self.root.withdraw()

    def set(self, text, color, clock="", meter=True):
        self.c.itemconfig(self.label, text=text)
        self.c.itemconfig(self.dot, fill=color)
        self.c.itemconfig(self.clock, text=clock)
        for b in self.bars:
            self.c.itemconfig(b, state="normal" if meter else "hidden")

    def blink(self, on):
        self.c.itemconfig(self.dot, fill=RED if on else "#7f1d1d")

    def meter(self, history):
        """history: list of dB values, oldest first. -60dB..0dB -> bar height."""
        for bar, db in zip(self.bars, history):
            frac = 0.0 if db is None else max(0.0, min(1.0, (db + 60) / 60))
            h = 3 + frac * 20
            x1, _, x2, _ = self.c.coords(bar)
            self.c.coords(bar, x1, 34 - h / 2, x2, 34 + h / 2)
            self.c.itemconfig(bar, fill=GREEN if frac > 0.12 else DIM)


# --- app ------------------------------------------------------------------
class App:
    def __init__(self):
        # pythonw has no console, so a bare sys.exit here would die invisibly.
        # Check both prerequisites up front rather than at first keypress.
        problem = ("GROQ_API_KEY is not set.\n\nRun:  setx GROQ_API_KEY gsk_..."
                   if not KEY else
                   "ffmpeg is not on PATH." if not shutil.which("ffmpeg") else "")
        if problem:
            from tkinter import messagebox
            messagebox.showerror("VoicePill", problem)
            sys.exit(problem)
        self.root = tk.Tk()
        self.pill = Pill(self.root)
        self.rec = Recorder()
        self.ui = queue.Queue()
        self.recording = False
        self.paste = True
        self.last = ""
        self.elapsed = 0
        self.blink_on = True
        self.history = [None] * BARS
        self.lock = threading.Lock()

        # hotkey callbacks run on the hook thread and must return fast, or they
        # stall every keystroke - so they only spawn work, never do it
        def spawn(fn, *a):
            return lambda: threading.Thread(target=fn, args=a, daemon=True).start()

        KEYS.bind(HOTKEY_PASTE, spawn(self.toggle, True), suppress=SUPPRESS)
        KEYS.bind(HOTKEY_COPY, spawn(self.toggle, False), suppress=SUPPRESS)
        KEYS.bind(HOTKEY_AGAIN, spawn(self.again), suppress=SUPPRESS)
        # esc and quit are never suppressed - swallowing esc globally would be rude
        KEYS.bind("esc", self.cancel, suppress=False)
        KEYS.bind(HOTKEY_QUIT, self.quit, suppress=False)
        KEYS.start()

        self.root.after(100, self.tick)
        self.root.after(50, self.drain)

    # hotkey thread ------------------------------------------------------
    def toggle(self, paste):
        with self.lock:
            if self.recording:
                self.recording = False
                path = self.rec.stop()
                self.ui.put(("working", None))
                threading.Thread(target=self.finish, args=(path,), daemon=True).start()
            else:
                self.paste = paste
                self.elapsed = 0
                self.history = [None] * BARS
                self.recording = True
                self.rec.start()
                self.ui.put(("recording", None))

    def again(self):
        """Re-paste the last transcript, for when auto-paste landed nowhere."""
        self.ui.put(("again", None) if self.last else ("error", "nothing to paste"))

    def cancel(self):
        with self.lock:
            if not self.recording:
                return
            self.recording = False
            self.rec.stop()
            self.ui.put(("idle", None))

    def quit(self):
        self.ui.put(("quit", None))

    # worker thread ------------------------------------------------------
    def finish(self, path):
        try:
            size = path.stat().st_size if path.exists() else 0
            if size < 5000:
                return self.ui.put(("error", "mic silent"))
            text = transcribe(path)
            if text.strip().lower() in SILENCE:
                return self.ui.put(("error", "mic silent"))
            if STRUCTURE:
                text = structure(text)
            self.ui.put(("done", text))
        except Exception as exc:
            self.ui.put(("error", str(exc)[:34]))

    # main thread --------------------------------------------------------
    def drain(self):
        while True:
            try:
                state, payload = self.ui.get_nowait()
            except queue.Empty:
                break
            if state == "recording":
                self.pill.set("REC", RED, "0:00")
                self.pill.show()
            elif state == "working":
                self.pill.set("transcribing", AMBER, "", meter=False)
            elif state == "done":
                self.deliver(payload)
            elif state == "again":
                self.to_clipboard(self.last)
                KEYS.send(PASTE_KEYS)
                self.pill.set(f"re-pasted {len(self.last)} chars", GREEN, "",
                              meter=False)
                self.pill.show()
                self.root.after(1400, self.pill.hide)
            elif state == "error":
                self.pill.set(payload, RED, "", meter=False)
                self.pill.show()
                self.root.after(3000, self.pill.hide)
            elif state == "idle":
                self.pill.hide()
            elif state == "quit":
                self.root.destroy()
                return
        self.root.after(50, self.drain)

    def deliver(self, text):
        self.last = text
        self.to_clipboard(text)
        if self.paste:
            KEYS.send(PASTE_KEYS)
        self.pill.set(f"{len(text)} chars" + ("" if self.paste else " copied"),
                      GREEN, "", meter=False)
        self.root.after(1400, self.pill.hide)

    def to_clipboard(self, text):
        """The transcript stays on the clipboard - no restoring the previous
        contents, so ctrl+v keeps working for as long as you need it."""
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()

    def tick(self):
        if self.recording:
            self.elapsed += 1
            self.blink_on = not self.blink_on
            self.pill.blink(self.blink_on)
            self.pill.set("REC", RED if self.blink_on else "#7f1d1d",
                          f"{self.elapsed // 10}:{(self.elapsed % 10) * 6:02d}")
            latest = None
            while True:
                try:
                    latest = self.rec.levels.get_nowait()
                except queue.Empty:
                    break
            if latest is None:      # no fresh sample this tick, hold the last one
                latest = self.history[-1]
            self.history = self.history[1:] + [latest]
            self.pill.meter(self.history)
        self.root.after(100, self.tick)

    def run(self):
        self.root.mainloop()


# --- cli ------------------------------------------------------------------
def devices():
    found = _list_devices()
    active = device()
    for i, (name, inp) in enumerate(found):
        mark = "*" if inp == active else " "
        print(f"{mark} [{i}] {name}")
        print(f'      DEVICE = r"{inp}"')
    print('\n* = in use. Set DEVICE = "" in voicepill.py to auto-pick [0].')


def levels():
    """Measure every input for 3s. Run this whenever a headset comes or goes -
    a device Windows has stopped routing to reads about -90dB."""
    active = device()
    for i, (name, inp) in enumerate(_list_devices()):
        r = run([ffmpeg(), "-hide_banner", "-f", CAPTURE_FMT, "-i", inp,
                 "-t", "3", "-af", "volumedetect", "-f", "null", "NUL"])
        got = [ln.split("] ", 1)[-1] for ln in (r.stderr or "").splitlines()
               if "_volume:" in ln]
        mark = "*" if inp == active else " "
        print(f"{mark} [{i}] {name}")
        print(f"      {'  '.join(got) if got else 'could not open'}")
    print('\n* = in use. Anything near -90dB is a dead/unrouted device.')


def check():
    print("ffmpeg:", ffmpeg())
    print("curl:  ", shutil.which("curl.exe") or "MISSING")
    print("key:   ", "set" if KEY else "MISSING")
    r = run([ffmpeg(), "-hide_banner", "-f", CAPTURE_FMT, "-i", device(),
             "-t", "3", "-af", "volumedetect", "-f", "null", "NUL"])
    for line in (r.stderr or "").splitlines():
        if "volume:" in line:
            print("mic:   ", line.split("] ", 1)[-1])
    if "max_volume" not in (r.stderr or ""):
        sys.exit("could not open capture device")
    code = _curl(["-o", "NUL", "-w", "%{http_code}", f"{API}/models",
                  "-H", f"Authorization: Bearer {KEY}"])
    print("groq:   HTTP", code)
    if code != "200":
        sys.exit("groq auth failed")


def selftest():
    """Records 2s and round-trips it through Groq. Regression test for the
    -flush_packets bug (a killed ffmpeg used to leave a 0-byte file)."""
    rec = Recorder()
    rec.start()
    import time
    time.sleep(2.5)
    path = rec.stop()
    size = path.stat().st_size
    print(f"recorded {size} bytes")
    assert size > 5000, f"recording too small ({size} bytes) - flush_packets regression?"
    text = transcribe(path)
    print(f"transcript: {text!r}")
    assert isinstance(text, str) and text, "empty transcript"
    if text.strip().lower() in SILENCE:
        print("WARNING: whisper heard silence - the capture device is wrong or muted")
    print("selftest OK")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--check":
        check()
    elif arg == "--selftest":
        selftest()
    elif arg == "--devices":
        devices()
    elif arg == "--levels":
        levels()
    else:
        if not single_instance():
            sys.exit("VoicePill is already running")
        print(f"VoicePill running. {HOTKEY_PASTE} = dictate+paste, "
              f"{HOTKEY_COPY} = copy only, esc = cancel, ctrl+alt+q = quit")
        App().run()
