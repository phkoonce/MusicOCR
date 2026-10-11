"""Typed container for everything stages hand to one another.

One dataclass field per artifact. Persistence (``musicocr.state``) is derived
from the field type annotations, so adding an artifact is a single edit here.
Mark a field ``field(metadata={"persist": False})`` to keep it out of the
state file.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


@dataclass
class Artifacts:
    # ingest
    source_pdf: Optional[Path] = None
    page_count: Optional[int] = None
    prepped_pages: Dict[int, Path] = field(default_factory=dict)
    preprocess_summary: str = field(default="", metadata={"persist": False})

    # omr
    omr_log: Optional[Path] = None
    omr_project: Optional[Path] = None
    mxl_files: List[Path] = field(default_factory=list)
    mxl_by_page: Dict[int, List[Path]] = field(default_factory=dict)
    omr_failed_pages: List[int] = field(default_factory=list)

    # extract / normalize / transpose
    musicxml: Optional[Path] = None
    musicxml_pages: List[Path] = field(default_factory=list)
    merge_skipped: List[str] = field(default_factory=list)
    extract_merged: bool = True
    transpose_stamps: Dict[str, int] = field(default_factory=dict)

    # validate
    validation: Dict[str, Any] = field(default_factory=dict)

    # convert / qa
    outputs: Dict[str, Union[Path, List[Path]]] = field(default_factory=dict)
    render_pdf: Optional[Path] = None
    render_pdfs: List[Path] = field(default_factory=list)
    qa_pages: Dict[str, List[str]] = field(default_factory=dict)
