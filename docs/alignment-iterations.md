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
