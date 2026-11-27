#!/usr/bin/env python3
"""EchoSpeech-style data collection: participants, numbered sessions, live echo picture.

    .venv/bin/python experiments/echospeech/collect_server.py         # open http://localhost:8765
    .venv/bin/python experiments/echospeech/collect_server.py --dry   # no Teensy (testing the page)

The Teensy must run experiments/split_bands/split_bands.ino (speaker 0 18-24 kHz, speaker 1 25-31 kHz).

Protocol (after EchoSpeech CHI '23, section 6): a session prompts each command of the chosen set
`reps` times in random order (default all 31 x 4 = 124). Push-to-record: space starts a take,
space again ends it (auto-end after 4 s), x redoes the current command, p pauses, q ends the
session early and saves it. Remount the glasses between sessions; mark the first one as practice.

Layout (git-ignored):
  captures/echospeech/<person>/s<NN>_<YYYYmmdd_HHMMSS>[_practice]/
      raw_words.npy   interleaved uint16 PDM words (mic 2/pin 8, mic 1/pin 6, ...), 200k words/s per mic
      events.json     person, session number, command set and order, settings, and every take with
                      start/end in 100 kHz audio samples from the start of raw_words
  captures/echospeech/_trash/...   deleted sessions (moved here, not erased)
"""
import argparse
import base64
import json
import queue
import random
import re
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(HERE))
from commands import ALL, COMMANDS  # noqa: E402
from live_view import LiveView  # noqa: E402

OUT = ROOT / "captures" / "echospeech"
TRASH = OUT / "_trash"
LEVEL_PERMILLE = 100  # 10%
MAX_TAKE_S = 4.0
COMMAND_SETS = {"all 31": ALL, **{k: v for k, v in COMMANDS.items()}}
RECOG = None  # live_recog.Recognizer when --model is given


def git_commit():
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return "unknown"


def safe_id(s):
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", (s or "").strip())
    return s[:40]


class Recorder:
    """Owns the serial port during a session; counts words so takes can be time-stamped."""

    def __init__(self, dry=False):
        self.dry = dry
        self.words_per_mic = 0
        self.chunks = []
        self.running = False
        self.lock = threading.Lock()
        self.thread = None
        self.error = None
        self.live = LiveView()
        self.q = queue.Queue()
        threading.Thread(target=self._live_worker, daemon=True).start()

    def _live_worker(self):
        while True:
            w = self.q.get()
            try:
                self.live.feed(w)
            except Exception as e:  # the display must never break the recording
                self.error = "live view: " + str(e)

    def start(self):
        self.words_per_mic, self.chunks, self.running, self.error = 0, [], True, None
        with self.q.mutex:
            self.q.queue.clear()
        self.live.reset()
        self.thread = threading.Thread(target=self._run_dry if self.dry else self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        return np.concatenate(self.chunks) if self.chunks else np.zeros(0, np.uint16)

    def words_so_far(self):
        with self.lock:
            chunks = list(self.chunks)
        return np.concatenate(chunks) if chunks else np.zeros(0, np.uint16)

    def now_sample(self):
        with self.lock:
            return self.words_per_mic // 2  # 32 PDM bits per 100 kHz sample, 16 bits per word

    def _run_dry(self):
        t0, rng = time.time(), np.random.default_rng(0)
        while self.running:
            time.sleep(0.05)
            w = rng.integers(0, 65536, 20_000, dtype=np.uint16)
            with self.lock:
                self.chunks.append(w)
                self.words_per_mic = int((time.time() - t0) * 200_000)
            self.q.put(w)

    def _run(self):
        import serial
        from mic_capture import HDR, MAGIC, find_port
        try:
            with serial.Serial(find_port(), timeout=0.2) as ser:
                lv = LEVEL_PERMILLE
                ser.write(bytes([ord("C"), 1, 1, 0, ord("W"), 1, 0, 0, ord("L"), lv & 255, lv >> 8, 0]))
                ser.reset_input_buffer()
                buf = bytearray()
                while self.running:
                    buf += ser.read(ser.in_waiting or 1)
                    while True:
                        i = buf.find(MAGIC)
                        if i < 0 or len(buf) - i < HDR.size:
                            break
                        f = HDR.unpack_from(buf, i)
                        end = i + HDR.size + 2 * f[6]
                        if len(buf) < end:
                            break
                        w = np.frombuffer(bytes(buf[i + HDR.size:end]), "<u2")
                        with self.lock:
                            self.chunks.append(w)
                            self.words_per_mic += len(w) // 2
                        self.q.put(w)
                        del buf[:end]
        except Exception as e:  # shown on the page
            self.error = str(e)
            self.running = False


class Session:
    def __init__(self):
        self.active = False

    def new(self, person, set_name, commands, reps, practice):
        self.person, self.set_name, self.reps, self.practice = person, set_name, reps, practice
        pdir = OUT / person
        nums = [int(m.group(1)) for d in pdir.glob("s*_*") if (m := re.match(r"s(\d+)_", d.name))]
        self.number = max(nums, default=0) + 1
        self.dirname = f"s{self.number:02d}_{time.strftime('%Y%m%d_%H%M%S')}" + ("_practice" if practice else "")
        self.commands = list(commands)
        self.prompts = [c for c in self.commands for _ in range(reps)]
        random.shuffle(self.prompts)
        self.idx, self.events, self.active, self.t0 = 0, [], True, time.time()
        self.grid, self.grid_final = None, False  # frame grid for live recognition
        self.recognize = RECOG is not None and set(self.commands) <= set(RECOG.classes)
        self.last_pred = None
        self.threads = []


def recognize_take(ev, ses):
    """Runs in a thread after a take ends: wait for the 0.3 s tail, then classify the take."""
    from build_dataset import FS, PAD_S
    try:
        need = ev["end"] + int(PAD_S * FS) + 2000
        t_end = time.time() + 3
        while REC.now_sample() < need and time.time() < t_end:
            time.sleep(0.05)
        words = REC.words_so_far()
        if not ses.grid_final:  # same grid rule as build_dataset: first 30 s of the session
            from live_recog import session_grid
            ses.grid = session_grid(words)
            ses.grid_final = len(words) >= 4 * 30 * FS
        r = RECOG.predict(words, ev["start"], ev["end"], ses.grid)
        r["correct"] = r["word"] == ev["command"]
        ev["pred"] = r
        ses.last_pred = dict(idx=ev["idx"], said=ev["command"], **r)
    except Exception as e:
        ses.last_pred = dict(idx=ev["idx"], said=ev["command"], error=str(e))


REC = None
SES = Session()


def list_sessions(person=None):
    rows = []
    people = [OUT / person] if person else [d for d in OUT.glob("*") if d.is_dir() and d.name != "_trash"]
    for pdir in sorted(people):
        for sdir in sorted(pdir.glob("s*_*")):
            try:
                m = json.load(open(sdir / "events.json"))
            except Exception:
                continue
            takes = [e for e in m["events"] if not e.get("redo") and "end" in e]
            rows.append(dict(person=pdir.name, session=sdir.name, number=m.get("session_number"),
                             practice=m.get("practice", False), command_set=m.get("command_set"),
                             takes=len(takes), planned=len(m.get("prompt_order", [])),
                             seconds=round(m.get("seconds", 0)), date=m.get("started")))
    return rows


def save_session(words, ended_early=False):
    d = OUT / SES.person / SES.dirname
    d.mkdir(parents=True, exist_ok=True)
    np.save(d / "raw_words.npy", words)
    meta = dict(person=SES.person, session_number=SES.number, practice=SES.practice,
                started=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(SES.t0)),
                seconds=len(words) / 2 / 200_000, ended_early=ended_early,
                command_set=SES.set_name, commands=SES.commands, reps=SES.reps, prompt_order=SES.prompts,
                protocol="after EchoSpeech CHI'23 discrete; push-to-record (space start/stop, auto-end 4 s)",
                firmware="experiments/split_bands (speaker 0 18-24 kHz, speaker 1 25-31 kHz)",
                level_percent=LEVEL_PERMILLE / 10, soft_edges=True, git_commit=git_commit(),
                live_model=(dict(file=RECOG.name, trained_on=RECOG.trained_on) if SES.recognize else None),
                audio_rate=100_000, word_layout="interleaved mic2(pin8), mic1(pin6)",
                note="take start/end in 100 kHz audio samples from the start of raw_words",
                events=SES.events)
    json.dump(meta, open(d / "events.json", "w"), indent=1)
    return d


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        args = dict(kv.split("=", 1) for kv in query.split("&") if "=" in kv)
        if path in ("/", "/index.html"):
            body = (HERE / "collect.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/config":
            people = sorted(d.name for d in OUT.glob("*") if d.is_dir() and d.name != "_trash")
            self._json(dict(command_sets={k: v for k, v in COMMAND_SETS.items()}, people=people,
                            max_take_s=MAX_TAKE_S, dry=REC.dry))
        elif path == "/sessions":
            self._json(dict(sessions=list_sessions(safe_id(args.get("person", "")) or None)))
        elif path == "/live":
            img = REC.live.snapshot()
            if img is None:
                self._json(dict(ready=False))
            else:
                self._json(dict(ready=True, paths=img.shape[0], bins=img.shape[1], frames=img.shape[2],
                                data=base64.b64encode(img.tobytes()).decode()))
        elif path == "/state":
            self._json(dict(active=SES.active, idx=getattr(SES, "idx", 0), total=len(getattr(SES, "prompts", [])),
                            prompt=SES.prompts[SES.idx] if SES.active and SES.idx < len(SES.prompts) else None,
                            error=REC.error))
        elif path == "/pred":
            done = [e["pred"] for e in getattr(SES, "events", []) if "pred" in e and not e.get("redo")]
            self._json(dict(enabled=bool(getattr(SES, "recognize", False)), last=getattr(SES, "last_pred", None),
                            n=len(done), correct=sum(p["correct"] for p in done),
                            model=RECOG.name if RECOG else None))
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or b"{}")
        p = self.path
        if p == "/start":
            person = safe_id(req.get("person"))
            if not person:
                return self._json({"error": "enter a participant ID"}, 400)
            set_name = req.get("command_set", "all 31")
            if set_name == "custom":
                cmds = [c.strip() for c in req.get("custom", "").split(",") if c.strip()]
            else:
                cmds = COMMAND_SETS.get(set_name, ALL)
            if not cmds:
                return self._json({"error": "the command list is empty"}, 400)
            reps = max(1, min(10, int(req.get("reps", 4))))
            SES.new(person, set_name, cmds, reps, bool(req.get("practice")))
            REC.start()
            self._json(dict(session=SES.dirname, number=SES.number, total=len(SES.prompts)))
        elif p == "/take_start":
            if SES.active and SES.idx < len(SES.prompts):
                SES.events.append(dict(type="take", idx=SES.idx, command=SES.prompts[SES.idx], start=REC.now_sample()))
            self._json(dict(ok=True))
        elif p in ("/take_end", "/redo"):
            if SES.events and SES.events[-1]["type"] == "take" and "end" not in SES.events[-1]:
                ev = SES.events[-1]
                ev["end"] = REC.now_sample()
                ev["how"] = req.get("how", "space")
                ev["redo"] = p == "/redo"
            if p == "/take_end":
                if SES.recognize and SES.events and "end" in SES.events[-1] and not SES.events[-1].get("redo"):
                    th = threading.Thread(target=recognize_take, args=(SES.events[-1], SES), daemon=True)
                    th.start()
                    SES.threads.append(th)
                SES.idx += 1
            self._json(dict(idx=SES.idx, finished=SES.idx >= len(SES.prompts)))
        elif p == "/finish":
            if not SES.active:
                return self._json({"error": "no session running"}, 400)
            for th in getattr(SES, "threads", []):  # let running recognitions read their tail and finish
                th.join(timeout=6)
            words = REC.stop()
            d = save_session(words, ended_early=bool(req.get("early")))
            SES.active = False
            takes = len([e for e in SES.events if not e.get("redo") and "end" in e])
            self._json(dict(saved=str(d.relative_to(ROOT)), seconds=len(words) / 2 / 200_000, takes=takes))
        elif p == "/delete":
            person, sess = safe_id(req.get("person")), req.get("session", "")
            src = OUT / person / sess
            if not re.fullmatch(r"s\d+_[0-9_]+(_practice)?", sess) or not src.is_dir():
                return self._json({"error": "no such session"}, 400)
            dst = TRASH / person / f"{sess}__deleted_{time.strftime('%Y%m%d_%H%M%S')}"
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            self._json(dict(moved_to=str(dst.relative_to(ROOT))))
        else:
            self._json({"error": "not found"}, 404)


def main():
    global REC
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--model", help="classify each take live with this model (from train_digits.py --save)")
    a = ap.parse_args()
    global RECOG
    if a.model:
        from live_recog import Recognizer
        RECOG = Recognizer(a.model)
        print(f"live recognition: {RECOG.name}, classes {RECOG.classes}")
    REC = Recorder(dry=a.dry)
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"open http://localhost:{a.port}  ({'dry run, no Teensy' if a.dry else 'recording from the Teensy'})")
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
