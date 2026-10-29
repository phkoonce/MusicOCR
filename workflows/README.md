# Workflows

Project-specific material for the generic `musicocr` package: the data and notes of one particular
project. Nothing in `musicocr/` imports from here, and a workflow holds no logic -- anything reusable
belongs in the package (the horn-quartet scripts became `musicocr collection`).

| Directory | What it is |
|---|---|
| `gb/` | The four-book horn-quartet collection (88 pieces sharing pages). Only data lives here: `collection.toml` (the books, table-of-contents corrections) and `summary.md` (the last full combine table). The work is done by `musicocr collection split / batch / combine`. |

Generic helpers that apply to any score stay in `scripts/` (`batch_run.py`, `combine_scenes.py`,
`compare_marks.py`, `experiment.py`, `make_sample.py`, `setup.sh`). Batch progress documents are written to
the git-ignored `batches/` directory next to the specs.
