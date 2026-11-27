# Bach Cello Suites (trans. Wendell Hoss): transcription from the 2026-11 rescan

Scores: `output/bach-suites-rescan/<name>/<name>.mscz`. Inputs: `input/bach-suites-rescan/` (the rescanned PDF
split by suite; Suite VI also by ensemble). Suite VI solo movements are horn in E, the rest horn in F.

| Script | Does | Model calls |
|---|---|---|
| `correct.sh NAME` | settles bars where Copisteria and homr differ | yes (stored in `answers/`) |
| `audit.py count/read NAME` | finds lost bars and re-reads suspect lines | yes (stored in `answers/`) |
| `rebuild.sh NAME` | replays the stored answers and `structure.json`, rebuilds the score | no |
| `review_all.py NAME` | review-tool fix of every line (duet and trio) | yes |
| `check.py NAME` | bars with unexplained lengths | no |
| `report.py` | writes `remaining.md` | no |

`rebuild.sh` overwrites the `.mscz`: do not run it after editing a score by hand.
The scripts need the pipeline output and `ttl=30d/` files, which expire after 30 days.
The duet and trio were fixed in the `.mscz` only; their `.musicxml` is the raw OCR.
