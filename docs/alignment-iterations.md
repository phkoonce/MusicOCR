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
