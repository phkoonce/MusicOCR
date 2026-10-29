"""Run RapidOCR (PP-OCR, ONNX, models bundled in the package) on one image.

Executed by *another* interpreter (the homr venv, which has ``rapidocr`` and
``onnxruntime`` installed), never imported by musicocr itself. Prints one JSON
line ``[{"text","xyxy"}]`` (whitespace-split words, boxes in image pixels).
"""
import json
import sys

from rapidocr import RapidOCR

res = RapidOCR()(sys.argv[1])
out = []
for box, text in zip(res.boxes if res.boxes is not None else [], res.txts or []):
    xs = [float(p[0]) for p in box]
    ys = [float(p[1]) for p in box]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    words = text.split()
    total = sum(len(w) for w in words) or 1
    cx = x0
    for w in words:                      # apportion the line box by character count
        wx1 = cx + (x1 - x0) * len(w) / total
        out.append({"text": w, "xyxy": [cx, y0, wx1, y1]})
        cx = wx1
print(json.dumps(out))
