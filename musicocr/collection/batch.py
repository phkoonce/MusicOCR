"""Write the batch spec of a collection: one part per piece and part-book.

Reads the ``pieces.json`` that ``split`` wrote and produces ``batches/<name>.toml``, which
``musicocr batch`` (``scripts/batch_run.py``) then runs part by part.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List

from musicocr.assemble.alignment.systems import pdf_page_count
from musicocr.collection.spec import CollectionSpec


def clean_title(title: str) -> str:
    """A table-of-contents title as OCR leaves it: re-join hyphenated words, drop trailing dots/spaces."""
    title = re.sub(r"(\w)- (\w)", r"\1\2", title)
    return re.sub(r"[\s.]+$", "", title).replace('"', "'")


def _value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return '"' + str(v).replace('"', "'") + '"'


def batch_toml(spec: CollectionSpec, table: Dict[str, Any],
               page_count: Callable[[str], int] = pdf_page_count) -> str:
    """The batch spec text for ``table`` (the parsed ``pieces.json``)."""
    out: List[str] = [f"title = {_value(spec.title)}"]
    if spec.key_prefix:
        out.append(f"key_prefix = {_value(spec.key_prefix)}")
    if not spec.bar_numbers:
        out.append("bar_numbers = false")
    out.append("")
    for e in table["entries"]:
        override = spec.overrides.get(e["num"], {})
        title = override.get("title") or clean_title(e["title"])
        composer = override.get("composer", e["composer"])
        if composer.startswith("Komponist:") or composer.isdigit():     # a column heading or a page number
            composer = ""
        for book in spec.books:
            pdf = table["pdfs"][book.id][str(e["num"])]
            num = e["num"]
            label = f"{num}. {title} - {spec.part_name} {book.id}"
            out += ["[[parts]]", f"id = {_value(spec.part_id(num, book))}", f"label = {_value(label)}"]
            if spec.instrument:
                out.append(f"instrument = {_value(spec.instrument)}")
            out += [f"title = {_value(title)}", f"composer = {_value(composer)}",
                    f"source_pdf = {_value(pdf)}", "first_page = 1", f"last_page = {page_count(pdf)}"]
            out += [f"{k} = {_value(v)}" for k, v in book.options.items()]
            out.append("")
    return "\n".join(out)


def write_batch(spec: CollectionSpec) -> int:
    """Write ``batches/<name>.toml``; returns the number of pieces."""
    table = json.loads(spec.pieces_json.read_text())
    spec.batch_path.parent.mkdir(parents=True, exist_ok=True)
    spec.batch_path.write_text(batch_toml(spec, table))
    return len(table["entries"])
