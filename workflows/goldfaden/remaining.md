# Goldfaden: state after the second correction pass (2026-11-27)

Score and parts all have 906 bars and the same bar numbers. They differ only in nine pickup bars
(bars 318, 319 and 420), where one file pads the bar with rests and the other does not.

## Still open
- Not checked against the print: slurs, dynamics, articulations, and bars where the score and part
  readings always agreed.
- Tremolo strokes are missing (for example horn 3 around bar 60).
- Horn 4 bars 381-394 were typed in from the printed score; the part book was not compared.
- Variation 4 (bars 400-419) in the parts was copied from the score, so any edition differences in
  slurs or dynamics there are lost.
- Many bars copied from the other file lost slurs and hairpins that cross the bar line.
- Flags left in the files: "tuplet rebuilt" (horn 1 bar 387, horn 3 bars 371 and 389) and a misread
  tempo "Allegro pacoi" (horn 3 bar 578).
- Some of the parts' line and page layout may no longer match the books.

## Corrections cache
`corrections-*.json` replays on a fresh pipeline run. Edits next to added bars cannot be replayed:
25 (horn 1), 13 (horn 2), 15 (horn 3) and 20 (horn 4) are listed for hand entry by `apply`.
The score's corrections replay completely.
