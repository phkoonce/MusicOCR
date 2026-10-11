"""Tests for musicocr.cli: parser, run overrides/exit codes, transpose, instruments."""
from __future__ import annotations

from xml.etree import ElementTree as ET

import pytest

from musicocr import cli
from musicocr.config import load_config
from musicocr.errors import StageResult
from musicocr.pipeline import STAGE_ORDER

_HORN = """<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="3.1">
  <part-list><score-part id="P1"><part-name>Horn in F</part-name></score-part></part-list>
  <part id="P1"><measure number="1">
    <attributes><divisions>1</divisions><key><fifths>0</fifths></key>
      <clef><sign>G</sign><line>2</line></clef></attributes>
    <note><pitch><step>C</step><octave>5</octave></pitch><duration>4</duration><type>whole</type></note>
  </measure></part>
</score-partwise>
"""


def _write(tmp_path, text=_HORN, name="horn.musicxml"):
    p = tmp_path / name
    p.write_text(text)
    return p


def _pitch(path):
    n = ET.parse(path).getroot().find(".//note/pitch")
    return n.findtext("step"), n.findtext("alter"), n.findtext("octave")


# ---- parser ----------------------------------------------------------------

def test_subcommand_required():
    with pytest.raises(SystemExit) as e:
        cli.build_parser().parse_args([])
    assert e.value.code == 2


def test_run_defaults():
    a = cli.build_parser().parse_args(["run", "in.pdf"])
    assert a.cmd == "run" and a.func is cli.cmd_run
    assert a.input == "in.pdf"
    for flag in ("merge", "preprocess", "deskew", "crop", "transpose"):
        assert getattr(a, flag) is None
    assert a.force is False and a.keep_going is False
    assert a.pages is None and a.engine is None
    assert a.from_stage is None and a.to_stage is None
    assert a.constant is None and a.transpose_semitones is None


def test_run_full_flags():
    a = cli.build_parser().parse_args([
        "--config", "c.toml", "run", "in.pdf", "--output", "o", "--pages", "1,4-5",
        "--engine", "audiveris", "--profile", "p", "--constant", "a=1", "--constant", "b=2",
        "--force", "--no-merge", "--preprocess", "--no-deskew", "--crop", "--no-transpose",
        "--transpose-semitones", "-7", "--from", "omr", "--to", "validate", "--keep-going"])
    assert a.config == "c.toml" and a.output == "o"
    assert a.pages == "1,4-5" and a.engine == "audiveris" and a.profile == "p"
    assert a.constant == ["a=1", "b=2"]
    assert a.force and a.keep_going
    assert a.merge is False and a.preprocess is True
    assert a.deskew is False and a.crop is True and a.transpose is False
    assert a.transpose_semitones == -7
    assert (a.from_stage, a.to_stage) == ("omr", "validate")


@pytest.mark.parametrize("argv", [
    ["run", "in.pdf", "--engine", "bogus"],
    ["run", "in.pdf", "--from", "nope"],
    ["run", "in.pdf", "--to", "nope"],
    ["run", "in.pdf", "--transpose-semitones", "x"],
    ["run"],
    ["transpose", "f", "--semitones", "1", "--instrument", "horn"],
    ["transpose", "f", "--clef", "alto"],
])
def test_parser_rejects_bad_args(argv):
    with pytest.raises(SystemExit) as e:
        cli.build_parser().parse_args(argv)
    assert e.value.code == 2


def test_every_stage_name_accepted_for_from():
    for s in STAGE_ORDER:
        assert cli.build_parser().parse_args(["run", "x", "--from", s]).from_stage == s


def test_transpose_parser():
    a = cli.build_parser().parse_args(["transpose", "f.xml", "-o", "o.xml", "--instrument",
                                       "tuba", "--clef", "bass"])
    assert (a.input, a.output, a.instrument, a.clef) == ("f.xml", "o.xml", "tuba", "bass")
    assert a.func is cli.cmd_transpose


# ---- apply_run_overrides ---------------------------------------------------

def _args(argv):
    return cli.build_parser().parse_args(["run", "in.pdf", *argv])


def test_overrides_no_flags_is_identity():
    cfg = load_config()
    assert cli.apply_run_overrides(cfg, _args([])) == cfg


def test_overrides_merge_both_directions():
    cfg = load_config()
    assert cli.apply_run_overrides(cfg, _args(["--no-merge"])).omr.merge_pages is False
    assert cli.apply_run_overrides(cfg, _args(["--merge"])).omr.merge_pages is True


def test_overrides_preprocess_explicit_wins_over_implied(capsys):
    cfg = load_config()
    out = cli.apply_run_overrides(cfg, _args(["--no-preprocess", "--deskew", "--crop"]))
    assert out.preprocess.enabled is False and out.preprocess.deskew is True
    assert "note:" not in capsys.readouterr().out


def test_overrides_crop_enables_preprocess_with_note(capsys):
    from dataclasses import replace
    cfg = load_config()
    cfg = replace(cfg, preprocess=replace(cfg.preprocess, enabled=False, crop_margins=False))
    out = cli.apply_run_overrides(cfg, _args(["--crop"]))
    assert out.preprocess.enabled is True and out.preprocess.crop_margins is True
    assert "turn on the [preprocess]" in capsys.readouterr().out


def test_overrides_transpose_instrument_clears_semitones():
    cfg = load_config()
    out = cli.apply_run_overrides(cfg, _args(["--transpose-semitones", "3"]))
    assert (out.stages.transpose.enabled, out.stages.transpose.semitones) == (True, 3)
    out = cli.apply_run_overrides(out, _args(["--transpose-instrument", "tuba"]))
    t = out.stages.transpose
    assert (t.enabled, t.instrument, t.semitones) == (True, "tuba", None)


def test_overrides_does_not_mutate_original():
    cfg = load_config()
    before = (cfg.omr.engine, cfg.omr.extra_constants[:], cfg.stages.transpose.enabled)
    cli.apply_run_overrides(cfg, _args(["--engine", "audiveris", "--constant", "a=1",
                                        "--transpose-semitones", "2"]))
    assert (cfg.omr.engine, cfg.omr.extra_constants, cfg.stages.transpose.enabled) == before


def test_overrides_constants_and_zero_semitones():
    cfg = load_config()
    out = cli.apply_run_overrides(cfg, _args(["--constant", "a=1", "--transpose-semitones", "0"]))
    assert out.omr.extra_constants == ["a=1"]
    assert out.stages.transpose.semitones == 0 and out.stages.transpose.enabled


# ---- cmd_run ---------------------------------------------------------------

@pytest.fixture
def fake_pipeline(monkeypatch):
    seen = {}

    def fake_run(ctx, from_stage, to_stage, keep_going):
        seen.update(ctx=ctx, from_stage=from_stage, to_stage=to_stage, keep_going=keep_going)
        return seen["results"]

    seen["results"] = [StageResult("ingest", "ok")]
    monkeypatch.setattr(cli, "run_pipeline", fake_run)
    monkeypatch.setattr(cli, "write_reports",
                        lambda ctx, results: (ctx.workdir / "r.json", ctx.workdir / "r.md"))
    return seen


def test_run_builds_context_and_returns_zero(tmp_path, fake_pipeline, capsys):
    rc = cli.main(["run", str(tmp_path / "My Song.pdf"), "--output", str(tmp_path / "out"),
                   "--pages", "1,3-4", "--force", "--keep-going", "--from", "omr",
                   "--to", "extract", "--no-merge", "--engine", "audiveris"])
    assert rc == 0
    ctx = fake_pipeline["ctx"]
    assert ctx.book_name == "My_Song"
    assert ctx.workdir == (tmp_path / "out" / "My_Song").resolve()
    assert ctx.pages == "1 3-4" and ctx.force is True
    assert ctx.config.omr.engine == "audiveris" and ctx.config.omr.merge_pages is False
    assert (fake_pipeline["from_stage"], fake_pipeline["to_stage"],
            fake_pipeline["keep_going"]) == ("omr", "extract", True)
    out = capsys.readouterr().out
    assert "book: My_Song" in out and out.rstrip().endswith("OK")


def test_run_exit_codes(tmp_path, fake_pipeline, capsys):
    argv = ["run", str(tmp_path / "a.pdf"), "--output", str(tmp_path)]
    fake_pipeline["results"] = [StageResult("ingest", "ok"), StageResult("omr", "failed", "x")]
    assert cli.main(argv) == 1
    assert "FAILED at: omr" in capsys.readouterr().err
    fake_pipeline["results"] = [StageResult("correct", "paused")]
    assert cli.main(argv) == 3
    assert "PAUSED at: correct" in capsys.readouterr().out
    # failure outranks pause
    fake_pipeline["results"] = [StageResult("correct", "paused"), StageResult("omr", "failed")]
    assert cli.main(argv) == 1


def test_run_report_failure_is_not_fatal(tmp_path, fake_pipeline, monkeypatch, capsys):
    def boom(ctx, results):
        raise RuntimeError("disk full")
    monkeypatch.setattr(cli, "write_reports", boom)
    assert cli.main(["run", str(tmp_path / "a.pdf"), "--output", str(tmp_path)]) == 0
    assert "could not write report: disk full" in capsys.readouterr().err


def test_run_missing_config_returns_2(tmp_path, capsys):
    rc = cli.main(["--config", str(tmp_path / "none.toml"), "run", "a.pdf"])
    assert rc == 2 and "config error" in capsys.readouterr().err


def test_run_unknown_profile_returns_2(tmp_path, capsys):
    rc = cli.main(["run", str(tmp_path / "a.pdf"), "--profile", "no-such-profile"])
    assert rc == 2 and "unknown omr profile" in capsys.readouterr().err


# ---- transpose / instruments ----------------------------------------------

def test_transpose_semitones_default_output(tmp_path, capsys):
    src = _write(tmp_path)
    assert cli.main(["transpose", str(src), "--semitones", "-7"]) == 0
    dst = tmp_path / "horn.transposed.musicxml"
    assert dst.exists() and f"wrote {dst}" in capsys.readouterr().out
    assert _pitch(dst) == ("F", None, "4")        # C5 - perfect fifth
    assert ET.parse(dst).getroot().findtext(".//key/fifths") == "-1"
    assert _pitch(src) == ("C", None, "5")        # source untouched


def test_transpose_by_part_name_with_output(tmp_path):
    src = _write(tmp_path)
    out = tmp_path / "sub.xml"
    assert cli.main(["transpose", str(src), "-o", str(out)]) == 0
    assert _pitch(out) == ("F", None, "4")


def test_transpose_by_instrument(tmp_path):
    src = _write(tmp_path, _HORN.replace("Horn in F", "Part 1"))
    out = tmp_path / "o.xml"
    assert cli.main(["transpose", str(src), "-o", str(out), "--instrument", "trumpet"]) == 0
    assert _pitch(out) == ("B", "-1", "4")        # C5 down a major second


def test_transpose_unknown_instrument_exit_2(tmp_path, capsys):
    src = _write(tmp_path)
    rc = cli.main(["transpose", str(src), "--instrument", "kazoo"])
    assert rc == 2 and "transpose error: unknown instrument" in capsys.readouterr().err
    assert not (tmp_path / "horn.transposed.musicxml").exists()


def test_transpose_clef_dependent_instrument_needs_clef(tmp_path, capsys):
    src = _write(tmp_path)
    rc = cli.main(["transpose", str(src), "--instrument", "tuba", "--clef", "treble"])
    assert rc == 2 and "transpose error" in capsys.readouterr().err


def test_transpose_bad_xml_and_missing_file_exit_2(tmp_path, capsys):
    bad = _write(tmp_path, "<score-partwise", "bad.musicxml")
    assert cli.main(["transpose", str(bad)]) == 2
    assert cli.main(["transpose", str(tmp_path / "missing.musicxml")]) == 2
    assert cli.main(["transpose", str(_write(tmp_path, "<score-timewise/>", "tw.xml"))]) == 2
    assert capsys.readouterr().err.count("transpose error") == 3


def test_instruments_lists_table(capsys):
    from musicocr.instruments import TRANSPOSITIONS
    assert cli.main(["instruments"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == len(TRANSPOSITIONS)
    names = [ln.split("  ")[0].strip() for ln in lines]
    assert names == sorted(names)
    horn = next(ln for ln in lines if ln.startswith("french horn"))
    assert "-7" in horn and "horn in f" in horn
    tuba = next(ln for ln in lines if ln.startswith("tuba "))
    assert "bass +0" in tuba
