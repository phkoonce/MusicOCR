from dataclasses import replace
from pathlib import Path

import pytest

from musicocr.config import Config, ConfigError, FixesConfig, load_config
from musicocr.fixes import (ALL_FIXES, FIXES_BY_NAME, Fix, FixContext, fix_enabled, run_fixes)
from musicocr.fixes.base import check_order


def _cfg(**over) -> Config:
    return Config(path=Path("x.toml"), fixes=FixesConfig(dict(over)))


def _fix(name="f", n=1, **kw):
    calls = []

    def apply(ctx):
        calls.append(ctx.label)
        return n

    return Fix(name, apply, summary="s", **kw), calls


def test_enabled_precedence_override_then_legacy_then_default():
    fix, _ = _fix(legacy=lambda c: False, default=True)
    assert not fix_enabled(fix, _cfg())                    # legacy says off
    assert fix_enabled(fix, _cfg(f=True))                  # [fixes] wins over legacy
    plain, _ = _fix("g", default=False)
    assert not fix_enabled(plain, _cfg()) and fix_enabled(plain, _cfg(g=True))


def test_run_fixes_logs_only_changes_and_returns_counts():
    log = []
    fix, calls = _fix(message="did {n} thing(s)", n=2)
    quiet, _ = _fix("q", n=0, message="never")
    res = run_fixes([fix, quiet], FixContext([Path("a.xml")], _cfg(), log.append, "page 1"))
    assert res == {"f": 2} and calls == ["page 1"]
    assert log == ["  page 1: did 2 thing(s)"]


def test_disabled_fix_does_not_run():
    fix, calls = _fix()
    assert run_fixes([fix], FixContext([Path("a.xml")], _cfg(f=False), print)) == {}
    assert calls == []


def test_a_failing_fix_is_logged_and_the_rest_still_run():
    log = []

    def boom(ctx):
        raise ValueError("bad bar")

    ok, calls = _fix("ok")
    res = run_fixes([Fix("boom", boom, summary="s"), ok], FixContext([Path("a.xml")], _cfg(), log.append, "p"))
    assert res == {"ok": 1} and calls == ["p"]
    assert log == ["  p: boom fix failed (ValueError: bad bar)"]


def test_scope_each_runs_per_file_scope_all_once():
    each, each_calls = _fix("e")
    both, all_calls = _fix("a", scope="all")
    run_fixes([each, both], FixContext([Path("1.xml"), Path("2.xml")], _cfg(), lambda s: None))
    assert each_calls == ["1.xml", "2.xml"] and len(all_calls) == 1


def test_when_and_engine_filters():
    only_p1, calls = _fix(when=lambda c: c.page == 1)
    run_fixes([only_p1], FixContext([Path("a.xml")], _cfg(), print, "x", page=2))
    assert calls == []
    run_fixes([only_p1], FixContext([Path("a.xml")], _cfg(), print, "x", page=1))
    assert calls == ["x"]
    other, calls2 = _fix("o", engines=frozenset({"audiveris"}))
    run_fixes([other], FixContext([Path("a.xml")], _cfg(), print))      # default engine is homr
    assert calls2 == []


def test_check_order_rejects_a_broken_dependency_and_duplicates():
    a, _ = _fix("a")
    b, _ = _fix("b", after=("a",))
    check_order([a, b])
    with pytest.raises(ValueError):
        check_order([b, a])
    with pytest.raises(ValueError):
        check_order([a, a])


def test_registry_is_consistent():
    assert len({f.name for f in ALL_FIXES}) == len(ALL_FIXES) == len(FIXES_BY_NAME)
    cfg = Config(path=Path("x.toml"))
    for f in ALL_FIXES:
        assert isinstance(fix_enabled(f, cfg), bool)            # every legacy key resolves on a default config


def test_legacy_copisteria_key_still_switches_its_fix():
    cfg = Config(path=Path("x.toml"))
    omr = replace(cfg.omr, copisteria=replace(cfg.omr.copisteria, fix_ghost_bars=False))
    cfg = replace(cfg, omr=omr)
    assert not fix_enabled(FIXES_BY_NAME["ghost_bars"], cfg)
    assert fix_enabled(FIXES_BY_NAME["ghost_bars"], replace(cfg, fixes=FixesConfig({"ghost_bars": True})))


def test_fixes_table_flattens_engine_groups_and_rejects_typos():
    cfg = FixesConfig.from_dict({"tuplets": False, "copisteria": {"ghost_bars": False}})
    assert cfg.overrides == {"tuplets": False, "ghost_bars": False}
    with pytest.raises(ConfigError, match="unknown fix 'tuplet'"):
        FixesConfig.from_dict({"tuplet": False})
    with pytest.raises(ConfigError):
        FixesConfig.from_dict({"tuplets": "no"})


def test_cli_overrides_force_fixes(tmp_path):
    cfg = FixesConfig().with_overrides(enable=["ghost_bars"], disable=["tuplets"])
    assert cfg.overrides == {"ghost_bars": True, "tuplets": False}
    with pytest.raises(ConfigError):
        FixesConfig().with_overrides(disable=["nope"])


def test_repo_config_loads():
    assert isinstance(load_config().fixes, FixesConfig)
