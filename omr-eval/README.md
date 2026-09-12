# OMR model evaluation

Trying pretrained end-to-end OMR models as a potential replacement for
Audiveris, specifically for monophonic single-voice parts (brass/woodwind
parts, not full scores). Motivation: Audiveris is a classical
segment-then-classify-then-reconstruct pipeline where errors compound across
stages; modern end-to-end image-to-sequence models (CNN/transformer encoder →
token decoder) tend to do meaningfully better on monophonic material because
there's no voice-separation ambiguity to get wrong.

## Setup

```bash
bash omr-eval/setup.sh
```

Creates two isolated `uv`-managed venvs (the main project's Python is 3.9;
these tools need 3.11/3.12):

| Tool | Venv | Notes |
|---|---|---|
| [homr](https://github.com/liebharc/homr) | `homr/.venv` (Python 3.12) | Transformer-based (built on NetEase's TrOMR), ONNX runtime — CPU inference, no GPU needed. Outputs MusicXML directly. |
| [oemer](https://github.com/BreezeWhite/oemer) | `oemer/.venv` (Python 3.11) | Older, unmaintained (last release 0.1.5). `setup.sh` patches two live bugs in its installed source: a removed `np.int` alias, and a `cv2.HoughLinesP` return-shape assumption that crashes (`IndexError`) specifically on sparse/single-staff pages — i.e. exactly the monophonic case we care about. |

Both venvs are gitignored (`.venv/` — large: homr ~475MB, oemer ~680MB,
mostly bundled model weights). Re-run `setup.sh` to recreate them from
scratch.

Model weights download automatically on first run of each tool (homr ~100MB,
oemer ~150MB), cached inside their respective venvs.

## Running

```bash
omr-eval/homr/.venv/bin/homr <page.png>          # writes <page>.musicxml next to the input
omr-eval/oemer/.venv/bin/oemer <page.png> -o <outdir>
```

Feed it the *preprocessed* page PNG from an existing pipeline run
(`output/<book>/prepped/pNNN.png`) rather than the raw PDF, so the comparison
against Audiveris is apples-to-apples on the same deskewed/cropped input —
see [deskew/crop preprocessing](../README.md#poor-scans).

Page images and generated MusicXML are gitignored (`/omr-eval/pages/`,
`*.musicxml`, `*.png` under `omr-eval/`) — they're derived from copyrighted
sheet music scans, same policy as `/input` and `/output`.

## Findings so far (2026-09-11)

One page, monophonic trombone part (`Trombone 1.pdf`, page 1, camera scan via
Adobe Scan, already deskewed/cropped by the main pipeline):

| Tool | Notes detected | Measures | Structure |
|---|---|---|---|
| Audiveris (existing pipeline output) | 198 | 55 | **split into 3 fragments** — misread system breaks as movement breaks |
| homr | 196 | 44 | one coherent document, no fragmentation |
| oemer | 176 | 32 | one document, 20 notes explicitly flagged "not a valid note" |

Only checked structurally (note/measure counts) so far, not pitch/rhythm
correctness against ground truth. Eyeballing the actual homr MusicXML output,
though, it looked clearly better than Audiveris's — worth pursuing further.

**Next steps:**
- Check pitch/rhythm correctness by ear (import into MuseScore, compare
  against the scan) rather than just structural counts.
- Try homr against a few more parts/pages, including anything Audiveris
  handles particularly badly, to see if the win holds up.
- If homr keeps winning, look at whether it can slot into the existing
  pipeline (`musicocr/stages/omr.py`) as an alternate/additional OMR backend
  rather than a one-off script.
