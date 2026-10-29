"""The collection spec: which part-books make up a collection and how its pieces are named.

A TOML file, kept next to the material it describes (``workflows/<collection>/collection.toml``)::

    name = "my-collection"            # batch spec batches/<name>.toml, results output/<name>/
    title = "Pieces for horn quartet"
    key_prefix = "MC_"                # prefix of the batch keys and piece PDF names
    instrument = "horn in f"          # see musicocr/scoreops/instruments.py
    part_name = "Horn"                # parts are labelled "<n>. <title> - Horn <book id>"
    part_id_prefix = "h"              # part ids pNN_h1, pNN_h2, ...
    bar_numbers = false               # the books print no bar numbers (align by rhythm)
    toc_pages = "5-11"                # where the table of contents is, in every book
    first_page = 12                   # first page with music
    input_dir = "input/my-collection" # per-piece PDFs and pieces.json are written here

    [[books]]                         # one per part-book
    id = "1"
    pdf = "~/Music/Book H1.pdf"
    [[books]]
    id = "4"
    pdf = "~/Music/Book H4.pdf"
    old_bass_clef = true              # any further key is copied into that book's batch parts

    [overrides.13]                    # a table-of-contents line the OCR got wrong
    title = "O Täler weit, o Höhen"
    composer = "Felix Mendelssohn Bartholdy"
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from musicocr.config import REPO_ROOT, ConfigError


@dataclass
class Book:
    id: str
    pdf: Path
    options: Dict[str, Any] = field(default_factory=dict)     # extra batch-part keys for this book


@dataclass
class CollectionSpec:
    path: Path
    name: str
    title: str
    books: List[Book]
    key_prefix: str = ""
    instrument: str = ""
    part_name: str = "Part"
    part_id_prefix: str = "b"
    bar_numbers: bool = True
    toc_pages: Tuple[int, int] = (1, 1)
    first_page: int = 1
    input_dir: Path = REPO_ROOT / "input"
    overrides: Dict[int, Dict[str, str]] = field(default_factory=dict)

    @property
    def pieces_json(self) -> Path:
        return self.input_dir / "pieces.json"

    @property
    def batch_path(self) -> Path:
        return REPO_ROOT / "batches" / f"{self.name}.toml"

    @property
    def combined_dir(self) -> Path:
        return REPO_ROOT / "output" / self.name / "_combined"

    def part_id(self, num: int, book: Book) -> str:
        return f"p{num:02d}_{self.part_id_prefix}{book.id}"

    def book_dir(self, book: Book) -> Path:
        return self.input_dir / f"{self.part_id_prefix}{book.id}"

    def book_prefix(self, book: Book) -> str:
        return f"{self.key_prefix}{self.part_id_prefix.upper()}{book.id}"


def _pages(text: str) -> Tuple[int, int]:
    a, _, b = str(text).partition("-")
    return int(a), int(b or a)


def parse_spec(data: Dict[str, Any], path: Path) -> CollectionSpec:
    for key in ("name", "title", "books"):
        if key not in data:
            raise ConfigError(f"{path}: collection spec needs {key!r}")
    books = []
    for b in data["books"]:
        if "id" not in b or "pdf" not in b:
            raise ConfigError(f"{path}: every [[books]] entry needs id and pdf")
        books.append(Book(str(b["id"]), Path(b["pdf"]).expanduser(),
                          {k: v for k, v in b.items() if k not in ("id", "pdf")}))
    spec = CollectionSpec(
        path=path, name=data["name"], title=data["title"], books=books,
        key_prefix=data.get("key_prefix", ""), instrument=data.get("instrument", ""),
        part_name=data.get("part_name", "Part"), part_id_prefix=data.get("part_id_prefix", "b"),
        bar_numbers=bool(data.get("bar_numbers", True)),
        toc_pages=_pages(data.get("toc_pages", "1")), first_page=int(data.get("first_page", 1)),
        overrides={int(k): dict(v) for k, v in (data.get("overrides") or {}).items()})
    if "input_dir" in data:
        spec.input_dir = REPO_ROOT / Path(data["input_dir"]).expanduser()
    return spec


def load_spec(path: Path) -> CollectionSpec:
    path = Path(path).expanduser()
    if not path.exists():
        raise ConfigError(f"collection spec not found: {path}")
    with path.open("rb") as fh:
        return parse_spec(tomllib.load(fh), path)


def piece_range(arg: Optional[str], count: int) -> List[int]:
    """``"3"`` / ``"1-3"`` -> piece numbers; no argument -> 1..count."""
    if not arg:
        return list(range(1, count + 1))
    a, _, b = arg.partition("-")
    return list(range(int(a), int(b or a) + 1))
