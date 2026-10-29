from __future__ import annotations

from pathlib import Path

import pytest

from musicocr import batch
from musicocr.batch import BatchSpecError, Part


def _data():
    return {
        "title": "Demo",
        "source_pdf": "~/x/demo.pdf",
        "key_prefix": "Demo_",
        "parts": [
            {"id": "a", "label": "Alpha", "first_page": 1, "last_page": 3},
            {"id": "b", "first_page": 4, "last_page": 4},
        ],
    }


def _spec(tmp_path: Path):
    return batch.parse_spec(_data(), "demo", root=tmp_path)


def test_parse_spec_defaults_and_paths(tmp_path):
    spec = _spec(tmp_path)
    assert spec.title == "Demo"
    assert spec.source_pdf == Path("~/x/demo.pdf").expanduser()
    assert [spec.key(p) for p in spec.parts] == ["Demo_a", "Demo_b"]
    assert spec.parts[1].label == "b"  # label defaults to id
    assert spec.output_dir == tmp_path / "output/demo"
    assert spec.state_path == tmp_path / "output/demo/_state.json"
    assert spec.doc_path == tmp_path / "docs/demo-progress.md"
    assert spec.input_dir == tmp_path / "input/demo"


def test_run_part_writes_into_batch_output_dir(tmp_path, monkeypatch):
    spec = _spec(tmp_path)
    seen = {}

    def fake_run(cmd, stdout=None, stderr=None):
        seen["cmd"] = cmd
        book = spec.output_dir / "Demo_a"
        book.mkdir(parents=True)
        (book / "Demo_a.musicxml").write_text("<x/>")
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(batch.subprocess, "run", fake_run)
    entry = batch.run_part(spec, spec.parts[0], tmp_path / "Demo_a.pdf")
    assert seen["cmd"][seen["cmd"].index("--output") + 1] == str(spec.output_dir)
    assert entry["status"] == batch.DONE and "output/demo/Demo_a/report.md" in entry["note"]
    assert (spec.output_dir / "Demo_a.run.log").exists()


def test_engines_selects_compare_command(tmp_path, monkeypatch):
    d = _data()
    d["engines"] = ["homr", "copisteria"]
    spec = batch.parse_spec(d, "demo", root=tmp_path)
    assert spec.engines == ("homr", "copisteria")
    seen = {}

    def fake_run(cmd, stdout=None, stderr=None):
        seen["cmd"] = cmd
        book = spec.output_dir / "Demo_a"
        book.mkdir(parents=True)
        (book / "compare.md").write_text("x")
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(batch.subprocess, "run", fake_run)
    assert batch.run_part(spec, spec.parts[0], tmp_path / "a.pdf")["status"] == batch.DONE
    cmd = seen["cmd"]
    assert cmd[1] == "compare" and cmd[cmd.index("--engines") + 1] == "homr,copisteria"


@pytest.mark.parametrize("bad", [["nope"], "homr", [1]])
def test_engines_validated(tmp_path, bad):
    d = _data()
    d["engines"] = bad
    with pytest.raises(BatchSpecError):
        batch.parse_spec(d, "demo", root=tmp_path)


def test_parse_spec_overrides(tmp_path):
    d = _data()
    d["progress_doc"] = "docs/other.md"
    assert batch.parse_spec(d, "demo", root=tmp_path).doc_path == tmp_path / "docs/other.md"


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("source_pdf"),
    lambda d: d.update(parts=[]),
    lambda d: d["parts"][0].update(last_page=0),
    lambda d: d["parts"][0].update(first_page="1"),
    lambda d: d["parts"][1].update(id="a"),
])
def test_parse_spec_rejects_bad_input(tmp_path, mutate):
    d = _data()
    mutate(d)
    with pytest.raises(BatchSpecError):
        batch.parse_spec(d, "demo", root=tmp_path)


def test_load_spec_from_toml(tmp_path):
    f = tmp_path / "demo.toml"
    f.write_text('source_pdf = "s.pdf"\n[[parts]]\nid = "x"\nfirst_page = 1\nlast_page = 2\n')
    spec = batch.load_spec(f, root=tmp_path)
    assert spec.name == "demo" and spec.parts == [Part("x", "x", 1, 2)]


def test_state_roundtrip_and_missing(tmp_path):
    path = tmp_path / "sub/state.json"
    assert batch.load_state(path) == {}
    batch.save_state(path, {"k": {"status": "done", "note": ""}})
    assert batch.load_state(path)["k"]["status"] == "done"


def test_select_parts_skip_only_redo(tmp_path):
    spec = _spec(tmp_path)
    state = {"Demo_a": {"status": batch.DONE}, "Demo_b": {"status": batch.FAILED}}
    assert [p.id for p in batch.select_parts(spec, state)] == ["b"]
    assert [p.id for p in batch.select_parts(spec, state, redo=True)] == ["a", "b"]
    assert [p.id for p in batch.select_parts(spec, state, only=["Demo_a"], redo=True)] == ["a"]
    assert batch.select_parts(spec, state, only=["a"]) == []


def test_render_progress_doc(tmp_path):
    spec = _spec(tmp_path)
    state = {"Demo_a": {"status": "done", "note": "1 min"}}
    text = batch.render_progress_doc(spec, state, "2026-01-01 00:00")
    assert text.startswith("# Demo OMR progress")
    assert "| Alpha (`Demo_a`) | 1-3 | done | 1 min |" in text
    assert "| b (`Demo_b`) | 4-4 | pending |  |" in text


def test_merge_progress_doc_preserves_tail():
    first = batch.merge_progress_doc(None, "BLOCK1\n")
    assert first.startswith(batch.DOC_BEGIN) and "BLOCK1" in first
    edited = first + "\n## Notes\nhand written\n"
    merged = batch.merge_progress_doc(edited, "BLOCK2\n")
    assert "BLOCK2" in merged and "BLOCK1" not in merged
    assert merged.endswith("## Notes\nhand written\n")


def test_run_batch_resumes_with_fakes(tmp_path):
    spec = _spec(tmp_path)
    calls = []

    def splitter(s, part):
        return tmp_path / f"{part.id}.pdf"

    def runner(s, part, pdf):
        calls.append(part.id)
        if part.id == "b":
            return {"status": batch.FAILED, "note": "boom"}
        return {"status": batch.DONE, "note": "ok"}

    batch.run_batch(spec, runner=runner, splitter=splitter)
    assert calls == ["a", "b"]
    batch.run_batch(spec, runner=runner, splitter=splitter)  # only failed b retried
    assert calls == ["a", "b", "b"]
    assert "FAILED" in spec.doc_path.read_text()
    assert batch.load_state(spec.state_path)["Demo_a"]["status"] == "done"


def test_main_reports_bad_spec(tmp_path, capsys):
    assert batch.main(["--batch", str(tmp_path / "missing.toml")]) == 2
    assert "error" in capsys.readouterr().err


def test_run_batch_jobs_runs_every_part_once(tmp_path):
    import threading
    import time
    spec = _spec(tmp_path)
    seen, active, peak, lock = [], [0], [0], threading.Lock()

    def runner(s, part, pdf):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.1)
        with lock:
            active[0] -= 1
            seen.append(part.id)
        return {"status": batch.DONE, "note": "ok"}

    state = batch.run_batch(spec, runner=runner, splitter=lambda s, p: tmp_path / f"{p.id}.pdf", jobs=3)
    assert sorted(seen) == ["a", "b"] and peak[0] == 2
    assert all(v["status"] == "done" for v in state.values())


def test_part_source_pdf_overrides_batch_source(tmp_path):
    d = _data()
    d["parts"][1]["source_pdf"] = "~/x/other.pdf"
    spec = batch.parse_spec(d, "demo", root=tmp_path)
    assert spec.parts[0].source_pdf is None
    assert spec.parts[1].source_pdf == Path("~/x/other.pdf").expanduser()


def test_batch_source_optional_when_every_part_has_one(tmp_path):
    d = _data()
    del d["source_pdf"]
    with pytest.raises(BatchSpecError):
        batch.parse_spec(d, "demo", root=tmp_path)
    for p in d["parts"]:
        p["source_pdf"] = "~/x/book.pdf"
    spec = batch.parse_spec(d, "demo", root=tmp_path)
    assert spec.parts[0].source_pdf == Path("~/x/book.pdf").expanduser()
