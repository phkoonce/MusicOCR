# Scan -> OCR -> Proof: component design (2026-11-27)

Status: proposal. Nothing has been moved or changed yet. Items marked are mine until you
say otherwise.

**Goal.** Every project ends in a verified **Score + Parts** (one and the same thing for a single-voice piece
such as the Cantilena horn solo).

**Decided (2026-11-27):**

- The corrected transcription is a project model plus a ledger; MuseScore is an export that can be re-imported.
- OCR reads each source on its own. Everything cross-source happens in `proof`.
- **Score first:** the scan of the score is the primary source. Part readings are references used where they help.
- One repository, **Scriptorium**; permanent data in `My Drive/Music/Projects/`.
- **One `.mscz` per project**: the score, with parts generated from it. Expression text, tempo and similar
  markings should match the original part books where those exist.
- A model proposal is applied automatically when two independent pieces of evidence agree.
- The part-books-only path is ported as a fallback.
- Machine verification and human verification are two tiers, tracked independently.
- Steps resume: a page can be added or redone and the improvement flows downstream. Starting clean is the
  exception.
- Machine verified means: structural checks pass, a check against the page image or an independent witness
  agrees, and no annotation is open. Notes and markings are verified separately.
- Work that cost time or quota is kept by default, but can be thrown away when it is no longer useful.

## 1. What goes wrong at the seams today

| Seam | Today | Consequence |
|---|---|---|
| Importer -> OCR | A PDF. OCR rasterises it again (350 dpi from 300 dpi pages), runs its own deskew/crop, finds staves again | Page geometry, printed page numbers and flags are lost; image work exists twice |
| Inside OCR | `retouch`, `transpose` and `extract` rewrite earlier files, with `pre-retouch/` and `written/` backups | Hard to say which file is "the reading" |
| OCR -> correction | The `.mscz` is the machine output and also where edits live | A rerun overwrites hand work; copies named `before-review`, `pipeline-baseline`, `before-corrections`, `before-pass2` |
| Corrections | Whole bars of MuseScore XML keyed by hash; one-off scripts per project (`arbitrate.py`, `bach-suites/`, `goldfaden/format.py`) | Added/removed bars cannot be replayed; many bars skipped because a slur or hairpin crosses the bar line |
| Status | `review.json`, `remaining.md`, `answers/*.json` | No single list of what is wrong, fixed or checked |
| Retention | `combine` and `layout` read per-page files from `ttl=30d/` | Later steps depend on files that expire |

## 2. Rules

1. **Each step owns its package and never changes its input.** A package is a folder with a manifest:
   format version, producer and code commit, settings, and a hash of every page it holds and was made from.
2. **Packages are updated in place, page by page.** Rerunning a step redoes only what changed; downstream
   steps compare hashes and pick up from there (section 2.1).
3. **Retention follows value.** Work that cost your time or model quota is kept by default; anything that can
   be rebuilt, or is no longer useful, goes to a `ttl=` folder.
4. **Final output = current baseline reading + ledger of decisions.** Both are saved, so rebuilding makes no
   model calls.
5. **All image positions are in source-page pixels.** A step that crops or rescales records the transform.
6. **Components share only the package formats**, not each other's internals.

```
scan  ──►  SOURCE package  ──►  ocr  ──►  READING package(s)  ──►  proof  ──►  EXPORT
(or PDF import)   pages + structure        notes + where each bar is      ledger      .mscz .musicxml .pdf
```

### 2.1 Updating a page and carrying it downstream

Typical cases: a page was missed, scanned badly, or a newer engine reads one page better.

| Step | What an update does |
|---|---|
| `scan` | Replace or insert one page. A page id is permanent and independent of page order, so an inserted page renumbers nothing. Other page images stay byte for byte the same |
| `ocr` | Reads only pages whose hash changed. The cheap cross-page work (page joining, key and meter carry-over, movements) reruns over all pages from their saved readings |
| `proof` | Compares old and new baseline bar by bar and applies the table below |

| Bar after the update | What happens to earlier work |
|---|---|
| Reads the same | Corrections, annotations and verifications stay |
| Changed, no correction on it | The new reading is taken; its verifications go stale |
| New reading equals your correction | The correction is marked satisfied |
| Changed and corrected differently | The correction still applies, and the bar is flagged as a conflict for review |
| New (missed page) | Inserted with new bar ids, unverified |
| Gone | Its work is set aside and listed, not silently dropped |

- By default, a verification whose content is unchanged stays valid even if the page was rescanned. It records
  that it was made against the older image.
- `proof update` shows a summary (bars unchanged, changed, conflicting) before applying it.
- If too little matches, the tool says so and asks. **Start clean** is an explicit command that sets the old
  ledger aside.
- Replaced pages and readings move to `ttl=30d/`, which gives a month to undo.
- Code or setting changes alone do not re-read anything; you ask for that.

What this asks of the importer: today `process` rebuilds every page, needs every raw scan, and takes some
settings from the whole piece (usual margins, orientation). It has to save those settings with the package
and process one page against them.

## 3. Components

### 3.1 `scan`: paper (or PDF) to a trustworthy source

Owns: scanner session, raw sheets, orientation, split, deskew, dewarp, denoise, sizing, page numbers,
ordering, completeness, metadata, and **what is on each page**.

- Two ways in, one output: the flatbed, and `import <pdf>` (born-digital, downloads, camera scans).
  OCR's current deskew/crop pre-processing moves here.
- "What is on each page" covers instrument, movement and piece, as page ranges or page regions. This
  replaces cutting a book into per-piece PDFs (`collection split`).
- Does not know about notes.

```
source/
  source.json        composer, title, edition, completeness (missing/duplicate pages), provenance
  structure.json     segments: {piece, movement, part(s) or "score", pages/regions, transposition}
  pages/<id>.png     final lossless page images; ids are permanent, order is listed
  pages.json         printed number, dpi, staff spacing, flags, sheet it came from
  text.json          PDF text layer, when there was one
  print.pdf          convenience copy for reading and printing (derived)
```

Raw sheets stay scratch with the current rule (deleted a few days after a complete package). A page redone
later is rescanned, not rebuilt from old raw sheets.

### 3.2 `ocr`: source to the best machine reading

Owns: engines (Copisteria, homr), pre/post-processing, every automatic repair that is justified by **the
page itself** (count digits, text layer, second pass, tuplets, meter, slur filter).

Does not: use other parts as evidence, apply house style, transpose pitches, lay out lines, or write into
the source.

```
reading/<segment>-<engine>/
  reading.json       engine, versions, settings, source page hashes, failed pages
  pages/             saved reading of each page, so unchanged pages are never re-read
  score.musicxml     written pitch, as printed; irregular bars stay irregular
  layout.json        per bar and part: page, system, staff, box in source pixels
  findings.json      gaps, guesses, bar-count mismatches, repairs applied (with the bar they touched)
  alternates/        other readings of the same bars (other engine or pass), where available
```

- One reading per source segment and engine. Part books are **not** merged here.
- Padding/truncating bars to the time signature (`measure_normalize`) hides evidence. It becomes an
  export option; the reading only reports the bar as irregular.
- Detections, prepared images, second-pass files and engine logs are cache (`ttl=30d`).

### 3.3 `proof`: readings to a verified transcription

A **project** is one piece of music to get right. Its bars and parts come from the score reading; for a
single-voice piece that one reading is the whole project.

| Concept | Meaning |
|---|---|
| Source | A source package. A project can have several: score, part books, another edition |
| Witness | A reading of a source. Score vs parts, and Copisteria vs homr, are both just two witnesses |
| Part | An instrument line in the result: name, transposition |
| Bar | A position in the piece with a permanent id; order is a separate list, so inserting a bar renumbers nothing |
| Cell | One part in one bar: the unit of correction |
| Line | One part's staff on one system of a source page: the unit of verification |
| Annotation | "Something is or may be wrong here": open, resolved or dismissed |
| Correction | A proposed change: proposed, accepted, rejected or superseded |
| Verification | "This line matched the source when its content was X" |

Every annotation, correction and verification has an **actor** (`human`, `model` + name, or `rule` +
version), a time, a target, and optionally the image region it was judged from.

**Ledger.** `ledger.jsonl`. A change of state is a new event, so history and disagreements are kept. An
optional `compact` command moves entries that no longer matter (rejected or superseded model proposals, raw
model answers) to `ttl=30d/`. It never removes human entries or anything the current output depends on.

```json
{"id":"c-0192","type":"correction","actor":{"kind":"model","name":"sonnet","run":"r-31"},
 "target":{"part":"hn1","bars":["b0147"]},"base":"sha:9f2c…",
 "change":{"op":"replace_cells","cells":["Bb4h.~"]},"evidence":{"source":"score","page":5,"box":[…]}}
{"id":"d-0201","type":"decision","actor":{"kind":"human"},"of":"c-0192","verdict":"accept"}
```

Operations: replace cell content; add/remove/move spanner (slur, tie, hairpin, octave line); insert/delete
bars; set key, time, clef; set marks (dynamics, text, rehearsal, repeats, endings); assign a witness
range to bars (alignment fixes).

**Content format.** Cells hold normalised MusicXML (nothing is lost), spanners are stored
separately with start and end positions, and a compact text form (`Bb4q. rq Bb4e`) is derived for models
and for you. Separate spanners remove the "crosses the bar line, not changed" failures.

**Replay.** `base` is the hash of what the correction was made against. If it no longer matches, section 2.1
applies; nothing is guessed. Old and new bars are matched by page id, system and bar position
(`layout.json`), then by content. Pixel positions alone are not used, because a rescan shifts them.

**Verification.** Two tiers, recorded independently; neither implies or overwrites the other.

| Tier | A line has it when | Recorded by |
|---|---|---|
| Machine verified | structural checks pass **and** a check against the page image or an independent witness agrees with the current content; no open annotation | `model` or `rule` |
| Human verified | you compared the line with the original and accepted it | `human` |

Each tier has two scopes, `notes` (pitch, rhythm, rests, ties) and `markings` (slurs, articulations,
dynamics, text). A verification stores the line's hash, so a later change makes it stale
automatically. A system is verified (per tier and scope) when all its parts are; a piece when all systems
are and every source bar is accounted for. A machine pass never hides a line from review: the review page
shows both states and can filter on either.

**Applying model proposals.** A proposal is applied without you only when two independent pieces of
evidence agree (for example a part reading and a model looking at the score image). It stays marked as
auto-applied and is not human verified.

**Tools**, all reading and writing through the same ledger API:

| Tool | Does | Comes from |
|---|---|---|
| attach | map reference readings (parts, second engine) onto the score's bars | new; ideas from `assemble/` |
| check | bar lengths, counts, ranges; open annotations | `validate`, `barcheck`, `check.py` |
| compare | disagreements between witnesses | `compare.py`, `arbitrate.py` |
| normalise | cross-part dynamics, hairpins, rehearsal marks, repeats, as **proposals** | `scoreops/marks.py`, `assemble/*` |
| ask | send crops and compact notation to a model; store answers | `review/claude.py`, workflow scripts |
| review | your line-by-line page: accept, reject, annotate, verify | `review/` |
| export | replay the ledger to `.musicxml` / `.mscz` with layout and pitch options | `scoreops/layout.py`, `transpose.py`, `convert` |

**MuseScore edits.** You can still fix things in MuseScore: export, edit, then `proof import`
diffs the score against what was exported and records your changes as accepted human corrections. Line
breaks and styling are not captured.

**Print errors.** The default transcription says what is printed. A fix to a misprint is a correction
tagged `editorial`, which export can include or leave out.

### 3.4 Post-processing: one plan for everything after the engine

Post-processing lives in three places today: inside the OCR pipeline, in the importer (image work, plus
scripts written during imports), and in per-project correction scripts. The rule for sorting it:
**page image -> `scan`; reading one page -> `ocr`; judging against anything else -> `proof`.**

| Capability | Today | New home | Change |
|---|---|---|---|
| Deskew, dewarp, whiten, sizing, margins, text-page centring | importer | `scan` | none |
| `work/bach-cello-suites/recentre.py`, `geo.py` | one-off script | `scan` | delete once the importer's margin work is confirmed to cover it |
| OCR deskew, crop, autocontrast for PDFs and camera scans | `preprocess.py` | `scan` (PDF import) | one image-cleaning implementation |
| Count digits, text layer, hairpins, second pass, tuplets, meter, slur filter, empty bars | `ocr` | `ocr` | stays; each repair reports the bars it touched |
| Page merge, key and meter carry-over, movement headings | `extract`, `retouch` | `ocr` | runs without rewriting earlier files |
| Bar padding and truncating | `normalize` | export option | reading keeps what was read |
| Transposition, title and composer, line and page layout | `transpose`, `retouch`, `convert` | export | taken from source metadata and `layout.json` |
| Bar-length, bar-count, odd-tuplet checks | `validate`, `barcheck`, `check.py`, `barcount.py` | `proof check` | one module; findings become annotations |
| Disagreement between readings | `compare`, `arbitrate.py`, `correct.py`, `lost.py` | `proof compare` + `ask` | score vs part and engine vs engine are the same operation |
| Re-reading suspect lines, counting bars with a model | `audit.py`, `review_all.py` | `proof ask` | answers stored in the ledger |
| Ossia removal, section breaks, false headings, rehearsal labels | `finish.py`, `survey.py`, `goldfaden/format.py` | structure operations in the ledger | no per-project code |
| Missing score page filled from parts | `goldfaden/page12.py` | "take these bars from witness X" | an ordinary correction |
| Cross-part dynamics, hairpins, text, tempo, repeats | `marks.py`, `assemble/{text_*,repeats,rehearsal}.py` | `proof normalise` | proposals only; mostly confirmation when a score exists |
| Aligning part books with no score | `assemble/{combine,anchored,unnumbered,voltas,scenes}`, `alignment/` | `proof` fallback baseline | ported; see below |
| Splitting collections | `collection/split.py` | `scan` structure | no cut PDFs |

**Score-first pipeline in `proof`:**

1. **Baseline** from the score reading.
2. **Structure check:** every system and bar of the source accounted for; bar counts against printed numbers.
3. **Attach references:** match part readings to score bars using printed bar numbers, rehearsal marks,
   multi-rests and content. A stretch that cannot be matched simply has no reference.
4. **Annotate:** failed checks and disagreements.
5. **Ask** a model about annotated bars, with crops of the score and the reference's reading.
6. **Review** by you.
7. **Normalise** markings, as proposals.
8. **Export** score and parts.

Because references are optional and may be partial, step 3 is far simpler than `combine`, which has to
produce a complete, consistent score from parts alone.

**One output file.** Export writes one `.mscz`: the score, with its parts generated from the same content
(multi-rests collapsed), so notes cannot drift apart. A single-voice piece is just the score.

**Markings follow the part books.** Where a part source exists, its wording and placement of expression
text, tempo marks, dynamics and hairpins are what the project records; the score reading fills what the
part does not have. Consequences:

- A marking can carry a different form for the score and for a part (text, placement, or shown in only
  one). By default there is one form, taken from the part.
- `markings` verification of a part's music compares against the part book; `notes` verification compares
  against the score.
- Part-only cues come in as proposals. Following the part book's line and page breaks is a later option.
- To check early: how far MuseScore lets a linked part differ from the score, and whether the exporter can
  write the parts into the file reliably.

**Fallback: part books with no score** (GB, Paxman and similar, until you have a good score scan). The
current `combine` and alignment code is ported into `proof` as a second way to make the baseline: it
aligns several part readings into bars and parts, then the same steps 4-8 follow. It is cross-source
work, so it belongs here and not in `ocr`. Its editorial passes become `proof normalise` proposals; its
uncertain alignments become annotations. If a score is scanned later, it becomes the primary source and
existing corrections are carried over by bar matching.

## 4. Data tiers

| Tier | Where | Contents |
|---|---|---|
| Kept | `My Drive/Music/Projects/<project>/` | `project.json`, source packages, the readings in use, `ledger.jsonl`, latest export |
| Disposable | `<repo>/ttl=30d/<project>/` | detections, prepared images, crops, renders, logs, replaced pages, unused readings, compacted entries |
| Short-lived | `<repo>/ttl=7d/<project>/` | raw sheets still awaiting a complete source package |

Plain files only (no database), so Drive sync is safe. Readings are kept because newer code will not
reproduce them. Nothing in a kept folder may depend on a file in a `ttl=` folder.

For scale: `work/` is 6.3 GB and `output/` 23 GB today; a kept project should be tens of MB.

## 5. Repository

One repository with both histories preserved:

```
Scriptorium/
  core/        package formats, ids, hashing, score model, ledger
  scan/        today's mimp + Swift helpers
  ocr/         engines, text, fixes, per-page assembly
  proof/       attach, tools, review page, export, fallback part alignment
  engines/     engine venvs (today omr-eval/)
  skills/      music-importer skill (from claude-skills)
  evals/       scorecards, comparison scripts, experiments
  docs/  tests/
```

`scan`, `ocr` and `proof` import `core`, never each other; a test enforces it. One command with
subcommands (`scriptorium scan | read | proof | export`).

## 6. Order of work

Each step leaves a usable tool.

0. Commit or drop the uncommitted review-library work and workflow files; settle the two feature worktrees.
1. Merge the repositories; move code, no behaviour change.
2. `core`: source package. `scan` writes it, `ocr` reads it. Compare OCR results with today's on a few pieces.
3. Reading package with `layout.json` and per-page readings; later stages stop rewriting earlier files.
4. Ledger, replay, verification tiers and export for **a single voice** (Cantilena). Port the review page.
   Includes redoing one page and carrying it through all three steps.
5. **Score + parts** (Goldfaden): attach references, compare and ask, part export.
6. Import existing corrected work (Goldfaden, Bach suites) by diffing final scores against their
   baselines. Acceptance test: replay reproduces today's final scores bar for bar.
7. Port the part-books-only fallback, checked against today's scorecards.

Biggest risks: round-trip fidelity through MuseScore (step 4) and part export from the score matching what
players expect (step 5). Try both on a real piece before building around them.
