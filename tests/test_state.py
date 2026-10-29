"""Round-trip tests for Artifacts <-> .musicocr-state.json."""
from __future__ import annotations

import dataclasses
import json
import typing
from pathlib import Path

from musicocr.artifacts import Artifacts
from musicocr.pipeline import PipelineContext
from musicocr.state import (STATE_FILE, artifacts_from_json, artifacts_to_json,
                            load_state, restore_missing, save_state)


def _full() -> Artifacts:
    return Artifacts(
        source_pdf=Path("/w/source.pdf"),
        page_count=3,
        prepped_pages={1: Path("/w/p1.png"), 2: Path("/w/p2.png")},
        omr_log=Path("/w/omr.log"),
        omr_project=Path("/w/book.omr"),
        mxl_files=[Path("/w/a.mxl"), Path("/w/b.mxl")],
        mxl_by_page={0: [Path("/w/a.mxl")], 2: [Path("/w/b.mxl"), Path("/w/c.mxl")]},
        omr_failed_pages=[2, 5],
        musicxml=Path("/w/book.musicxml"),
        musicxml_pages=[Path("/w/book.1.musicxml")],
        merge_skipped=["/w/skip.mxl"],
        extract_merged=False,
        transpose_stamps={"/w/book.musicxml": 123456789},
        retouch_stamps={"/w/book.musicxml": 987654321},
        validation={"findings": [{"sev": "warn"}], "counts": {"warn": 1}},
        outputs={"pdf": Path("/w/book.pdf"), "mxl": [Path("/w/b.1.mxl"), Path("/w/b.2.mxl")]},
        render_pdf=Path("/w/render.pdf"),
        render_pdfs=[Path("/w/render.pdf")],
        qa_pages={"src": ["/w/s1.png"], "omr": ["/w/o1.png"]},
    )


def test_every_persisted_field_is_exercised():
    art = _full()
    for f in dataclasses.fields(Artifacts):
        if f.metadata.get("persist", True):
            assert getattr(art, f.name) != Artifacts().__dict__[f.name] or f.name == "extract_merged", f.name


def test_round_trip_all_types(tmp_path):
    art = _full()
    save_state(tmp_path, art)
    back = load_state(tmp_path)
    for f in dataclasses.fields(Artifacts):
        if f.metadata.get("persist", True):
            assert getattr(back, f.name) == getattr(art, f.name), f.name
    assert back.preprocess_summary == ""          # not persisted


def test_types_restored_exactly(tmp_path):
    save_state(tmp_path, _full())
    b = load_state(tmp_path)
    assert isinstance(b.source_pdf, Path)
    assert all(isinstance(k, int) for k in b.prepped_pages)
    assert all(isinstance(p, Path) for p in b.mxl_by_page[2])
    assert isinstance(b.outputs["pdf"], Path) and isinstance(b.outputs["mxl"], list)


def test_on_disk_format_is_backward_compatible(tmp_path):
    save_state(tmp_path, _full())
    raw = json.loads((tmp_path / STATE_FILE).read_text())
    assert raw["source_pdf"] == "/w/source.pdf"
    assert raw["mxl_by_page"] == {"0": ["/w/a.mxl"], "2": ["/w/b.mxl", "/w/c.mxl"]}
    assert raw["prepped_pages"] == {"1": "/w/p1.png", "2": "/w/p2.png"}
    assert raw["outputs"]["mxl"] == ["/w/b.1.mxl", "/w/b.2.mxl"]
    assert "preprocess_summary" not in raw


def test_loads_old_state_file_with_missing_and_unknown_keys(tmp_path):
    (tmp_path / STATE_FILE).write_text(json.dumps(
        {"source_pdf": "/x.pdf", "page_count": 2, "mystery": 1}))
    a = load_state(tmp_path)
    assert a.source_pdf == Path("/x.pdf") and a.page_count == 2
    assert a.extract_merged is True and a.mxl_files == []


def test_none_fields_omitted_and_missing_file_gives_defaults(tmp_path):
    assert load_state(tmp_path) == Artifacts()
    save_state(tmp_path, Artifacts())
    assert "source_pdf" not in json.loads((tmp_path / STATE_FILE).read_text())


def test_corrupt_state_file_gives_defaults(tmp_path):
    (tmp_path / STATE_FILE).write_text("{not json")
    assert load_state(tmp_path) == Artifacts()


def test_unserialisable_value_raises(tmp_path):
    import pytest
    with pytest.raises(TypeError):
        save_state(tmp_path, Artifacts(validation={"x": object()}))


def test_restore_missing_keeps_existing_values():
    cur = Artifacts(musicxml=Path("/new.xml"))
    prior = Artifacts(musicxml=Path("/old.xml"), page_count=4, extract_merged=False)
    n = restore_missing(cur, prior)
    assert cur.musicxml == Path("/new.xml")
    assert cur.page_count == 4 and cur.extract_merged is False
    assert n == 2


def test_artifacts_json_helpers_inverse():
    art = _full()
    assert artifacts_from_json(artifacts_to_json(art)) == dataclasses.replace(art)
