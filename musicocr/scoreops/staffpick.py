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
    from musicocr.text.words import find_staves
    img = Image.open(img_path).convert("L")
    staves = find_staves(img)
    if len(staves) < 2:
        return 0
    arr = np.asarray(img) < 160
    all_groups = linked_groups(arr, staves)
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
