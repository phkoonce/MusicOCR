"""Load and validate config.toml."""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from musicocr.engines import VALID_ENGINES
from musicocr.preprocess import PreprocessOpts

import tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.toml"

VALID_FORMATS = {"musicxml", "mscz", "midi", "qa-pdf"}


VALID_CLEFS = ("treble", "bass", "c")

DEFAULT_FORMATS = ("musicxml", "mscz", "midi", "qa-pdf")


class ConfigError(RuntimeError):
    pass


@dataclass
class Tools:
    audiveris: str = ""
    homr_python: str = str(REPO_ROOT / "omr-eval" / "homr" / ".venv" / "bin" / "python")
    copisteria_python: str = str(REPO_ROOT / "omr-eval" / "copisteria" / ".venv" / "bin" / "python")
    musescore: str = ""
    pdfinfo: str = "pdfinfo"
    pdftoppm: str = "pdftoppm"
    pdftotext: str = "pdftotext"

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
class CopisteriaConfig:
    detector: str = "28m"   # "28m" (default) or "2m" (smaller, faster)
    fix_empty: bool = True   # give bare-<forward> bars a whole-measure rest
    second_pass: bool = True   # re-detect gap pages one size up and re-read (accepted only if confined to gap bars)
    second_pass_scale: float = 1.28
    second_pass_retry_scales: tuple = (1.12, 1.5)   # tried in turn while gap bars remain
    second_pass_extra_scales: tuple = (1.75,)   # more input sizes unioned in (blind spots depend on the size)
    fix_half_tuplets: bool = True   # finish a duplet/triplet the model gave to only some of its notes
    fill_empty_bars: bool = True   # bars the detector read nothing in: re-detect one size up, add what is found there
    text_tuplets: bool = True   # small "2"/"3" digits from the text layer mark duplets/triplets (copisteria_run.py)
    fix_pickup: bool = True   # page 1: make a padded opening bar a real pickup, show its time signature
    reveal_gaps: bool = True   # gaps Copisteria left (missed symbols) -> visible coloured rests
    fix_key_cancel: bool = True   # staff opens with no printed key sig -> C major
    fix_time_inherit: bool = True # unprinted page-start meter continues the previous page's
    fix_ghost_bars: bool = True   # drop key/time-signature "bars" misread from cautionary signatures
    texts: str = "pdf-layer"   # "pdf-layer", "ocr", "auto" (layer if the PDF has one, else OCR) or "none"
    ocr_backend: str = "auto"   # texts = "ocr": "auto" (union of all that work), "rapidocr" or "vision"
    multirest_form: str = "collapse"   # "collapse" (homr's form), "expand", or "keep"
    repo: str = str(REPO_ROOT / "omr-eval" / "copisteria" / "repo")

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CopisteriaConfig":
        base = cls()
        detector = str(d.get("detector", base.detector))
        if detector not in ("28m", "2m"):
            raise ConfigError("[omr.copisteria] detector must be '28m' or '2m'")
        texts = str(d.get("texts", base.texts))
        if texts not in ("pdf-layer", "ocr", "auto", "none"):
            raise ConfigError("[omr.copisteria] texts must be 'pdf-layer', 'ocr', 'auto' or 'none'")
        ocr_backend = str(d.get("ocr_backend", base.ocr_backend))
        if ocr_backend not in ("auto", "rapidocr", "vision"):
            raise ConfigError("[omr.copisteria] ocr_backend must be 'auto', 'rapidocr' or 'vision'")
        form = str(d.get("multirest_form", base.multirest_form))
        if form not in ("collapse", "expand", "keep"):
            raise ConfigError("[omr.copisteria] multirest_form must be 'collapse', 'expand' or 'keep'")
        return cls(detector=detector, texts=texts, fix_empty=bool(d.get("fix_empty", True)),
                   fix_key_cancel=bool(d.get("fix_key_cancel", True)),
                   fix_time_inherit=bool(d.get("fix_time_inherit", True)),
                   fix_ghost_bars=bool(d.get("fix_ghost_bars", True)),
                   fix_pickup=bool(d.get("fix_pickup", True)),
                   second_pass=bool(d.get("second_pass", True)),
                   second_pass_scale=float(d.get("second_pass_scale", 1.28)),
                   second_pass_retry_scales=tuple(float(x) for x in d.get("second_pass_retry_scales", (1.12, 1.5))),
                   second_pass_extra_scales=tuple(float(x) for x in d.get("second_pass_extra_scales", base.second_pass_extra_scales)),
                   fix_half_tuplets=bool(d.get("fix_half_tuplets", True)),
                   fill_empty_bars=bool(d.get("fill_empty_bars", True)),
                   text_tuplets=bool(d.get("text_tuplets", True)),
                   reveal_gaps=bool(d.get("reveal_gaps", True)),
                   multirest_form=form, ocr_backend=ocr_backend,
                   repo=str(d.get("repo", base.repo)))


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
    copisteria: CopisteriaConfig = field(default_factory=CopisteriaConfig)
    # Extra Audiveris constants from the CLI (--constant); never read from TOML.
    extra_constants: List[str] = field(default_factory=list)

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
            copisteria=CopisteriaConfig.from_dict(d.get("copisteria", {}) or {}),
        )


@dataclass
class TransposeConfig:
    enabled: bool = False
    semitones: Optional[int] = None
    instrument: str = ""
    clef: str = ""
    mode: str = "written"   # "written": keep pitches, declare instrument + <transpose>; "concert": rewrite pitches

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TransposeConfig":
        if d.get("mode", "written") not in ("written", "concert"):
            raise ConfigError("[stages.transpose] mode must be written or concert")
        if d.get("semitones") is not None and d.get("instrument"):
            raise ConfigError("[stages.transpose]: set either semitones or instrument, not both")
        if d.get("clef") and d["clef"] not in VALID_CLEFS:
            raise ConfigError("[stages.transpose] clef must be treble, bass or c")
        return cls(
            enabled=bool(d.get("enabled", False)),
            semitones=d.get("semitones"),
            instrument=d.get("instrument") or "",
            clef=d.get("clef") or "",
            mode=d.get("mode", "written"),
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

    def audiveris_constants(self) -> List[str]:
        """Audiveris ``-constant`` values: the profile's, then CLI extras."""
        return self.constants_for_profile() + list(self.omr.extra_constants)

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
