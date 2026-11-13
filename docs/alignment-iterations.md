# Alignment iterations

Cache-only iterations on `musicocr combine` (no OMR run): `scripts/score_alignment.py <batch> --recombine SCRATCH`, scorecards
in `output/scorecards/iter/<tag>-<corpus>.json` (summary: `output/scorecards/alignment-iterations-2026-11-12.json`).
Numbers are inserted / dropped / wrong-length bars. Baselines were re-measured with the current code (the old
braveheart-horns and paxman scorecards were from an older run); GB p01-03 vs refs (extra/missing 30/34 over three pieces) and
Zerberus vs ref (76/76) were unchanged by every kept change.

## 2026-11-12

| corpus | before | after |
|---|---|---|
| paxman-all | 1938 / 242 / 767 | 1073 / 54 / 410 |
| ghs-opera | 1486 / 39 / 430 | 1310 / 40 / 395 |
| ghs-xmas | 309 / 41 / 210 | 207 / 21 / 199 |
| gb-all | 163 / 74 / 265 | 133 / 84 / 262 (the +10 dropped are p84 extras read from a neighbour piece, correctly dropped) |
| goldfaden | 172 / 202 / 167 | 67 / 69 / 126 (888 bars, was 881) |
| gb p01-03, zerberus, braveheart-horns | unchanged | unchanged |

### 1. A page start far behind the bars already read is a misread (kept)
Evidence: paxman p73 (508 bars, 239 wrong-length) and p81: page starts read from camera scans ("52" on a page that follows 136 read
bars) made `target` negative or too large, so pages were ignored ("no printed bars") and the parts' totals disagreed. `_increasing` only
compared start numbers with each other. Now it also tracks where the bars read so far reach (`reach`, unnumbered pages included) and
drops a start more than max(4, 15%) behind it. Bars lost only make `reach` short, so a real start is never ahead of it.
Side effect: multi-movement pieces whose bar numbers restart (p81 = three movements) are laid out as one continuous run, which is what
the score needs. Paxman p73 222/43/239 -> 9/1/24, p81 277/0/78 -> 31/0/32; xmas p02 93/23/44 -> 0/0/33.
Known cost: p88 (3-staff score page read as one part, 54 bars per page) went from "66 bars, 144 dropped" to "102 bars, nothing
dropped": the metric hides it, neither is right (about 30 bars). Needs the per-staff fix below.

### 2. Rehearsal-mark labels matched to a reference part by position (kept; goldfaden only)
Evidence: in goldfaden h3/h4 the lone "1" opening movement 4 is unread, so the label counter carried movement 3 on and every
later label landed in the wrong movement (stretches of 300 bars aligned by rhythm, "94 bars read for 25"). `rematch_marks` labels each
part's raw numbers by the lead part's (most marks) labels: equal numbers, in order, the offset to the lead changing slowly (longest
chain). Marks agreed 67 -> 72, 172/202/167 -> 67/69/126. A "larger tie wins" variant for stretch targets was tried and reverted
(unverifiable, mixed).

### 3. Score total: the biggest group of agreeing totals, not a four-way tie (kept)
Evidence: ghs-opera p06 last-page totals {85, 80, 144, 79}: all different, the tie went to the largest (144, one part that read a
neighbouring piece's systems). `_agreeing_totals` keeps the largest group within 6% (ties: commoner exact total, then larger). First version
(ties to the larger group) regressed gb p07 and opera p15 and was fixed before keeping. Paxman p44 148 -> 56 bars (286/2/22 -> 10/2/1), p86
99 -> 10 inserted, opera p06 193 -> 17 inserted.

### 4. A dropped source bar hands its key/time signature to the next kept bar (kept)
Evidence: paxman p82 reported "1: 4/4; 36: 3/4" with 68 wrong-length bars: within-system alignment dropped the page's first (pickup)
bar, the only place the 3/4 was declared. `_hand_on_declarations` copies the declaration on. p82 68 -> 1 wrong-length, paxman 477 -> 410, opera p10 21 -> 12.

### Remaining error, by size
1. OMR lost systems/pages (needs OMR re-runs, not alignment): ghs-opera p13 (h2 39, h3 70, h4 70 of 198 bars), p20 (h2 21 of 222), p16, p22;
   paxman p44 (h1 47 of 148 read), p71 (h2 59, h3 52 of 117), p24, p41, p55 (h1,h4). Roughly 2/3 of the remaining 1073+1310 inserted bars.
2. Score pages inside part PDFs (paxman p88; p84 h1/h4 read 158 vs 34 bars): the OMR merges the staves of one printed system into one part
   (18 bars per 6-bar system). Choose the staff by part rank in a pre-combine step.
3. Pieces sharing pages read the neighbour's bars (gb p83 h2 97 bars for 52, p84): dropped correctly but cost accuracy.
4. goldfaden: 2-4 parts disagreeing on stretch lengths (e.g. 4 vs 12 bars at 743-746) -> need the marks re-read; marks 16 and others unread.
5. Wrong-length bars after meter changes (p71, p56: 3/4 vs 4/4 vote).

### Needs OMR re-runs to test
- paxman p88 h1-h4 and p84 h1/h4 with the page cropped to one staff (or a score-aware split); then check bar counts ~30 / 34.
- ghs-opera p13 h2-h4, p20 h2, p16, p22; paxman p44 h1, p71 h2/h3, p24, p41, p55 h1/h4 (parts that read under half the bars): re-run with `--force` and
  check whether the OMR or the page crop lost the systems.
- goldfaden h3/h4 mark 1 of each movement (circles.py label reading) to confirm the rematch needs no fill.

## 2026-11-12/13, round 2: parts that "lost systems" were mis-split, not mis-read

Diagnosis (page images vs `workflows/*/cache/pieces.json` and the per-part page counts): the OMR did not lose the systems. ghs-opera
p13 h2 is one page of ~40 bars, but h1 (5 pages), h3 (3) and h4 (2) were cut with the wrong page ranges, so "the true total"
(198 bars) was the sum of neighbouring pieces. Three bugs in `musicocr/collection/split.py` (the `collection split` heading finder):
1. A **bar number** at a system start ("13" above a staff, x0 ~0.1 of the page width) matched as the heading of piece 13 (`_heading`
   accepted a bare number from 0.1 of the width). It cut the book there: h1 piece 13 began on page 18, pieces 11-12 vanished (their
   headings could not "fit" between 10 and the false 13), 10 lost its second half. Same for 20/22 in h2-h4, 6/10/13/20 in h3. A bare number
   is now a heading only when centred (x0 > 0.2 of the width, `_BARE_MIN_X`).
2. The **"lone number above the title"** rule took any number-only line within 0.12 page heights above a heading, including bar numbers
   at the left margin: pieces were cut one system too high (opera p8 `.23` vs the real `.32`, paxman p13/p56/p58/p63), putting the last
   system of the previous piece into the next. Same centring test now.
3. A **page number glued to the heading line** by the OCR ("18 10. Largo religioso") hid the heading (`_strip_page_number`).
Also `build_piece_pdfs(only=...)` to cut just some pieces (used for these re-runs). Tests: `tests/test_collection_split.py` (3 new).
Offline check with the cached page OCR (`find_pieces` + `fill_from_peers`, no OCR): GB found-table unchanged; ghs-opera 21 heading
changes, paxman p14/56/69-71/82-84/87, xmas p31/35.

Re-runs (copisteria, only the affected pieces, into `output/iter/ghs-opera-fix` and `output/iter/paxman-fix`; existing parts copied, the
originals untouched; `score_alignment.py --recombine`), inserted / dropped / wrong-length bars and bars read per part:

| piece | before | after |
|---|---|---|
| opera p13 | 411 / 0 / 173, parts 186,35,70,70 of 198 | 3 / 2 / 5, parts 42,35,44,39 of 42 |
| opera p16 | 171 / 0 / 56, parts 37,88,37,28 | 0 / 1 / 0, parts 37,32,37,28 |
| opera p17 | 1 / 0 / 1 (h2 missing) | 1 / 0 / 1, parts 55,47,56,56 (h2 added) |
| opera p20 | 257 / 0 / 87, parts 21,222 of 251 | 0 / 0 / 0, all 4 parts 58 bars (h1, h4 added) |
| opera p22 | 135 / 0 / 2, parts 36,91,40,101 | 2 / 2 / 0, parts 36,32,40,34 of 38 |
| paxman p13 | 11 / 0 / 6, h1 14 of 25 | 0 / 3 / 7 |
| paxman p14 | 3 / 10 / 1 | 4 / 0 / 0 |
| paxman p56 | 51 / 0 / 49, 108,89,89,97 | 4 / 0 / 3, 85,89,89,79 of 90 |
| paxman p70 | 33 / 0 / 1, 12,28,23,12 | 1 / 15 / 3, 27,28,23,25 |
| paxman p71 | 124 / 0 / 96, 116,59,52,112 | 8 / 0 / 2, 60,59,52,59 |
| paxman p84 | 0 / 0 / 37, 158,34,34,162 | 0 / 0 / 0, all 34 |

Opera p13+16+17+20+22: 975 inserted bars -> 6. GB p01-03 and Zerberus are not split by this code and combine is unchanged: unaffected
(GB's found-table is identical under the new rules).

Not done / remaining: paxman p88 (3-staff score page read as one part, 3 staves per system -> one part of 54 bars/page): a
different cause (single-staff mode treats each staff as a system); needs a per-part staff choice, not attempted. Paxman p24 (64,68,74,62 vs
total 108), p63 (11,17,11,16 vs 23), p44 h2 (149 vs 56) are alignment/total problems with no split change; opera p05 h3 (5 of 69), p18
(1019 bars: h3 18) and p19 h2/h3 not investigated. p70 now drops 15 (h3 reads 23 of 28).

Broader re-runs worth doing with the fix (`collection split` itself rewrites input/; use a scratch spec or back up first):
- ghs-opera: pieces 5-14 and 17-24, mainly the books/pieces whose cut changed: 6 h3, 8 h1/h3, 10 h3, 11 h1, 12 h1, 13 h1/h3, 14 h4, 17 h2,
  19 h3, 20 h1/h3/h4, 21 h2/h4, 22 h2/h4 (plus the piece ending where each starts: 7, 9, 10, 12, 18-21), ~25 parts.
- paxman-all: 8 h3, 13 h4, 45/46 h2, 58 h4, 63 h2/h4, 69/82/83 h1/h4 (+ 87 h1, 55 h4, 56/70/71/84 are done above): ~15 parts.
- ghs-xmas: 5 h4, 13 h4, 31 and 35 (all books), 33 h1: ~10 parts.

## 2026-11-13, round 3: the split fix applied to the real corpora

Baselines re-measured with the code at e8721e8 (`output/scorecards/r3-base-*.json`), then every changed part re-split, re-run (copisteria,
`--jobs 2`) and recombined in a staging area (`output/iter/<corpus>-r3`: symlinks to the unchanged parts, round-2 re-runs reused when their
input is identical, `scratch/r3/stage*.py`), scored (`r3-new-*.json`) and only then adopted into `output/<corpus>/`. Replaced part
outputs and combined pieces are in `output/<corpus>-pre-round3/`; the old split PDFs are in `input/_pre-round3/<project>/`, the old
`pieces.json` / batch spec are `pieces.pre-round3.json` / `batches/<corpus>.pre-round3.toml`.

| corpus | before | after (all pieces recombined) | on disk now |
|---|---|---|---|
| ghs-opera | 1310 / 40 / 395 | 176 / 42 / 39 (bars 2863 -> 1466; \|part - combined\| 5218 -> 335) | same |
| paxman-all | 1073 / 54 / 410 | 499 / 58 / 221 | 497 / 206 / 221 (p88 and p76 deliberately kept as they were) |
| ghs-xmas | 207 / 21 / 199 | 146 / 17 / 189 | same |
| gb-all | 133 / 84 / 262 | 133 / 84 / 262 | unchanged |
| gb p01-03 (vs refs), zerberus (vs ref) | | split not touched; combine identical with and without the round-3 changes; zerberus 76/76 | unchanged |

Re-split: GB's found-table is identical under the fixed splitter, so its PDFs are unchanged. 30 opera, 34 paxman and 20 xmas part PDFs
changed (page images compared); 19 / 29 / 20 of them needed an OMR run (the rest were reused from round 2 or are new parts such as opera
p11/12/14/17/19-21 h1-h4 and paxman p69/82/83 h4 that the old, buggy cuts had lost). Opera p13/16/19/20/22, paxman p44/55/56/63/70/71/84 and
xmas p5/12/13/30-35 now read their own pages only.

### Kept combine changes (each one measured on all corpora, cache-only)
1. **Lone misread "last system" number must not set the lower bound** (`_plausible_lasts`). Paxman p24: one scan read "99" for 66, the
   score became 108 bars for a 75-bar piece (4 parts read 62-74). The largest of >= 3 distinct printed numbers is dropped when it is more than
   15% above both the next one and the largest total read. p24 118 -> 30 inserted, p23 64 -> 9, p41 110 -> 17 (the same cause).
2. **A page start or system number far beyond anything read is a misread** (`_far_ahead`, factor 2 x the most bars any part read + 10).
   Opera p18 h2/h3: "opt. Schluss 2" numbers 77b, 83b ... were OCR'd as "776", "836" -> a 1019-bar score; now 150 (parts read 129-140).
   Xmas p02 95 -> 64 bars (parts 60-64).
3. **`_common_left` crashed** when a page had >= 3 staves but no staff-start columns (`max()` of an empty list): `combine` of GB p01-03 failed
   outright with the current code. It now returns None. GB p01-03 can be recombined again: vs refs extra/missing 9/9, 10/10, 16/24
   (the scorecard saved earlier had 7/7 for p01; the difference is not from round 3: the control run with the round-3 constants switched off
   is byte-identical).
Tests: `tests/test_alignment_iterations.py` (3 new).

### Diagnosed, no change needed any more (they were mis-splits)
paxman p44 h2 (149 vs 56: its 4-page cut held the next piece's pages; now 57), p63 (h2/h4 began on the previous piece's last system, "23" as the
first number), opera p05 h3 (5 of 69 bars, now 59), p19 h2/h3 (0 inserted, all four parts 46 bars). Opera p20 and p19 are now exact (0/0/0).

### Not adopted
Paxman p76: the combine changes alone move its total 22 -> 26 (the misread "107" page number no longer blocks the noisy
printed-system-start total); abs part-vs-score gap 5 -> 21, so its old combined score stays. Paxman p88 (3-staff score page): new
recombine reads 102 bars without drops, the old one 66 with 144 dropped; neither is right (~30 bars), left as it was. Needs the per-part staff
choice (not attempted).

### Remaining worst pieces (OMR lost or misread bars; not alignment)
paxman p11 (157 bars: 39 inserted), p10 (h1 reads 35 of 57), p24 (30), p31 (24), p33 (23), p07 (23), p45 (22), p86 (20 / 10 wrong-length), p81
(23 wrong-length), p88; opera p03 (26), p06 (22: three parts lose the last 5 bars), p21 (19: h1/h3 read the "11" multi-rest as one bar),
p18 (19), p02 (15), p12 (14), p10 (11, 8 dropped); xmas p02 (37 wrong-length), p01, p06, p15, p34.

## 2026-11-13, round 4: Szeged arrangements, one staff of a score page, small multi-rest counts

### Szeged (new corpora, copisteria, `batches/szeged-frozen.toml`, `batches/szeged-alpen.toml`, outputs in `output/szeged-*`)
Part boundaries (the Stimmen PDFs are scans; the title block "If you like our arrangement ..." opens each part):
Frozen 22 pp = Tbn 1 (1-2), 2 (3-4), 3 (5-6), 4 (7-8), Baritone in C (9-10), Tbn 5 (11-13), Bass Tbn 1 (14-16), Bass Tbn 2 (17-19), Drum Set
(20-21), Glockenspiel (22). Alpen 57 pp = Tbn 1/Alto 1-5, Tbn 2 6-10, 3 11-15, 4 16-20, 5 21-25, Euphonium Bb 26-30, Euphonium C 31-35,
Bass Tbn 36-40, Contrabass Tbn 41-44, Tuba 45-48, Timpani 49-52, Snare/Cymbals 53-54, Glockenspiel 55, Bass Drum 56-57 (drums `score = false`).
Reference for Frozen: `Source/Musescore/Lopez - Let It Go - misc {Frozen}.mscz` converted to `output/refs/szeged_frozen_misc.ref.musicxml`
(125 bars; 9 pitched parts; `Frozen - Szeged.mscz~` backup also converted, 125 bars, no Bass Tbn 2); `score_alignment.py` finds it by rule
(`szeged-frozen`). No reference exists for Alpensinfonie.

| corpus | combined bars | inserted / dropped / wrong-length | vs ref (matched / extra / missing) |
|---|---|---|---|
| szeged-frozen, round-3 code | 128 | 141 / 1 / 2 | 944 / 208 / 181 (raw parts 833 / 95 / 292) |
| szeged-frozen, round-4 code | 128 | 119 / 1 / 2 | 968 / 184 / 157 (raw 837 / 93 / 288) |
| szeged-alpen, round-3 code | 296 (parts read 198-262) | 511 / 32 / 85 | no ref |
| szeged-alpen, round-4 code | 296 | 506 / 35 / 85 | no ref |

Findings: Frozen's 128 bars vs 125 in the reference: the arrangement prints the last bars 126-128 (the ref lacks them or merges). The
"raw bars" of a part are under-counted by `score_alignment` because the merged part file keeps only some `multiple-rest` tags
(`score_alignment` counts a tag as N bars; `combine` reads the per-page files). Both pieces are dominated by OMR-side losses:
multi-rest counts (see below), "a 2-bar-long measure read as one" (two tied whole notes whose bar line the OMR missed; combine splits
these, 29 in Alpen), and 8-19 bar multi-rests that the OMR reads without count. Alpen total printed bars: 296 (the last-page totals of
the parts that lost nothing). Glockenspiel and Bass Drum read 28 / 13 of 296 bars (mostly multi-rests).

### Kept changes
1. **Multi-rest counts 2.0-2.5 staff spaces tall were dropped** (`engines/copisteria/run.py` `_is_count`; `text/page_text.py`
   `add_missing_multirest_dets`, `_blocks_to_multirests`, `_firm_bar_span`). The digit-height bound (2.5 spaces, then 3.5 for
   synthesised H-bars) discarded the counts of engraved parts and camera scans (Szeged "8"/"3", Wagner "11", Paxman "2"-"7"). Now a
   digit >= 1.5 spaces counts when it is centred over the multi-rest bar (nearest to the bar's centre first, so a bar number at the
   bar's left end still loses); a digit >= 1.8 spaces over an *empty* bar of >= 6 spaces' width, not stacked (time signature), not inside a
   circled rehearsal number (`find_circles` boxes passed from `stage._prepare_dets`), makes a `multiRest`; a short thick block
   (1-2.6 spaces) under such a digit that the detector read as `barLine_single`/whole rest/breve is re-labelled `multiRest`, and the
   bar's extent comes from the *firm* bar lines around it. Offline check of the changed rule on all cached detections (`add_missing_...`
   on copies; precision judged from ~40 page crops: paxman, gb, httyd2): every sampled addition a real count except GB rehearsal rings (fixed by the
   ring list and the width bound; GB p01-03 p03 h1/h2 add 4 genuine "2" rests; re-read in scratch: bars and vs-ref unchanged). Re-runs:
   opera p21 h1 21 -> 31 bars (p21 inserted 19 -> 4; h3 still 21 because Vision reads the bold "9" as "6"), paxman p10 inserted
   31 -> 13 (h2-h4 55/50/55 -> 57/56/57, h1 35 -> 40 of 57). Szeged Frozen 141 -> 119 inserted.
2. **Per-part staff choice for a score page inside a part book** (`scoreops/staffpick.py`, `staff_rank` batch/CLI/config key). The trio
   page (paxman p88: "Horn 1/2/3" on joined staves) was read as one part with 3 staves per system. The staves of a system are told by a
   vertical line (system barline / bracket) across the gap left of the staves; the other staves of each system are whitened before the
   OMR (original kept as `pNNN.full.png`). Pages of single staves are untouched (tested on a part page). paxman p88 h1-h3 66-102
   bars -> 34/34/34, piece 34 bars 0 inserted/dropped/wrong (was 66 bars with 144 dropped; or 102); `p88_h4` set `score = false` (a trio:
   there is no fourth staff). The batch part needs `staff_rank = N`; there is no auto-detect of N.
Tests: `tests/test_round4.py` (4). Full suite 496 passed.

### Regression check
Combine code is unchanged in this round, so scorecards of corpora whose parts were not re-run are identical to round 3 (GB p01-03 vs refs
9/9, 10/10, 16/24 extra/missing; Zerberus vs ref 76/76 with 210 bars; `output/scorecards/r4-gb-p01-03.json`, `r4-zerberus.json`). The OMR
changes were verified offline on cached detections for goldfaden (1 genuine addition), ghs-opera / ghs-xmas / zerberus / braveheart (0 additions)
and not re-run on those corpora. Replaced outputs are backed up in `output/paxman-all-pre-round4/` (p88 h1-h4, p10 h1-h4, their
combined pieces) and `output/ghs-opera-pre-round4/` (p21 h1/h3 and combined); the first Szeged runs (round-3 code) are in
`output/szeged-frozen-r4base/`, `output/szeged-alpen-r4base/`. Scorecards: `output/scorecards/r4-*.json`, summary
`alignment-round4-2026-11-13.json`.

### Not done / remaining
- Multi-rest counts: Vision reads a bold "9" as "6" (opera p21 h3): a vote over the other parts' counts at combine would fix it; paxman p10 h1 still reads 40 of 57
  (thick blocks read as strong bar lines / whole rests in some bars: re-run with the final code was done, the rest are digits not
  over a firm-bar-lines bar); p11 (157 bars) and p07 were not re-run (same kind of "2"-"7" counts). Re-run the pages that the offline check changes (paxman about 90
  page-parts, gb-all about 6)
  to adopt the fix corpus-wide; httyd2 has 139 page-parts with additions (its cached detections predate the synthetic H-bars).
- Szeged: a staff-wise `score_alignment` for Alpen needs a reference (none); the merged part file drops `multiple-rest` tags (the
  per-page files keep them), so "raw bars" in the scorecard under-count parts that open with multi-rests.
- `staff_rank` is manual; a trio page read for a fourth horn (`p88_h4`) is simply not combined.

## 2026-11-13, round 5: round-4 multi-rest rules applied corpus-wide, tacet parts, auto staff pick

Baselines = the on-disk outputs recombined with the round-4 code (`output/scorecards/r5-base-*.json`); "final" =
`r5-final-paxman-all.json`, `r5-v2-*.json` (corpora whose parts were not replaced); summary `alignment-round5-2026-11-13.json`.
Numbers: inserted / dropped / wrong-length bars.

| corpus | before | after |
|---|---|---|
| paxman-all | 481 / 59 / 221 (193 parts differing) | 338 / 67 / 214 (176) |
| szeged-alpen | 506 / 35 / 85 | 246 / 35 / 85 |
| szeged-frozen (vs ref 184/157 extra/missing) | 119 / 1 / 2 | 118 / 1 / 2, vs ref unchanged |
| ghs-opera, ghs-xmas, gb-all, goldfaden, braveheart-horns | 161/42/39, 146/17/189, 133/84/262, 67/69/126, 52/0/117 | identical (nothing re-run or adopted) |
| GB p01-03 vs refs (extra/missing) | 9/9, 10/10, 16/24 | identical (output not touched) |
| Zerberus vs ref | 76/76 | 76/76 |

### 1. The round-4 multi-rest rule applied to the cached pages (kept for 20 paxman pieces)
Listing the page-parts the rule changes: `scripts/multirest_offline_check.py output/<corpus>` runs `add_missing_multirest_dets` on copies
of every page's cached detections/texts (nothing written). Paxman 83 of 617 pages, gb-all 4 of 260 (GB p03 h1/h2/h4 and p09 h3), goldfaden 1,
ghs-opera / ghs-xmas / Zerberus / Szeged 0 (Szeged was already re-run in round 4), httyd2 139 of 157 (not run: a scene book, no scorecard).
Staging (`output/iter/<corpus>-r5`, real copies of the affected part dirs, only those pages' `pNNN.musicxml` deleted, batch `--redo`), scored
against the on-disk state, adopted **per piece** when inserted+dropped+wrong-length fell (or tied with fewer differing parts). Adopted paxman pieces:
4, 6, 7, 11, 12, 14, 17, 24, 33, 36, 38, 41, 43, 44, 45, 61, 75, 79, 80, 85 (e.g. p07 23 -> 3 inserted, p43 11 -> 0, p85 12 -> 0, p11 39 -> 18, p33 23 -> 9,
p06 24 -> 12, p24 30 -> 24, p45 22 -> 16). Not adopted (worse): p20, p55, p73, p78, p81, p86 (dropped/wrong-length rose; p73/p81 re-reads gain
bars of real multi-rests but the page layout then disagrees by a few bars). Old outputs of every adopted piece (parts and combined) are in
`output/paxman-all-pre-round5/`. Two bugs in the rule were found by looking at page crops of the additions and fixed before adopting:
- **A multi-rest was synthesised twice for one digit**: the thick block under a count digit was re-labelled `multiRest` and the next loop added
  another over the "empty" bar, because `content` was computed before the re-labelling (`add_missing_multirest_dets`). About 30 pages.
- **A bar number at the start of a system is not a count** (paxman p56 h4: "65" over an undetected first bar became a 65-bar rest, the part read 144
  bars): a digit whose centre is within `SYSTEM_START_SP` = 6 spaces of the system's left edge (leftmost measure or clef) is a bar number.
GB: p03 (hand-edited, never replaced) compared in scratch only: bars 99 -> 102 (= the reference's 102), extra/missing vs ref 16/24 -> 20/20; GB p09 h3
gains a genuine "2" bar (26 bars vs 25) but the piece's inserted count rises 5 -> 8 (the other parts read the "2" bar as one), so it was not adopted. Goldfaden
h4 p7: genuine addition, scorecard identical, not adopted.

### 2. Vote on multi-rest counts at combine (small; kept)
Where the parts agree a stretch is rest-only, a part that read it short or lost the count is an alignment of rest bars, not a doubt:
`_extended_rests` (combine.py) takes the slots the aligner had to insert (>= 2 other parts rest there, on pages that read the right number of bars, and the
slot follows a rest-only source bar) and reports them as "multi-rest count read short or lost ... added without doubt" instead of
"inserted ... -- check" (no check flag on the bar). On the corpora this is rare: only 5/161 (opera), 19/481 (paxman), 19/506 (Alpen) of the inserted
bars sit where all other parts rest; the others are lost bars among playing parts, which no vote can restore. Szeged Frozen -1 inserted, everything else identical.
Vision's "9" read as "6" (opera p21 h3) is not fixed by it: h1 rests 11 bars, h2 7, h3 10 - the parts do not agree on the stretch.
**Tacet to the end** (found while looking at the Alpen glockenspiel, which plays bars 1-36 of 296 and then has a bare "TACET" rest): a part whose last
page ends in a rest-only bar with >= 8 slots to go gets those slots as rests without doubt, and `voltas.segments(open_last=True)` no longer drops all
the system anchors because the last system "holds more than 14 bars per system": the glockenspiel's playing bars were landing in the wrong bars (all 60 first
bars rests) and are now at 14-20 and 31-35 as printed. Alpen inserted 506 -> 246 (the other 260 were the tacet tail).

### 3. Scorecard raw bars from the per-page files (kept)
`score_alignment.read_raw_part` concatenates `<key>.pNNN.musicxml` (what `combine` reads) instead of the merged part file, which keeps only some
`multiple-rest` tags. Zerberus parts differing 19 -> 6 (|raw - combined| 996 -> 40), raw vs ref extra/missing 67/828 -> 89/93; combined numbers unchanged
(76/76). Also: the reference rules accept a `stage-` prefixed batch name.

### 4. Auto staff rank (kept as a feature; paxman p89 not adopted)
`staff_rank = "auto"` (batch part) / `--staff-rank-auto`: N is the number the part's label ends with, and only a page that is *wholly* a score
is touched (`staffpick.is_score_page`: every staff in a joined system of one size >= 2). A looser test was tried first and rejected: the joined-staves
test alone fires on spiral-binding shadows (paxman p56 h1 `[6]`, p65, p71-p73 `[2]`/`[3]`), so p56 would have been cut. Pages with a stray joined
group are left alone; a part whose number exceeds the staves of the page (horn 4 of a trio) still needs `score = false`. Paxman p88 is unchanged
(manual ranks); p89 (three-voice canzonet, in every horn's book) was re-run as a test: horn 1 reads 11 + 16 bars (right: 27), but `find_staves`
misses staves of the last system of page 2 in the h2/h3 scans (9 and 10 of 12 staves), so those books still read that system whole (24 / 48 bars) and
the piece was not adopted. Needs a staff finder that survives the scans, or a per-part rank by system spacing (intra-system gaps 170 px, between systems 240+ px,
perfectly regular on p89).
Tests: `tests/test_round5.py` (8 new). Full suite 504 passed.

### Remaining worst pieces and findings
- paxman p11 (18 inserted, 8 dropped), p24 (24: h1's page 2 gets bars 30-75 because page 1 lost bars and the page start "37" is unread: other parts read 33/34/36, the true
  start 37), p31 (4 inserted but 11 wrong-length), p33 (9), p45 (16: second-time numbers "4 (12)", "33 (41)" in parentheses: the third system's printed "21" cannot
  be reconciled with 8 + 5 bars read, 8 bars are inserted in a system whose bars are all played - a segment whose slots exceed its bars by >= 4 with no rest bar in it should
  drop its anchor), p81 (19 wrong-length, three movements), p86 (22 inserted / 9 wrong-length), p73 (507-515 bars); opera p03/p06; xmas p02.
- httyd2: 139 of 157 cached pages would change; run after a scene scorecard exists (`scripts/combine_scenes.py`).
- Szeged Alpen: 246 inserted remain, all parts lose multi-rest counts among playing parts (tbn1 220-296, euph 217-296 ...); the bass drum read 13 of 296 bars (round-4 figure, not re-checked).

## 2026-11-13, round 6: staff choice on skewed scans (paxman p89), all-playing stretches, what dominates the reference diffs

Baselines = the current outputs recombined with the HEAD code (`output/scorecards/r6-base-*.json`), "final" = `r6-final-*.json`, summary
`alignment-round6-2026-11-13.json`. Inserted / dropped / wrong-length bars (recombined, i.e. before adopting anything on disk in the
last column):

| corpus | round-5 on disk | base (p89 re-run) | final (recombined) |
|---|---|---|---|
| paxman-all | 338 / 67 / 214 | 333 / 67 / 210 | 327 / 67 / 210 (p76 4 instead of 10: not adopted on disk) |
| ghs-opera, ghs-xmas, goldfaden, gb-all, braveheart-horns/tubas, airbender, szeged-alpen, szeged-frozen | | | identical to base |
| GB p01-03 vs refs / Zerberus vs ref (extra/missing) | 35/43, 76/76 | | identical |

### 1. Per-part staff choice by staff spacing (kept; paxman p89 adopted)
`staffpick.staff_groups`: the staves come from `find_staves` or from a narrow-window finder (`local_staves`: 6 windows of 6% of the page
width near the staves' left ends, the one with most staves wins) and the systems of the page from the system barline (as before) or, when that
does not give a score page, from the gaps between staves (`spacing_groups`: two firm clusters, >= 2 system breaks, small gaps within 25%, big
gaps within 1.7x; **and** some barline evidence on the page, so a part page of paired staves (xmas p39 h3: 270 / 450 px gaps, two versions
per page) is never taken for a score). Cause of the p89 failures: a warped camera scan bends the lines so the wide band of `find_staves`
lost the last system's staves (h2 p2: 9 of 12, h3: 10 of 12; the narrow window sees 12 of 12) and the barline test failed on the left-bound
staves of h4 p2 (the first four systems). Scan of every cached page of every corpus (`prepped/p*.png`; paxman, gb, opera, xmas, goldfaden,
Szeged, Zerberus, Braveheart, airbender): the only pages that are score pages by the new test are p88 and p89 (p88 gives the same groups as before),
after two false positives (paxman p15/p41 h4, p65 h2: a part page with one odd gap; xmas p39: pairs) were excluded by the tests above.
Re-run (copisteria, `staff_rank = "auto"`, h1-h3; `p89_h4` set `score = false`, a trio): p89 reads 27/27/27 bars (was 81 bars: 5 inserted, 4 wrong-length, 4 parts
for a 3-voice piece); piece recombined with `--convert`. Old part dirs and combined piece in `output/paxman-all-pre-round6/`; `batches/paxman-all.toml`
(git-ignored) has `staff_rank = "auto"` on p89_h1-h3 and `score = false` on p89_h4 (old file: `output/paxman-all-pre-round6/paxman-all.toml.bak`).
p88 not re-run (same groups, same ranks).

### 2. `segments`: an anchor that an all-playing stretch cannot fill is ignored (kept, threshold 6)
`voltas.segments(rest=...)`: when the bars read between the previous anchor and this one hold no rest-only bar and the slots exceed the bars by
`NO_REST_EXCESS` = 6 or more, the anchor is a misread (or a printed repeat the part does not write out: paxman p45 horn 4 prints "4 (12)", "8 (16)", "21", the other parts
write the repeated 1-8 and 33-40 out: 40 bars read for 56) and is dropped. Threshold 4 (the request) was measured: paxman 333 -> 319 inserted (p26 4 -> 0, p66 4 -> 0, p76 10 -> 4)
but xmas p15 +5 wrong-length (a system that really lost 4 bars, anchor correct) and p10 +1; 6 keeps only p76 (10 -> 4) with no regression anywhere.
Value is small: p76's total (26 vs 20-22 bars read) is still doubtful, so it was not adopted on disk. The p45 case is not an anchor problem but a
repeat the others write out (h4 gets 8 + 8 inserted rest bars where copies of bars 1-8 and 33-40 belong): "unroll a part's own repeat when the
other parts write it out" is the real fix and was not attempted.
**Tried and rejected: recover a page start from the previous page's last system number** (`start = last-system number + bars read in it`). It gives the
right 37 for p24 h1 (and xmas p06 68 -> 58 bars, 31 -> 7 inserted, but 2 -> 18 dropped), but changed the Alpen total 296 -> 287 and
paxman +10 on the sum, with p24 unchanged: removed. The real cause of unread starts is the OCR (Szeged tbn1 page 5 prints "234", read as "24", then rejected by the
`reach` test: the page is bars 234-296).

### 3. Szeged
- **Frozen vs the reference (184 extra / 157 missing)**: the dominant difference is tie notation. The reference writes a tied pair as one longer note
  ((1.5, 1.5, 1.0) where the OMR has (1.0, 0.5, 0.5, 1.0, 1.0) with ties): `score_alignment` now also reports `extra_tm` / `missing_tm` with tied notes
  merged inside each bar: **130 / 103** (54 extra and 54 missing bars were tie notation). What remains: 3 tail bars (the arrangement prints 126-128, the
  reference ends at 125: ~25 extra rest bars), the glockenspiel (a 12-bar play stretch read as rests/other bars) and scattered pitch/rhythm misreads; not repeats or
  cues, and no further generic cause. NB: the same merge makes GB p01-03 worse (35/43 -> 101/109: its refs are written with separate tied notes), so it is a supplementary column, not a replacement.
- **Alpen**: per part 252-284 of 296 bars, 246 inserted. The page checked (tbn1 p5, 46 bars read for 63 printed): 2 of its 4 bold counts (the "4" and the "6" over the H-bars) are not read at all and
  the page start "234" is read as "24". Vision does not read a bold lone digit even from a clean contact sheet of the strip above the staff (tested: tiles read nothing, "3" only at 2x), rapidocr reads nothing.
  Fixing it means a digit classifier (templates from the digits Vision did read on the same part, or a small CNN) and a re-run of the part pages: the biggest lever left for Alpen (and for opera p21 and
  Braveheart). Bass drum / snare (`score = false`): single-line staves with multi-rests and cue staves ("4th Tbn."), the 5-line staff finder does not apply: not attempted.
- Zerberus vs 'New' ref (76/76): all 210 bars aligned, the 152 differences are substitutions in place: horns 1-4 play bars 63-68 in the print/OMR where the ref has rests (6 bars x 4 parts = 24 + 24),
  the 12 timpani bars, small pitch/rhythm differences. Content differences between the edition and the hand-edited ref, not alignment.

### 4. Survey: ten worst (inserted + dropped + wrong-length, base)
Alpen 366, goldfaden 262, braveheart-horns 169 (117 wrong-length: 8 horns read 67-104 of 124 bars, "no part reaches the printed lower bound", pages with lost multi-rest counts / systems), Frozen 121, airbender 105 (97 inserted, no
raw outputs to compare), gb p84 84 and p83 71 (pieces sharing pages), xmas p06 56, braveheart-tubas 56, xmas p02 45 (alternating 7/8 - 6/8, durations misread against the declared meter).
No generic alignment cause left in these: lost multi-rest counts (digit not read), neighbours' bars on shared pages, OMR duration misreads in changing meters. httyd2 not run (scene book, no scorecard).

### Plateau
Alignment-side changes are now worth a few bars per corpus (this round: p89 -5/-4, p76 -6); the remaining error is on the OMR side (count digits, shared-page neighbours, single-line percussion). Another alignment round is not worth it; a digit reader for bold counts is.
Tests: `tests/test_round6.py` (4). Full suite 508 passed.
