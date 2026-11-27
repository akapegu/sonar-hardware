#!/usr/bin/env python3
"""Live silent-digit demo: mouth digits, the page shows what the glasses recognised.

    .venv/bin/python experiments/echospeech/demo_server.py --model models/digits_pegu_all10_extra.pt
    open http://localhost:8770

No key presses needed: word_detect.py finds each mouthed word in the live echo profiles and
live_recog.py classifies it (the take is cut exactly like a training clip). Space still works as
a manual fallback (press before mouthing, press after). Sound plays only while the demo runs.
Each run is saved to captures/demo/<time>/ (raw words + detected words), outside the training set.
"""
import argparse
import base64
import json
import queue
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
import collect_server as cs  # noqa: E402  (Recorder, level settings)
from build_dataset import FS, PAD_S  # noqa: E402
from live_recog import Recognizer, session_grid  # noqa: E402
from word_detect import WordDetector  # noqa: E402

OUT = ROOT / "captures" / "demo"
REC = None
MODEL = None


class WordsView:
    """Indexable like the session's interleaved word array, reading only the slice asked for."""

    def __init__(self, rec):
        self.rec = rec

    def __getitem__(self, sl):
        return self.rec.words_range(sl.start or 0, sl.stop)


class Demo:
    def __init__(self):
        self.running = False
        self.k_on = 2.5
        self.results, self.state, self.manual_start = [], "stopped", None
        self.q = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

    def start(self):
        self.results, self.grid, self.grid_final, self.manual_start = [], None, False, None
        self.t0 = time.time()
        REC.start()
        self.det = WordDetector(REC.live.frame_sample, self._on_word, k_on=self.k_on)
        REC.live.listeners.append(self.det)
        self.running, self.state = True, "settling"

    def stop(self):
        self.running, self.state = False, "stopped"
        words = REC.stop()
        d = OUT / time.strftime("%Y%m%d_%H%M%S", time.localtime(self.t0))
        d.mkdir(parents=True, exist_ok=True)
        np.save(d / "raw_words.npy", words)
        json.dump(dict(model=MODEL.name, trained_on=MODEL.trained_on, k_on=self.k_on, results=self.results,
                       note="start/end in 100 kHz samples from the start of raw_words"),
                  open(d / "events.json", "w"), indent=1)
        return d

    def _on_word(self, a, b, how="auto"):
        if self.running:
            self.q.put((a, b, how))

    def manual(self):
        now = REC.now_sample()
        if self.manual_start is None:
            self.manual_start = now
            self.det.state = "manual"  # pause the automatic detector during a manual take
            return "marking"
        a, self.manual_start = self.manual_start, None
        self.det.state = "idle"
        self._on_word(a, now, "manual")
        return "sent"

    def _worker(self):
        while True:
            a, b, how = self.q.get()
            try:
                need = b + int(PAD_S * FS) + 2000
                t_end = time.time() + 3
                while REC.now_sample() < need and time.time() < t_end:
                    time.sleep(0.03)
                self.state = "thinking"
                words = WordsView(REC)
                if not self.grid_final:  # frame grid as build_dataset: first 30 s of the run
                    self.grid = session_grid(words[0:4 * 30 * FS])
                    self.grid_final = REC.now_sample() >= 30 * FS
                r = MODEL.predict(words, a, b, self.grid)
                r.update(t=round(a / FS, 2), dur=round((b - a) / FS, 2), how=how, start=int(a), end=int(b))
                self.results.append(r)
            except Exception as e:
                self.results.append(dict(error=str(e), t=round(a / FS, 2), how=how))
            finally:
                if self.running:
                    self.state = "listening"


DEMO = Demo()


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
        path = self.path.partition("?")[0]
        if path in ("/", "/index.html"):
            body = (HERE / "demo.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/live":
            img = REC.live.snapshot() if DEMO.running else None
            if img is None:
                self._json(dict(ready=False))
            else:
                self._json(dict(ready=True, paths=img.shape[0], bins=img.shape[1], frames=img.shape[2],
                                data=base64.b64encode(img.tobytes()).decode()))
        elif path == "/status":
            det = getattr(DEMO, "det", None)
            st = DEMO.state
            if DEMO.running and det is not None and st == "listening" and det.state == "word":
                st = "word"
            if DEMO.running and st == "settling" and det is not None and det.quiet is not None and REC.live.count > det.warm:
                st = DEMO.state = "listening"
            self._json(dict(running=DEMO.running, state=st, manual=DEMO.manual_start is not None,
                            level=round(det.level, 2) if det else 0, k_on=DEMO.k_on, results=DEMO.results[-30:],
                            n=len(DEMO.results), model=MODEL.name, error=REC.error))
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/start":
            if not DEMO.running:
                DEMO.start()
            self._json(dict(ok=True))
        elif self.path == "/stop":
            d = DEMO.stop() if DEMO.running else None
            self._json(dict(saved=str(d.relative_to(ROOT)) if d else None))
        elif self.path == "/mark":
            self._json(dict(state=DEMO.manual() if DEMO.running else "stopped"))
        elif self.path == "/sensitivity":
            DEMO.k_on = float(min(5.0, max(1.5, req.get("k_on", 2.5))))
            if getattr(DEMO, "det", None):
                DEMO.det.k_on = DEMO.k_on
            self._json(dict(k_on=DEMO.k_on))
        else:
            self._json({"error": "not found"}, 404)


def main():
    global REC, MODEL
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=str(ROOT / "models" / "digits_pegu_all10_extra.pt"))
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    MODEL = Recognizer(a.model)
    REC = cs.Recorder(dry=a.dry)
    print(f"demo: {MODEL.name} ({MODEL.classes}); open http://localhost:{a.port}")
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
