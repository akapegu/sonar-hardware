#!/usr/bin/env python3
"""EchoSpeech-style data collection: records the rig while a browser page prompts the commands.

    .venv/bin/python experiments/echospeech/collect_server.py            # then open http://localhost:8765
    .venv/bin/python experiments/echospeech/collect_server.py --dry      # page only, no Teensy (testing)

Protocol (EchoSpeech CHI '23, section 6): each session shows the 31 commands 4 times in random
order (124 prompts), up to 3 s each, auto-advance; space = next now, x = redo the current one;
remount the glasses between sessions; session 1 is familiarisation. The Teensy must run
experiments/split_bands/split_bands.ino (speaker 0 18-24 kHz, speaker 1 25-31 kHz).

Each session is saved to captures/echospeech/<session_id>/:
  raw_words.npy  interleaved uint16 PDM words (mic 2/pin 8, mic 1/pin 6, ...), 200k words/s per mic
  events.json    prompts with start/end in audio samples at 100 kHz (word index per mic / 2)
"""
import argparse
import json
import random
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
from commands import ALL  # noqa: E402

OUT = ROOT / "captures" / "echospeech"
REPS = 4
LEVEL_PERMILLE = 100  # 10%


class Recorder:
    """Owns the serial port while a session runs; counts words so events can be time-stamped."""

    def __init__(self, dry=False):
        self.dry = dry
        self.words_per_mic = 0
        self.chunks = []
        self.running = False
        self.lock = threading.Lock()
        self.thread = None
        self.error = None

    def start(self):
        self.words_per_mic, self.chunks, self.running, self.error = 0, [], True, None
        self.thread = threading.Thread(target=self._run_dry if self.dry else self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        return np.concatenate(self.chunks) if self.chunks else np.zeros(0, np.uint16)

    def now_sample(self):
        with self.lock:
            return self.words_per_mic // 2  # 16 PDM bits per word, 32 bits per 100 kHz sample

    def _run_dry(self):
        t0 = time.time()
        while self.running:
            time.sleep(0.01)
            with self.lock:
                self.words_per_mic = int((time.time() - t0) * 200_000)

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
                        del buf[:end]
        except Exception as e:  # shown on the page
            self.error = str(e)
            self.running = False


class Session:
    def __init__(self):
        self.id = None
        self.prompts = []
        self.idx = 0
        self.events = []
        self.started = False

    def new(self, name):
        self.id = time.strftime("%Y%m%d_%H%M%S") + (f"_{name}" if name else "")
        self.prompts = [c for c in ALL for _ in range(REPS)]
        random.shuffle(self.prompts)
        self.idx, self.events, self.started = 0, [], True


REC = None
SES = Session()


def save_session(words):
    d = OUT / SES.id
    d.mkdir(parents=True, exist_ok=True)
    np.save(d / "raw_words.npy", words)
    meta = dict(session=SES.id, protocol="EchoSpeech CHI'23 discrete: 31 commands x 4, random order, <=3 s",
                firmware="experiments/split_bands (speaker 0 18-24 kHz, speaker 1 25-31 kHz)",
                level_percent=LEVEL_PERMILLE / 10, audio_rate=100_000, word_layout="interleaved mic2(pin8), mic1(pin6)",
                note="events in 100 kHz audio samples from the start of raw_words (before any soft-start skip)",
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
        if self.path in ("/", "/index.html"):
            body = (HERE / "collect.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/state":
            self._json(dict(session=SES.id, idx=SES.idx, total=len(SES.prompts), started=SES.started,
                            prompt=SES.prompts[SES.idx] if SES.started and SES.idx < len(SES.prompts) else None,
                            sample=REC.now_sample() if REC.running else None, error=REC.error,
                            done=len([s for s in OUT.glob("*") if s.is_dir()]) if OUT.exists() else 0))
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or b"{}")
        p = self.path
        if p == "/start":
            SES.new(req.get("name", "").strip().replace(" ", "_"))
            REC.start()
            self._json(dict(session=SES.id, total=len(SES.prompts)))
        elif p == "/show":  # prompt appears on screen
            SES.events.append(dict(type="show", idx=SES.idx, command=SES.prompts[SES.idx], start=REC.now_sample()))
            self._json(dict(ok=True))
        elif p in ("/next", "/redo"):  # prompt ends (auto after 3 s, space, or x)
            if SES.events and SES.events[-1]["type"] == "show":
                ev = SES.events[-1]
                ev["end"] = REC.now_sample()
                ev["how"] = req.get("how", "auto")
                ev["redo"] = p == "/redo"
            if p == "/next":
                SES.idx += 1
            finished = SES.idx >= len(SES.prompts)
            self._json(dict(idx=SES.idx, finished=finished))
        elif p == "/finish":
            words = REC.stop()
            d = save_session(words)
            SES.started = False
            self._json(dict(saved=str(d.relative_to(ROOT)), seconds=len(words) / 2 / 200_000,
                            prompts=len([e for e in SES.events if not e.get("redo")])))
        else:
            self._json({"error": "not found"}, 404)


def main():
    global REC
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    REC = Recorder(dry=a.dry)
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"open http://localhost:{a.port}  ({'dry run, no Teensy' if a.dry else 'recording from the Teensy'})")
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
