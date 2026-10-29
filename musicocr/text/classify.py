"""Classify a phrase of text found around a staff (docs/text-layer-design.md section 5.4).

Data-driven: the vocabulary lives in ``text_lexicon.toml``. Precision comes first: a phrase is
given a kind only when every token of it is in the lexicon (exactly, or with one OCR error for
words of five letters or more); anything else letter-like is ``unknown`` and the caller decides
whether to flag it.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

LEXICON_PATH = Path(__file__).with_name("lexicon.toml")
KIND_PRIORITY = ["tempo_change", "tempo", "technique", "expression"]
MIN_BPM, MAX_BPM = 30, 320

# "<words> (<glyph> = <number>)": the note glyph is rarely read right (a dot, a bullet, "d", "J"),
# so any 0-3 non-word characters or one of J/d stand for it.
_METRO = re.compile(
    r"^(?P<words>.*?)\s*(?P<open>\()?\s*(?P<glyph>[^\w\s=()]{0,3}|[JjdD]|(?<![A-Za-z])[qeh])\s*(?P<dot>\.)?\s*=\s*"
    r"(?:c\.?\s*)?(?P<n>\d{2,3})(?:\s*[-–]\s*\d{2,3})?\s*(?P<close>\))?\s*$")


@dataclass
class Marking:
    kind: str                       # tempo | tempo_change | expression | technique | cue | navigation
    #                                 | rehearsal | unknown | ignore
    text: str = ""                  # display text (canonical spelling for known words)
    bpm: Optional[int] = None
    beat_unit: str = "quarter"
    parens: bool = False
    dotted: Optional[bool] = None   # known only from a music-font glyph letter ("q." in a PDF text layer)
    leftover: str = ""              # tempo: words next to the metronome mark that were not understood
    words_kind: str = ""            # tempo: kind of the words part (tempo / tempo_change / expression ...)


# --- lexicon ---------------------------------------------------------------------

@dataclass
class Lexicon:
    words: Dict[str, List[Tuple[str, str]]]      # normalised key -> [(kind, canonical)]
    modifiers: Dict[str, str]                    # normalised key -> canonical
    patterns: Dict[str, List[re.Pattern]]
    ignore: List[re.Pattern]
    pairs: List[Tuple[str, str]]
    max_tokens: int = 3


def norm(s: str) -> str:
    s = s.lower().replace("’", "'")
    s = re.sub(r"[.\-_]", "", s)
    s = s.replace("à", "a").replace("é", "e").replace("ù", "u").replace("ì", "i")
    s = s.replace("ü", "u").replace("ö", "o")
    return re.sub(r"\s+", " ", s).strip()


def _merge(dst: dict, src: dict) -> None:
    for k, v in src.items():
        if isinstance(v, dict):
            _merge(dst.setdefault(k, {}), v)
        elif isinstance(v, list):
            dst[k] = list(dst.get(k, [])) + v
        else:
            dst[k] = v


def load_lexicon(extra: Sequence[Path] = ()) -> Lexicon:
    data: dict = tomllib.loads(LEXICON_PATH.read_text())
    for p in extra:
        _merge(data, tomllib.loads(Path(p).expanduser().read_text()))
    words: Dict[str, List[Tuple[str, str]]] = {}
    mods: Dict[str, str] = {}
    longest = 1
    for kind in KIND_PRIORITY:
        sec = data.get(kind, {})
        for w in sec.get("words", []):
            words.setdefault(norm(w), []).append((kind, w))
            longest = max(longest, len(norm(w).split()))
        for m in sec.get("modifiers", []):
            mods.setdefault(norm(m), m)
            longest = max(longest, len(norm(m).split()))
    pats = {k: [re.compile(p, re.I) for p in v] for k, v in data.get("patterns", {}).items()}
    ign = [re.compile(p, re.I) for p in data.get("ignore", {}).get("patterns", [])]
    pairs = [tuple(p) for p in data.get("confusions", {}).get("pairs", [])]
    return Lexicon(words, mods, pats, ign, pairs, min(longest, 4))


@lru_cache(maxsize=4)
def default_lexicon(extra: Tuple[str, ...] = ()) -> Lexicon:
    return load_lexicon([Path(e) for e in extra])


# --- matching --------------------------------------------------------------------

def _within_one(a: str, b: str) -> bool:
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    if len(a) == len(b):
        return a[i + 1:] == b[i + 1:]
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return short[i:] == long_[i + 1:]


def _variants(s: str, pairs: Sequence[Tuple[str, str]]) -> List[str]:
    out = [s]
    for a, b in pairs:
        for x, y in ((a, b), (b, a)):
            if x in s:
                out.append(s.replace(x, y))
    return out


def _lookup(table: dict, key: str, lex: Lexicon):
    """Exact or confusion-corrected match first; then edit distance 1 for keys of 5+ letters."""
    for v in _variants(key, lex.pairs):
        if v in table:
            return table[v]
    if len(key) >= 5:
        hits = [t for k, t in table.items() if len(k) >= 5 and _within_one(key, k)]
        if len(hits) == 1:
            return hits[0]
    return None


def _match_tokens(text: str, lex: Lexicon) -> Optional[Tuple[List[str], List[str], List[str]]]:
    """Cover ``text`` word by word with lexicon entries (longest match first).
    Returns (kinds, display_tokens, kinds_per_word_entry) or None when any token is unknown."""
    toks = text.split()
    i, shown, kinds = 0, [], []
    while i < len(toks):
        for n in range(min(lex.max_tokens, len(toks) - i), 0, -1):
            key = norm(" ".join(toks[i:i + n]))
            if not key:
                continue
            raw = clean(" ".join(toks[i:i + n]))
            w = _lookup(lex.words, key, lex)
            if w:
                kinds += [k for k, _ in w]
                shown.append(raw if key == norm(w[0][1]) else w[0][1])     # keep the printed capitalisation
                i += n
                break
            m = _lookup(lex.modifiers, key, lex)
            if m:
                shown.append(raw if key == norm(m) else m)
                i += n
                break
        else:
            return None
    return kinds, shown, kinds


def _kind_of(kinds: Sequence[str]) -> Optional[str]:
    for k in KIND_PRIORITY:
        if k in kinds:
            return k
    return None


_EDGE = re.compile(r"^[^\w(\[+=•♩]+|[^\w.)\]]+$")


def clean(text: str) -> str:
    t = text.replace("–", "-").replace("—", "-").strip()
    t = _EDGE.sub("", t)
    t = re.sub(r"\.{2,}", ".", t)
    return re.sub(r"\s+", " ", t).strip()


_DYN_TOKEN = re.compile(r"^[(\[]?[pmfszr]{1,5}[)\]]?[-=<>.~_]*$", re.I)
_NUM_TOKEN = re.compile(r"^\d{1,3}$")


def _strip_edge_noise(t: str, lex: Lexicon) -> Tuple[str, bool]:
    """Drop dynamics and bar-number tokens from either end of a multi-token phrase."""
    toks = t.split()
    n0 = len(toks)
    noise = lambda w: bool(_DYN_TOKEN.match(w) or _NUM_TOKEN.match(w) or (len(w) == 1 and w.isalpha() and w not in "aAI"))
    while len(toks) > 1 and noise(toks[-1]):
        toks.pop()
    while len(toks) > 1 and noise(toks[0]):
        toks.pop(0)
    return " ".join(toks), len(toks) < n0


def _plausible_unknown(t: str) -> bool:
    if any(c.isalpha() and ord(c) > 0x24F for c in t):      # Cyrillic/Greek/CJK: OCR noise on music symbols
        return False
    if any(len(w.strip(".,")) < 2 and w.lower() not in ("a",) for w in t.split()):    # "te e": fragments
        return False
    letters = sum(c.isalpha() for c in t)
    return (letters >= 3 and letters / max(1, len(t.replace(" ", ""))) >= 0.7
            and re.search(r"[aeiouyAEIOUY]", t) is not None)


def classify(text: str, lex: Optional[Lexicon] = None, height_sp: float = 1.5,
             min_unknown_letters: int = 3) -> Marking:
    """Kind (and normalised display text) of one phrase. ``height_sp`` is the phrase's box height
    in staff spaces (used only to accept a lone capital letter as a rehearsal mark)."""
    lex = lex or default_lexicon()
    t = clean(text)
    if not t:
        return Marking("ignore")
    t = re.sub(r"=\s*(?:[^\w\s=]{0,2}\s*)?=", "=", t)           # "J= = 119": the sign read twice (two passes)
    m = _METRO.match(t)
    bare = m is not None and not m.group("words").strip() and not m.group("glyph") and not m.group("open") and not m.group("close") \
        and text.strip().startswith("=")      # "=200" with no note glyph at all: a bar number under a hairpin
    if m and not bare and MIN_BPM <= int(m.group("n")) <= MAX_BPM:
        words = re.sub(r"(\s+[^\w\s])+$", "", m.group("words").strip())          # the note glyph read as a "."
        words = clean(re.sub(r"^[\d\s,.\-]+$|^\d+[\s,.\-]+(?=[(\w])", "", words))               # a bar number printed just before it
        words = re.sub(r"^(?:\w\s+)+|(?:\s+\w)+$", "", words)                       # stray one-letter fragments at the ends
        g = m.group("glyph")
        unit = {"q": "quarter", "e": "eighth", "h": "half"}.get(g, "quarter")
        mk = Marking("tempo", "", int(m.group("n")), unit,
                     parens=bool(m.group("open") or m.group("close")),
                     dotted=bool(m.group("dot")) if g in ("q", "e", "h") else None)
        if words:
            sub = classify(words, lex, height_sp)
            if sub.kind in ("tempo", "tempo_change", "expression", "technique"):
                mk.text, mk.words_kind = sub.text, sub.kind
            else:
                mk.leftover = words
        return mk
    if re.fullmatch(r"[A-Z]", t) and height_sp >= 1.8:
        return Marking("rehearsal", t)
    for p in lex.ignore:
        if p.match(t):
            return Marking("ignore")
    for kind in ("navigation", "cue"):
        if any(p.match(t) for p in lex.patterns.get(kind, [])):
            return Marking(kind, re.sub(r"\bV[I1]n", "Vln", re.sub(r"\bF[1I]\b", "Fl", t)) if kind == "cue" else t)
    t = re.sub(r"\b(\w+\.?)(\s+\1)+(?=\s|$)", r"\1", t, flags=re.I)         # a word read twice ("poco poco")
    t, stripped = _strip_edge_noise(t, lex)       # a bar number or dynamic printed right beside the words
    if not t:
        return Marking("ignore")
    r = _match_tokens(t, lex)
    if r:
        kinds, shown, _ = r
        k = _kind_of(kinds)
        if k:
            return Marking(k, " ".join(shown))
        if stripped:                                # "molto f": only modifiers left of a dynamic
            return Marking("ignore")
    if any(p.match(t) for p in lex.patterns.get("technique", [])):
        return Marking("technique", t)
    letters = sum(c.isalpha() for c in t)
    if letters >= min_unknown_letters and _plausible_unknown(t):
        return Marking("unknown", t)
    return Marking("ignore")
