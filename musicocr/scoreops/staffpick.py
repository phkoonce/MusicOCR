"""One part out of a score page printed inside a part book.

Some part books hold a page of the whole score (a trio printed in every horn's book: "Horn 1 / Horn 2 /
Horn 3" on staves joined by a system barline). Read as a part, every staff counts as a system of its own, so a
3-staff system comes out as 3 systems and the part gets 3 times the bars. ``isolate_staff`` blanks the other
staves of every multi-staff system on the page image so that only the part's own staff (``rank`` of n, 1-based)
stays. Pages of single staves (a normal part) are left alone: the staves of a system are told from the
staves of separate systems by the system barline or bracket left of the staves, which only joined staves have.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

ABOVE_SP, BELOW_SP = 4.0, 4.5       # what stays with the kept staff: dynamics, hairpins, words beside it


def linked_groups(arr, staves) -> List[List[int]]:
    """Indices of consecutive staves joined by a vertical line across the gap (system barline or bracket) left of the staves."""
    groups: List[List[int]] = []
    for k, s in enumerate(staves):
        joined = False
        if k:
            p = staves[k - 1]
            y0, y1 = int(p.bottom) + 3, int(s.top) - 2
            if y1 - y0 >= 4:
                x0, x1 = int(arr.shape[1] * .06), int(arr.shape[1] * .5)      # not the page's edge (binding, shadow)
                joined = bool((arr[y0:y1, x0:x1].mean(axis=0) >= 0.9).any())
        if joined:
            groups[-1].append(k)
        else:
            groups.append([k])
    return groups


def is_score_page(groups: List[List[int]], n_staves: int) -> bool:
    """True when *every* staff of the page sits in a joined system of the same size (>= 2): a page of a score.
    A part page with one stray joined pair (a cue, a binding shadow, a piano reduction) is not."""
    sizes = {len(g) for g in groups}
    return len(sizes) == 1 and min(sizes) >= 2 and sum(len(g) for g in groups) == n_staves


def local_staves(img):
    """Staves read in narrow column windows near the staves' left ends, the window with most staves winning.
    A warped camera scan bends the lines, which ``find_staves`` (one wide band) smears: the last system of paxman p89
    h2/h3 was missed there (9 and 10 of 12 staves). A narrow window sees them straight."""
    import numpy as np
    from musicocr.text.words import Staff
    a = np.asarray(img) < 200
    w = a.shape[1]
    best: list = []
    for fx in (0.2, 0.15, 0.25, 0.3, 0.4, 0.6):
        x0 = int(w * fx)
        band = a[:, x0:x0 + int(w * .06)].mean(axis=1)
        lines: List[List[int]] = []
        for y in np.flatnonzero(band > 0.5):
            if lines and y - lines[-1][-1] <= 2:
                lines[-1].append(int(y))
            else:
                lines.append([int(y)])
        ys = [sum(g) // len(g) for g in lines]
        found, i = [], 0
        while i + 4 < len(ys):
            gaps = [ys[i + j + 1] - ys[i + j] for j in range(4)]
            if max(gaps) <= 1.5 * min(gaps) + 1 and min(gaps) >= 6:
                found.append(Staff(ys[i], ys[i + 4], x0))
                i += 5
            else:
                i += 1
        if len(found) > len(best):
            best = found
    return best


def spacing_groups(staves) -> List[List[int]]:
    """Systems told by spacing alone: the gaps between consecutive staves fall into two clusters (inside a system ~1.7 staff
    heights, between systems 2.4+, regular on an engraved score page); a page whose gaps do not split into two firm clusters (at least two system breaks, or a single odd gap such as a
    title or a missing system on a part page) gives one group per staff."""
    n = len(staves)
    gaps = [staves[i + 1].top - staves[i].bottom for i in range(n - 1)]
    if len(gaps) >= 2:
        order = sorted(gaps)
        ratios = [(order[i + 1] / max(order[i], 1), i) for i in range(len(order) - 1)]
        ratio, k = max(ratios)
        small, large = order[:k + 1], order[k + 1:]
        if ratio >= 1.3 and len(large) >= 2 and max(small) <= 1.25 * min(small) and max(large) <= 1.7 * min(large):
            cut = (order[k] + order[k + 1]) / 2
            groups: List[List[int]] = [[0]]
            for i, g in enumerate(gaps):
                if g > cut:
                    groups.append([i + 1])
                else:
                    groups[-1].append(i + 1)
            return groups
    return [[i] for i in range(n)]


def staff_groups(img, arr):
    """(staves, groups) of a page: the systems told by the system barline across the gap (``linked_groups``) or, when that
    does not make a score page (a skewed scan), by staff spacing (``spacing_groups``), on the staves of ``find_staves`` or
    of the narrow-window ``local_staves`` (whichever found more). A page that is no score page by any test keeps
    ``find_staves`` and its barline groups."""
    from musicocr.text.words import find_staves
    staves = find_staves(img)
    base = linked_groups(arr, staves) if len(staves) >= 2 else [[k] for k in range(len(staves))]
    loc = local_staves(img)
    cands = [loc, staves] if len(loc) > len(staves) else [staves]
    for st in cands:
        if len(st) < 4:
            continue
        lg = base if st is staves else linked_groups(arr, st)
        if is_score_page(lg, len(st)):
            return st, lg
        sg = spacing_groups(st)
        if is_score_page(sg, len(st)) and any(len(g) >= 2 for g in lg):     # some barline evidence: a part page of paired
            return st, sg                                                    # staves (two versions per page) has none
    return staves, base
    cands = [staves]
    loc = local_staves(img)
    if len(loc) > len(staves):
        cands.insert(0, loc)
    for st in cands:
        if len(st) < 4:
            continue
        lg = linked_groups(arr, st)
        if is_score_page(lg, len(st)):
            return st, lg
        sg = spacing_groups(st)
        if is_score_page(sg, len(st)) and any(len(g) >= 2 for g in lg):     # some barline evidence: a part page of paired
            return st, sg                                                    # staves (two versions per page) has none
    return staves, base


def part_number(label: str) -> int:
    """The number a part's label ends with ("... - Horn 3" -> 3; 0 when it has none)."""
    import re
    m = re.search(r"(\d+)\s*$", label.strip())
    return int(m.group(1)) if m else 0


def isolate_staff(img_path: Path, rank: int, log=print, uniform: bool = False) -> int:
    """Blank all but staff ``rank`` (1-based) of each multi-staff system, in place; the original is kept as
    ``<stem>.full.png``. Returns the number of systems handled (0: the page is untouched).
    ``uniform``: only a page whose staves all sit in joined systems of one size is touched (``is_score_page``)."""
    import numpy as np
    from PIL import Image
    img = Image.open(img_path).convert("L")
    arr = np.asarray(img) < 160
    staves, all_groups = staff_groups(img, arr)
    if len(staves) < 2:
        return 0
    if uniform and not is_score_page(all_groups, len(staves)):
        return 0
    groups = [g for g in all_groups if len(g) >= 2]
    if not groups:
        return 0
    out = np.asarray(img).copy()
    done = 0
    for g in groups:
        if rank > len(g):
            log(f"  staff {rank} asked for, but a system here has {len(g)} staves: left as it is")
            continue
        keep = staves[g[rank - 1]]
        top = int(staves[g[0]].top - ABOVE_SP * staves[g[0]].sp)
        bot = int(staves[g[-1]].bottom + BELOW_SP * staves[g[-1]].sp)
        k0 = int(keep.top - ABOVE_SP * keep.sp)
        k1 = int(keep.bottom + BELOW_SP * keep.sp)
        if rank > 1:                       # never reach into the staff above / below
            k0 = max(k0, int((staves[g[rank - 2]].bottom + keep.top) / 2))
        if rank < len(g):
            k1 = min(k1, int((keep.bottom + staves[g[rank]].top) / 2))
        out[max(top, 0):max(k0, 0), :] = 255
        out[min(k1, out.shape[0]):min(bot, out.shape[0]), :] = 255
        done += 1
    if done:
        full = img_path.with_name(f"{img_path.stem}.full.png")
        if not full.exists():
            full.write_bytes(img_path.read_bytes())
        Image.fromarray(out).save(img_path)
    return done
