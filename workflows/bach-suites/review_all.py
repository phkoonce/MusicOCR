"""Send every line of the given scores to the review tool's fixer (``musicocr review`` without the page).

Used for the duet and the trio, which have no second reading to compare against. Each line's changes
stay marked "check" and can be undone on the review page.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from musicocr.review import claude  # noqa: E402
from musicocr.review.session import Session  # noqa: E402

for name in sys.argv[1:]:
    s = Session(ROOT / f"output/bach-suites-rescan/{name}/{name}.mscz", ROOT / f"input/bach-suites-rescan/{name}.pdf",
                "/Applications/MuseScore 4.app/Contents/MacOS/mscore", claude_tool=claude.find_claude(), model="sonnet")
    todo = [u for u in range(s.units()) if s.entry(u)["status"] == "unchecked"]
    for u in todo:
        s.send(u, [])
    print(name, len(todo), "lines queued", flush=True)
    while s.work_once():
        pass
    for u in todo:
        e = s.entry(u)
        n, staff = s.locate(u)
        print(f"{name} line {n + 1} staff {staff + 1} (bars {s.lines[n]['bars'][0] + 1}-{s.lines[n]['bars'][-1] + 1}): "
              f"{e['status']} | {e.get('summary') or e.get('error', '')} | unsure: {e.get('unresolved', [])}", flush=True)
