"""Write remaining.md: what the correction could not settle, by file and by the finished score's bar numbers."""
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = json.loads((HERE / "structure.json").read_text())


def final(name, number):
    """Pipeline bar number -> bar number in the finished file (inserted and removed bars shift it)."""
    audit = HERE / "answers" / name / "audit.json"
    added = json.loads(audit.read_text())["inserts"] if audit.exists() else []
    gone = spec.get(name, {}).get("ossia", [])
    return None if number in gone else number + sum(a["after"] < number for a in added) - sum(g < number for g in gone)


lines = ["# Bach Cello Suites: bars still worth a look", "",
         "Bar numbers are counted from the first bar of the movement, a pickup counting as bar 1.",
         ""]
for n in range(1, 7):
    name = f"Bach_Suite_{n}"
    folder = HERE / "answers" / name
    applied = json.loads((folder / "applied.json").read_text())
    audit = json.loads((folder / "audit.json").read_text())
    starts = [(final(name, b), title) for b, title, _ in spec[name]["movements"]]

    def where(number):
        bar = final(name, number)
        if bar is None:
            return None
        start, title = [s for s in starts if s[0] <= bar][-1] if bar >= starts[0][0] else starts[0]
        return f"{title.title()} bar {bar - start + 1}"

    found = {}
    for text in applied["left"] + audit["doubts"]:
        m = re.match(r"bar (\d+)\b[^:]*:\s*(.*)", text)
        if m and where(int(m.group(1))):
            found.setdefault(int(m.group(1)), []).append(m.group(2))
    lines += [f"## {name}", ""] + [f"- {where(b)}: {'; '.join(v)}" for b, v in sorted(found.items())] + [""]
(HERE / "remaining.md").write_text("\n".join(lines), "utf-8")
print(sum(l.startswith("- ") for l in lines), "bars listed")
