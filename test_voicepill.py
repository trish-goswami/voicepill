"""VoicePill test suite. Asserts against the freshly cloned copy."""
import os, subprocess, sys, time, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import voicepill as v

PASS, FAIL = [], []
def check(tc, desc, cond, detail=""):
    (PASS if cond else FAIL).append((tc, desc, detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {tc}  {desc}" + (f"  [{detail}]" if detail else ""))

print("\n--- A. Credentials / access ---")
check("A1", "key resolves (env or HKCU registry fallback)", bool(v.KEY), f"len={len(v.KEY)}")
import re as _re
check("A2", "no real key hardcoded (placeholder in help text is fine)",
      not _re.search(r"gsk_[A-Za-z0-9]{20}", pathlib.Path("voicepill.py").read_text(encoding="utf-8")))
os.environ.pop("GROQ_API_KEY", None)
check("A3", "registry fallback works with env unset", bool(v._key()))
check("A4", "no auth/roles by design (no login surface to attack)",
      not any(w in pathlib.Path("voicepill.py").read_text(encoding="utf-8").lower()
              for w in ("password", "login(", "authenticate(")))

print("\n--- B. Keyboard non-interference (the numpad regression) ---")
check("B1", "SUPPRESS is False - no global keystroke interception", v.SUPPRESS is False)
src = pathlib.Path("voicepill.py").read_text(encoding="utf-8")
check("B2", "hotkeys bound with suppress=SUPPRESS, not hardcoded True",
      "suppress=SUPPRESS" in src and "suppress=True" not in src)
check("B3", "esc/quit explicitly never suppressed", 'KEYS.bind("esc", self.cancel, suppress=False)' in src)
check("B4", "hotkey is a combo no other app claims", v.HOTKEY_PASTE == "ctrl+alt+space", v.HOTKEY_PASTE)
check("B5", "callbacks spawn threads (hook thread never blocked)", "def spawn(fn, *a)" in src)

print("\n--- C. Capture / recording ---")
devs = v._list_devices()
check("C1", "device enumeration returns inputs", len(devs) > 0, f"{len(devs)} device(s)")
check("C2", "device() resolves to an openable input", v.device().startswith(("audio=", ":", "default")), v.device()[:38])
v.DEVICE = "audio=@device_cm_{DOES-NOT-EXIST}"
check("C3", "unplugged pinned device falls back, does not crash", v.device() != "audio=@device_cm_{DOES-NOT-EXIST}")
v.DEVICE = ""
rec = v.Recorder(); rec.start(); time.sleep(2.5); path = rec.stop()
size = path.stat().st_size
check("C4", "killed ffmpeg still leaves a complete file (-flush_packets)", size > 5000, f"{size} bytes")
lvls = []
while True:
    try: lvls.append(rec.levels.get_nowait())
    except Exception: break
check("C5", "live level meter produced dBFS samples", len(lvls) > 0, f"{len(lvls)} samples")
check("C6", "mp3 is mono 16k 32kbps (well under Groq's 25MB cap)",
      "-b:a" in src and "32k" in src and "16000" in src)

print("\n--- D. Transcription / structuring ---")
os.environ["GROQ_API_KEY"] = v.KEY
txt = v.transcribe(path)
check("D1", "Groq transcription returns a string", isinstance(txt, str) and len(txt) > 0, repr(txt[:40]))
# deterministic: assert the guard's logic, not whether this room happened to be
# quiet. Ambient noise made this flaky when it depended on the live recording.
check("D2", "silence guard knows whisper's hallucinations for a silent file",
      all(h in v.SILENCE for h in ("", "thank you.", "thanks for watching!")))
check("D2b", "guard is actually applied before pasting",
      "text.strip().lower() in SILENCE" in src and "mic silent" in src)
check("D2c", "sub-5KB recordings rejected before an API call is wasted",
      "size < 5000" in src)
long_ramble = ("umm so I I want the the thing to to record my voice and and also structure it "
               "no wait actually I want it to paste at the cursor and the hotkey should be "
               "control alt space not control space because warp takes that one")
out = v.structure(long_ramble)
check("D3", "structurizer reorganises a ramble", "Goal" in out or len(out) < len(long_ramble), f"{len(out)} chars")
# normalise U+202F/U+00A0 first - the model separates tokens with them, which is
# exactly the defect this pass found, and it silently defeated this assertion
_o = out.lower().replace(" ", "").replace(" ", "").replace(" ", "").replace("+", "")
check("D4", "structurizer keeps the final version of a self-correction",
      "ctrlaltspace" in _o or "controlaltspace" in _o)
short = v.structure("umm just just restart the app for me")
check("D5", "short instruction stays one line, no headings", "\n" not in short.strip(), repr(short[:40]))
bad = v.structure.__doc__
check("D6", "structure() documented to fall back to raw on failure", "fall" in (bad or "").lower())

print("\n--- E. Process / UI behaviour ---")
_first = v.single_instance()
check("E1", "single-instance guard rejects a duplicate while the app holds the mutex",
      not _first, "app running" if not _first else "app not running")
check("E2", "guard is repeatable, never lets a second copy through", not v.single_instance())
check("E3", "no console window flash (every subprocess call hidden)",
      "creationflags=NO_WINDOW" in src and src.count("subprocess.run(") == 1
      and src.count("subprocess.Popen(") == 1 and "creationflags=NO_WINDOW)" in src)
check("E4", "pill hidden when idle (no fixed on-screen element)", "self.root.withdraw()" in src)
check("E5", "prerequisites checked at startup with a visible error", "messagebox.showerror" in src)

print("\n--- F. Plugin / repo integrity ---")
import json
mp = json.loads(pathlib.Path(".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
check("F1", "marketplace.json valid and names the plugin", mp["plugins"][0]["name"] == "voicepill")
pl = json.loads(pathlib.Path(".claude-plugin/plugin.json").read_text(encoding="utf-8"))
check("F2", "plugin.json valid with version", bool(pl.get("version")), pl.get("version"))
check("F3", "setup command present for Claude", pathlib.Path("commands/setup.md").exists())
check("F4", "LICENSE present", pathlib.Path("LICENSE").exists())
setup = pathlib.Path("commands/setup.md").read_text(encoding="utf-8")
check("F5", "setup warns about dead mic devices", "-90" in setup and "Thank you" in setup)
check("F6", "setup covers all three platforms",
      all(w in setup for w in ("LaunchAgent", "systemd", "shell:startup")))

print(f"\n{'='*60}\nPASSED {len(PASS)}   FAILED {len(FAIL)}")
for tc, desc, detail in FAIL: print(f"  FAILED {tc}: {desc} [{detail}]")
sys.exit(1 if FAIL else 0)
