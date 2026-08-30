# MusicOCR

Automated pipeline: **PDF score/part → MusicXML + MuseScore file**, with a
quality report that points at the measures most likely to be wrong.

It orchestrates tools that are already installed on this machine:

| Tool | Role |
|---|---|
| [Audiveris](https://audiveris.github.io/audiveris/) 5.x | Optical music recognition (PDF → MusicXML). Bundles its own Java + Tesseract. |
| [MuseScore](https://musescore.org) 4.x | MusicXML → `.mscz`, `.mid`, rendered `.pdf` |
| [music21](https://www.music21.org) | Post-OMR validation / measure checks |
| poppler (`pdfinfo`, `pdftoppm`) | Page count + QA page images |

OMR is never perfect. This tool runs **fully automated** and then tells you where
to look — it does not guess pitches or rhythms. Correcting the output is done by
hand afterward in MuseScore (or, later, via the Audiveris GUI — see below).

## Setup

```bash
bash scripts/setup.sh          # brew install poppler + create .venv + pip install
source .venv/bin/activate
python -m musicocr doctor       # verify all tools are found
```

## Usage

```bash
# drop a PDF in input/ (or point anywhere)
python -m musicocr run input/my-part.pdf
```

Everything lands in `output/<book>/`:

| File | What it is |
|---|---|
| `<book>.musicxml` | interchange master — pages stitched into one score |
| `<book>.pNNN.musicxml` | per-page MusicXML (kept even when merged) |
| `<book>.mscz` | open/edit in MuseScore |
| `<book>.mid` | quick aural check |
| `render.pdf` | MuseScore's rendering of the OMR result |
| `qa/src-*.png`, `qa/omr-*.png` | source scan vs render, page by page |
| `report.md` / `report.json` | stage log + validation findings + QA images |
| `pages/pNNN/*.log` | per-page Audiveris logs |
| `<book>.omr` | Audiveris project file — only when GUI correction is enabled (see below) |
| `source.pdf` | copy of the input |

Read `report.md` first. Findings are ranked 🔴 error / 🟡 warn / 🔵 info, with
part + measure numbers. Cross-check flagged measures against the `qa/` image
pairs, then fix in MuseScore.

### Partial transcription

Real scans have pages Audiveris chokes on. When that happens the run **keeps
going** — the bad pages are simply missing from the output, and `report.md` opens
with a ⚠️ listing them. Re-run just those pages after tuning:

```
python -m musicocr run INPUT.pdf --pages 12,15 --profile <name> --force
```

(The pipeline does not skip `-save` fragility for free: enabling GUI correction
re-adds Audiveris's `-save`, which is what makes one bad sheet able to abort a
whole multi-page book. Automated runs leave it off.)

### Options

```
python -m musicocr run INPUT.pdf \
  --pages 1,4-5          # only these pages (Audiveris -sheets)
  --profile clean-typeset # constants preset from config.toml
  --constant k=v          # extra Audiveris constant, repeatable
  --force                 # re-run Audiveris even if output exists
  --from validate         # re-run from a stage (reuses earlier artifacts)
  --to omr                # stop after a stage
```

Stages, in order: `ingest → omr → correct → extract → validate → convert → qa`.
Re-running `--from extract` after editing the `.mxl`/`.omr` is the normal way to
iterate without paying for another full OMR pass.

## Tuning Audiveris

Audiveris behaviour is controlled by "constants". The workflow:

1. Run once, read `report.md`, look at the `qa/` images.
2. Add constants to a profile in `config.toml` (`[omr.profiles.*]`) or pass
   `--constant` ad hoc.
3. Re-run with `--force`.

List available constants with `/Applications/Audiveris.app/Contents/MacOS/Audiveris -help`
and the Audiveris docs. Common ones: input resolution hints, binarization
filter, whether to keep/merge movements.

## Manual correction via the Audiveris GUI (optional, off by default)

v1 does not touch the GUI. When you want to try it:

1. Set `[stages.correct] enabled = true` in `config.toml`.
2. `python -m musicocr run <pdf>` now stops after OMR (status `paused`) and
   prints instructions.
3. `open -a Audiveris`, open `output/<book>/<book>.omr`, fix errors, then
   **Book → Export**.
4. `python -m musicocr run <pdf> --from extract` to finish the pipeline on the
   corrected export.

`musicocr/stages/correct.py` is where this lives; it is structured so it can
later drive the GUI round-trip automatically instead of just pausing.

## No-scan smoke test

```bash
python scripts/make_sample.py          # writes samples/sample.pdf from a known score
python -m musicocr run samples/sample.pdf
```

A clean synthetic score should come back with few or no findings.

## Layout

```
musicocr/
  cli.py            # `run` / `doctor`
  config.py         # config.toml loader
  pipeline.py       # stage protocol + ordered runner
  report.py         # report.md / report.json
  stages/           # ingest, omr, correct, extract, validate, convert, qa
config.toml         # tool paths, OMR profiles, output formats
scripts/setup.sh    # environment bootstrap
scripts/make_sample.py
```
