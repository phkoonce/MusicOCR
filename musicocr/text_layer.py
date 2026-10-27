"""Text layer: tempo, expression and technique words as MusicXML directions
(docs/text-layer-design.md).

``build_items`` turns a page's words into classified, placed phrases; ``write_textlayer_json``
runs the whole chain for one page image and writes ``<stem>.textlayer.json`` beside it, which
``copisteria_run.py`` reads to attach each phrase to the right staff and bar.

Zones (staff spaces, ``sp``): a phrase belongs to the staff it is nearest to, up to
``above_sp`` above / ``below_sp`` below it. Above the page's first staff only metronome marks
(and classified words close to the staff) are kept: that is where the title block, composer and
instrument header live.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from musicocr.config import TextLayerConfig as TextLayerSettings
from musicocr.text_classify import Lexicon, Marking, classify, default_lexicon
from musicocr.text_words import (Phrase, Staff, Word, find_staves, load_cached, ocr_page_words,
                                 page_key, pdf_page_words, phrases, save_cache, stack_tempo_lines)

FIRST_STAFF_FURNITURE_SP = 4.5       # above the first staff, further than this = title block / header
ABOVE_ONLY = ("tempo", "rehearsal")
EMIT_KINDS = {"tempo", "tempo_change", "expression", "technique", "cue", "navigation", "rehearsal"}
STYLE = {"tempo": "bold", "tempo_change": "bold italic", "expression": "italic",
         "technique": "bold italic", "cue": "italic", "navigation": "bold", "rehearsal": "", "unknown": "italic"}


@dataclass
class Result:
    items: List[dict] = field(default_factory=list)
    dropped: List[dict] = field(default_factory=list)      # phrases not kept, with the reason (for reports)


def _zone(box, staves: Sequence[Staff]) -> Tuple[int, float, bool]:
    """(nearest staff, distance in staff spaces, above?) for a phrase box."""
    cy = (box[1] + box[3]) / 2
    best = (0, 1e9, True)
    for i, s in enumerate(staves):
        if cy < s.top:
            d, above = (s.top - box[3]) / s.sp, True
        elif cy > s.bottom:
            d, above = (box[1] - s.bottom) / s.sp, False
        else:
            d, above = 0.0, cy < (s.top + s.bottom) / 2
        if d < best[1]:
            best = (i, d, above)
    return best


MUSIC_CLASSES = ("note_", "rest_", "artic_", "accid_", "beam", "flag", "curve", "dots_", "tuplet", "hairpin",
                 "dynam", "fermata", "clef_", "meterSig", "keySig", "barLine", "mRest", "multiRest", "stem", "ornam")


def music_boxes(dets: Optional[Sequence[dict]]) -> List[Sequence[float]]:
    """Boxes of detected music symbols (accents, note heads, ...): OCR 'words' made of them are noise."""
    return [d["xyxy"] for d in dets or [] if str(d.get("cls", "")).startswith(MUSIC_CLASSES)]


def _on_music(box, boxes, frac: float = 0.3) -> bool:
    """True if some detected symbol lies mostly inside ``box`` (the phrase is that symbol misread)."""
    for b in boxes:
        iw = min(box[2], b[2]) - max(box[0], b[0])
        ih = min(box[3], b[3]) - max(box[1], b[1])
        area = max(1e-6, (b[2] - b[0]) * (b[3] - b[1]))
        if iw > 0 and ih > 0 and iw * ih / area >= frac:
            return True
    return False


def _runs(mask) -> List[Tuple[int, int]]:
    out, start = [], None
    for i, v in enumerate(list(mask) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start, i))
            start = None
    return out


def is_dotted_beat(ink) -> Optional[bool]:
    """Is the metronome note in this crop (numpy bool array, True = ink, the whole phrase) dotted?

    The mark ends "note [dot] = number": the '=' is the rightmost short, wide blob; the blob left of it
    is a dot when it is a small speck, else the note. None when no '=' blob is found."""
    import numpy as np
    cols = ink.any(axis=0)
    segs = []
    for a, b in _runs(cols):
        rows = np.flatnonzero(ink[:, a:b].any(axis=1))
        if b - a >= 2 and len(rows):
            segs.append((a, b, int(rows[0]), int(rows[-1]) + 1))
    if len(segs) < 3:
        return None
    tail = segs[-8:]
    tall = max(s[3] - s[2] for s in tail)
    for i in range(len(tail) - 1, 0, -1):
        a, b, y0, y1 = tail[i]
        w, h = b - a, y1 - y0
        if w >= 1.1 * h and h <= 0.45 * tall and w >= 0.25 * tall:           # the '=' sign
            la, lb, ly0, ly1 = tail[i - 1]
            return (lb - la) <= 0.3 * tall and (ly1 - ly0) <= 0.3 * tall
    return None


def build_items(words: Sequence[Word], staves: Sequence[Staff], lex: Optional[Lexicon] = None,
                cfg: Optional[TextLayerSettings] = None, dets: Optional[Sequence[dict]] = None,
                img=None) -> Result:
    cfg = cfg or TextLayerSettings()
    lex = lex or default_lexicon(tuple(cfg.lexicon))
    res = Result()
    if not staves or cfg.mode == "off":
        return res
    sp = sorted(s.sp for s in staves)[len(staves) // 2]
    ps = stack_tempo_lines(phrases(words, sp), sp)
    music = music_boxes(dets)
    for p in ps:
        h = (p.box[3] - p.box[1]) / sp
        mk = classify(p.text, lex, h, cfg.min_unknown_letters)
        if mk.kind == "ignore":
            continue
        si, d, above = _zone(p.box, staves)
        if mk.kind in ABOVE_ONLY:             # printed above its staff: in the gap between systems it belongs to the next one
            nxt = next((i for i, s in enumerate(staves) if s.top > (p.box[1] + p.box[3]) / 2), None)
            if nxt is not None and (staves[nxt].top - p.box[3]) / staves[nxt].sp <= cfg.above_sp:
                si, d, above = nxt, max(0.0, (staves[nxt].top - p.box[3]) / staves[nxt].sp), True
        rec = {"text": p.text, "kind": mk.kind, "staff": si, "dist_sp": round(d, 1)}

        def drop(why):
            res.dropped.append({**rec, "why": why, "xyxy": [round(v, 1) for v in p.box]})

        limit = cfg.above_sp if above else cfg.below_sp
        if d > limit:
            drop("out of zone")
            continue
        if si == 0 and above and d > FIRST_STAFF_FURNITURE_SP and mk.bpm is None:
            drop("page furniture (title zone)")
            continue
        if mk.kind == "unknown" and _on_music(p.box, music):
            drop("unknown text sitting on detected music symbols")
            continue
        if mk.kind == "unknown" and (cfg.mode != "flag" or not above or d == 0):
            drop("unknown text not above a staff" if cfg.mode == "flag" else "unknown (mode on)")
            continue
        if mk.kind == "tempo" and mk.bpm is None and not mk.text:
            continue
        if mk.kind == "tempo" and mk.leftover:
            drop("words beside the metronome mark not understood")
            continue
        if mk.kind == "tempo" and not above:
            drop("tempo mark below a staff (bar numbers / hairpins read as '=')")
            continue
        if mk.kind == "rehearsal" and (not above or p.words[0].source != "pdf" or _on_music(p.box, music)):
            drop("rehearsal letter not read from the PDF text layer (OCR reads accents as letters) or sitting on music")
            continue
        item = {"text": mk.text if mk.kind == "tempo" else (mk.text or p.text), "kind": mk.kind,
                "xyxy": [round(v, 1) for v in p.box],
                "style": p.style or STYLE.get(mk.kind, ""), "source": p.words[0].source,
                "flag": mk.kind == "unknown"}
        if mk.kind in ABOVE_ONLY:
            item["above"] = True
        if mk.kind == "tempo":
            item.update(bpm=mk.bpm, beat_unit=mk.beat_unit, parens=mk.parens, words_kind=mk.words_kind)
            if mk.leftover:
                item["leftover"] = mk.leftover
            if mk.dotted:
                item["dotted"] = True
            elif img is not None and mk.bpm and mk.dotted is None:
                import numpy as np
                x0, y0, x1, y1 = (int(v) for v in p.box)
                dotted = is_dotted_beat(np.asarray(img.crop((x0, y0, x1, y1))) < 140)
                if dotted:
                    item["dotted"] = True
        res.items.append(item)
    res.items = _dedupe(res.items)
    return res


def _dedupe(items: List[dict]) -> List[dict]:
    out: List[dict] = []
    for it in items:
        if not any(o["text"] == it["text"] and o.get("bpm") == it.get("bpm") and
                   _overlap(o["xyxy"], it["xyxy"]) for o in out):
            out.append(it)
    return out


def _overlap(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def page_words(png: Path, cfg: TextLayerSettings, *, pdftotext: Optional[str] = None,
               pdf: Optional[Path] = None, page: int = 0, geom=None, ocr_python: Optional[str] = None,
               log=None) -> Tuple[List[Word], List[Staff]]:
    """Words and staves of a page image: PDF text layer when the page has one (needs ``pdf``,
    ``pdftotext`` and ``geom``), else OCR of the bands around the staves. Cached beside the image."""
    from PIL import Image
    use_pdf = bool(pdftotext and pdf and pdf.exists() and geom is not None and page)
    params = {"src": "pdf" if use_pdf else "ocr", "backend": cfg.backend, "passes": list(cfg.passes),
              "above": cfg.above_sp, "below": cfg.below_sp}
    if use_pdf:
        params["pdf"] = f"{pdf.name}:{pdf.stat().st_size}:{page}"
    key = page_key(png, params)
    cache = png.with_name(f"{png.stem}.textwords.json")
    hit = load_cached(cache, key)
    if hit:
        return hit
    img = Image.open(png).convert("L")
    staves = find_staves(img)
    words: List[Word] = []
    if use_pdf:
        words = pdf_page_words(pdftotext, pdf, page, geom, img.size, log=log)
    if not words:
        backend = "vision" if cfg.backend == "auto" else cfg.backend
        words, _ = ocr_page_words(png, staves, backend, cfg.passes, cfg.above_sp, cfg.below_sp, log=log,
                                  python=ocr_python)
        if not words and cfg.backend == "auto":
            words, _ = ocr_page_words(png, staves, "auto", cfg.passes, cfg.above_sp, cfg.below_sp, log=log,
                                      python=ocr_python)
    save_cache(cache, key, words, staves)
    return words, staves


def write_textlayer_json(png: Path, cfg: TextLayerSettings, log=None, **kw) -> int:
    """Write ``<stem>.textlayer.json`` (and ``.textlayer.dropped.json``, for the evaluation report);
    return the number of phrases kept. Removes a stale file when there are none."""
    dst = png.with_name(f"{png.stem}.textlayer.json")
    dropped = png.with_name(f"{png.stem}.textlayer.dropped.json")
    if cfg.mode == "off":
        dst.unlink(missing_ok=True)
        return 0
    words, staves = page_words(png, cfg, log=log, **kw)
    dets = None
    found = sorted(png.parent.glob(f"{png.stem}.dets_*.json"))
    if len(found) == 1:
        try:
            dets = json.loads(found[0].read_text())
        except (OSError, ValueError):
            dets = None
    from PIL import Image
    res = build_items(words, staves, cfg=cfg, dets=dets, img=Image.open(png).convert("L"))
    dropped.write_text(json.dumps(res.dropped, indent=0))
    if not res.items:
        dst.unlink(missing_ok=True)
        return 0
    dst.write_text(json.dumps(res.items, indent=0))
    return len(res.items)
