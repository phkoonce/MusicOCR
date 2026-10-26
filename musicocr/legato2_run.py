"""Run inside the LEGATO 2 venv: transcribe one page image to ABC.

    python legato2_run.py <page.png> --out <page.abc> [--model ID] [--device auto]
                          [--beam-size 1] [--yolo-conf 0.7]

Standalone on purpose (no musicocr imports): the LEGATO 2 venv has torch /
ultralytics but not this project's dependencies. ABC -> MusicXML happens back in
the main interpreter (``musicocr.abc_convert``).

Uses the API from the model card (guangyangmusic/legato-2):
``legato.pipeline.Legato2.from_pretrained(model, device=...)`` then ``omr(image)``.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def pick_device(requested: str) -> str:
    if requested != "auto":
        return requested
    import torch
    if torch.cuda.is_available():
        return "cuda:0"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="guangyangmusic/legato-2")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--beam-size", type=int, default=1)
    ap.add_argument("--yolo-conf", type=float, default=0.7)
    a = ap.parse_args(argv)

    try:
        from legato.pipeline import Legato2
    except ImportError as exc:
        print(f"legato2_run: cannot import legato.pipeline.Legato2 ({exc}); the LEGATO 2 "
              "inference code must be on PYTHONPATH (see omr-eval/setup.sh)", file=sys.stderr)
        return 3

    device = pick_device(a.device)
    print(f"legato2_run: model={a.model} device={device}", file=sys.stderr)
    omr = Legato2.from_pretrained(a.model, device=device)
    abc = omr(a.image, beam_size=a.beam_size, yolo_conf=a.yolo_conf)
    Path(a.out).write_text(abc if isinstance(abc, str) else "\n".join(abc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
