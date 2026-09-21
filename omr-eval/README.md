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

If `omr-eval/homr/.venv/bin/homr` fails with `bad interpreter: .../python3:
no such file or directory`, its shebang was baked with an old absolute path
(e.g. if the `omr-eval/` directory got moved after the venv was created) and
no longer matches. Work around it without recreating the venv by invoking the
interpreter explicitly: `omr-eval/homr/.venv/bin/python
omr-eval/homr/.venv/bin/homr <page.png>`. Re-running `setup.sh` fixes it
properly.

## Findings so far (2026-09-13): full piece, all 8 horn parts

Ran homr on the whole `Bach_Preludio e Fuga - 8 Horns - Parts.pdf` (16 pages,
2 per horn — p001/p002 = horn1, p003/p004 = horn2, ... p015/p016 = horn8),
not just horn1 as before. Fed it the same preprocessed page PNGs the pipeline
already produced for each horn's Audiveris run
(`output/bach-horn-parts/hornN/prepped/pNNN.png`) — confirmed byte-identical
across horns since preprocessing runs on the same source PDF regardless of
which horn's ingest triggered it.

| Page | Measures | Notes | Rests |
|---|---|---|---|
| horn1 p001 | 39 | 126 | 32 |
| horn1 p002 | 36 | 119 | 24 |
| horn2 p003 | 39 | 144 | 26 |
| horn2 p004 | 40 | 167 | 18 |
| horn3 p005 | 38 | 130 | 25 |
| horn3 p006 | 41 | 119 | 31 |
| horn4 p007 | 43 | 136 | 24 |
| horn4 p008 | 40 | 125 | 19 |
| horn5 p009 | 35 | 125 | 23 |
| horn5 p010 | 27 | 90 | 18 |
| horn6 p011 | 25 | 59 | 22 |
| horn6 p012 | 39 | 122 | 17 |
| horn7 p013 | 34 | 113 | 25 |
| horn7 p014 | 27 | 87 | 13 |
| horn8 p015 | 24 | 59 | 19 |
| horn8 p016 | 38 | 135 | 13 |

All 14 new pages (horn1 was already done) ran and parsed as one coherent
document each, same as horn1 — no fragmentation across the whole piece.

**`fix_multirest_xml.py`** (the generator-bug fix from 2026-09-12) fixed 11
measures across 6 pages (horn2 p003 ×2, horn3 p005 ×1, horn4 p007 ×2, horn5
p009 ×3, horn6 p011 ×1, horn7 p013 ×2). Two pages hit a case the script can't
handle: `horn3_p006` measure 17 and `horn8_p016` measures 12 and 17 are
tagged `<multiple-rest>` but occur before the script has seen any
divisions/time-signature in that file, so it can't compute a duration and
skips them (logged as a WARNING, not silently dropped) — worth a manual check
against the scan on those three measures specifically.

**`check_multirest.py`** grand-total-duration cross-check across all 8 parts
came back with no agreement at all — every part but horn1 disagrees with the
(arbitrary) majority:

| Part | Total (quarter notes) |
|---|---|
| horn1 | 318 |
| horn2 | 350.25 |
| horn3 | 331 |
| horn4 | 354 |
| horn5 | 277.5 |
| horn6 | 297 |
| horn7 | 287 |
| horn8 | 299 |

It also flagged 19 plain whole-measure rests across 10 pages as "not tagged
as a multi-rest — could be a dropped multi-rest," i.e. candidates for the
2026-09-12 "model silently dropped the multi-rest tag" failure mode, spread
across the whole piece rather than isolated to horn1.

**`check_measure_duration.py`** found internal measure-duration mismatches
(measure content doesn't sum to the time signature) on 12 of the 16 pages —
only horn1 p002, horn3 p006, horn5 p010, horn7 p014, and horn8 p016 came back
clean. The dominant pattern is the same one already documented for horn1:
`6.0 beats, expected 4.0` (one note read a step too long right before a fast
run that should have summed to 4.0 on its own), repeated dozens of times
across parts.

**Conclusion:** the structural strengths seen on horn1 (coherent per-page
parsing, no system-break fragmentation, measure counts in the right
ballpark vs. Audiveris) hold up across the whole piece. But the rhythm risks
also hold up at full scale, not just on horn1 — no two of the 8 parts agree
on total duration even after the known generator-bug fix, and 3 in 4 pages
have at least one internal measure-duration mismatch. Per the 2026-09-12
root-cause finding, this isn't fixable by preprocessing or the post-processing
script; it's the model's rhythm classification. Treat this full run the same
way as the single-part finding: a structurally-solid draft that still needs a
hand-QA pass per part before trusting the rhythm, not a replacement for
Audiveris + correction on its own.

### Per-part concatenation and measure-length normalization (2026-09-13)

Two more post-processing scripts, meant to run in this order after
`fix_multirest_xml.py`, to get homr's output into better shape for
hand-correction in MuseScore:

```bash
# 1. One MusicXML per part instead of one per page. Reuses the same
#    measure-append logic the main pipeline already uses to stitch
#    Audiveris's per-page output (musicocr.stages.extract._concat): the
#    first page's score (identification, part-list, attributes) is kept,
#    each subsequent page's measures are appended and renumbered 1..N. Only
#    the first page's non-measure data survives -- that's the deduplication.
.venv/bin/python omr-eval/concat_pages.py \
    horn1=omr-eval/pages/bach_horn1_p001.musicxml,omr-eval/pages/bach_horn1_p002.musicxml \
    horn2=omr-eval/pages/bach_horn2_p003.musicxml,omr-eval/pages/bach_horn2_p004.musicxml ...
# writes omr-eval/pages/hornN.musicxml

# 2. Force every measure check_measure_duration.py flags onto its time
#    signature's nominal length (run on the *merged* per-part file, not
#    individual pages -- see the script's docstring for why). Overlong
#    measures get truncated at the barline; underlong ones (other than a
#    real pickup or the piece's true final measure) get padded with a rest.
.venv/bin/python omr-eval/normalize_measure_length.py omr-eval/pages/horn1.musicxml ...
```

The motivation for (2): MusicXML has no field for "this measure is
irregular," so a measure whose notes sum to something other than the time
signature just gets whatever length its content implies. MuseScore then
treats that as the measure's own actual duration, distinct from the nominal
one — barlines stop lining up with neighboring measures, which makes
hand-correction (dragging notes between measures, working across a passage
on the same beat grid) much more annoying than editing a wrong-but-regular
measure. Since the note actually at fault can't be identified without the
scan anyway, this trades note accuracy (already wrong) for a uniform grid:
truncate overlong measures at the barline, pad underlong ones with a rest.

Ran against all 8 merged parts: 158 measures normalized across the piece (16
to 22 per part), `check_measure_duration.py` comes back clean on all 8
afterward, and measure counts are unchanged (concatenation nor
normalization add or remove any measures — the piece is still 75, 79, 79,
83, 62, 64, 61, 62 measures per horn 1–8).

**Implementation snag worth recording:** naively truncating a `Measure`
object's notes in place (`stream.remove()`, shortening a note's
`.duration.quarterLength`) does *not* change what gets written — music21
writes back the *original* pre-edit length regardless, padding the gap with
an invisible (`print-object="no"`) filler rest to match. Confirmed this
isn't about any obvious cache (`Stream._cache`, `coreElementsChanged()`,
even explicitly reassigning `.duration`, all had zero effect on the
written-out length — see the script's git history for the debugging trail).
The fix that actually works: build a *brand-new* `Measure`/`Voice` object
and copy over only the elements you want to keep, then `Stream.replace()`
the old one with it. A freshly constructed container has no stale span to
fall back to. Separately, replacing a measure in place doesn't shift
anything *after* it, so every measure past a truncated/padded one needs its
part-level offset recomputed too, or the next measure is left sitting in a
gap (or overlapping) relative to the one that changed size.

## Findings so far (2026-09-12)

homr on `Bach_Preludio e Fuga - 8 Horns - Parts.pdf`, Horn 1 (2 pages, run one
page at a time on the pipeline's preprocessed `prepped/p001.png` /
`p002.png`):

| Page | Notes | Measures | Rests |
|---|---|---|---|
| p001 | 126 | 39 | 29 |
| p002 | 119 | 36 | 24 |
| **total** | **245** | **75** | **53** |
| Audiveris (merged, existing pipeline output) | 220 | 77 | 48 |

Both pages ran in ~5s each (CPU) and each parsed as one coherent staff, no
fragmentation. Counts are structurally close to Audiveris.

**Multi-bar rests are the clear weak point.** Checking the `<multiple-rest>`
tags homr emitted against the scan by eye:

- p001 measure 9: `multiple-rest=2` — correct, matches the "2" printed above
  the rest.
- p001 measure 25: `multiple-rest=8` — correct, but the `<measure>` element
  has no `<note><rest/></note>` at all, just the style tag — i.e. even a
  correctly-read multi-rest can come out with no encoded duration for that
  measure.
- p001 measure 26: the scan shows a "3"-bar rest here (before rehearsal
  letter E); homr silently dropped the numeral and wrote it as an ordinary
  1-measure whole rest, losing 2 measures of duration with no trace it
  happened.
- p001 measures 38–39: the scan has two consecutive 3-bar rests ("3", then
  "G", then "3"); homr misread both digits, as `multiple-rest=6` and
  `multiple-rest=4` — neither number is right, and (as with measure 25)
  neither measure has an actual rest note.
- p002: one 4-bar rest on the page (marked "4" after rehearsal letter K);
  homr didn't tag it as a multi-rest at all — came out as ordinary rests.

So of 6 multi-bar rests across the two pages, homr got 2 right (and even
those lack proper duration content in the XML), misread the digit on 2, and
dropped 2 entirely. This is a bigger structural risk than raw note-count
accuracy suggests, since a missed or wrong multi-rest silently shifts every
downstream measure's timing relative to the other parts — exactly the kind
of error that matters most for a multi-part score.

**Next steps:**
- Before considering homr a drop-in replacement, either detect multi-rests
  as a separate validation check (compare declared `<multiple-rest>` measure
  count against the printed page count of measures, or flag any adjacent
  whole/long rests as suspicious) or plan to hand-correct them.
- Check whether homr has any recall on the underlying issue upstream (numeral
  classification above a multi-rest is a known-hard target for TrOMR-style
  models); look for related issues in the homr repo before treating this as
  fixed-cost per page.
- Try more parts to see whether the mis-detection rate on multi-rests holds
  up or was specific to these two pages.

### Multi-bar rest root-cause investigation (2026-09-12)

Traced the two multi-rest failure modes to two *different* layers, by
diffing our observed XML against homr's own raw-token debug output
(`Writing XML [[...]]`, printed to stderr before the file is written) and
against `liebharc/homr` upstream on GitHub:

1. **Generator bug (fixable locally, doesn't touch the model).** For a
   measure the model *did* tag correctly (our page 1 "2" and "8" rests),
   the exported `<measure>` has the `<measure-style><multiple-rest>` tag
   but no `<note><rest/></note>` at all — no encoded duration for that
   measure. Root cause: `homr/transformer/vocabulary.py`'s
   `kern_to_symbol_duration()` has an `if kern.endswith("m"): SymbolDuration(...)`
   branch with a missing `return` — it builds the multirest duration object
   and discards it, then falls through to the normal numeric-duration
   parser, which misparses e.g. `"8m"` as duration base `8`. This is a
   confirmed, still-open upstream bug —
   [liebharc/homr#140](https://github.com/liebharc/homr/issues/140), filed
   2026-08-24, maintainer agreed a fix is needed, no PR yet, still present
   on `main` as of this check. Separately, `build_multi_measure_rest()` in
   `music_xml_generator.py` (also unchanged on `main`) never adds a rest
   note itself either way — even a corrected duration fraction wouldn't
   reach the exported measure without also touching that function. Given
   the duration/type derivation upstream is clearly unfinished (the
   maintainer's placeholder doesn't obviously produce the right `<type>` /
   `<duration>` pairing once the `return` is restored), the lower-risk local
   fix is a **post-processing pass over homr's output XML**: for any
   `<measure>` with a `<multiple-rest>` tag and no note children, inject a
   whole-measure `<rest measure="yes"/>` with duration computed from the
   divisions/time-signature already present in the file, rather than
   patching homr's internal duration engine directly.
2. **Model accuracy (not fixable by us).** The miscounted ("3"→6, "3"→4)
   and fully-dropped ("3", "4" rests that came out as plain untagged whole
   rests) cases are already wrong in the *raw* decoded token sequence,
   before the XML generator ever runs — this is the transformer
   misclassifying the numeral, not a code bug. Why it's a hard target:
   `transformer/configs.py` resizes every staff-line image to a fixed
   256×1280 px canvas (`staff_parsing.py`) before decoding regardless of
   source scan DPI, so a numeral that's legible at 350 DPI becomes a
   handful of pixels once the whole system line is squeezed to fit — more
   scan DPI on our end can't help, since homr always downsamples to this
   fixed size, and it's an architecture constant tied to the trained
   checkpoint (not a runtime flag). On top of that, the rhythm vocabulary
   only has 9 multirest classes (`rest_2m`…`rest_10m` —
   `vocabulary.py:53`, unchanged on `main`), so an 11+ measure rest has no
   token to represent it at all regardless of accuracy. Actually improving
   this would mean fine-tuning/retraining the model on more multi-rest
   examples — per the maintainer's own comment on
   [#57](https://github.com/liebharc/homr/issues/57), model-side work here
   is "very time-consuming," not something to attempt for this evaluation.

**Conclusion:** worth adding the small post-processing fix for (1) since
it's low-risk and independent of homr's internals, but the real risk noted
in the 2026-09-12 findings above (silent duration loss from mis-tagged or
untagged multi-rests) has no clean fix short of retraining — treat it as a
required manual QA step (cross-check each part's total measure count, or
flag isolated whole/long rests) rather than something to tune away.

Both are implemented:

```bash
# 1. Fix measures homr tagged <multiple-rest> but left with no note content
#    (case 1 above). Run this on homr's output before anything else touches it.
python3 omr-eval/fix_multirest_xml.py PAGE.musicxml [PAGE2.musicxml ...]

# 2. Validation: total-duration agreement across a piece's parts (the one
#    ground-truth-free signal for case 2 -- a misread or dropped multi-rest
#    shows up as a part whose grand total doesn't match its piece-mates'),
#    plus a weak single-file heuristic (untagged whole-measure rests, worth
#    a manual glance but expect false positives from legitimate single-bar
#    rests). Run fix_multirest_xml.py first so a part isn't shorted purely
#    by the bug (1) already explains.
python3 omr-eval/check_multirest.py horn1=p001.musicxml,p002.musicxml \
    horn2=p003.musicxml,p004.musicxml ...
```

`check_multirest.py` exits 1 when parts disagree, so it's usable as a CI-ish
gate once more than one part has been run through homr.

### Rhythm misreads beyond multi-rests, and a preprocessing experiment (2026-09-12)

Two more things came up eyeballing the fixed horn 1 output in MuseScore:

1. **The fugue's opening two multi-bar rests ("8" then "3") still don't look
   right.** Checked against the current state: the "8" (measure 25) *is*
   now correct — right count, and `fix_multirest_xml.py` gave it a real
   duration. The "3" right after it (measure 26) is the case already
   documented above: dropped entirely by the model, exported as a plain
   1-measure rest with no `<multiple-rest>` tag at all, so there's nothing
   for the fix script to act on. If it still looks wrong after re-running
   `fix_multirest_xml.py`, that measure is the reason — not a regression.
2. **A few notes read a step too long** (a half note coming out as a whole
   note, etc.). Ran musicocr's own measure-duration validator
   (`musicocr/stages/validate.py` — the same "Voice m.2: 5.5 beats, expected
   4.0" check used on Audiveris output) against homr's XML directly via the
   new `check_measure_duration.py`. It found 9 measures whose total duration
   doesn't match the time signature on page 1 alone, several by exactly one
   extra half note's worth (measures 18, 30, 32, 37 all read `6.0 beats,
   expected 4.0` — a 4.0 “long note” immediately followed by content that
   should have summed to 4.0 on its own). That pattern — one long note right
   before a fast beamed run reads as too long — matches the user's report of
   half notes coming out as whole notes.

Tested whether preprocessing tuning helps either problem: regenerated page 1
at (a) the pipeline's current defaults, (b) more aggressive unsharp masking
(sharper stems, radius 1.0 / percent 300 vs the default 2.0 / 150), and (c)
600 DPI with similarly boosted unsharp, then re-ran homr and both checks
against all three:

| Variant | measure-duration mismatches | multi-rests detected (want 2,8,3,3) |
|---|---|---|
| baseline (350 DPI, default unsharp) | 9 | 2, 8, 6, 4 |
| sharper unsharp (same DPI) | 11 (worse, plus a new wrong measure 1) | 6, 4 (lost the "8" entirely) |
| 600 DPI + boosted unsharp | 9 (same count, different measures) | 2, 6, 6, 4 (lost the "8" here too) |

**Preprocessing tuning does not help, and can actively hurt** — sharpening
past the current default introduced a new error and lost a previously-correct
multi-rest detection, and higher DPI alone was a wash. This is consistent
with the multi-rest finding above: homr resizes every staff line to a fixed
256×1280 canvas before decoding, so it's not looking at our source resolution
directly, and duration/pitch classification is apparently just as
insensitive to it. There's no evidence here that any preprocessing knob in
`config.toml` moves the needle on homr's rhythm accuracy — the current
defaults (tuned for Audiveris) are as good as it gets.

**Post-processing:** unlike multi-rests, there's no way to correct the note
itself without the scan (no code fix, since the wrong value is a genuine
model misclassification, not a bug in how it's written out). What
`check_measure_duration.py` gives instead is a mechanical way to *find*
these measures — sum a measure's content and compare it to its time
signature — without eyeballing every measure against the scan by hand:

```bash
.venv/bin/python omr-eval/check_measure_duration.py PAGE.musicxml [PAGE2.musicxml ...]
```

Uses the main project's venv (needs music21), not homr's. Exits 1 if any
measure-duration mismatch is found.

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
