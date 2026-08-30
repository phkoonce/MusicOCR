# Fixing a page in the Audiveris GUI

The batch pipeline never opens Audiveris. When a page comes out badly (or not at
all), you can open it in the Audiveris desktop app, tune settings and/or fix
symbols by hand, and export corrected MusicXML that the pipeline picks back up.

Official handbook (authoritative, with screenshots):
<https://audiveris.github.io/audiveris/>

---

## 1. Open the app and the page

```bash
open -a Audiveris
```

- **File ▸ Input…** — open the original PDF (or a single-page image). All pages
  show up as sheet tabs along the top. Work on one at a time.
- **File ▸ Open books…** — open a saved `.omr` project instead (e.g.
  `output/<book>/pages/pNNN/source.omr`, written when
  `[stages.correct] enabled = true`). This reloads a previous transcription,
  errors and all, so you can pick up where batch left off.

To limit work to a few pages: **Book ▸ Select sheets…**.

---

## 2. Tune settings before re-transcribing  ← do this first

**Book ▸ Set book parameters…** opens a dialog with scope tabs
(**Default** / this book / per-sheet — pick the book or sheet tab so you don't
change global defaults) and these panes:

| Pane | What it does | For a poor scan |
|---|---|---|
| **Input quality** | `Synthetic` / `Standard` / `Poor` | set **Poor** — loosens many tolerances at once |
| **Filter** | Binarisation: `Kind` = Adaptive/Global, plus `Threshold` (global) or `Coeff for Mean` / `Coeff for StdDev` (adaptive) | keep **Adaptive**; lower *Coeff for Mean* (e.g. 0.60 → 0.50) to pull out faint ink. Global almost always fails on a grey scan. |
| **Interline** | manual staff-line spacing override | set it if the log shows a wide range like `interline(16,19,20)` |
| **Beam thickness** | manual beam-height override | **key for rhythm** — if Audiveris "guessed" and "measured" beam heights disagree a lot, pin the measured value |
| **OCR language(s)** | Tesseract languages | `eng` |
| Switch checkboxes | small heads, small beams, system indentation, keep gray images, implicit tuplets, articulations, dynamics above/below staff, … | turn **off "Use of system indentation"** if one page is being split into several — indentation detection is what makes Audiveris think a new movement starts |

Then re-run: **Sheet ▸ Transcribe sheet** (or the ▶ button). Repeat: change a
setting, re-transcribe, look.

> The batch `--constant` flag reaches the same *constants*, but the switch
> constants (poor-input etc.) are unreliable from the CLI — several are silently
> ignored. The Book Parameters dialog is the dependable way to set them.
> `Tools`-menu **Options** (the constants table) exposes everything else, with
> descriptions and a search box.

---

## 3. Fix symbols by hand

Each sheet has view tabs: **Binary** (the binarised image — check your filter
here), **Data** (the recognised symbols overlaid). Work in **Data**.

- **Errors board** (bottom / a board on the right) lists every unresolved
  problem. Double-click one to jump to it.
- **Select** a wrong symbol by clicking it. The **Inter board** on the right
  shows what Audiveris thinks it is and its confidence.
- **Reassign**: right-click the selection → context menu → choose the correct
  shape; or open the **shape palette** board, click the right shape, then click
  where it goes. Clefs live in the palette's clef section — this is how you
  replace a mis-read tenor C-clef.
- **Delete**: select → <kbd>Delete</kbd>. Good for spurious slurs, octave
  markers, stray dots.
- **Move**: drag. **Undo**: <kbd>⌘Z</kbd>.
- **Toggle repetitive input** (Sheet menu) speeds up adding many similar
  symbols.
- After a batch of edits, **Sheet ▸ Transcribe sheet** again re-derives rhythm
  and page structure from your corrections.

Zoom with the mouse wheel; there's a zoom control at the sheet view's corner.

---

## 4. Export and hand back to the pipeline

- **Book ▸ Export book** (or **Sheet ▸ Export sheet as…**) writes `.mxl` next to
  the project.
- **Book ▸ Save book** updates the `.omr` so you can resume editing later.

Then, back in the pipeline — point it at the folder holding your corrected
`.mxl` and run from `extract`:

```bash
python -m musicocr run "input/Frozen Bass Trombone 1.pdf" --from extract
```

`extract` collects every `.mxl` under the work dir, so a corrected export placed
in `output/<book>/pages/pNNN/` replaces the batch result for that page.

---

## 5. When the scan is the problem

If even careful GUI work can't get a page right, the input resolution is
probably too low (Audiveris wants ~250–400 DPI of actual detail). Check:

```bash
pdfimages -list -f 1 -l 1 "input/your.pdf"
```

If `x-ppi` / `y-ppi` is ~150 or below, rescan at a higher setting — that will
help more than any amount of tuning. Failing that, the
`scripts/experiment.py` sweep found that **rasterising the page at 300 DPI with
autocontrast + an unsharp-mask pass** recovers a lot on this kind of scan; that
pre-processing is available in the pipeline via `[preprocess]` in `config.toml`.
