"""List stretches where homr read more bars than Copisteria: candidates for lost bars."""
import difflib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from correct import OUT, baseline, measures, read  # noqa: E402

for name in sys.argv[1:]:
    a = read(measures(baseline(name)))
    b = read(measures(OUT / "ttl=30d/homr" / name / f"{name}.musicxml"))
    m = difflib.SequenceMatcher(None, [x["sig"] for x in a], [x["sig"] for x in b], autojunk=False)
    print(name, len(a), len(b))
    for tag, i1, i2, j1, j2 in m.get_opcodes():
        if j2 - j1 > i2 - i1:
            print(f"  ours {i1 + 1}-{i2} ({i2 - i1}) homr {j2 - j1}")
            for i in range(i1, i2):
                print("     ours", i + 1, a[i]["text"][:80])
            for j in range(j1, j2):
                print("     homr     ", b[j]["text"][:80])
