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
bash omr-eval/setup.sh         # creates the engine venvs (homr, copisteria, legato2)
source .venv/bin/activate
python -m musicocr doctor       # verify all tools are found
```

Requires Python 3.11+ (setup.sh picks python3.13/3.12/3.11, or falls back to uv). Manual install: `pip install -e .` (add `.[test]` for pytest). An editable install is required: `config.toml` and the homr venv default are located relative to the repo checkout.

## Usage

```bash
# drop a PDF in input/ (or point anywhere)
python -m musicocr run input/my-part.pdf
```

Only the deliverables stay in `output/<book>/` indefinitely:

| File | What it is |
|---|---|
| `<book>.musicxml` | interchange master — pages stitched into one score |
| `<book>.mscz` | open/edit in MuseScore |

Everything else goes to `output/<book>/ttl=30d/` (set by `[output] scratch_ttl`), which the
`~/Dev/ttl-sweep` launchd job prunes by file age. Paths below are relative to that folder:

| File | What it is |
|---|---|
| `<book>.pNNN.musicxml` | per-page MusicXML (kept even when merged; `combine` reads these) |
| `<book>.mid` | quick aural check |
| `render.pdf` | MuseScore's rendering of the OMR result |
| `qa/src-*.png`, `qa/omr-*.png` | source scan vs render, page by page |
| `report.md` / `report.json` | stage log + validation findings + QA images |
| `pages/pNNN/*.log` | per-page OMR engine logs (homr or Audiveris) |
| `pages/…/*.omr` | Audiveris project files |
| `source.pdf`, `prepped/` | copy of the input and its rasterised pages |
| `written/`, `pre-retouch/` | pre-transpose / pre-retouch backups |
| `.musicocr-state.json` | resume state (after expiry, re-run from `ingest`) |

Read `ttl=30d/report.md` first. Findings are ranked 🔴 error / 🟡 warn / 🔵 info, with
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
`musicocr/fixes/timesig.py` re-derives each numerator from the bars it governs, treating
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
on `run`). The untransposed file is kept in `output/<book>/ttl=30d/written/`, and re-running
`--from transpose` always starts from that copy, so shifts never stack. The `qa`
render is at the new pitch in `concert` mode, so it won't visually match a written-pitch scan.

Pitches and key signatures move together and are respelled (C major horn part →
F major). The shared instrument table lives in `musicocr/scoreops/instruments.py`; values
are semitones to add to written pitch, and clef-dependent instruments (baritone /
tenor horn: treble −14, bass 0) are resolved from each part's first clef.
Unknown part names are skipped, never guessed.

### Options

```
python -m musicocr run INPUT.pdf \
  --pages 1,4-5          # only these pages
  --engine homr|audiveris|copisteria|legato2 # OMR engine (default: config [omr] engine; built-in default homr)
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

`musicocr combine --normalize-marks [RULES]` runs a post-pass (`musicocr/scoreops/marks.py`) that uses the
other parts as evidence to tidy dynamics and hairpins. `RULES` is a comma list; bare means
everything but `fill`. `musicocr collection combine` runs `dedupe,reach,placement,lines`.

- `dedupe` -- the same dynamic twice in one bar (e.g. above and below the staff): keep one.
- `hairpins` -- a hairpin that stops on the note it starts on is extended: to where the other parts
  end it, else to the next dynamic if that is within a bar of the bar's end, else to the end of the bar.
- `reach` -- a hairpin that stops 2-4 beats short of the part's next dynamic runs on to it (measured on GB
  pieces 1-3 against hand corrections: OMR ends the line on the last note it covers instead of after it).
- `placement` -- every dynamic and hairpin goes on the side most of them are on (parts: below).
- `lines` -- "cresc." / "dim." printed as text becomes a dashed text line that runs to the next dynamic.
- `fill` (opt-in, or `all`) -- a dynamic that at least half of a section's playing parts carry
  (min. 4 parts) is added to the section's parts that play but carry none.

Inserted dynamics and moved hairpin stops are coloured blue; every change is in the combine report.
`musicocr marks IN.musicxml --rules all` runs the same pass on an existing file, and
`scripts/compare_marks.py before.musicxml cleaned.musicxml` scores it against a hand-cleaned score.

## OMR engine

`[omr] engine` in `config.toml` (or `--engine`) picks the transcription engine:

* **`homr`** (the built-in default; this checkout's `config.toml` currently selects
  `copisteria`, so check `[omr] engine` there) — an end-to-end transformer OMR model. No whole-book
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
* **`legato2`** — [LEGATO 2](https://huggingface.co/guangyangmusic/legato-2) (arXiv:2607.05769,
  Apache-2.0, 0.9B): finds systems on the page, transcribes each to ABC notation, which is converted
  to MusicXML with music21 (`musicocr/engines/legato2/abc_convert.py`). Own venv under `omr-eval/legato2/`
  (`omr-eval/setup.sh`); the weights are gated on Hugging Face (log in and accept the terms), and
  the inference code (`legato.pipeline.Legato2`) must be present in `omr-eval/legato2/repo`.
  Settings in `[omr.legato2]` (`model`, `device`, `beam_size`, `yolo_conf`). No Copisteria-style
  post-fixes: you get the model's raw reading, which is what `musicocr compare` wants.
* **`audiveris`** — the original classical pipeline. Still the better choice
  for full scores / multi-staff systems, and the only engine with a hand
  correction workflow (see below).

`--pages` + `--no-merge` is the combination for a PDF that's really several
independent parts glued together (e.g. a full part set). Run each part's page
range on its own; merging different instruments into one score is meaningless.

Stages, in order: `ingest → omr → correct → extract → normalize → transpose →
validate → convert → qa` (`transpose` is optional, off by default). Re-running `--from extract` after editing the `.mxl`/`.omr` is
the normal way to iterate without paying for another full OMR pass.

### Automatic repairs ("fixes")

Every automatic repair of the OMR output is a named, switchable *fix*, declared once in
`musicocr/fixes/registry.py` and run by one runner (`musicocr/fixes/base.py`): same switch, same log
format, and one error policy (a fix that raises is logged and skipped, never fatal to the page).

```bash
python -m musicocr fixes                      # every fix, its engine, and whether it is on
python -m musicocr run X.pdf --no-fix tuplets # suppress one for a run (repeatable; --fix forces one on)
```

In `config.toml`, `[fixes] name = false` turns one off for good. A name left unset keeps its older key
(`[omr.copisteria] fix_*`, `[omr.homr] fix_multirest`, `[stages.tuplets]`, `[stages.normalize]`) or its
default (on). Fixes declare ordering constraints (`after=`), which the registry checks when it loads.

### Copisteria-specific pre/post-processing

* **Page text → multi-rest counts.** Copisteria only reads the count over a
  multi-measure rest from OCR text boxes (`pNNN.texts.json`). For born-digital
  PDFs the `omr` stage builds that file from the PDF's text layer
  (`[omr.copisteria] texts = "pdf-layer"`, or the default `"auto"`; `musicocr/text/page_text.py`), mapping
  the digit boxes onto the preprocessed page via the geometry `preprocess`
  records in `prepped/pNNN.geom.json` (DPI, deskew angle, crop offset).
  Scanned PDFs have no text layer; `texts = "ocr"` instead OCRs the prepped page
  image (`musicocr/text/ocr_backends.py`, pluggable: RapidOCR from the homr venv and/or
  macOS Vision through a tiny Swift helper compiled on first use; `ocr_backend`
  picks one or the union, the default, since each misses some isolated numerals) and
  `texts = "auto"` picks the PDF layer when the page has one, else OCR. Without
  either, scanned multi-rest counts are lost.
* **Small count digits.** Old-style engravings (Paxman, Szeged, Wagner parts) set a multi-rest's count only 2.0-2.5 staff
  spaces tall. A digit that size is a count when it sits over the middle of the multi-rest H-bar, or over an empty bar of at
  least 6 spaces' width where the detector found only a short thick block (read as a `barLine_single`, a whole rest or a
  breve: it is re-labelled `multiRest`), is not stacked with another digit (time signature) and is not inside a circled
  rehearsal number (`musicocr/text/page_text.py`, `engines/copisteria/run.py`).
* **Multi-rest form.** With counts, Copisteria writes the standard expanded form
  (a tagged bar plus N−1 following rest bars, so bar numbers match print);
  homr writes one collapsed bar per multi-rest. The pipeline's canonical form is
  collapsed, so `[omr.copisteria] multirest_form = "collapse"` (default) folds
  Copisteria's output after the empty-bar fix (`musicocr/fixes/multirest.py`;
  followers that aren't pure whole-measure rests are kept with a logged
  warning). `"expand"` does the inverse (for collapsed input only) and `"keep"`
  leaves the output as written.
* **Empty bars** (bare `<forward>`, e.g. a multi-rest whose count it could not
  place) get a whole-measure rest (`[omr.copisteria] fix_empty`,
  `musicocr/engines/copisteria/fixes.py`).
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
* **Long hairpins** (`long_hairpins`, `musicocr/text/hairpins.py`): a hairpin running over several bars is too
  long for the detector; two straight lines between the staves meeting in a point are added to its detections.
* **Pickup bar** (page 1 only, `fix_pickup`): when Copisteria misses the printed
  opening meter it pads an upbeat bar to a full bar with a hidden rest and hides
  the time signature; the padding is dropped and the meter shown.
* **Second pass for gaps** (`second_pass`, `second_pass_scale`; in a score, part by part): the detector
  has blind spots at its default input size (e.g. the first eighth after a
  dotted quarter), but sees those notes at ~1.0 confidence one size up (which in
  turn reads bars worse). For a page whose first reading left gaps, the page is
  re-detected at `second_pass_scale`x, the extra note/rest detections are added
  to the cached detections (`musicocr/engines/copisteria/detect.py`), the page is
  re-read, and the new version of each gap bar is spliced into the first reading
  only where it fills the gap and keeps the bar's attributes
  (`musicocr/engines/copisteria/second_pass.py`). The first reading is kept as
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
* **Unmarked duplets/triplets** (`text_tuplets`, `musicocr/engines/copisteria/run.py`):
  small "2"/"3" digits in the PDF text layer tag the note heads under them as
  2:3 / 3:2 tuplets (Copisteria's decoder only offers triplets), and small
  digits are no longer taken as multi-rest counts. `fix_half_tuplets` finishes
  a duplet/triplet the model marked on only some of its notes.
* **Stray slurs** (`slur_filter`, `musicocr/engines/copisteria/run.py`): a curve between two or three
  neighbouring notes across a bar line whose box is 2.5+ staff spaces tall (real slurs there are flat) is
  not written as a slur. Measured on the hand-cleaned GB pieces 2-3: 15 of the 18 slurs removed by hand
  across bar lines were such curves, 4 of the 23 kept ones too. The detector's confidence does not
  separate them. Same-pitch curves (ties) are left to `slur_ties`.
* **Key and meter carry-over**: `fix_key_cancel` resets to C major at a staff
  start that prints no key signature (reading.json evidence; skips multi-rest
  follower bars), `fix_time_inherit` makes a page's unprinted opening meter
  continue the previous page's.
* **Scanned big-digit clean-up** (`musicocr/text/page_text.py`, runs with the page text on scans): a
  count digit is "big" from 3.5 staff spaces up (`MIN_TALL_SP`; OCR boxes of scanned counts measure
  3.7-10). `repeatMark_*` (segno/coda) detections sitting on a big digit are dropped
  (`drop_digit_repeat_marks`: a numeral's curl reads as a segno), and a weak `note_breve` under a big
  digit, or a very weak one alone in its bar, is re-labelled as the multi-rest H-bar it is
  (the bar was otherwise read as an 8-beat breve swallowing its neighbour).
* **Count-digit reader** (`read_count_digits`, on by default; `musicocr/text/countdigits.py`): Vision/rapidocr often miss
  a bold lone digit over a multi-rest H-bar or misread it ("9" as "6"). For every detected `multiRest` the digit(s) above it
  are cut from the page image (components of count-glyph size on one baseline, centred over the bar, glued pairs split), and
  classified by nearest template: a shipped bank (`musicocr/text/data/countdigits_bank.npz`, built by `scripts/build_digit_bank.py`
  from the digits OCR read confidently in several books), plus the part's own OCR-read counts, plus synthetic bold fonts
  as fallback, against letters/dynamics as a "not a digit" class. A count below 2, an unsure match, a number at a system's start
  (bar number) or in a circled rehearsal number is never written; an OCR digit is replaced only by a firm, overlapping read.
  The count goes into `<page>.texts.json` (`"source": "countdigits"`), so the engine's count rules are unchanged.
  `scripts/countdigits_offline_check.py output/<corpus>` lists the cached page-parts the reader would change.
* `synth_multirest` (on by default) synthesises a multi-rest from a tall, centred,
  non-stacked count digit over an empty bar (stacked digits are a time signature).
  **Opt-in:** `small_graces` (size-based grace-note marking, tuned on timpani;
  catches beamed eighths on horn parts, so off by default).
* **Gaps as coloured rests** (`reveal_gaps`): Copisteria draws nothing it did not
  read, so a missed symbol leaves a gap (`<forward>`), which MuseScore shows as an
  invisible rest. Each gap becomes a visible orange rest instead, so bars add up
  and every probable dropped symbol is easy to find when hand-correcting.
* `validate` flags `odd-tuplets` for any engine: bars with mixed tuplet ratios
  or a tuplet note shorter than a 32nd (Copisteria infers tuplets from spacing
  and can invent them while still summing to the right bar length).

### Text layer: tempo, expression and technique words (Copisteria)

The detector reads symbols and the page text only supplies digits, so printed words
used to be lost. `[omr.copisteria.text_layer]` (default `mode = "flag"`) recovers them as
MusicXML `<direction>`s: `musicocr/text/words.py` gets the words (the PDF text layer when the
page has one, else Vision OCR of bands around each staff, read at ~1.5x under two resampling
filters and unioned, because Vision garbles small italic words in one crop and reads them in
another), `musicocr/text/classify.py` + `musicocr/text_lexicon.toml` classify the phrases, and
`musicocr/text/layer.py` filters page furniture and writes `<page>.textlayer.json`, which
`copisteria_run.py` turns into directions in the staff and bar the phrase sits in.

* Tempo: `Sl. Faster (quarter) = 103` becomes `<words>` + `<metronome>` + `<sound tempo>` (a dotted
  beat unit is read from the glyph's pixels; the OCR text cannot tell `J.` from `J`). Tempo
  change (`poco accel.`, `rall. poco a poco`), expression (`maestoso`, `molto cantabile`, `Solo`),
  technique (`marc.`, `sim.`, `to St. Mute`, `Open`), cue labels (`Vln. 1`, `(+Tpt2-3)`) and
  navigation (`Segue to 3M21`) become `<words>`. Dynamics words are ignored (the detector draws
  them); only `<direction>` elements are added, never notes, rests or timing.
* Precision first: a phrase gets a kind only when every word is in the lexicon (one OCR error is
  tolerated in words of 5+ letters). Other text above a staff is kept as purple
  `check: <text>` words (`mode = "flag"`); `mode = "on"` keeps classified text only, `"off"` disables.
  Text sitting on detected music symbols (accents read as letters), the title block above the first
  staff, tempo marks under a staff and (OCR) rehearsal letters are never emitted.
* Extend the vocabulary with `lexicon = ["my_terms.toml"]` (same format as `text_lexicon.toml`; lists are
  appended). `<page>.textlayer.dropped.json` next to a page lists what was left out and why.
* Not done (v1): cue-size notes (`5.7`), lyrics/chord symbols, title metadata, tempo voting across
  parts at combine time (each part keeps its own tempo marks).

### homr-specific pre/post-processing

`omr-eval/README.md` documents two recurring homr failure modes found across a
full multi-part piece; the pipeline now handles both automatically:

* **Empty multi-rest measures** — homr has an open upstream generator bug
  (`liebharc/homr#140`) where a correctly-recognised multi-measure rest is
  exported with a `<multiple-rest>` tag but no actual rest/duration content.
  The `omr` stage patches each page's export in place right after homr writes
  it (`[omr.homr] fix_multirest`, on by default; `musicocr/engines/homr/fixes.py`).
* **Rhythm misreads that leave a measure's duration wrong** (e.g. a half note
  read as a whole note) can't be corrected — the actually-wrong note can't be
  identified without the scan — but they leave MuseScore's barlines out of
  alignment with neighboring measures, which makes hand-correction much more
  tedious. The `normalize` stage (after `extract`, `[stages.normalize]`, on by
  default) mechanically truncates/pads every such measure to match its time
  signature, trading note accuracy (already wrong) for a uniform grid to
  correct against. See `musicocr/fixes/measure_normalize.py`.

Neither pass can fix a *misread* multi-rest count (e.g. a printed "3" read as
"6") or a multi-rest dropped entirely — those are transformer misclassifications,
not bugs. `validate.py` flags plain whole-measure rests as a weak
(false-positive-prone) `info`-level hint that one might be a dropped multi-rest,
when running the homr engine.

### Tuplet clean-up (any engine)

A tuplet read as a mix (3:2 with 6:4 and 5:4, a bracket starting on the third note, a 0.4-beat rest)
leaves the bar the wrong length, and MuseScore spreads the damage to the next bar when it is edited.
`[stages.tuplets]` (on by default; `musicocr/fixes/tuplets.py`; also `musicocr tuplets FILE...` on existing
files) runs inside `extract` after the meter repair. In a bar that does not add up to its time
signature it rebuilds the inconsistent tuplet runs with the ratio most of their notes carry,
durations from the printed note values and whole bracket groups, and keeps the result only if the
bar then has exactly the right length. Bars that add up are never touched. Rebuilt notes are purple
with a "check: tuplet rebuilt" text (`flag`), since the fix is a guess from the printed values.

### Whole-bar rests (any engine)

A whole-bar rest that also holds cue-size notes (read as grace notes) comes out 4.5 or 7 beats long,
so MuseScore shows an irregular bar. `musicocr/fixes/barrests.py` runs inside `extract` right after the
meter repair (no config switch): a bar whose only sounding content is one rest longer than the time
signature's bar is cut to the bar. Shorter rests (pickups) and bars with notes are not touched.

### Movements (any raster engine)

A part or score whose movements are headed by a centred Roman numeral (`I.`, `II`) or "Version A", "Version B" gets, for each one, the
numeral and tempo mark over its first bar, a final barline before it, and in the `.mscz` a section break
(new line, bar numbers restart at 1). `musicocr/scoreops/movements.py` OCRs each page image during
`extract` (a few seconds a page); `retouch` and `convert` write the result. Switch off with
`--no-movements` or `[stages.movements] enabled = false`.

Limits: the headings need a merged score and the engine's page images (not Audiveris). A numeral the
OCR misses is reported and the others are still used. The metronome note is read as dotted or not from
the image, falling back to the meter; other note symbols in a tempo line (`♪ = ♩`) are left out.

### Layout as the original

`convert` gives a merged `.mscz` the original's bars per line and lines per page: line and page breaks
from the system marks the engine writes, and page size, margins and staff size from the original page
(`musicocr/scoreops/layout.py`). MuseScore then lays the score out and the result is checked: a line
that does not fit gets a lower layout stretch on its bars (smaller staves if many lines wrap), an
overflowing page tighter system spacing. The log says what still differs. Switch off with
`[stages.layout] enabled = false`.

For a score made earlier, hand edits included (a copy is written, `<book>.layout.mscz`):

```
musicocr layout output/<book>
```

It replaces line and page breaks, keeps section breaks, and refuses a score whose bar count no longer
matches the pages read. It needs the per-page files under `ttl=*/`, so run it before they are pruned.
Tried so far on clean 600 dpi and born-digital scans only; it needs the copisteria engine's system marks.

## Reviewing a score against the original (`musicocr review`)

A local page that compares a `.mscz` with its original line by line, independent of the pipeline:

```
musicocr review score.mscz --source original.pdf
```

Each part's line (one staff of one line of the original) shows the original, the score as MuseScore draws it (stretched so the bar lines meet) and an
overlay (red: original, blue: score). `--source` is remembered. The score needs the original's line
breaks (see above).

* **D** marks a line as right. Click a picture to mark a spot, with or without a comment.
* **S** sends the line to Claude and moves on; with no marks Claude looks for the differences itself.
* In a score, each part's lines are checked and sent separately: work **system by system** or **part by
  part**, and tick **show other parts** to see the rest of the system.
* A multi-measure rest is one block; marks and fixes inside it use its real bar numbers. A wrong rest
  *count* means adding or removing bars, which the tool cannot do: fix that in MuseScore.
* **Whole line** (scores with several parts) shows only the overlay, all parts of a line stacked as one
  system. Click a part to mark it. **D** accepts every part; **S** sends only the marked parts to Claude
  and accepts the others. **B** shows the original alone.
* **Page re-check** shows a page's lines together with what Claude changed: accept, undo, or mark again.

Fixes run in the background through the `claude` command, which must be installed and signed in
(run `claude` once and `/login`); until then sent lines wait. Claude may only read the job's folder;
the app checks its replacement bars (well formed, right length) before writing them into the score.
Keep the score closed in MuseScore while lines are with Claude. A copy from before the first fix is
kept as `<score>.before-review.mscz`, progress in `<score>.review.json`, and a line changed after you
marked it right is flagged. Edits you make in MuseScore are picked up when you save.

The original must show every part on every line (no hidden empty staves, and no one-line percussion
staves). Tried on clean scans only.

## Score books (every part on each page)

A book of many pieces printed as a score is a collection with a single "book" (the score):
`workflows/ghs-xmas-score/collection.toml` is the example. `musicocr collection split` cuts it into one PDF per
piece, `musicocr collection batch` writes the batch, and `scripts/batch_run.py` runs it. Each piece comes out as
one score (`output/<name>/<key>/<key>.mscz`); there is no `combine` step.

* A page whose staves are all joined into systems is read as a score even though the batch names an instrument
  (`single_staff = "auto"`); several parts of one instrument are numbered ("Horn in F 1" ... "4").
* Contents lines whose number the OCR lost are still looked for by number on the music pages. Credits such as
  "Version A (Arr. ...)" move from the title to the composer line.
* "Version A" / "Version B" headings are treated like movements (below).
* Dynamics and hairpins between two staves go to the staff above when the page's clear cases sit below their staff.

## Scanned parts, bar numbers and scenes

`combine` aligns parts by their printed bar numbers. Scanned PDFs have no text layer, so
`pdf_words_or_ocr` (`musicocr/alignment/systems.py`) finds each staff by line projection and OCRs
the strips beside it with macOS Vision: above the left end (system-start numbers) and below it
(engravers that number every bar), keeping whichever zone gives more strictly increasing system
starts, and reading printed multi-rest ranges ("130-133") from the lower strips. Results are cached
per PDF page. The multi-rest ranges and the highest printed bar number (`printed_max_bar`) then pin
down bars a part lost: `align_to_target` never puts a played bar in a printed multi-rest and lets
the other parts' meter changes steer where a lost bar lands.

With Copisteria the OMR marks the first bar of each system (`<print new-system="yes"/>`), and the number read
at each staff's start anchors that system (`ocr_page_systems`), so a page is aligned system by system
(`musicocr/assemble/voltas.py`, `_lay_out_segments`): a bar read too many or lost moves nothing outside its
own system. Printed numbers count a first and a second ending as one bar while the OMR reads both, so the
endings that most parts read at the same number give a *grid* with one slot per notated bar (`Grid`); every
page start, multi-rest span and printed total is converted onto it. Scans of a bound book: the ink at the page's
left edge no longer joins staves into systems, and `[omr.copisteria] single_staff` (auto for a part with an
instrument) keeps every staff a system of its own.

Film-score parts are a run of short *scenes* (cues) whose bar numbers restart in each one:

```bash
.venv/bin/python scripts/combine_scenes.py --batch batches/NAME.toml [--cues NAME.cues.json] \
    [--engine copisteria] [--only 1M1,4M30] [--detect] [--convert]
```

It finds each scene in every part PDF (`musicocr/assemble/scenes.py`: the title block starting a page, and the
"0:16 until 1M4" box ending it), combines the scenes one at a time (an upbeat before bar 1 is set
aside during alignment and put back), and strings them into one `NAME_scenes.musicxml`: a rehearsal
mark opens each scene on a new system and the box text closes it. `--convert` also writes the
`.mscz` and adds a *section break* before each scene (`musicocr/assemble/mscz_sections.py`), because
MuseScore's MusicXML import turns `new-system` into a plain line break and so never restarts the bar
numbers at 1.

## Collections of short pieces (`musicocr collection`)

A collection is a set of part-books that each hold the same many short pieces, several to a page (hymn and
quartet books). A spec file (`musicocr/collection/spec.py`; example `workflows/gb/collection.toml`) names the
books and the table-of-contents pages, then:

```bash
python -m musicocr collection split   SPEC.toml   # one PDF per piece and book (TOC + OCR to find each piece)
python -m musicocr collection batch   SPEC.toml   # write batches/<name>.toml: a part per piece and book
python scripts/batch_run.py --batch batches/<name>.toml   # OMR every part
python -m musicocr collection combine SPEC.toml [--pieces 1-3] [--redo]   # one score per piece + summary table
```

## Ensemble books without bar numbers

Quartet/ensemble books print no bar numbers (only circled rehearsal marks), so `combine` cannot align
the parts by number. With `--no-bar-numbers` (or `bar_numbers = false` in the batch) each part is one
run of bars, the total is the plurality of the parts' lengths, and a part of the wrong length is
aligned to the others by the *rhythm* of its bars (`musicocr/assemble/unnumbered.py`); inserted rest bars are
flagged "check". Pick one piece's parts with `--only`; `title` / `composer` on the batch parts name
the combined score. Per-part options for such books: `old_bass_clef` (old-notation horn editions:
octave-up bass clef, notes up an octave), `title`, `composer`, and a per-part `source_pdf` when each
part is its own PDF (`musicocr/scoreops/retouch.py`, `musicocr/batch.py`).

**A score page inside a part book.** A trio printed on staves joined by a system barline in every horn's book
would read as one part with all its staves (three times the bars). `staff_rank = N` on a batch part (or `--staff-rank N`
on `run`) keeps staff N of every multi-staff system and blanks the rest of the page image before the OMR
(`musicocr/scoreops/staffpick.py`; pages of single staves are left alone; the original page is kept as `pNNN.full.png`)). `staff_rank = "auto"` takes N from the number the part's label ends with ("... - Horn 3") and touches only pages that are wholly a score (every staff in a joined system of one size, so a stray bracket or a spiral binding never triggers it); a part whose number exceeds the staves of the page (the fourth horn of a trio) still needs `score = false`. The systems of a page are told by the system barline across the gaps or, on a warped scan where that fails, by the spacing of the staves (staves are also looked for in narrow column windows, which survive a bent page).

**Rehearsal marks, whatever the edition prints.** Every page is searched for rings *and* boxes (no setting) and the
label inside each is read from a contact sheet (a lone digit or letter defeats Vision, a row of them does not):
`<rehearsal>#12</rehearsal>`, `#C`, or `#` when unread. `musicocr/assemble/labels.py` decides the book's style, number
or letter, by vote; a label of the other kind is a misread (an 8 for a B) and counts as unread. Unread labels are filled
in the style (the one after the last, 1 after a restart), so a book comes out all numbers or all letters. Marks that at
least half the parts print define stretches (`musicocr/assemble/anchored.py`: the plurality length, a part that lost or
invented a bar is aligned by rhythm inside its own stretch only); with fewer than three agreed marks, or when the
book has bar numbers, the usual alignment is kept and the marks serve as a second opinion: a part whose copy of an
agreed mark sits more than a bar from the others' is reported and the bar flagged ("check"). Marks the PDF text layer
carries (plain `A`, `12`) count for that check too. A lone `1` is the label Vision most often drops.

What `combine` also does for such a book, by voting over the parts (the same marks are printed in every part):

- **Rehearsal marks.** Found by shape (above), placed above their bar (or under the staff), written once with
  their printed numbers or letters (`musicocr/assemble/rehearsal.py`; needs two parts to agree).
- **Repeats and 1st/2nd endings** that two or more parts read are written to every part
  (`musicocr/assemble/repeats.py`).
- **Key changes** a part hung on a system's first bar move to the bar another part reads the glyph on
  (`slide_key_changes` in `combine.py`).
- **Opening tempo word** ("Allegro", "Adagio", "Presto" ...) becomes a tempo mark at MuseScore's palette speed
  (`musicocr/assemble/text_tempo.py`); `rit.` / `a tempo` stay text.

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
     A strip that holds a staff is never trimmed (a first line under a movement
     heading looks like a header block).

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

Engines that are unavailable (missing tool or code) are
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

Not yet: batch-TOML `engines=[...]`, ground-truth scoring (`--truth`).

## No-scan smoke test

```bash
python scripts/make_sample.py          # writes samples/sample.pdf from a known score
python -m musicocr run samples/sample.pdf
```

A clean synthetic score should come back with few or no findings.

## Layout

```
musicocr/
  cli.py  config.py  pipeline.py  state.py  artifacts.py  errors.py  report.py  util.py
                       # the pipeline core: CLI, config.toml loader, stage runner, artifacts, reports
  stages/              # ingest, omr (dispatches to engines/), correct, extract, normalize, retouch,
                       # transpose, validate, convert, qa -- one `run(ctx)` each
  engines/             # registry.py (which engines exist), raster.py (shared per-page loop),
                       # audiveris.py, homr/, copisteria/, legato2/ -- each: stage.py (+ its own fixes,
                       # helper scripts run in the engine's venv)
  fixes/               # the Fix abstraction (base.py), the registry of every automatic repair
                       # (registry.py) and the engine-agnostic repairs: timesig, barrests, tuplets,
                       # multirest, measure_normalize
  musicxml/            # shared MusicXML helpers (measure lengths, writing files, page merge)
  scoreops/            # transpose, instruments, retouch, marks (dynamics/hairpin consistency), layout
  review/              # line-by-line proofreading page, fixes by Claude (`musicocr review`)
  text/                # page text and OCR, count-digit reader (countdigits.py), tempo/expression/technique words -> <direction>
                       # (page_text, ocr_backends, words, classify, layer, lexicon.toml)
  assemble/            # combine parts by printed bar number: combine, alignment/, unnumbered,
                       # scenes, mscz_sections, text_system, text_tempo
  collection/          # books of many short pieces: split by table of contents, batch spec, combine all
  batch.py compare.py barcheck.py preprocess.py pages.py   # batch runs, engine comparison, bar-count
                       # check, scan clean-up, page specs
workflows/            # project-specific data and notes (workflows/README.md); nothing in musicocr/ imports them
tests/                # pytest unit tests (one file per module; run with the command below)
config.toml           # tool paths, OMR engine/profiles, output formats
scripts/setup.sh      # environment bootstrap (main pipeline)
scripts/make_sample.py
scripts/combine_scenes.py  # combine scene-structured (film-cue) parts into one score
scripts/batch_run.py   # resumable multi-part batch (--batch SPEC.toml; see scripts/batch.example.toml;
                       # logic in musicocr/batch.py, local specs in git-ignored batches/)
omr-eval/setup.sh     # installs homr's own venv (default OMR engine)
omr-eval/*.py         # standalone CLI wrappers for ad-hoc eval work, thin
                       # over the same musicocr modules the pipeline uses
```

Run the test suite and the linter (config in `pyproject.toml`) with:

```bash
.venv/bin/python -m pytest tests/
.venv/bin/ruff check musicocr tests scripts workflows   # pip install -e '.[dev]' for ruff
```
