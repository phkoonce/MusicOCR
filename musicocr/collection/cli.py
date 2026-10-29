"""``musicocr collection``: split a collection into pieces, write its batch spec, combine every piece."""
from __future__ import annotations

import sys
from pathlib import Path

from musicocr.config import ConfigError


def add_parser(sub) -> None:
    c = sub.add_parser(
        "collection", help="work on a book of many short pieces: split, batch spec, combine all",
        description="A collection is a set of part-books, each holding the same many short pieces. "
                    "The spec (see musicocr/collection/spec.py) names the books and the table-of-contents pages. "
                    "Typical order: split -> batch -> run scripts/batch_run.py on the written spec -> combine.")
    steps = c.add_subparsers(dest="step", required=True)

    s = steps.add_parser("split", help="cut every book into one PDF per piece (table of contents + OCR)")
    s.add_argument("spec", help="collection spec TOML")
    s.add_argument("--toc-pages", metavar="A-B", help="override the spec's table-of-contents pages")
    s.add_argument("--first-page", type=int, help="override the spec's first music page")
    s.set_defaults(func=cmd_split)

    b = steps.add_parser("batch", help="write the batch spec: one part per piece and book")
    b.add_argument("spec", help="collection spec TOML")
    b.set_defaults(func=cmd_batch)

    m = steps.add_parser("combine", help="combine each piece's parts into one score and tabulate the results")
    m.add_argument("spec", help="collection spec TOML")
    m.add_argument("--pieces", metavar="N[-M]", help="only these piece numbers (default: all)")
    m.add_argument("--redo", action="store_true", help="combine again even if the piece's score exists")
    m.add_argument("--summary", type=Path, help="write the table here (default: summary.md next to the spec "
                                                "when all pieces ran, otherwise printed)")
    m.set_defaults(func=cmd_combine)


def _load(args):
    from musicocr.collection.spec import load_spec
    try:
        return load_spec(args.spec)
    except ConfigError as exc:
        print(f"collection error: {exc}", file=sys.stderr)
        return None


def cmd_split(args) -> int:
    from musicocr.collection.split import split_collection
    from musicocr.collection.spec import _pages

    spec = _load(args)
    if spec is None:
        return 2
    out = split_collection(spec, _pages(args.toc_pages) if args.toc_pages else None, args.first_page)
    print(f"wrote {out}")
    return 0


def cmd_batch(args) -> int:
    from musicocr.collection.batch import write_batch

    spec = _load(args)
    if spec is None:
        return 2
    if not spec.pieces_json.exists():
        print(f"collection error: {spec.pieces_json} missing; run `musicocr collection split` first", file=sys.stderr)
        return 2
    n = write_batch(spec)
    print(f"{n} pieces -> {spec.batch_path}")
    return 0


def cmd_combine(args) -> int:
    from musicocr.collection.combine import combine_all

    spec = _load(args)
    if spec is None:
        return 2
    if not spec.batch_path.exists():
        print(f"collection error: {spec.batch_path} missing; run `musicocr collection batch` first", file=sys.stderr)
        return 2
    combine_all(spec, args.pieces, args.redo, args.summary)
    return 0
