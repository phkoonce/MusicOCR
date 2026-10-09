# Zerberus OMR progress

Auto-written by `scripts/zerberus_batch.py` (updated 2026-10-09 22:11). Outputs in `output/<key>/`; per-part PDFs in `input/zerberus/`. Re-run the script to resume.

| Part | Pages | Status | Notes |
|---|---|---|---|
| Horn in F 1 (`Zerberus_hn01`) | 19-21 | done | 0.5 min, rc=0; see output/Zerberus_hn01/report.md |
| Horn in F 2 (`Zerberus_hn02`) | 22-24 | done | 0.4 min, rc=0; see output/Zerberus_hn02/report.md |
| Horn in F 3 (`Zerberus_hn03`) | 25-27 | done | 0.4 min, rc=0; see output/Zerberus_hn03/report.md |
| Horn in F 4 (`Zerberus_hn04`) | 28-30 | done | 0.5 min, rc=0; see output/Zerberus_hn04/report.md |
| Horn in F 5 (`Zerberus_hn05`) | 31-34 | done | 0.5 min, rc=0; see output/Zerberus_hn05/report.md |
| Horn in F 6 (`Zerberus_hn06`) | 35-38 | done | 0.5 min, rc=0; see output/Zerberus_hn06/report.md |
| Horn in F 7 (`Zerberus_hn07`) | 39-41 | done | 0.5 min, rc=0; see output/Zerberus_hn07/report.md |
| Horn in F 8 (`Zerberus_hn08`) | 42-44 | done | 0.5 min, rc=0; see output/Zerberus_hn08/report.md |
| Horn in F 9 (`Zerberus_hn09`) | 45-47 | done | 0.5 min, rc=0; see output/Zerberus_hn09/report.md |
| Horn in F 10 (`Zerberus_hn10`) | 48-50 | done | 0.6 min, rc=0; see output/Zerberus_hn10/report.md |
| Horn in F 11 (`Zerberus_hn11`) | 51-53 | done | 0.5 min, rc=0; see output/Zerberus_hn11/report.md |
| Horn in F 12 (`Zerberus_hn12`) | 54-56 | done | 0.5 min, rc=0; see output/Zerberus_hn12/report.md |
| Euphonium 1 (`Zerberus_eu1`) | 57-59 | done | 0.5 min, rc=0; see output/Zerberus_eu1/report.md |
| Euphonium 2 (`Zerberus_eu2`) | 60-62 | done | 0.5 min, rc=0; see output/Zerberus_eu2/report.md |
| Euphonium 3 (`Zerberus_eu3`) | 63-65 | done | 0.5 min, rc=0; see output/Zerberus_eu3/report.md |
| Tuba (`Zerberus_tuba`) | 66-68 | done | 0.5 min, rc=0; see output/Zerberus_tuba/report.md |
| Pauken (`Zerberus_pauken`) | 69-71 | done | 0.4 min, rc=0; see output/Zerberus_pauken/report.md |
| Perkussion 1 (`Zerberus_perc1`) | 72-73 | done | 0.3 min, rc=0; see output/Zerberus_perc1/report.md |
| Perkussion 2 (`Zerberus_perc2`) | 74-77 | done | 0.6 min, rc=0; see output/Zerberus_perc2/report.md |

## Status

All 19 parts OMR'd with homr (2026-10-09). **Next: manual correction of each part** (open `output/<key>/<key>.mscz` in MuseScore, compare against `render.pdf`/`qa/`, save corrected MusicXML), then build `omr-eval/specs/zerberus.py` and combine with `omr-eval/align_combine.py`.
