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


_VERSIONS = re.compile(r"\s*(?=\bVersion [A-F]\b|\((?:Arr|arr)\.)")


def split_credit(title: str, composer: str) -> tuple:
    """A table-of-contents entry of several lines leaves the arranger credits in the title ("Deck the Halls Version A
    (Arr. X)" + "Version B (Arr. Y)"): the title ends where they begin, the rest belongs to the composer line."""
    head, *rest = _VERSIONS.split(title, maxsplit=1)
    if not rest or not head.strip():
        return title, composer
    return head.strip(), " ".join(x for x in (rest[0].strip(), composer.strip()) if x)


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
        title, composer = clean_title(e["title"]), e["composer"]
        if composer.startswith("Komponist:") or composer.isdigit() or composer.lower() == "number":
            composer = ""                                              # a column heading or a page number
        title, composer = split_credit(title, composer)
        title = override.get("title") or title
        composer = override.get("composer", composer)
        for book in spec.books:
            pdf = table["pdfs"][book.id].get(str(e["num"]))
            if pdf is None:                  # split could not find this piece's heading in this book
                continue
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
