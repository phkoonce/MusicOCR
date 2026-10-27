# Mini design: cue notes printed over a whole-bar rest

Status: proposal, ready to hand to an implementer. Written 2026-10-27.
Context: HTTYD2 trumpet parts, scene 5M38-39A (found while diagnosing why that scene has ~39 flagged bars).

## 1. Problem

Parts often print a **whole-bar rest** (the player rests) together with a line of **cue-size notes**
(what another instrument plays: "Choir, Cel", "Vln. 1"). The OMR reads both as ordinary notes of the same
voice, so the bar holds the rest *and* the cue notes and is exactly twice as long as it should be:

```
printed bar (3/4):   [ whole-bar rest ]   + small cue notes: quarter, quarter, quarter   (3 beats)
read (voice 1):      R(18, no type) quarter(6) quarter(6) quarter(6)                     = 6.0 beats, expected 3
```

Evidence (output of the full run; page files under `output/httyd2/HTTYD2_tpt2/HTTYD2_tpt2.p046.musicxml`,
divisions 6, 3/4, measures 5-10; Trumpet 3 is page 44):

| measure | notes read (type, duration) | total |
|---|---|---|
| 5 | R quarter 6, R (typeless) 18, A quarter 6, B quarter 6 | 36 = 6 beats |
| 6 | R (typeless) 18, C quarter 6 (tied), C eighth 3, B eighth 3, A quarter 6 | 36 |
| 7 | R (typeless) 18, G half 12, F quarter 6 | 36 |
| 8 | R (typeless) 18, D half 18 (tied) | 36 |

The typeless rest (`<rest/>` or `<rest measure="yes"/>`, no `<type>`) has exactly the bar's length
(18 = 3 beats x 6); the other notes add up to exactly one more bar. Bars 35-40 of the combined scene
(Trumpet 2) and 36-41 (Trumpet 3) are flagged "LENGTH 6 beats, expected 3" because of this.
The same printed pattern appeared in scene 1M2S, where the cue notes were read as *grace* notes and the
rest came out too long instead; that case is already handled by `musicocr/barrests.py` (a lone
overlong rest is cut to the bar). This design covers the case where the cue notes are *ordinary* notes.

## 2. Goal and non-goals

Goal: such a bar becomes length-correct (equals the time signature) and still shows the cue notes,
marked as cue notes, without changing how bars without this pattern behave.

Non-goals: detecting that notes are cue-sized from the image (small-notehead detection exists in
`mark_small_graces` but is unreliable); playback of cue notes; any change to tuplet handling.

## 3. Detection rule (conservative)

A measure qualifies when, in one voice with no `<backup>`/`<forward>` already present:
1. it contains exactly one *whole-bar rest*: `<rest>` with no `<type>` (or `measure="yes"`), whose
   `<duration>` equals the bar capacity (divisions x 4 x beats / beat-type), and
2. the remaining non-grace, non-chord notes/rests together last **exactly** one bar capacity, and
3. there is at least one pitched note among them.

(Equality in 2 is the safety: a real bar of notes plus a stray rest will rarely add up to exactly one extra
bar. Do not relax it without evidence.)

## 4. Representation

Standard MusicXML for two simultaneous lines: keep the whole-bar rest in voice 1, put the other notes in
voice 2 after `<backup><duration>capacity</duration></backup>`, and give each of them `<cue/>` (as the
first child before `<chord/>`/`<pitch>`, per the schema order). The measure then has voice-1 length =
capacity and voice-2 length = capacity.

Why not graces: playback of 3 beats of "grace" notes is nonsense and rhythm is lost. Why not drop them:
the cue is information the player wants.

## 5. Code changes (where)

1. `musicocr/barrests.py`: add `cue_notes_over_rest(measure, capacity) -> bool` next to `fix_measure`
   and call it from `fix_tree` (same capacity walk, `musicocr.util.measures_with_capacity`). Log
   "cue notes moved to voice 2". Wired already into the extract stage through `fix_file`, so new pages get
   it automatically.
2. `musicocr/alignment/build.py::measure_total`: today it sums every non-chord/non-grace note, so a
   two-voice bar double counts. Make it the longest voice (sum per `<voice>`, take the max) so
   `check_length`, `read_page`'s multi-rest unit, `split_overlong`, `scenes.py` pickup detection and the
   combine LENGTH check all see the true bar length. Check every caller (grep `measure_total`).
3. `musicocr/alignment/build.py::is_rest_only`: ignore `<cue/>` notes (a bar whose sounding voice is a
   rest is a rest bar for the alignment's rest pattern; `combine._rest_flag` uses it).
4. `musicocr/combine.py::copy_marks`: copies only `note`, `direction` and light-light barline children.
   It must also copy `backup`/`forward` (rescaled like durations) and keep `<voice>`, otherwise voice 2
   is appended after voice 1 and the bar overflows again. Same for `alignment/build.py::copy_notes`
   (used by the non-scene path).
5. Other code already skips bars with `<backup>` (`tuplets.fix_measure`, `copisteria_fixes`,
   `combine.split_overlong`, `barrests.fix_measure`); `marks.py` and `timesig.py` understand backup.
   Confirm nothing assumes one voice per bar: grep for `findall("note")` in `combine.py`, `scenes.py`,
   `mscz_sections.py`, `marks.py`.
6. Do not add a config switch unless the rule misfires in testing; if added, name it under
   `[stages]` and document it in `config.toml` and the README next to the whole-bar-rest section.

## 6. Tests (add to `tests/test_barrests.py` and `tests/test_combine*.py` style files)

- The table in section 1 as fixtures: measure 5/6/7/8 style bars become voice 1 = capacity, voice 2 =
  capacity, `<backup>` present, `<cue/>` on moved notes; schema child order valid.
- Negative: a bar with notes and a rest that do *not* add up to exactly one extra bar is untouched; a bar
  with two typeless rests; a pickup bar; a bar already containing `<backup>`.
- `measure_total` on a two-voice bar returns the capacity (regression for the other callers).
- Combine end-to-end on a tiny synthetic 3-part scene: the cue bar is not flagged and keeps both voices.

## 7. Verify on real data

Work in your own git worktree (the main checkout `/Users/philip/Dev/MusicOCR` is on another branch; use it
only for data). Symlink `.venv` and `omr-eval/copisteria` from it into the worktree, run Python as
`PYTHONPATH=<worktree> /Users/philip/Dev/MusicOCR/.venv/bin/python`, and run `pytest -q` (297 pass today).

1. Apply the new step to the existing page files without re-running OMR:
   `from musicocr.barrests import fix_file` over
   `output/httyd2/HTTYD2_tpt{1,2,3}/HTTYD2_tpt?.p0??.musicxml` (make a backup copy first).
2. Re-combine only the scene:
   `PYTHONPATH=<wt> .../python scripts/combine_scenes.py --batch <scratchpad>/httyd2.toml --cues batches/httyd2.cues.json --only 5M38-39A --convert`
   (spec path and cue file: see `/private/tmp/claude-501/-Users-philip-Dev-MusicOCR/adf48c7b-ebeb-4f84-a01e-502d369962c4/scratchpad/httyd2.toml`
   and `/Users/philip/Dev/MusicOCR/batches/httyd2.cues.json`).
3. Expected: the "LENGTH 6 beats, expected 3" flags on Trumpet 2 bars 35-40 and Trumpet 3 bars 36-41 are
   gone; the scene's flagged count drops from 39. Report what did not clear and why (bars 14-19 and 98-113
   of Trumpet 1 are a *different* problem: a cautionary 3/4 attached to a multi-rest bar wins the meter
   vote; do not chase those here).
4. Open the resulting `.mscz` converted file or inspect the `.mscx` (zip) to confirm MuseScore imports the
   `<cue/>` notes as small/cue notes in voice 2 and keeps bar lengths regular (MuseScore CLI:
   `"/Applications/MuseScore 4.app/Contents/MacOS/mscore" -f -o out.mscz in.musicxml`; it SIGABRTs on exit
   but writes the file; judge by the output).
5. Scan all 31 scenes' reports for newly flagged bars (compare with
   `scratchpad/prev_full/report_after_run1.txt`); the change must not add flags anywhere.

## 8. Risks

| Risk | Mitigation |
|---|---|
| False positive: a real bar of notes plus a rest that happens to total capacity + capacity | exact-sum rule, typeless-rest rule, at least one pitched note |
| Downstream code assuming one voice per bar | section 5 item 5 grep + tests; MuseScore import check |
| Playback of cue voice | `<cue/>` marks them; verify MuseScore does not play them (else mute voice 2) |
| Rests that are cue-line rests (e.g. R quarter 6 in measure 5) | they move with the cue line (rule 2 counts them in the extra bar) |

## 9. Deliverable

Commits (with trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`), pushed to `main`
unless told to open a PR; a short report: before/after flagged-bar counts for scene 5M38-39A and the full
report diff, what was verified in MuseScore, and anything left alone.
