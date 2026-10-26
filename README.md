# MusicOCR

Automated pipeline: **PDF score/part → MusicXML + MuseScore file**, with a
quality report that points at the measures most likely to be wrong.

It orchestrates tools that are already installed on this machine:

| Tool | Role |
|---|---|
| [homr](https://github.com/liebharc/homr) | **Default OMR engine.** End-to-end transformer image→MusicXML model, own Python 3.12/ONNX venv under `omr-eval/homr/`. Consistently outperformed Audiveris on monophonic wind/brass parts in local evaluation (`omr-eval/README.md`). |
| [Audiveris](https://audiveris.github.io/audiveris/) 5.x | Alternative OMR engine (`[omr] engine = "audiveris"`). Classical segment-classify-reconstruct pipeline, tunable via constants, and the only engine with a GUI hand-correction round-trip. Bundles its own Java + Tesseract. |
| [MuseScore](https://musescore.org) 4.x | MusicXML → `.mscz`, `.mid`, rendered `.pdf` |
| [music21](https://www.music21.org) | Post-OMR validation / measure checks |
| poppler (`pdfinfo`, `pdftoppm`) | Page rasterisation, page count, QA page images |

OMR is never perfect. This tool runs **fully automated** and then tells you where
to look — it does not guess pitches or rhythms. Correcting the output is done by
hand afterward in MuseScore (or, when using the Audiveris engine, via its GUI —
see below).

## Setup

```bash
bash scripts/setup.sh          # brew install poppler + create .venv + pip install -e .
bash omr-eval/setup.sh         # creates the homr venv (default OMR engine)
source .venv/bin/activate
python -m musicocr doctor       # verify all tools are found
```

Requires Python 3.11+ (setup.sh picks python3.13/3.12/3.11, or falls back to uv). Manual install: `pip install -e .` (add `.[test]` for pytest). An editable install is required: `config.toml` and the homr venv default are located relative to the repo checkout.

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
| `pages/pNNN/*.log` | per-page OMR engine logs (homr or Audiveris) |
| `<book>.omr` | Audiveris project file — only when using the Audiveris engine with GUI correction enabled (see below) |
| `source.pdf` | copy of the input |

Read `report.md` first. Findings are ranked 🔴 error / 🟡 warn / 🔵 info, with
part + measure numbers. Cross-check flagged measures against the `qa/` image
pairs, then fix in MuseScore.

### Partial transcription

Real scans have pages the OMR engine chokes on. When that happens the run **keeps
going** — the bad pages are simply missing from the output, and `report.md` opens
with a ⚠️ listing them. Re-run just those pages after tuning:

```
python -m musicocr run INPUT.pdf --pages 12,15 --force
```

(Audiveris-specific: the pipeline does not skip `-save` fragility for free —
enabling GUI correction re-adds Audiveris's `-save`, which is what makes one bad
sheet able to abort a whole multi-page book. Automated runs leave it off. homr
has no whole-book mode to abort — it always runs one page per process.)

### Time-signature repair

homr has no way to hint the time signature, and it gets the *numerator* wrong
whenever the signature changes mid-page (6/8 comes out as "8/8": the model reads the
denominator, but the numerator is derived from the page's median bar length).
`musicocr/timesig.py` re-derives each numerator from the bars it governs, treating
the per-page files as one part, and carries the signature onto pages that declare
none. It runs in `extract` just before the page merge and logs every change.

### Transposition

OMR output is *written* pitch. To get concert pitch (or any other shift):

```
python -m musicocr transpose part.musicxml --instrument "horn in f"   # table lookup
python -m musicocr transpose score.musicxml                           # each part by its <part-name>
python -m musicocr transpose part.musicxml --semitones -2 -o out.musicxml
python -m musicocr instruments                                        # show the table
```

(The `transpose` command rewrites pitches at concert pitch.) Inside a run the stage
defaults to `mode = "written"`: notes stay exactly as printed, and the part gets the
instrument name and a MusicXML `<transpose>` so MuseScore shows the written score
and its "Concert pitch" toggle does the shift. Set `mode = "concert"` to rewrite
the pitches instead. To do it inside a run, enable the `transpose` stage (`[stages.transpose]` in
`config.toml`, or `--transpose-instrument "horn in f"` / `--transpose-semitones -7`
on `run`). The untransposed file is kept in `output/<book>/written/`, and re-running
`--from transpose` always starts from that copy, so shifts never stack. The `qa`
render is at the new pitch in `concert` mode, so it won't visually match a written-pitch scan.

Pitches and key signatures move together and are respelled (C major horn part →
F major). The shared instrument table lives in `musicocr/instruments.py`; values
are semitones to add to written pitch, and clef-dependent instruments (baritone /
tenor horn: treble −14, bass 0) are resolved from each part's first clef.
Unknown part names are skipped, never guessed.

### Options

```
python -m musicocr run INPUT.pdf \
  --pages 1,4-5          # only these pages
  --engine homr|audiveris # OMR engine (default: homr, config [omr] engine)
  --profile clean-typeset # Audiveris constants preset from config.toml
  --constant k=v          # extra Audiveris constant, repeatable
  --force                 # re-run OMR even if output exists
  --no-merge              # keep pages separate (book of independent parts/songs)
  --preprocess / --no-preprocess   # force scan clean-up on/off (see below)
  --deskew --crop         # extra clean-up passes for camera scans (see below)
  --from validate         # re-run from a stage (reuses earlier artifacts)
  --to omr                # stop after a stage
```

### Dynamics / hairpin consistency (optional)

`musicocr combine --normalize-marks [RULES]` runs a post-pass (`musicocr/marks.py`) that uses the
other parts as evidence to tidy dynamics and hairpins. `RULES` is a comma list; bare means
`dedupe,hairpins`.

- `dedupe` -- the same dynamic twice in one bar (e.g. above and below the staff): keep one.
- `hairpins` -- a hairpin that stops on the note it starts on is extended: to where the other parts
  end it, else to the next dynamic if that is within a bar of the bar's end, else to the end of the bar.
- `fill` (opt-in, or `all`) -- a dynamic that at least half of a section's playing parts carry
  (min. 4 parts) is added to the section's parts that play but carry none.

Inserted dynamics and moved hairpin stops are coloured blue; every change is in the combine report.
`musicocr marks IN.musicxml --rules all` runs the same pass on an existing file, and
`scripts/compare_marks.py before.musicxml cleaned.musicxml` scores it against a hand-cleaned score.

## OMR engine

`[omr] engine` in `config.toml` (or `--engine`) picks the transcription engine:

* **`homr`** (default) — an end-to-end transformer OMR model. No whole-book
  mode, no Audiveris-style constant tuning, no GUI correction round-trip; it
  just reads one rasterised page image and writes MusicXML. Needs its own
  venv: `bash omr-eval/setup.sh` (see `omr-eval/README.md` for background on
  why it was adopted — it beat Audiveris consistently on monophonic
  wind/brass parts in local testing). Because it needs an image, it always
  rasterises pages regardless of `[preprocess] enabled`.
* **`copisteria`** — [KobiMusic's reader](https://github.com/kobimusic/copisteria)
  (page image → MusicXML with a learned evidence model; Apache-2.0). Own
  Python 3.12 venv + repo clone under `omr-eval/copisteria/`, installed by
  `omr-eval/setup.sh`; `[omr.copisteria] detector = "28m" | "2m"`. Its
  published benchmarks are on multi-staff scores, so it is being evaluated
  side by side with homr on parts. Also leaves `pNNN.reading.json` (per-symbol
  evidence) and `pNNN.html` (viewer) beside each page's MusicXML.
* **`legato2`** — reserved placeholder. LEGATO 2 (arXiv:2607.05769) has no
  released code or weights yet; selecting it fails with an explanatory error.
  See `musicocr/stages/omr_legato2.py` for the wiring checklist.
* **`audiveris`** — the original classical pipeline. Still the better choice
  for full scores / multi-staff systems, and the only engine with a hand
  correction workflow (see below).

`--pages` + `--no-merge` is the combination for a PDF that's really several
independent parts glued together (e.g. a full part set). Run each part's page
range on its own; merging different instruments into one score is meaningless.

Stages, in order: `ingest → omr → correct → extract → normalize → transpose →
validate → convert → qa` (`transpose` is optional, off by default). Re-running `--from extract` after editing the `.mxl`/`.omr` is
the normal way to iterate without paying for another full OMR pass.

### Copisteria-specific pre/post-processing

* **Page text → multi-rest counts.** Copisteria only reads the count over a
  multi-measure rest from OCR text boxes (`pNNN.texts.json`). For born-digital
  PDFs the `omr` stage builds that file from the PDF's text layer
  (`[omr.copisteria] texts = "pdf-layer"`; `musicocr/page_text.py`), mapping
  the digit boxes onto the preprocessed page via the geometry `preprocess`
  records in `prepped/pNNN.geom.json` (DPI, deskew angle, crop offset).
  Scanned PDFs have no text layer; `texts = "ocr"` instead OCRs the prepped page
  image (`musicocr/ocr_backends.py`, pluggable: RapidOCR from the homr venv and/or
  macOS Vision through a tiny Swift helper compiled on first use; `ocr_backend`
  picks one or the union, the default, since each misses some isolated numerals) and
  `texts = "auto"` picks the PDF layer when the page has one, else OCR. Without
  either, scanned multi-rest counts are lost.
* **Multi-rest form.** With counts, Copisteria writes the standard expanded form
  (a tagged bar plus N−1 following rest bars, so bar numbers match print);
  homr writes one collapsed bar per multi-rest. The pipeline's canonical form is
  collapsed, so `[omr.copisteria] multirest_form = "collapse"` (default) folds
  Copisteria's output after the empty-bar fix (`musicocr/multirest.py`;
  followers that aren't pure whole-measure rests are kept with a logged
  warning). `"expand"` does the inverse (for collapsed input only) and `"keep"`
  leaves the output as written.
* **Empty bars** (bare `<forward>`, e.g. a multi-rest whose count it could not
  place) get a whole-measure rest (`[omr.copisteria] fix_empty`,
  `musicocr/copisteria_fixes.py`).
* **Ghost bars.** A cautionary key + time signature after a double bar can be read
  as an extra bar holding a key change and a spurious note, with the time change in
  the next bar. `[omr.copisteria] fix_ghost_bars` (default on) drops such a bar only
  when it follows a double barline, holds only key/time attributes plus 1-2 plain
  notes (no rests, ties, articulations, directions), and the next bar carries the
  complementary half of the signature; the declarations move to that bar and the drop
  is logged.
* `validate` flags `bar-count` for any engine when the source PDF has a text layer:
  measure units per system (or per page when the XML has no system marks, as
  Copisteria) are compared with the printed system-start bar numbers minus
  multi-rest counts (`musicocr/barcheck.py`), reporting ghost (extra) or missing bars.
* **Pickup bar** (page 1 only, `fix_pickup`): when Copisteria misses the printed
  opening meter it pads an upbeat bar to a full bar with a hidden rest and hides
  the time signature; the padding is dropped and the meter shown.
* **Second pass for gaps** (`second_pass`, `second_pass_scale`): the detector
  has blind spots at its default input size (e.g. the first eighth after a
  dotted quarter), but sees those notes at ~1.0 confidence one size up (which in
  turn reads bars worse). For a page whose first reading left gaps, the page is
  re-detected at `second_pass_scale`x, the extra note/rest detections are added
  to the cached detections (`musicocr/second_pass_detect.py`), the page is
  re-read, and the new version of each gap bar is spliced into the first reading
  only where it fills the gap and keeps the bar's attributes
  (`musicocr/copisteria_second_pass.py`). The first reading is kept as
  `pNNN.pass1.musicxml`. Lowering confidence cutoffs does not help: the missing
  symbols are not low-confidence detections.
* **Multi-scale second pass** (`second_pass_extra_scales` = 1.75, plus
  `second_pass_retry_scales` for gaps still open): the detector's blind spots
  depend on the input size, so several sizes are unioned in; gap bars still
  unfilled are retried at the other scales.
* **Lost bars** (`fill_empty_bars`): a measure box the detector found *empty*
  (a whole-bar rest or a bar's only note undetected) makes Copisteria merge it
  into its neighbour -- a lost bar with no gap to see. Such pages are re-detected
  one size up and the confident note/rest/multi-rest detections inside the empty
  box are added before reading.
* **Unmarked duplets/triplets** (`text_tuplets`, `musicocr/copisteria_run.py`):
  small "2"/"3" digits in the PDF text layer tag the note heads under them as
  2:3 / 3:2 tuplets (Copisteria's decoder only offers triplets), and small
  digits are no longer taken as multi-rest counts. `fix_half_tuplets` finishes
  a duplet/triplet the model marked on only some of its notes.
* **Key and meter carry-over**: `fix_key_cancel` resets to C major at a staff
  start that prints no key signature (reading.json evidence; skips multi-rest
  follower bars), `fix_time_inherit` makes a page's unprinted opening meter
  continue the previous page's.
* **Opt-in:** `small_graces` (size-based grace-note marking, tuned on timpani;
  catches beamed eighths on horn parts, so off by default) and `synth_multirest`
  (synthesise a multi-rest from a tall digit over an empty bar; can mistake a
  time-signature digit).
* **Gaps as coloured rests** (`reveal_gaps`): Copisteria draws nothing it did not
  read, so a missed symbol leaves a gap (`<forward>`), which MuseScore shows as an
  invisible rest. Each gap becomes a visible orange rest instead, so bars add up
  and every probable dropped symbol is easy to find when hand-correcting.
* `validate` flags `odd-tuplets` for any engine: bars with mixed tuplet ratios
  or a tuplet note shorter than a 32nd (Copisteria infers tuplets from spacing
  and can invent them while still summing to the right bar length).

### homr-specific pre/post-processing

`omr-eval/README.md` documents two recurring homr failure modes found across a
full multi-part piece; the pipeline now handles both automatically:

* **Empty multi-rest measures** — homr has an open upstream generator bug
  (`liebharc/homr#140`) where a correctly-recognised multi-measure rest is
  exported with a `<multiple-rest>` tag but no actual rest/duration content.
  The `omr` stage patches each page's export in place right after homr writes
  it (`[omr.homr] fix_multirest`, on by default; `musicocr/homr_fixes.py`).
* **Rhythm misreads that leave a measure's duration wrong** (e.g. a half note
  read as a whole note) can't be corrected — the actually-wrong note can't be
  identified without the scan — but they leave MuseScore's barlines out of
  alignment with neighboring measures, which makes hand-correction much more
  tedious. The `normalize` stage (after `extract`, `[stages.normalize]`, on by
  default) mechanically truncates/pads every such measure to match its time
  signature, trading note accuracy (already wrong) for a uniform grid to
  correct against. See `musicocr/measure_normalize.py`.

Neither pass can fix a *misread* multi-rest count (e.g. a printed "3" read as
"6") or a multi-rest dropped entirely — those are transformer misclassifications,
not bugs. `validate.py` flags plain whole-measure rests as a weak
(false-positive-prone) `info`-level hint that one might be a dropped multi-rest,
when running the homr engine.

### Tuplet clean-up (any engine)

A tuplet read as a mix (3:2 with 6:4 and 5:4, a bracket starting on the third note, a 0.4-beat rest)
leaves the bar the wrong length, and MuseScore spreads the damage to the next bar when it is edited.
`[stages.tuplets]` (on by default; `musicocr/tuplets.py`; also `musicocr tuplets FILE...` on existing
files) runs inside `extract` after the meter repair. In a bar that does not add up to its time
signature it rebuilds the inconsistent tuplet runs with the ratio most of their notes carry,
durations from the printed note values and whole bracket groups, and keeps the result only if the
bar then has exactly the right length. Bars that add up are never touched. Rebuilt notes are purple
with a "check: tuplet rebuilt" text (`flag`), since the fix is a guess from the printed values.

## Poor scans

If `report.md` shows most of the page missing or garbled, check the scan
resolution:

```bash
pdfimages -list -f 1 -l 1 "input/your.pdf"
```

`x-ppi` under ~150 means Audiveris doesn't have enough detail. Two levers:

1. **Rescan higher** (250–400 DPI) — beats any amount of tuning.
2. **Pre-process** — turn on `[preprocess]` in `config.toml` (or pass
   `--preprocess`). It rasterises each page at 300 DPI and runs autocontrast +
   an unsharp-mask pass before OMR. On the test scan this recovered ~8x more
   music and was the difference between Audiveris reading the clef and not.

   **Camera / phone scans** (Adobe Scan, photos) also need geometry fixes —
   turn on `deskew` and `crop_margins` in `[preprocess]`, or pass
   `--deskew --crop`:

   - **deskew** rotates each page so the staves are level. Audiveris only
     tolerates a degree or so of skew; a hand-held scan is usually more.
   - **crop_margins** trims the stray text lines above and below the music —
     the running-header line (`… - 3rd Trombone` + page number) and the footer
     (copyright, URL, logos) — that OMR otherwise turns into junk text symbols.
     It's conservative: it leaves a first page's centred title block in place
     (delete that frame once in MuseScore) rather than risk clipping a system.

   Tune all of it with:

   ```bash
   python scripts/experiment.py "input/your.pdf" --page 1
   ```

   which sweeps binarisation constants and pre-processing options on one page
   and writes `experiments/<book>/pNN/summary.md` with a render of each.

## Tuning Audiveris (Audiveris engine only)

Audiveris behaviour is controlled by "constants" and "switches". From the
pipeline: add `--constant key=value` (repeatable), or put constants in an
`[omr.profiles.*]` preset and pass `--profile`. Filter constants
(`org.audiveris.omr.image.FilterDescriptor.defaultKind`,
`...GlobalDescriptor.defaultThreshold`,
`...AdaptiveDescriptor.defaultMeanCoeff`) work from the CLI; several *switch*
constants are silently ignored there — set those in the GUI instead.

## Manual correction via the Audiveris GUI (Audiveris engine only, off by default)

Full walkthrough: **[docs/audiveris-gui.md](docs/audiveris-gui.md)**.

Short version:

1. Set `[omr] engine = "audiveris"` and `[stages.correct] enabled = true` in
   `config.toml` (the latter also makes the OMR stage save a `.omr` project
   per page).
2. `python -m musicocr run <pdf>` stops after OMR (status `paused`) and lists the
   projects to open.
3. `open -a Audiveris`; **File ▸ Open books…**; tune **Book ▸ Set book
   parameters…**, **Sheet ▸ Transcribe sheet**, fix symbols, **Book ▸ Export
   book**.
4. `python -m musicocr run <pdf> --from extract` to finish on the corrected
   export.

## Comparing engines

```bash
python -m musicocr compare input/your.pdf --engines homr,copisteria \
    --output /some/dir --transpose-instrument "horn in f"
```

Runs the same PDF through several OMR engines and writes a side-by-side report.
Ingest (rasterise/deskew/crop) runs **once**; every engine then reuses the same
`prepped/` pages and geometry sidecars, so all shared pre/post-processing
(multi-rest form, time-signature fix, normalize, transpose, validate) is applied
identically:

```
<output>/<book>/source.pdf, prepped/       shared ingest
<output>/<book>/engines/<engine>/          that engine's pages/, extract..validate
                                           artifacts, state file, report.md
<output>/<book>/compare.md, compare.json   the comparison
```

Engines that are unavailable (missing tool, or the `legato2` placeholder) are
skipped with a message. Each engine stops after `validate` unless `--convert`
is given (then convert and qa run too). Other flags: `--pages`, `--force`,
`--keep-going`, `--preprocess/--deskew/--crop` (and `--no-*`),
`--transpose-instrument`, `--transpose-semitones`.

`compare.md` has one column per engine: wall-clock per stage, failed pages,
measures/bars/notes/rests/tuplets (per page and in total, multi-rests in the
collapsed convention), validate findings by code, normalize fixes, time
signatures / keys / multi-rest counts per page, and a **measure-level diff** per
engine pair. The diff aligns the measure sequences (pitch + duration per
measure; ties and articulations ignored) with `difflib` and lists each
disagreeing range with both engines' content and a locator (page, measure on
page, running printed-bar estimate - each engine's own count, so it drifts
after an engine drops a multi-rest count) to look up in the scan. Re-running
into the same directory reuses finished pages, so stage timings then show ~0
for OMR; use `--force` for fresh timings.

Not yet: batch-TOML `engines=[...]`, ground-truth scoring (`--truth`), LEGATO 2.

## No-scan smoke test

```bash
python scripts/make_sample.py          # writes samples/sample.pdf from a known score
python -m musicocr run samples/sample.pdf
```

A clean synthetic score should come back with few or no findings.

## Layout

```
musicocr/
  cli.py               # `run` / `compare` / `doctor` / ...
  compare.py           # engine comparison: metrics, measure diff, runner
  config.py            # config.toml loader
  pipeline.py          # stage protocol + ordered runner
  report.py            # report.md / report.json
  homr_fixes.py         # homr multi-rest generator-bug fix + dropped-multirest heuristic
  measure_normalize.py  # measure-duration normalization (homr rhythm misreads)
  transpose.py / instruments.py  # semitone transposition + shared instrument table
  stages/               # ingest, omr (dispatches to omr_homr/omr_audiveris),
                         # correct, extract, normalize, transpose, validate, convert, qa
tests/                # pytest unit tests for homr_fixes / measure_normalize
config.toml           # tool paths, OMR engine/profiles, output formats
scripts/setup.sh      # environment bootstrap (main pipeline)
scripts/make_sample.py
scripts/batch_run.py   # resumable multi-part batch (--batch SPEC.toml; see scripts/batch.example.toml;
                       # logic in musicocr/batch.py, local specs in git-ignored batches/)
omr-eval/setup.sh     # installs homr's own venv (default OMR engine)
omr-eval/*.py         # standalone CLI wrappers for ad-hoc eval work, thin
                       # over the same musicocr modules the pipeline uses
```

Run the test suite with:

```bash
.venv/bin/python -m pytest tests/
```
