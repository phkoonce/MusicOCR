<!-- batch:begin (auto-generated; text outside the markers is preserved) -->
# Zerberus OMR progress

Auto-written by `musicocr.batch` from `zerberus.toml` (updated 2026-10-11 11:41). Outputs in `output/zerberus/<key>/`; per-part PDFs in `input/zerberus/`. Re-run the batch to resume.

| Part | Pages | Status | Notes |
|---|---|---|---|
| Horn in F 1 (`Zerberus_hn01`) | 19-21 | done | 0.1 min, rc=0; see output/zerberus/Zerberus_hn01/report.md |
| Horn in F 2 (`Zerberus_hn02`) | 22-24 | done | 0.2 min, rc=0; see output/zerberus/Zerberus_hn02/report.md |
| Horn in F 3 (`Zerberus_hn03`) | 25-27 | done | 0.1 min, rc=0; see output/zerberus/Zerberus_hn03/report.md |
| Horn in F 4 (`Zerberus_hn04`) | 28-30 | done | 0.1 min, rc=0; see output/zerberus/Zerberus_hn04/report.md |
| Horn in F 5 (`Zerberus_hn05`) | 31-34 | done | 0.1 min, rc=0; see output/zerberus/Zerberus_hn05/report.md |
| Horn in F 6 (`Zerberus_hn06`) | 35-38 | done | 0.1 min, rc=0; see output/zerberus/Zerberus_hn06/report.md |
| Horn in F 7 (`Zerberus_hn07`) | 39-41 | done | 0.1 min, rc=0; see output/zerberus/Zerberus_hn07/report.md |
| Horn in F 8 (`Zerberus_hn08`) | 42-44 | done | 0.1 min, rc=0; see output/zerberus/Zerberus_hn08/report.md |
| Horn in F 9 (`Zerberus_hn09`) | 45-47 | done | 0.1 min, rc=0; see output/zerberus/Zerberus_hn09/report.md |
| Horn in F 10 (`Zerberus_hn10`) | 48-50 | done | 0.1 min, rc=0; see output/zerberus/Zerberus_hn10/report.md |
| Horn in F 11 (`Zerberus_hn11`) | 51-53 | done | 0.1 min, rc=0; see output/zerberus/Zerberus_hn11/report.md |
| Horn in F 12 (`Zerberus_hn12`) | 54-56 | done | 0.1 min, rc=0; see output/zerberus/Zerberus_hn12/report.md |
| Euphonium 1 (`Zerberus_eu1`) | 57-59 | done | 0.1 min, rc=0; see output/zerberus/Zerberus_eu1/report.md |
| Euphonium 2 (`Zerberus_eu2`) | 60-62 | done | 0.1 min, rc=0; see output/zerberus/Zerberus_eu2/report.md |
| Euphonium 3 (`Zerberus_eu3`) | 63-65 | done | 0.1 min, rc=0; see output/zerberus/Zerberus_eu3/report.md |
| Tuba (`Zerberus_tuba`) | 66-68 | done | 0.1 min, rc=0; see output/zerberus/Zerberus_tuba/report.md |
| Pauken (`Zerberus_pauken`) | 69-71 | done | 0.1 min, rc=0; see output/zerberus/Zerberus_pauken/report.md |
| Perkussion 1 (`Zerberus_perc1`) | 72-73 | done | 0.1 min, rc=0; see output/zerberus/Zerberus_perc1/report.md |
| Perkussion 2 (`Zerberus_perc2`) | 74-77 | done | 0.1 min, rc=0; see output/zerberus/Zerberus_perc2/report.md |
<!-- batch:end -->

## Status

All 19 parts OMR'd with homr (2026-10-09). **Next: manual correction of each part** (open `output/zerberus/<key>/<key>.mscz` in MuseScore, compare against `render.pdf`/`qa/`, save corrected MusicXML), then build `omr-eval/specs/zerberus.py` and combine with `omr-eval/align_combine.py`.

## Combined score (non-percussion), 2026-10-09

`omr-eval/align_systems.py` + `omr-eval/specs/zerberus.py` align the 16 pitched parts to the
210 printed bars and combine them into `output/zerberus/Zerberus_combined/Zerberus_combined.{musicxml,mscz,mid,pdf}`:

    .venv/bin/python omr-eval/align_systems.py omr-eval/specs/zerberus.py output/zerberus/Zerberus_combined/Zerberus_combined.musicxml

Alignment is derived automatically (printed system-start bar numbers + multi-rest counts from the
part PDFs' text layer vs. homr's per-system measure counts); the spec only holds score-wide facts
(meter: 4/4 to bar 34 with a 1-beat pickup bar 1, 6/8 from bar 35; key map; tempo/rehearsal marks).
`Zerberus_combined.report.txt` lists every repair (dropped stray measures, lost/misread multi-rests)
and every bar whose note content doesn't add up to the bar length (those are the OMR errors to fix).
Pauken and Perkussion 1/2 are not included yet.
