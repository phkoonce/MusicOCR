# Design: a text layer for tempo, expression and technique markings

Status: v1 implemented 2026-10-27 (cue-note marking, 5.7, is out of scope and handled separately); see
"Implementation notes" at the end. Written 2026-10-26 after the HTTYD2 trumpet runs.

## 1. Problem

The pipeline reads *symbols* (notes, rests, clefs, dynamics hairpins, repeat marks) with the
detector, and only *digits* from page text (multi-rest counts, tuplet numbers, bar numbers, the
"m:ss until X" box). Everything else a player reads above or below the staff is lost:

| Kind | Examples in HTTYD2 / typical parts |
|---|---|
| Tempo | `♩ = 140`, `Allegro`, `Moderato` |
| Tempo change | `rit.`, `accel.`, `a tempo`, `poco rall.` |
| Expression | `maestoso`, `cantabile`, `dolce`, `espr.`, `solo`, `tutti` |
| Technique / articulation words | `marc.`, `legato`, `sim.`, `con sord.`, `open`, `flutter` |
| Cue labels | `Vln. 1`, `Fl.` over cue-size notes |
| Rehearsal / section | `A`, `B`, `[12]`, scene labels (partly handled by `scenes.py`) |
| Page furniture (ignore) | title block, composer, header ("TRUMPET 2 in Bb"), page number, bar numbers, tuplet digits, `0:16 until 1M4` box |

Today a score converted from a scan has none of these, so a player has to reread the original.
The same gap exists in the planned book-of-~100-quartets project (titles, tempo words, expression).

## 2. Goals and non-goals

Goals
1. Recover tempo, tempo-change, expression and technique text as MusicXML `<direction>` elements
   in the right part and bar, so MuseScore shows them in place.
2. Tempo marks with a metronome value play back (`<sound tempo=…>`).
3. Work for both inputs: PDFs with a text layer and scans (OCR).
4. Never silently invent text: low-confidence text is kept but flagged for review.
5. Reusable across scores and projects: no HTTYD2-specific rules; lexicon and zones configurable.

Non-goals (v1)
- Lyrics, figured bass, chord symbols, guitar tablature.
- Reading dynamics words (`mf`, `p`): the detector already draws dynamics; only dedupe against it.
- Re-creating exact typography (font, size, position offsets). Placement above/below is enough.
- Full-text search/layout analysis of title pages (titles are only used to ignore text).

## 3. Where it fits in the existing pipeline

```
PDF -> pages(png) -> [page text: digits only] -> Copisteria detection (dets json)
        -> detection prep (page_text.py) -> evidence model -> MusicXML per page
        -> extract (meter, tuplets, bar rests) -> merge -> ... -> combine / scenes
```

Existing seams to reuse:
- `musicocr/ocr_backends.py::ocr_words(png, backend)`: the single OCR seam (Vision, RapidOCR),
  boxes in image pixels, results cached.
- `musicocr/page_text.py`: `write_texts_json` (PDF layer) and `write_ocr_texts_json` (OCR) write
  `<page>.texts.json` (digits only); `digit_boxes_from_words` is the filter to widen.
- `musicocr/copisteria_run.py`: patches Copisteria's `_events(rd, staff, bar, clef, key, texts)`,
  which already receives page text per staff/bar and returns `(events, dirs, marks)`. `dirs` is the
  direction list: the natural attachment point.
- `musicocr/alignment/systems.py::_strip_tokens`: OCR of an upscaled strip beside/above/below a
  staff (needed because whole-page OCR misses small italics); `_staff_systems` finds staves.
- `musicocr/combine.py::copy_marks`, `musicocr/marks.py`: carry directions into the combined score.
- `musicocr/tuplets.py` pattern: a conservative default-on fixer that flags what it changed.

## 4. Requirements

Functional
- F1 Extract every word with box (page pixels), from the PDF text layer when present (including
  font name/italic/bold via `pdftotext -bbox-layout`), else from OCR.
- F2 Group words into phrases (same baseline, gap < ~1.5 staff spaces; hyphenated "ri-tard." joined).
- F3 Assign each phrase to a staff and a bar, or to "page furniture".
- F4 Classify: tempo / tempo-change / expression / technique / cue label / rehearsal / ignore / unknown.
- F5 Emit MusicXML: `<metronome>` + `<sound tempo>` for tempo; `<words>` (italic for expression,
  bold for technique/tempo words); rehearsal as `<rehearsal>`; cue labels as `<words>`.
- F6 `unknown` text above a staff is kept as flagged `<words>` ("check: text").
- F7 Deduplicate against detector output (dynamics, repeat marks) and across parts for tempo.
- F8 Configurable: on/flag/off, lexicon extensions, zones, backend.

Quality
- Q1 Precision over recall for *classified* text; recall via the flagged-unknown path.
- Q2 Deterministic given the same OCR output; OCR results cached like the existing seam.
- Q3 Fast: OCR only strips above/below staves (like the bar-number reader), two passes max.
- Q4 No change to notes/rests/timing: text only adds `<direction>` elements.

## 5. Approach

### 5.1 Word source
1. *PDF with text layer:* `pdftotext -bbox-layout` gives words, lines, blocks and fonts; use font
   style as a classification feature (expression is italic, technique/tempo words often bold).
2. *Scan:* render at the existing OCR dpi; for every staff (found by `_staff_systems`) OCR three
   strips: above (top - 8 sp .. top), below (bottom .. bottom + 6 sp), and the between-staff gap
   when systems are tight. Upscale 3x. **Two resampling passes (LANCZOS and BICUBIC) are unioned**:
   Vision's output on these strips is fragile to the resize filter (observed: "130-133" found under
   one and not the other, and "130-133ł" with a stray mark). Merge by box overlap; keep the higher
   confidence. A third scale (2x) is a cheap fallback if recall is still poor.
3. Normalise: strip stray non-alphanumerics at word edges, map OCR dash variants, lower-case a copy
   for lexicon matching, keep the original for display.

### 5.2 Phrase building
Sort words by (y band, x); join into a phrase while the horizontal gap is < 1.5 sp and baselines
agree within 0.4 sp. Join a trailing "." or hyphenated break. Output
`{text, xyxy, style, source, conf}`.

### 5.3 Placement
- *Page furniture* first: the title zone above the first staff (anything above the first staff top
  minus ~10 sp), left/right header band, footer band, bar numbers (digits below staves, already
  recognised by `_strip_tokens`), tuplet digits (size < 3 sp), the until-box (existing reader).
- *Staff and bar:* a phrase belongs to the staff whose vertical band [top - 8 sp, bottom + 6 sp]
  contains its centre (nearest wins on overlap). Its bar is the measure box (from the dets
  `measure` class) containing its left edge; if none, the nearest event to the left.
- *Implementation point:* inside the patched `_events` in `copisteria_run.py`, which already has
  `staff`, `bar`, `texts` and builds `dirs`. This avoids re-deriving page-measure to MusicXML-measure
  mapping, which is unreliable around multi-rests.
- *Above vs below:* `placement="above"` for text above the staff, `"below"` otherwise
  (dynamics-like words below).

### 5.4 Classification
A small, data-driven rule set (`musicocr/text_lexicon.toml`), applied in order:
1. **Tempo (metronome):** a note glyph (detected `note_*` just left of the phrase, or OCR'd
   `♩ ♪ = n` / `J = n`) followed by `= <number>` (or `=c.<n>`). The number is already in the digit
   file today (HTTYD2: "140", "124"). Beat unit from the glyph (quarter default), dotted if a dot.
2. **Tempo words:** Italian/English list (Allegro, Moderato, Andante, Adagio, Presto, Largo,
   Maestoso, Vivace, "Slow", "Fast" ...) optionally followed by a metronome mark.
3. **Tempo change:** rit., ritard., rall., accel., a tempo, "poco a poco ...", "tempo primo".
4. **Expression:** dolce, cantabile, espr., espressivo, legato, sostenuto, maestoso(*), solo, tutti,
   "sempre ...", "poco ...", "molto ..." (prefixes extend an expression word).
5. **Technique:** marc., marcato, stacc., sim., sempre, con sord., senza sord., open, muted, flutter,
   "to Flugel".
6. **Cue label:** instrument abbreviation + optional number ("Vln. 1", "Fl.", "Hn."), followed or
   preceded by cue-size notes (small detections): marks the notes as cue (see 5.7).
7. **Rehearsal:** a single capital letter or number in a box/outlined glyph, or the pattern the
   scene reader already finds.
8. **Ignore:** matches page-furniture zones, bar numbers, dynamics words (mf, p, f, ff, sfz ...) when
   a `dynam_*` detection exists within 1 sp, digits, single characters that are not rehearsal marks.
9. **Unknown:** everything else above a staff that is at least 3 letters and plausible text.

Fuzzy matching: edit distance <= 1 for words of length >= 5, <= 0 for shorter ones; accept OCR
confusions from a table (`rn`/`m`, `l`/`i`/`1`, `0`/`o`, stray `ł`). (*) "maestoso" is both a tempo
and expression word: classified as expression when a metronome mark is not adjacent.

### 5.5 MusicXML output
- Tempo: `<direction placement="above"><direction-type><metronome><beat-unit>quarter</beat-unit>
  <per-minute>140</per-minute></metronome></direction-type><sound tempo="140"/></direction>`;
  with a word, an extra `<words>` in the same `direction`.
- Expression/technique/tempo-change/cue: `<words font-style="italic|normal" font-weight=...>`.
- Unknown: `<words color="#7B1FA2">check: <text></words>`, the same convention the tuplet cleanup
  and bar-rest fixes use, so flagged output looks consistent.
- Direction order within a measure: after `<attributes>`, before the first note (the position
  `apply_marks` in `alignment/build.py` already uses).

### 5.6 Cross-part handling (combine)
- Per-part text stays per part (parts differ legitimately).
- Tempo/tempo-change: plurality across parts per bar; written in the first part only (like the
  transposition declaration) to avoid N copies; voted across parts also fixes single-part OCR misses.
- Words printed above the same bar in 2+ parts ("Allegro", "a tempo", "rit.") are written once, in the
  first part, as system text (`<direction system="only-top">`, which MuseScore imports as System Text
  instead of Staff Text); `musicocr/text_system.py`. Texts only one part prints stay staff text.
- Without printed bar numbers the same texts steer the rhythm alignment (`align_by_rhythm`): a bar
  carrying a text the other parts print over a slot is pulled to that slot.
- Scene concatenation (`scenes.py`) already carries directions with bar content; no change.

### 5.7 Cue notes (optional second phase)
A cue label over small notes (HTTYD2 "Vln. 1" over a whole-bar rest; currently read as grace notes
and the rest was too long, fixed by `barrests.py`) could mark those notes as cue size
(`<cue/>`, smaller) so they do not play back. Needs the small-note detections already used by
`mark_small_graces`. Keep out of v1.

### 5.8 Configuration
```toml
[omr.copisteria.text_layer]
mode = "flag"          # "off" | "flag" (classified + flagged unknown) | "on" (classified only)
backend = "auto"       # same as ocr_backend
passes = ["lanczos", "bicubic"]
lexicon = []           # extra TOML files merged over the built-in lexicon
above_sp = 8.0         # zone above a staff, in staff spaces
below_sp = 6.0
min_unknown_letters = 3
```

## 6. Detailed steps (work plan)

Phase 0 (about 1 hour): **feasibility probe.** Run Vision on the above-staff strips of
`output/httyd2sub/HTTYD2_tpt1/pages/p003/p003.png` and a few other pages with two resampling
passes; list what comes back for the known printed words ("marc.", "cantabile", "maestoso",
"♩ = 124", "Vln. 1"). Decide go/no-go and whether 2x/3x/4x scales are needed. Output: a table of
printed vs recognised, per backend.

Phase 1: **words + phrases.**
1. `musicocr/text_words.py`: `words_for_page(png, pdf, page, backend) -> list[Word]` with the two
   sources (5.1), caching under the existing OCR cache.
2. `phrases(words, sp) -> list[Phrase]` (5.2). Unit tests on synthetic word boxes.

Phase 2: **placement.**
3. `place(phrases, dets, staves) -> list[Placed]` (5.3): staff, bar, above/below, furniture filter.
4. Tests with synthetic dets (measure boxes, staves) and the page-furniture cases.

Phase 3: **classification.**
5. `musicocr/text_lexicon.toml` + `classify(phrase, context) -> Kind` (5.4), fuzzy matching and OCR
   confusion table. Tests: table-driven, including negatives (page header, bar numbers, "3" tuplet,
   dynamics words) and OCR-damaged inputs ("rnarc", "canta bile", "130-133ł").

Phase 4: **emit + integrate.**
6. `to_direction(placed) -> ET.Element` (5.5); tempo metronome beat-unit from the glyph.
7. Hook into `copisteria_run.py::_events` behind `[...text_layer].mode`; flagged unknowns coloured.
8. For PDF-layer pages feed the same path from `pdftotext -bbox-layout`.
9. Combine: tempo plurality (5.6). Tests on a two-part fixture.

Phase 5: **evaluate on real pages.**
10. Re-run `output/httyd2sub` (3 scenes). Compare against the printed text on pages 2-6 of each
    part: precision (classified texts that are right), recall (printed markings found), and the
    number of flagged unknowns. Target: no wrong classified text on the subset; recall >= 80% of
    tempo/expression words; every miss either flagged or listed in the report.
11. Run on a text-layer score (Zerberus parts) to validate the PDF path and the lexicon on a
    different composer/style; extend the lexicon from what is found.

Phase 6: **docs and cleanup.** README section, `config.toml` comments, lexicon format doc; add the
module to the pipeline diagram.

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| OCR misses small italic text on 150-ppi scans | strip OCR at 3x with two resampling passes; flag unknowns rather than drop; Phase 0 decides feasibility |
| Wrong word classified (e.g. title text above staff 1) | furniture zones; precision-first lexicon; `flag` mode default |
| Text attached to the wrong bar at multi-rests / system starts | attach in `_events` (staff + measure box), not via page-measure order |
| Duplicate marks (dynamics, repeat marks) | dedupe against detector classes; reuse the digit-box guard used for false segnos |
| Language/lexicon coverage (non-Italian terms, German/French) | lexicon in TOML, user-extendable; unknown path keeps the text |
| Cost (extra OCR passes per page) | only strips near staves; cached by image content; OCR is a small fraction of the ~50 min/part OMR time |

## 8. Open questions

1. Should expression words be emitted as `<words>` only, or mapped to MuseScore staff-text
   styles (expression vs technique vs tempo text)? (Affects how MuseScore styles them.)
2. Is cue-size marking (5.7) in scope for the quartets project?
3. Where should tempo live in multi-part output: first part only (proposed) or every part?
4. For the quartets books: are titles/composer lines worth reading into score metadata
   (`<work-title>`, `<creator>`)? The same OCR words would feed it.

## 9. Implementation notes (v1, 2026-10-27)

Phase 0 probe (macOS Vision, bands around each staff, 1.5x, Lanczos + bicubic; HTTYD2 trumpet 1,
350-dpi page images): go for scans.

| Printed | Read |
|---|---|
| `marc.`, `cantabile`, `maestoso`, `molto cantabile`, `Solo`/`Soli`, `sim.`, `stacc.`, `feroce`, `poco accel.`, `poco rit.`, `to St. Mute`, `Open`, `Vln. 1`, `Fl, Vln`, `Ob Solo`, `Segue to 3M21` | read, in one pass or the other (occasionally garbled in one: `Tocter`, `marc.y`) |
| `Sl. Faster (quarter) = 103`, `Much Slower (dotted quarter) = 59`, `(quarter) = 124` | words and number read; the note glyph is read as `.`, `•`, `d`, `J` or dropped; `=` sometimes twice |
| accents, note clusters, hairpins | read as letters (`Ibv`, `AAA`, `>>>>`): must be filtered |

Scale 1.5x is enough (1x also finds most; 2-4x not needed). Per band the two filters are unioned; a
box read two ways keeps the reading the lexicon (or a metronome pattern) understands.

Departures from the proposal, and section 8 answers:
* `pdftotext -bbox-layout` has no fonts, so the PDF path uses plain `-bbox` words and no font style;
  style comes from the lexicon kind (expression italic, technique/tempo bold). A `pdftohtml -xml`
  font lookup would add real italic/bold (not done).
* Placement is done in `copisteria_run.py` (`place_items`, staff and bar from Copisteria's own layout),
  from a sidecar `<page>.textlayer.json`; classification, furniture and OCR stay in `musicocr/`.
  Tempo marks are attached to the staff *below* them (they print above their staff).
* Beat unit: the unit is read from the glyph: PDF text gives `q`/`e`/`h` and a dot; on scans a dot
  between note and `=` is found from the pixels (`is_dotted_beat`), since OCR cannot tell `J.` from `J`.
* Q8.1 words only, no MuseScore staff-text styles. Q8.3 tempo in the first part only
  (`text_tempo.unify_tempo`, called from `combine`, plurality per bar; disagreements are reported).
  Q8.2 cue-size marking is out of scope for this work. Q8.4 not done (titles are only ignored).
* Not in v1: rehearsal letters from scans (OCR reads accents as letters; PDF layer only), rehearsal
  boxed numbers, `(note = note)` equivalence marks, dynamics words (ignored), fermatas.
