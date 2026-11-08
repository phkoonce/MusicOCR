# GB pieces 1-3: what the manual cleanup tells us (2026-11-08)

Method: re-ran pieces 1-3 with the current pipeline (`batches/gb-rerun.toml` -> `output/gb-rerun/`, combined
in `output/gb-rerun/_combined/pNN/`; the output is element-for-element the same as the Nov 6 run), imported the
combined MusicXML, aligned bars to the hand-edited `.mscz` by pitch/rhythm signature, and diffed per bar.
Note: the hand edits were saved over `output/gb-all/_combined/pNN/*.mscz` (same bytes as the Source copies), so
the untouched original is the sibling `.musicxml`.

| # | Issue | Evidence | Technique (cheapest first) |
|---|---|---|---|
| 1 | Rehearsal marks | 0 detected; manual has 8/15/10. The OCR text layer never sees them (the circle breaks the digit); the detector has no class for them. A plain OpenCV contour pass (outer contour with a hole, 70-130 px, square) found exactly the 4 circles on each of two test pages (p1 H1 p.12, p3 H1 p.16), no false positives. | Add a `circled number` finder -> existing `kind="rehearsal"` path in `engines/copisteria/run.py`. Number = reading-order sequence (verify by OCR of the crop). Above staff: bar at its x. Below staff (edge case): bar after the nearest barline. Vote across the 4 parts. Manual used letters A, B, ... for the printed 1, 2, ...: decide which you want. |
| 2 | Extra slurs | 17 of 40 barline-crossing slurs were removed by hand, only 2 of 299 slurs inside a bar. | Barline-crossing slurs are the suspects (2 notes, last note of a bar -> first of next). The note shapes of kept and removed ones overlap, so use slur-box evidence: the detector gives `curve` boxes with a confidence (some 0.2-0.6); require the box to actually span the barline x and drop low-confidence ones. |
| 3 | Key change misplaced (#3) | Detector found `keySig_1f` mid-system after the `\|:` (x=1886) and a courtesy one at the next system start. Pipeline put the change at bar 38 (system start, the "Trio" line); correct is bar 42 (after the double bar). Also spurious changes at bars 72/76 (base 16 key sigs vs 12). | Anchor a key change to the barline immediately left of the detected glyph, not the system start; ignore a glyph that repeats the key already in force (courtesy); vote across parts. |
| 4 | cresc./dim./rit./a tempo as text | Manual: `cresc. - - -` became a CRESC_LINE hairpin (subtype 2, 2 bars), `dim.` DECRESC_LINE (3); first tempo word became a Tempo element; rit./a tempo mostly kept as SystemText. Raw output also has per-part staff texts and "check:" noise (23/34/30 StaffTexts that were all deleted). | Classifier already knows cresc./dim./rit. (lexicon kinds). Emit cresc/dim as `<wedge>`-less text lines ending at the next dynamic (same rule as 6), tempo words once as system text via cross-part vote, and don't emit `check:` staff texts into the .mscz (keep them in the report). |
| 5 | Dynamics above/below | Raw: 5/8/13 dynamics with `direction=up` in p1/p2/p3, rest down. Manual: none keep a direction (default = below). | Per book (or per part set) take the majority placement and force it; an instrument-part default of "below" is safe. |
| 6 | Hairpins too short | Manual hairpins average 1.6-2.2x longer. 27/42 (p1) and 27/51 (p3) end exactly on the next dynamic vs 11/40 and 12/44 raw. | Extend a hairpin end to the next dynamic in the same part when the direction fits (< ends louder, > ends softer) and no other hairpin starts in between. |
| 7 | Repeats / 1st-2nd endings | OMR reads them (3 of 4 parts have both repeats and both endings for p3; detector has `barLine_repeat_*`, `ending_1/2`), but the combined score has 0 `<repeat>` and 7 stray `<ending type=stop>`. They are lost in `combine`. | Carry repeats/endings through combine: map each part's bar numbers through the alignment, take the majority vote per barline, write to all parts. |

Also seen: dynamics land one eighth early when the bar starts with a rest (should sit on the first note), and a few dynamics (e.g. bar 37, all four parts of p1) are dropped.


## What was built from this (2026-11-08)

Checked by re-running pieces 1-3 (`output/gb-rerun2/`) and diffing against the hand-edited scores:

| # | Result |
|---|---|
| 1 rehearsal | Circle finder (`text/circles.py`) + placement on the barline (`ring_bar`) + vote and lettering (`assemble/rehearsal.py`): 8 / 15 / 10 marks, as many as by hand; positions equal to yours in 7/8 (p1) and 8/10 (p3) bars. |
| 2 slurs | `slur_filter` (`engines/copisteria/run.py`): a curve across a bar line between 2-3 notes whose box is >= 2.5 staff spaces tall is not written (real ones are 0.7-2.4 tall). Of the 41 barline-crossing slurs in pieces 2-3 (not piece 1: its are ties), 15 of the 18 removed by hand are caught, 4 of the 23 kept are lost too (piece 2 horns 2-3, a messy piece). Detector confidence (`det`/`p`) does not separate them. Piece 3: all 4 removed caught, 0 kept lost. |
| 3 key change | `slide_key_changes`: a change hung on a system's first bar moves to where another part reads the glyph (p3: bar 42, and the spurious 72 gone). |
| 4 cresc./dim./tempo | cresc./dim. text -> dashed text line to the next dynamic (`marks.py` rule `lines`); opening word ("Allegro", "Marsch") -> tempo mark (`text_tempo.opening_tempo`). rit. / a tempo stay text. |
| 5 placement | `marks.py` rule `placement`: no dynamic is left above the staff (5/8/13 before, 0 now). |
| 6 hairpins | `marks.py` rule `reach`; the older `hairpins` rule overshoots (16 too long vs 5) and is left out of the collection default. Measured against yours: of the 66 hairpins with a counterpart in yours (pieces 1 and 3), 46 now end identically (44 before), 15 are still too short (20 before), 5 too long (2 before). |
| 7 repeats | `assemble/repeats.py`: p3 gets both repeats and both 1st/2nd endings at your bars. |
