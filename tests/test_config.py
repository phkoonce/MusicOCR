import argparse

import pytest

from musicocr.cli import apply_run_overrides
from musicocr.config import ConfigError, load_config
from musicocr.preprocess import PreprocessOpts


def _write(tmp_path, text):
    p = tmp_path / "c.toml"
    p.write_text(text)
    return p


def _args(**kw):
    base = dict(engine=None, profile=None, merge=None, preprocess=None, deskew=None,
                crop=None, transpose=None, transpose_instrument=None,
                transpose_semitones=None)
    base.update(kw)
    return argparse.Namespace(**base)


def test_repo_config_loads():
    cfg = load_config()
    assert cfg.omr.engine == "homr"
    assert cfg.omr.timeout == 1800 and cfg.omr.page_timeout == 900
    assert cfg.omr.per_page and cfg.omr.merge_pages and cfg.omr.homr.fix_multirest
    assert {"default", "dense-orchestral", "clean-typeset"} <= set(cfg.omr.profiles)
    assert cfg.preprocess.enabled and cfg.preprocess.dpi == 350 and cfg.preprocess.deskew
    assert cfg.stages.normalize_enabled and not cfg.stages.correct_enabled
    assert not cfg.stages.transpose.enabled
    assert cfg.output.formats == ["musicxml", "mscz", "midi", "qa-pdf"]
    assert cfg.output.qa_dpi == 150
    assert cfg.tools.pdfinfo == "pdfinfo"


def test_defaults_when_sections_absent(tmp_path):
    cfg = load_config(_write(tmp_path, ""))
    assert cfg.omr.engine == "homr" and cfg.omr.profiles == {"default": []}
    assert cfg.preprocess == PreprocessOpts()
    assert cfg.stages.normalize_enabled and not cfg.stages.correct_enabled
    assert cfg.stages.transpose.semitones is None and cfg.stages.transpose.instrument == ""
    assert cfg.output.qa_dpi == 150 and "qa-pdf" in cfg.output.formats
    assert cfg.tools.homr_python.endswith("homr/.venv/bin/python")
    assert cfg.constants_for_profile() == []


def test_profiles_and_unknown_profile(tmp_path):
    cfg = load_config(_write(tmp_path, '[omr.profiles.x]\nconstants = ["a.b=1"]\n'))
    assert cfg.constants_for_profile("x") == ["a.b=1"]
    assert "default" in cfg.omr.profiles
    with pytest.raises(ConfigError, match="unknown omr profile"):
        cfg.constants_for_profile("nope")


@pytest.mark.parametrize("text,msg", [
    ('[omr]\nengine = "bogus"\n', "invalid \\[omr\\] engine"),
    ('[output]\nformats = ["pdf"]\n', "invalid output.formats"),
    ('[stages.transpose]\nsemitones = -2\ninstrument = "horn in f"\n', "either semitones or instrument"),
    ('[stages.transpose]\nclef = "alto"\n', "clef must be"),
])
def test_validation_errors(tmp_path, text, msg):
    with pytest.raises(ConfigError, match=msg):
        load_config(_write(tmp_path, text))


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")


def test_overrides_do_not_mutate_and_apply():
    cfg = load_config()
    out = apply_run_overrides(cfg, _args(engine="audiveris", profile="clean-typeset",
                                         merge=False, preprocess=False, crop=True))
    assert (out.omr.engine, out.omr.profile, out.omr.merge_pages) == \
        ("audiveris", "clean-typeset", False)
    assert out.preprocess.crop_margins and not out.preprocess.enabled
    assert cfg.omr.engine == "homr" and cfg.preprocess.enabled     # original untouched


def test_deskew_crop_enable_preprocess(tmp_path, capsys):
    cfg = load_config(_write(tmp_path, ""))
    assert not cfg.preprocess.enabled
    out = apply_run_overrides(cfg, _args(deskew=True))
    assert out.preprocess.enabled and out.preprocess.deskew
    assert "turn on" in capsys.readouterr().out


def test_transpose_overrides():
    cfg = load_config()
    out = apply_run_overrides(cfg, _args(transpose_instrument="horn in f"))
    t = out.stages.transpose
    assert t.enabled and t.instrument == "horn in f" and t.semitones is None
    out = apply_run_overrides(out, _args(transpose_semitones=-7))
    t = out.stages.transpose
    assert t.enabled and t.semitones == -7 and t.instrument == ""
    assert not apply_run_overrides(out, _args(transpose=False)).stages.transpose.enabled
    assert not cfg.stages.transpose.enabled
