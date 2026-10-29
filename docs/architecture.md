# Architecture

## Flow

```
PDF ─ ingest ─ omr ─ correct ─ extract ─ normalize ─ retouch ─ transpose ─ validate ─ convert ─ qa ─▶ .musicxml .mscz .mid report
        │        │               │          │
        │        │               │          └ fixes: score level (measure_lengths)
        │        │               └ fixes: per page, any engine (time_signatures, whole_bar_rests, tuplets) then page merge
        │        └ engine adapter (engines/<name>/stage.py) + that engine's fixes, per page
        └ rasterise / deskew / crop once (shared between engines)
```

`pipeline.py` runs an ordered slice of `STAGE_ORDER` (`--from` / `--to`). Stages are modules in `stages/`
with `run(ctx) -> StageResult`; they share state through `ctx.artifacts` (saved after each stage, so a
later `--from` resumes). Downstream stages never learn which engine ran: every engine publishes the same
artifacts (`mxl_files`, `mxl_by_page`, `omr_failed_pages`, ...).

## Packages

| Package | Responsibility | Depends on |
|---|---|---|
| core modules (`cli`, `config`, `pipeline`, `state`, `artifacts`, `report`, `util`) | run a slice of stages; load config; report | `stages`, `engines` registry, `fixes` registry |
| `stages/` | one module per pipeline stage | `engines`, `fixes`, `musicxml`, `scoreops` |
| `engines/` | OMR engine registry + per-engine adapter, fixes and helper scripts | `fixes`, `text`, `musicxml` |
| `fixes/` | the `Fix` abstraction, the registry of all repairs, and the engine-agnostic repairs | `musicxml` |
| `musicxml/` | measure arithmetic, file writing, page merge | stdlib only |
| `scoreops/` | transpose, instruments, retouch, dynamics/hairpin consistency | `musicxml` |
| `text/` | page text and OCR, tempo/expression words | stdlib, engine venvs via subprocess |
| `assemble/` | combine many parts into one score by printed bar number | `musicxml`, `scoreops`, `text` |
| `collection/` | books of many short pieces: split by table of contents, batch spec, combine every piece | `assemble`, `batch` |

Nothing under `musicocr/` imports from `workflows/` or `scripts/`.

## Fixes

A *fix* is one named repair of OMR output (`fixes/base.py`). The registry (`fixes/registry.py`) lists
every one in the order it runs, with its summary, default, older config key and ordering constraints
(`after=`). One runner applies them: it honours the switch, logs only when something changed, and turns
an exception into a log line instead of a failed page.

Switching: `[fixes] name = false` in `config.toml`; `--no-fix NAME` / `--fix NAME` per run;
`musicocr fixes` lists names and states. Precedence: CLI > `[fixes]` > the fix's older config key
(`[omr.copisteria] fix_*`, `[omr.homr] fix_multirest`, `[stages.tuplets]`, `[stages.normalize]`) > default.

To add a repair: write `fix_x(path, log) -> int` in the module it belongs to, add a `Fix(...)` to the right
list in `registry.py`, add a test. Nothing else changes.

## Adding an engine

Add an `Engine(...)` entry in `engines/registry.py`, a package `engines/<name>/` whose `stage.py` exposes
`run(ctx)` (use `engines/raster.py` for the per-page loop if it reads page images), its fixes as `Fix`
entries in `fixes/registry.py`, and a `Tools` field in `config.py` if it has its own interpreter.
