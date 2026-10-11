"""Load and validate config.toml."""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # Python 3.9 / 3.10
    import tomli as tomllib  # type: ignore

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.toml"

VALID_FORMATS = {"musicxml", "mscz", "midi", "qa-pdf"}
VALID_ENGINES = {"homr", "audiveris"}


VALID_CLEFS = ("treble", "bass", "c")

DEFAULT_FORMATS = ("musicxml", "mscz", "midi", "qa-pdf")


class ConfigError(RuntimeError):
    pass


@dataclass
class Tools:
    audiveris: str = ""
    homr_python: str = str(REPO_ROOT / "omr-eval" / "homr" / ".venv" / "bin" / "python")
    musescore: str = ""
    pdfinfo: str = "pdfinfo"
    pdftoppm: str = "pdftoppm"

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Tools":
        base = cls()
        return cls(**{k: d.get(k, getattr(base, k)) for k in cls.__dataclass_fields__})


@dataclass
class HomrConfig:
    fix_multirest: bool = True

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "HomrConfig":
        return cls(fix_multirest=bool(d.get("fix_multirest", True)))


@dataclass
class OmrConfig:
    engine: str = "homr"
    profile: str = "default"
    timeout: int = 1800
    page_timeout: int = 900
    per_page: bool = True
    merge_pages: bool = True
    profiles: Dict[str, List[str]] = field(default_factory=lambda: {"default": []})
    homr: HomrConfig = field(default_factory=HomrConfig)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "OmrConfig":
        engine = d.get("engine", "homr")
        if engine not in VALID_ENGINES:
            raise ConfigError(
                f"invalid [omr] engine {engine!r}; known: {sorted(VALID_ENGINES)}"
            )
        profiles = {
            name: list(spec.get("constants", []) or [])
            for name, spec in (d.get("profiles", {}) or {}).items()
        }
        if "default" not in profiles:
            profiles["default"] = []
        return cls(
            engine=engine,
            profile=d.get("profile", "default"),
            timeout=int(d.get("timeout_seconds", 1800)),
            page_timeout=int(d.get("page_timeout_seconds", 900)),
            per_page=bool(d.get("per_page", True)),
            merge_pages=bool(d.get("merge_pages", True)),
            profiles=profiles,
            homr=HomrConfig.from_dict(d.get("homr", {}) or {}),
        )


@dataclass
class PreprocessOpts:
    """``[preprocess]`` section; also the options object ``prepare_page`` takes."""
    enabled: bool = False
    dpi: int = 300
    autocontrast: bool = True
    autocontrast_cutoff: int = 1
    unsharp: bool = True
    unsharp_radius: float = 2.0
    unsharp_percent: int = 150
    median: int = 0            # 0 = off; else median-filter window (denoise)
    deskew: bool = False       # projection-profile rotation to level the staves
    max_skew: float = 5.0      # only correct skew within +/- this many degrees
    crop_margins: bool = False  # trim the page down to the staff band
    crop_pad_frac: float = 0.035  # keep this much page-height whitespace around it

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PreprocessOpts":
        f = {k: d[k] for k in d if k in cls.__dataclass_fields__}
        return cls(**f)


@dataclass
class TransposeConfig:
    enabled: bool = False
    semitones: Optional[int] = None
    instrument: str = ""
    clef: str = ""

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TransposeConfig":
        if d.get("semitones") is not None and d.get("instrument"):
            raise ConfigError("[stages.transpose]: set either semitones or instrument, not both")
        if d.get("clef") and d["clef"] not in VALID_CLEFS:
            raise ConfigError("[stages.transpose] clef must be treble, bass or c")
        return cls(
            enabled=bool(d.get("enabled", False)),
            semitones=d.get("semitones"),
            instrument=d.get("instrument") or "",
            clef=d.get("clef") or "",
        )


@dataclass
class OutputConfig:
    formats: List[str] = field(default_factory=lambda: list(DEFAULT_FORMATS))
    qa_dpi: int = 150

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "OutputConfig":
        formats = list(d.get("formats", DEFAULT_FORMATS))
        bad = set(formats) - VALID_FORMATS
        if bad:
            raise ConfigError(f"invalid output.formats entries: {sorted(bad)}")
        return cls(formats=formats, qa_dpi=int(d.get("qa_dpi", 150)))


@dataclass
class StagesConfig:
    correct_enabled: bool = False
    normalize_enabled: bool = True
    transpose: TransposeConfig = field(default_factory=TransposeConfig)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "StagesConfig":
        return cls(
            correct_enabled=bool((d.get("correct", {}) or {}).get("enabled", False)),
            normalize_enabled=bool((d.get("normalize", {}) or {}).get("enabled", True)),
            transpose=TransposeConfig.from_dict(dict(d.get("transpose", {}) or {})),
        )


@dataclass
class Config:
    path: Path
    tools: Tools = field(default_factory=Tools)
    omr: OmrConfig = field(default_factory=OmrConfig)
    preprocess: PreprocessOpts = field(default_factory=PreprocessOpts)
    stages: StagesConfig = field(default_factory=StagesConfig)
    output: OutputConfig = field(default_factory=OutputConfig)

    def constants_for_profile(self, name: Optional[str] = None) -> List[str]:
        name = name or self.omr.profile
        if name not in self.omr.profiles:
            raise ConfigError(
                f"unknown omr profile {name!r}; known: {sorted(self.omr.profiles)}"
            )
        return list(self.omr.profiles[name])

    def resolve_tool(self, name: str) -> Optional[str]:
        """Return an absolute path to a configured tool, or None if not found."""
        value = getattr(self.tools, name)
        p = Path(value).expanduser()
        if p.is_absolute():
            return str(p) if p.exists() else None
        return shutil.which(value)


def load_config(path: Optional[Union[str, Path]] = None) -> Config:
    cfg_path = Path(path).expanduser() if path else DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        raise ConfigError(f"config file not found: {cfg_path}")

    with cfg_path.open("rb") as fh:
        data = tomllib.load(fh)

    return Config(
        path=cfg_path,
        tools=Tools.from_dict(data.get("tools", {})),
        omr=OmrConfig.from_dict(data.get("omr", {})),
        preprocess=PreprocessOpts.from_dict(dict(data.get("preprocess", {}))),
        stages=StagesConfig.from_dict(data.get("stages", {})),
        output=OutputConfig.from_dict(data.get("output", {})),
    )
