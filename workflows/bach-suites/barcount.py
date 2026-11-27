"""Count the bar lines on every printed line and list lines whose bar count differs from the score's."""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from correct import OUT, images, open_session, projection_staves, work  # noqa: E402


def barlines(path):
    """x of each bar line: a thin vertical stroke from the top staff line to the bottom one, no further."""
    img = np.asarray(Image.open(path).convert("L")) < 160
    h, w = img.shape
    rows = np.flatnonzero(img.mean(axis=1) > 0.45)
    if not len(rows):
        return []
    groups = [g.mean() for g in np.split(rows, np.flatnonzero(np.diff(rows) > 1) + 1)]
    fives = [groups[i:i + 5] for i in range(len(groups) - 4)
             if np.diff(groups[i:i + 5]).max() < 1.35 * np.diff(groups[i:i + 5]).min() and 8 < np.diff(groups[i:i + 5]).mean() < 22]
    if not fives:
        return []
    five = min(fives, key=lambda f: abs((f[0] + f[4]) / 2 - h / 2))
    top, bottom, sp = int(round(five[0])), int(round(five[4])), (five[4] - five[0]) / 4
    wide = img | np.roll(img, 1, 1) | np.roll(img, -1, 1)          # allow a little lean
    full = wide[top + 2:bottom - 1].mean(axis=0) > 0.97
    found = []
    columns = np.flatnonzero(full)
    for run in np.split(columns, np.flatnonzero(np.diff(columns) > 1) + 1) if len(columns) else []:
        a, b = int(run[0]), int(run[-1])
        if b - a > sp * 0.9:
            continue
        # a stem goes on beyond the staff or carries a note head or beam
        over = wide[max(0, int(top - sp)):max(0, top - 3), a:b + 1].mean()
        under = wide[bottom + 4:int(bottom + sp), a:b + 1].mean()
        left = img[top:bottom, max(0, a - int(sp)):max(0, a - 2)].mean()
        right = img[top:bottom, b + 3:b + int(sp)].mean()
        if over > 0.3 or under > 0.3 or max(left, right) > 0.42:
            continue
        found.append((a + b) / 2)
    merged = []
    for x in found:
        if not merged or x - merged[-1] > 2.5 * sp:
            merged.append(x)
        else:
            merged[-1] = x
    return [x for x in merged if x > 12 * sp or x > 220]


if __name__ == "__main__":
    for name in sys.argv[1:]:
        s = open_session(name, work(name) / f"{name}.mscz")
        bad = []
        for n, line in enumerate(s.lines):
            found = len(barlines(s.image_path(n, 0, "orig")))
            have = len(line["cells"])
            if found != have:
                bad.append((n, line["page"] + 1, [c[0] + 1 for c in line["cells"]], found))
        print(name, f"{len(bad)} of {len(s.lines)} lines differ")
        for n, page, bars, found in bad:
            print(f"  line {n} page {page} score bars {bars[0]}-{bars[-1]} ({len(bars)}) print {found}")
