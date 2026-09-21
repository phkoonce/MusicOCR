"""Load and validate config.toml."""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # Python 3.9 / 3.10
    import tomli as tomllib  # type: ignore

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.toml"

VALID_FORMATS = {"musicxml", "mscz", "midi", "qa-pdf"}
VALID_ENGINES = {"homr", "audiveris"}


class ConfigError(RuntimeError):
    pass


@dataclass
class Config:
    audiveris: str
    homr_python: str
    musescore: str
    pdfinfo: str
    pdftoppm: str
    omr_engine: str
    omr_profile: str
    omr_timeout: int
    omr_page_timeout: int
    omr_per_page: bool
    omr_merge_pages: bool
    homr_fix_multirest: bool
    profiles: dict[str, list[str]]
    correct_enabled: bool
    normalize_enabled: bool
    preprocess: dict
    formats: list[str]
    qa_dpi: int
    path: Path
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    def constants_for_profile(self, name: str | None = None) -> list[str]:
        name = name or self.omr_profile
        if name not in self.profiles:
            raise ConfigError(
                f"unknown omr profile {name!r}; known: {sorted(self.profiles)}"
            )
        return list(self.profiles[name])

    def resolve_tool(self, name: str) -> str | None:
        """Return an absolute path to a configured tool, or None if not found."""
        value = getattr(self, name)
        p = Path(value).expanduser()
        if p.is_absolute():
            return str(p) if p.exists() else None
        return shutil.which(value)


def load_config(path: str | Path | None = None) -> Config:
    cfg_path = Path(path).expanduser() if path else DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        raise ConfigError(f"config file not found: {cfg_path}")

    with cfg_path.open("rb") as fh:
        data = tomllib.load(fh)

    tools = data.get("tools", {})
    omr = data.get("omr", {})
    profiles_raw = omr.get("profiles", {}) or {}
    profiles = {
        name: list(spec.get("constants", []) or [])
        for name, spec in profiles_raw.items()
    }
    if "default" not in profiles:
        profiles["default"] = []

    output = data.get("output", {})
    formats = list(output.get("formats", ["musicxml", "mscz", "midi", "qa-pdf"]))
    bad = set(formats) - VALID_FORMATS
    if bad:
        raise ConfigError(f"invalid output.formats entries: {sorted(bad)}")

    engine = omr.get("engine", "homr")
    if engine not in VALID_ENGINES:
        raise ConfigError(f"invalid [omr] engine {engine!r}; known: {sorted(VALID_ENGINES)}")

    default_homr_python = REPO_ROOT / "omr-eval" / "homr" / ".venv" / "bin" / "python"

    return Config(
        audiveris=tools.get("audiveris", ""),
        homr_python=tools.get("homr_python", str(default_homr_python)),
        musescore=tools.get("musescore", ""),
        pdfinfo=tools.get("pdfinfo", "pdfinfo"),
        pdftoppm=tools.get("pdftoppm", "pdftoppm"),
        omr_engine=engine,
        omr_profile=omr.get("profile", "default"),
        omr_timeout=int(omr.get("timeout_seconds", 1800)),
        omr_page_timeout=int(omr.get("page_timeout_seconds", 900)),
        omr_per_page=bool(omr.get("per_page", True)),
        omr_merge_pages=bool(omr.get("merge_pages", True)),
        homr_fix_multirest=bool(omr.get("homr", {}).get("fix_multirest", True)),
        profiles=profiles,
        correct_enabled=bool(
            data.get("stages", {}).get("correct", {}).get("enabled", False)
        ),
        normalize_enabled=bool(
            data.get("stages", {}).get("normalize", {}).get("enabled", True)
        ),
        preprocess=dict(data.get("preprocess", {})),
        formats=formats,
        qa_dpi=int(output.get("qa_dpi", 150)),
        path=cfg_path,
        raw=data,
    )
