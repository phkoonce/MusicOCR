"""Correct the Bach Cello Suites transcriptions against the print, cheaply.

Copisteria's reading is the score; homr's is a second opinion. Every bar where they differ, or that
the pipeline flagged, is cut from the print and a model says which reading matches or gives the right one.

    .venv/bin/python workflows/bach-suites/correct.py items  NAME     # free: list and crop
    .venv/bin/python workflows/bach-suites/correct.py run    NAME     # model calls
    .venv/bin/python workflows/bach-suites/correct.py retry  NAME     # "?" bars again, from the whole line
    .venv/bin/python workflows/bach-suites/correct.py apply  NAME     # edit the MusicXML (baseline kept)

Then rebuild the .mscz: ./run.sh run input/bach-suites-rescan/NAME.pdf --output output/bach-suites-rescan --from convert
"""
from __future__ import annotations

import concurrent.futures
import copy
import difflib
import json
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from musicocr.review import claude, images  # noqa: E402
from musicocr.review.session import ReviewError, Session  # noqa: E402
from musicocr.scoreops.layout import read_mscz, write_mscz  # noqa: E402

OUT = ROOT / "output/bach-suites-rescan"
KEPT = Path(__file__).resolve().parent / "answers"       # answers cost quota: keep them
MUSESCORE = "/Applications/MuseScore 4.app/Contents/MacOS/mscore"
PER_BATCH, COLS, CELL = 14, 2, 760
TYPES = {"w": ("whole", Fraction(4)), "h": ("half", Fraction(2)), "q": ("quarter", Fraction(1)),
         "e": ("eighth", Fraction(1, 2)), "s": ("16th", Fraction(1, 4)), "t": ("32nd", Fraction(1, 8)),
         "x": ("64th", Fraction(1, 16))}
LETTER = {v[0]: k for k, v in TYPES.items()}
ALTER = {-2: "bb", -1: "b", 0: "", 1: "#", 2: "x"}
ACCIDENTAL = {-2: "flat-flat", -1: "flat", 0: "natural", 1: "sharp", 2: "double-sharp"}
TOKEN = re.compile(r"^(grace:)?(\+)?(r|[A-G](?:bb|b|#|x)?\d)([whqestx])(\.*)(?:\((\d)-tuplet\))?(~)?$")


def work(name):
    return OUT / "ttl=30d/arbitration" / name


def measures(path):
    return ET.parse(path).getroot().find("part").findall("measure")


def baseline(name):
    """The pipeline's own MusicXML: kept before the first correction."""
    path = OUT / name / f"{name}.musicxml"
    kept = work(name).parent / f"{name}.pipeline-baseline.musicxml"
    if not kept.exists():
        kept.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, kept)
    return kept


def triplet_bars(name):
    """Bar indices structure.json lists as all eighth-note triplets with the "3" left unprinted."""
    path = Path(__file__).parent / "structure.json"
    spec = json.loads(path.read_text()).get(name, {}) if path.exists() else {}
    return {i for a, b in spec.get("triplets", []) for i in range(a - 1, b)}


def tripletize(notes, length):
    """``notes`` with the eighths as triplets, if that and only that makes the bar ``length`` long."""
    if not notes or sum(map(quarters, notes), Fraction(0)) == length:
        return notes
    new = [dict(n, tuplet=3) if n["value"] == "e" and not n["grace"] else n for n in notes]
    return new if sum(map(quarters, new), Fraction(0)) == length else notes


def load_baseline(name):
    """The pipeline's MusicXML with the misread key and time signatures of structure.json put right."""
    tree = ET.parse(baseline(name))
    path = Path(__file__).parent / "structure.json"
    spec = json.loads(path.read_text()).get(name, {}) if path.exists() else {}
    for part in tree.getroot().findall("part"):
        bars = part.findall("measure")
        for kind, tags in (("keys", ("key", ("fifths",))), ("times", ("time", ("beats", "beat-type")))):
            tag, children = tags
            for number, value in spec.get(kind, {}).items():
                m = bars[int(number) - 1]
                attributes = m.find("attributes")
                if attributes is None:
                    attributes = ET.Element("attributes")
                    m.insert(1 if m.find("print") is not None else 0, attributes)
                old = attributes.find(tag)
                at = len(attributes) if old is None else list(attributes).index(old)
                if old is not None:
                    attributes.remove(old)
                elif tag == "key":
                    at = 1 if attributes.find("divisions") is not None else 0
                if value is None:                      # a signature change that is not in the print
                    continue
                new = ET.Element(tag)
                for child, text in zip(children, str(value).split("/")):
                    ET.SubElement(new, child).text = text
                attributes.insert(at, new)
        divisions, time = 1, "4/4"
        for i, m in enumerate(bars):
            divisions = int(m.findtext("attributes/divisions") or divisions)
            if m.find("attributes/time") is not None:
                time = f"{m.findtext('attributes/time/beats')}/{m.findtext('attributes/time/beat-type')}"
            if i in triplet_bars(name):
                notes = parse(read([m])[0]["text"])
                new = tripletize(notes, bar_length(time))
                if new is not notes and divisions % 3 == 0:
                    rewrite(m, new, divisions, 0 if m.find("attributes/key") is None else
                            int(m.findtext("attributes/key/fifths")), time)
    return tree


def spell(note):
    if note.find("rest") is not None:
        return "r"
    p = note.find("pitch")
    return p.findtext("step") + ALTER[int(float(p.findtext("alter") or 0))] + p.findtext("octave")


def read(bars):
    """Per bar: dict(sig, text, key, clef, time)."""
    out, key, clef, time = [], 0, "treble", "4/4"
    for m in bars:
        key = int(m.findtext("attributes/key/fifths") or key)
        sign = m.findtext("attributes/clef/sign")
        clef = {"G": "treble", "F": "bass", "C": "alto/tenor"}.get(sign, clef)
        if m.find("attributes/time") is not None:
            time = f"{m.findtext('attributes/time/beats')}/{m.findtext('attributes/time/beat-type')}"
        words, sig = [], []
        for n in m.findall("note"):
            if n.find("cue") is not None:
                continue
            word = spell(n) + LETTER.get(n.findtext("type") or "", "q" if n.find("rest") is None else "w")
            word += "." * len(n.findall("dot"))
            if n.find("chord") is not None:
                word = "+" + word
            if n.find("time-modification") is not None:
                word += "(%s-tuplet)" % n.findtext("time-modification/actual-notes")
            if any(t.get("type") == "start" for t in n.findall("tie")):
                word += "~"
            if n.find("grace") is not None:
                word = "grace:" + word
            else:
                sig.append((spell(n), word.rstrip("~").lstrip("+")))
            words.append(word)
        if sig and all(s[0] == "r" for s in sig) and len(sig) == 1:
            sig = [("R", "R")]
        flagged = any(n.get("color") for n in m.findall("note")) or any(
            (w.text or "").startswith("check:") and ("bar" in w.text or "tuplet" in w.text) for w in m.iter("words"))
        out.append({"sig": tuple(sig), "text": " ".join(words) or "(empty)", "key": key, "clef": clef,
                    "time": time, "flagged": flagged})
    return out


def differences(name):
    a = read(load_baseline(name).getroot().find("part").findall("measure"))
    other = OUT / "ttl=30d/homr" / name / f"{name}.musicxml"
    b = read(measures(other))
    matcher = difflib.SequenceMatcher(None, [x["sig"] for x in a], [x["sig"] for x in b], autojunk=False)
    pick, extra = {}, []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "insert":
            extra.append({"after_bar": i1, "homr": [b[j]["text"] for j in range(j1, j2)]})
            continue
        for i in range(i1, i2):
            if i2 - i1 == j2 - j1:
                pick[i] = b[j1 + i - i1]["text"]
            else:                      # unequal stretch: the most similar bar of the other reading
                near = max(range(j1, j2), default=None, key=lambda j: difflib.SequenceMatcher(
                    None, a[i]["sig"], b[j]["sig"]).ratio())
                pick[i] = b[near]["text"] if near is not None else None
    for i, bar in enumerate(a):
        if bar["flagged"]:
            pick.setdefault(i, None)
    items = [{"bar": i, "a": a[i]["text"], "b": pick[i], "key": a[i]["key"], "clef": a[i]["clef"],
              "time": a[i]["time"], "flagged": a[i]["flagged"]} for i in sorted(pick)]
    return items, extra, len(a)


def projection_staves(img, page):
    """Staves of these clean, level scans: five evenly spaced rows that are dark over much of the width.

    The review tool's detector misses a staff here and there (a beam taken for a line).
    """
    dark = np.asarray(img) < 190
    thick = dark | np.roll(dark, 1, 0) | np.roll(dark, -1, 0)
    rows = np.flatnonzero(thick.mean(axis=1) > 0.55)
    lines = [float(g.mean()) for g in np.split(rows, np.flatnonzero(np.diff(rows) > 1) + 1) if len(g)]
    found, i = [], 0
    while i + 4 < len(lines):
        gaps = np.diff(lines[i:i + 5])
        if gaps.max() < 1.35 * gaps.min() and 8 < gaps.mean() < 22:
            top, bottom = round(lines[i]), round(lines[i + 4])
            extent = images._extent(dark, top, bottom)
            if extent:
                found.append(images.Staff(page, top, bottom, *extent))
            i += 5
        else:
            i += 1
    return images._with_margins(found, img.height)


_DETECTOR = images._find_staves


def open_session(name, mscz):
    """A review session on ``mscz``; tries the second staff detector when the first loses a line."""
    source = ROOT / f"input/bach-suites-rescan/{name}.pdf"
    marked = work(name).parent / f"{name}.review-source.pdf"          # ossia staves painted out
    source = marked if marked.exists() else source
    for detector in (_DETECTOR, projection_staves):
        images._find_staves = detector
        try:
            return Session(mscz, source, MUSESCORE)
        except ReviewError as exc:
            failed = exc
    raise failed


def make_items(name):
    w = work(name)
    w.mkdir(parents=True, exist_ok=True)
    items, extra, total = differences(name)
    mscz = w / f"{name}.mscz"
    shutil.copy2(OUT / name / f"{name}.mscz", mscz)
    # smaller staves in this working copy, so that no line wraps and lines match the print one to one
    mscx, style = read_mscz(mscz)
    style = re.sub(r"<(spatium|Spatium)>[^<]*<", r"<\1>1.15<", style)
    write_mscz(mscz, mscz.with_suffix(".tmp"), mscx, style)
    mscz.with_suffix(".tmp").replace(mscz)
    session = open_session(name, mscz)
    where = {bar: (n, c) for n, line in enumerate(session.lines) for c, cell in enumerate(line["cells"]) for bar in cell}
    assert len(where) == total, (len(where), total)
    placed = []
    for n, item in enumerate(items, 1):
        item["id"] = n
        line, c = where[item["bar"]]
        edges = session.lines[line]["staves"][0]["edges"]
        image = Image.open(session.image_path(line, 0, "orig")).convert("L")
        left, right = edges[min(c, len(edges) - 2)], edges[min(c + 1, len(edges) - 1)]
        left = left - (110 if c == 0 else 18)
        crop = image.crop((max(0, left), 0, min(image.width, right + 18), image.height))
        if crop.width > CELL - 20:
            crop = crop.resize((CELL - 20, crop.height * (CELL - 20) // crop.width), Image.LANCZOS)
        crop.save(w / f"bar{n:03d}.png")
        item["line"], item["page"] = line, session.lines[line]["page"] + 1
        placed.append(item)
    for start in range(0, len(placed), PER_BATCH):
        batch = placed[start:start + PER_BATCH]
        crops = [Image.open(w / f"bar{d['id']:03d}.png") for d in batch]
        heights = [max(c.height for c in crops[i:i + COLS]) + 30 for i in range(0, len(crops), COLS)]
        sheet = Image.new("L", (CELL * COLS, sum(heights)), 255)
        draw, y = ImageDraw.Draw(sheet), 0
        for row, h in enumerate(heights):
            for col, (d, crop) in enumerate(zip(batch[row * COLS:(row + 1) * COLS], crops[row * COLS:(row + 1) * COLS])):
                draw.text((col * CELL + 6, y + 4), f"#{d['id']}", fill=0, font_size=22)
                sheet.paste(crop, (col * CELL + 10, y + 28))
                draw.line((col * CELL, y, col * CELL, y + h), fill=0, width=3)
            y += h
            draw.line((0, y - 2, CELL * COLS, y - 2), fill=0, width=3)
        sheet.save(w / f"batch{start // PER_BATCH:02d}.png")
    (w / "items.json").write_text(json.dumps({"items": placed, "homr_only": extra, "bars": total}, indent=1))
    print(f"{name}: {len(placed)} of {total} bars to settle, {-(-len(placed) // PER_BATCH)} batches")


PROMPT = """Read the image {image} in this folder (use the Read tool once). It shows single bars cut from a printed
horn part (Bach cello suites, transcribed for horn), each labelled #n. Judge only the middle staff, between its bar
lines; bits of neighbouring staves or bars may show at the edges (the first bar of a line also shows the clef).

For each bar there is a program's reading A and sometimes another's reading B. Notation, notes separated by spaces:
written pitch with the accidental as it SOUNDS in the key and bar (F#4 = F sharp in octave 4, C4 = middle C; with a
one-sharp key signature every unmarked F is F#), then the value w h q e s(16th) t(32nd) x(64th), '.' dotted,
'(3-tuplet)' for tuplet members, '~' tied to the next note, '+' prefix = stacked on the previous note (chord),
'grace:' prefix for small grace notes, r = rest. Example: "grace:D4e G3s D4s B4e. (nothing else)".
Key signature is given in fifths (+ sharps, - flats). Ignore slurs, dynamics, articulations, breath marks, trills
and fingerings. A note in parentheses is an ordinary note: include it.
Special signs: two thick slanted strokes "//" after half a bar mean "play the first half of the bar again" - write
those repeated notes out in full. A bar holding only a slash-and-dots sign repeats the previous bar: use verdict "R".

{items}

Answer with one JSON object and nothing else:
{{"changes": [], "answers": [{{"id": 1, "v": "A"}}, {{"id": 2, "v": "N", "fix": "G3s D4s ..."}}]}}
v is "A" (A matches the print exactly), "B" (B matches exactly), "N" (neither: put the complete correct bar in "fix"),
"R" (repeat-previous-bar sign), or "?" (cannot tell, or the crop does not show one whole bar of this music).
Check every note, and count that the values fill the time signature. Do not guess: use "?" when unsure."""


def ask(name, model, b, data):
    w, store = work(name), KEPT / name / f"batch{b:02d}.json"
    if store.exists():
        return 0.0
    batch = data["items"][b * PER_BATCH:(b + 1) * PER_BATCH]
    listing = "\n".join(
        f"#{d['id']} ({d['clef']} clef, key {d['key']:+d}, time {d['time']})\n  A: {d['a']}"
        + (f"\n  B: {d['b']}" if d["b"] else "") for d in batch)
    prompt = PROMPT.format(image=f"batch{b:02d}.png", items=listing)
    done = subprocess.run(claude.command(claude.find_claude(), model), input=prompt, cwd=w, capture_output=True,
                          text=True, timeout=900)
    (w / f"batch{b:02d}.log").write_text(done.stdout + done.stderr)
    out = json.loads(done.stdout)
    if out.get("is_error"):
        raise RuntimeError(str(out.get("result"))[:300])
    reply = json.loads(
        out["result"][out["result"].index("{"):out["result"].rindex("}") + 1])
    wanted = {d["id"] for d in batch}
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps([a for a in reply["answers"] if a.get("id") in wanted], indent=1))
    return out.get("total_cost_usd", 0)


def run(name, model="sonnet"):
    data = json.loads((work(name) / "items.json").read_text())
    total = -(-len(data["items"]) // PER_BATCH)
    cost, failed = 0.0, []
    with concurrent.futures.ThreadPoolExecutor(3) as pool:
        jobs = {pool.submit(ask, name, model, b, data): b for b in range(total)}
        for job in concurrent.futures.as_completed(jobs):
            try:
                cost += job.result()
            except Exception as exc:  # noqa: BLE001
                failed.append((jobs[job], str(exc)[:200]))
    print(f"{name}: {total} batches, ${cost:.2f} (list price), failed: {failed}")


RETRY = """Read the image {image} in this folder (use the Read tool once): one whole printed line of a horn part
(Bach cello suites). It holds {count} bars, here numbered {first} to {last} from the left. {clef} clef, key {key:+d},
time {time}. A program read the bars as follows (it may be wrong, and unreadable bars show as a rest):
{readings}

Give the correct reading of bars {wanted} only, in the same notation: written pitch with the accidental as it SOUNDS
in key and bar (F#4; C4 = middle C), value w h q e s t x, '.' dotted, '(3-tuplet)', '~' tie, '+' chord, 'grace:'
prefix, r = rest. A note in parentheses counts. Two thick slanted strokes "//" after half a bar mean the first half
is played again: write those notes out. A slash-and-dots sign alone in a bar repeats the previous bar: use "R".
Answer with one JSON object and nothing else:
{{"changes": [], "answers": [{{"id": <bar number>, "v": "N", "fix": "..."}}]}}
Use "v": "?" for a bar you cannot read with confidence."""


def retry(name, model="sonnet"):
    """Settle what is still "?" from the whole line: a single-bar crop can be cut in the wrong place."""
    w = work(name)
    data = json.loads((w / "items.json").read_text())
    store = KEPT / name / "batch_retry.json"
    if store.exists():
        return
    answers = {}
    for path in sorted((KEPT / name).glob("batch[0-9]*.json")):
        answers.update({a["id"]: a for a in json.loads(path.read_text())})
    open_items = [d for d in data["items"] if answers.get(d["id"], {}).get("v", "?") == "?"]
    bars = read(measures(baseline(name)))
    session = open_session(name, w / f"{name}.mscz")
    lines = sorted({d["line"] for d in open_items})

    def one(n):
        numbers = [c[0] for c in session.lines[n]["cells"]]
        image = f"retry{n:03d}.png"
        shutil.copy2(session.image_path(n, 0, "orig"), w / image)
        wanted = {d["bar"] + 1: d for d in open_items if d["line"] == n}
        first = bars[numbers[0]]
        prompt = RETRY.format(image=image, count=len(numbers), first=numbers[0] + 1, last=numbers[-1] + 1,
                              clef=first["clef"], key=first["key"], time=first["time"],
                              readings="\n".join(f"  bar {i + 1}: {bars[i]['text']}" for i in numbers),
                              wanted=", ".join(map(str, wanted)))
        done = subprocess.run(claude.command(claude.find_claude(), model), input=prompt, cwd=w,
                              capture_output=True, text=True, timeout=900)
        out = json.loads(done.stdout)
        text = out["result"]
        reply = json.loads(text[text.index("{"):text.rindex("}") + 1])
        found = [dict(a, id=wanted[a["id"]]["id"]) for a in reply["answers"] if a.get("id") in wanted]
        return found, out.get("total_cost_usd", 0)

    results, cost = [], 0.0
    with concurrent.futures.ThreadPoolExecutor(3) as pool:
        for job in [pool.submit(one, n) for n in lines]:
            try:
                found, c = job.result()
                results += found
                cost += c
            except Exception as exc:  # noqa: BLE001
                print("retry failed:", str(exc)[:200])
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps(results, indent=1))
    print(f"{name}: retried {len(open_items)} bars on {len(lines)} lines, ${cost:.2f} (list price)")


# ---------------------------------------------------------------- applying

def parse(text):
    notes = []
    text = re.sub(r"\b([A-G](?:bb|b|#|x)?\d)\+([A-G](?:bb|b|#|x)?\d)([whqestx]\.*)", r"\1\3 +\2\3", text)
    for word in text.split():
        m = TOKEN.match(word)
        if not m:
            return None
        grace, chord, pitch, value, dots, tuplet, tie = m.groups()
        notes.append({"grace": bool(grace), "chord": bool(chord), "pitch": pitch, "value": value,
                      "dots": len(dots), "tuplet": int(tuplet) if tuplet else 0, "tie": bool(tie)})
    return notes


def quarters(note):
    if note["grace"] or note["chord"]:
        return Fraction(0)
    base = TYPES[note["value"]][1]
    total = base * (2 - Fraction(1, 2 ** note["dots"]))
    if note["tuplet"]:
        normal = {3: 2, 2: 3, 5: 4, 6: 4, 7: 4}[note["tuplet"]]
        total *= Fraction(normal, note["tuplet"])
    return total


def bar_length(time):
    beats, unit = time.split("/")
    return Fraction(int(beats) * 4, int(unit))


def key_alters(fifths):
    order = "FCGDAEB" if fifths > 0 else "BEADGCF"
    return {s: (1 if fifths > 0 else -1) for s in order[:abs(fifths)]}


def build(old, note, divisions):
    """A <note> for ``note``, keeping slurs and other marks of ``old`` (the note it replaces)."""
    el = ET.Element("note")
    if note["grace"]:
        ET.SubElement(el, "grace", {} if old is None or old.find("grace") is None else old.find("grace").attrib)
    if note["chord"]:
        ET.SubElement(el, "chord")
    if note["pitch"] == "r":
        ET.SubElement(el, "rest")
    else:
        step, mark, octave = note["pitch"][0], note["pitch"][1:-1], note["pitch"][-1]
        p = ET.SubElement(el, "pitch")
        ET.SubElement(p, "step").text = step
        alter = {v: k for k, v in ALTER.items()}[mark]
        if alter:
            ET.SubElement(p, "alter").text = str(alter)
        ET.SubElement(p, "octave").text = octave
    if not note["grace"]:
        duration = quarters(dict(note, chord=False)) * divisions
        assert duration.denominator == 1, duration
        ET.SubElement(el, "duration").text = str(duration)
    if note["tie"]:
        ET.SubElement(el, "tie", type="start")
    ET.SubElement(el, "voice").text = "1"
    ET.SubElement(el, "type").text = TYPES[note["value"]][0]
    for _ in range(note["dots"]):
        ET.SubElement(el, "dot")
    if note["tuplet"]:
        t = ET.SubElement(el, "time-modification")
        ET.SubElement(t, "actual-notes").text = str(note["tuplet"])
        ET.SubElement(t, "normal-notes").text = str({3: 2, 2: 3, 5: 4, 6: 4, 7: 4}[note["tuplet"]])
    marks = [] if old is None else [c for n in old.findall("notations") for c in n
                                    if c.tag not in ("tuplet", "tied")]
    if marks or note["tie"]:
        holder = ET.SubElement(el, "notations")
        if note["tie"]:
            ET.SubElement(holder, "tied", type="start")
        holder.extend(copy.deepcopy(marks))
    return el


def add_beams(built, notes, olds, time):
    """Keep the old beaming when the values are unchanged, else beam by the beat."""
    def values(elements):
        return [(e.findtext("type"), len(e.findall("dot")), e.find("grace") is not None,
                 e.find("rest") is not None) for e in elements]
    if values(built) == values(olds):
        groups = None
    else:
        beats, unit = (int(x) for x in time.split("/"))
        beat = Fraction(3, 2) if unit == 8 and beats % 3 == 0 else Fraction(1)
        groups, run, at, current = [], [], Fraction(0), None
        for j, n in enumerate(notes):
            if n["grace"] or n["chord"]:
                continue
            slot = at // beat
            if n["value"] not in "estx" or n["pitch"] == "r" or slot != current:
                groups.append(run)
                run, current = [], slot
            if n["value"] in "estx" and n["pitch"] != "r":
                run.append(j)
            at += quarters(n)
        groups.append(run)
    marks = {}
    if groups is None:
        for j, old in enumerate(olds):
            marks[j] = [(b.get("number", "1"), b.text) for b in old.findall("beam")]
    else:
        flags = {"e": 1, "s": 2, "t": 3, "x": 4}
        for group in (g for g in groups if len(g) > 1):
            for level in range(1, 5):
                for k, j in enumerate(group):
                    if flags[notes[j]["value"]] < level:
                        continue
                    left = k > 0 and flags[notes[group[k - 1]]["value"]] >= level
                    right = k + 1 < len(group) and flags[notes[group[k + 1]]["value"]] >= level
                    kind = ("continue" if left and right else "begin" if right else "end" if left
                            else "forward hook" if k == 0 else "backward hook")
                    marks.setdefault(j, []).append((str(level), kind))
    for j, found in marks.items():
        el = built[j]
        at = next((i for i, c in enumerate(el) if c.tag == "notations"), len(el))
        for offset, (number, kind) in enumerate(found):
            beam = ET.Element("beam", number=number)
            beam.text = kind
            el.insert(at + offset, beam)


def rewrite(measure, notes, divisions, fifths, time):
    """Replace the notes of ``measure``, leaving directions and attributes in place."""
    children = list(measure)
    olds = [c for c in children if c.tag == "note" and c.find("cue") is None]
    pairs = {}
    matcher = difflib.SequenceMatcher(None, [spell(o) for o in olds], [n["pitch"] for n in notes], autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            for i, j in zip(range(i1, i2), range(j1, j2)):
                pairs[j] = i
    built = [build(olds[pairs[j]] if j in pairs else None, n, divisions) for j, n in enumerate(notes)]
    # incoming ties
    for old in olds[:1]:
        if built and any(t.get("type") == "stop" for t in old.findall("tie")):
            ET.SubElement(built[0], "tie", type="stop")
    # tuplet brackets
    run_start, filled = None, Fraction(0)
    for j, n in enumerate(notes):
        if not n["tuplet"] or n["grace"] or n["chord"]:
            continue
        if run_start is None:
            run_start, filled, unit = j, Fraction(0), TYPES[n["value"]][1]
        filled += TYPES[n["value"]][1] * (2 - Fraction(1, 2 ** n["dots"]))
        if filled >= unit * n["tuplet"]:
            for index, kind in ((run_start, "start"), (j, "stop")):
                holder = built[index].find("notations")
                holder = ET.SubElement(built[index], "notations") if holder is None else holder
                ET.SubElement(holder, "tuplet", type=kind)
            run_start = None
    # printed accidentals
    state, base = {}, key_alters(fifths)
    for el in built:
        p = el.find("pitch")
        if p is None:
            continue
        step, spot = p.findtext("step"), (p.findtext("step"), p.findtext("octave"))
        alter = int(p.findtext("alter") or 0)
        if state.get(spot, base.get(step, 0)) != alter:
            mark = ET.Element("accidental")
            mark.text = ACCIDENTAL[alter]
            el.insert(list(el).index(el.find("type")) + 1 + len(el.findall("dot")), mark)
        state[spot] = alter
    add_beams(built, notes, olds, time)
    # directions keep their place relative to the note they preceded
    before = {}
    seen = 0
    for c in children:
        if c.tag == "note" and c in olds:
            seen += 1
        elif c.tag == "direction":
            before.setdefault(seen, []).append(c)
    inverse = {i: j for j, i in pairs.items()}
    placed = {}
    for index, items in before.items():
        if index == 0:
            j = 0
        else:
            j = next((inverse[i] for i in range(index, len(olds)) if i in inverse),
                     min(len(built), round(index * len(built) / max(1, len(olds)))))
        placed.setdefault(j, []).extend(items)
    head = [c for c in children if c.tag in ("print", "attributes") or (c.tag == "barline" and c.get("location") == "left")]
    tail = [c for c in children if c.tag == "barline" and c.get("location") != "left"]
    for c in list(measure):
        measure.remove(c)
    measure.extend(head)
    for j, el in enumerate(built):
        measure.extend(placed.pop(j, []))
        measure.append(el)
    for rest in placed.values():
        measure.extend(rest)
    measure.extend(tail)


def apply(name):
    data = json.loads((work(name) / "items.json").read_text())
    answers = {}
    for path in sorted((KEPT / name).glob("batch*.json")):          # batch_retry sorts last
        answers.update({a["id"]: a for a in json.loads(path.read_text())})
    tree = load_baseline(name)
    bars = tree.getroot().find("part").findall("measure")
    effective = read(bars)
    divisions, per_bar = 1, []
    for m in bars:
        divisions = int(m.findtext("attributes/divisions") or divisions)
        per_bar.append(divisions)
    changed, kept, left = [], 0, []
    path = Path(__file__).parent / "structure.json"
    manual = json.loads(path.read_text()).get(name, {}).get("manual", {}) if path.exists() else {}
    items = [d for d in data["items"] if str(d["bar"] + 1) not in manual]
    for number, text in manual.items():                 # bars read from the print by hand
        i = int(number) - 1
        items.append({"bar": i, "id": f"manual{number}", "a": effective[i]["text"], "b": None, "page": 0,
                      "flagged": False})
        answers[f"manual{number}"] = {"v": "N", "fix": text}
    for item in sorted(items, key=lambda d: d["bar"]):
        i, a = item["bar"], answers.get(item["id"], {"v": "?"})
        label = f"bar {i + 1} (page {item['page']})"
        if a["v"] == "A":
            kept += 1
            if item["flagged"]:
                left.append(f"{label}: flagged by the pipeline, though the reading was judged right")
            continue
        if a["v"] == "R" and i:
            text = read(bars[:i])[-1]["text"]
        else:
            text = {"B": item["b"], "N": a.get("fix")}.get(a["v"])
        notes = parse(text) if text and text != "(empty)" else None
        if not notes:
            left.append(f"{label}: not settled ({a['v']})")
            continue
        if i in triplet_bars(name):
            notes = tripletize(notes, bar_length(effective[i]["time"]))
        length, want = sum(map(quarters, notes), Fraction(0)), bar_length(effective[i]["time"])
        pickup = length < want and (i == 0 or length <= sum(map(quarters, parse(item["a"]) or []), Fraction(0)))
        if length != want and not pickup:
            left.append(f"{label}: proposed reading has {float(length):g} of {float(want):g} beats, not applied: {text}")
            continue
        # divisions fine enough for every value in the new bar
        need = per_bar[i]
        if any((quarters(dict(n, chord=False)) * need).denominator != 1 for n in notes if not n["grace"]):
            left.append(f"{label}: needs a finer time grid, not applied: {text}")
            continue
        try:
            rewrite(bars[i], notes, need, effective[i]["key"], effective[i]["time"])
        except (AssertionError, KeyError) as exc:
            left.append(f"{label}: could not write the bar ({exc!r}): {text}")
            continue
        for direction in bars[i].findall("direction"):
            if any(re.match(r"check: (bar|tuplet)", w.text or "") for w in direction.iter("words")):
                bars[i].remove(direction)
        changed.append({"bar": i + 1, "page": item["page"], "was": item["a"], "now": text})
    target = OUT / name / f"{name}.musicxml"
    header = target.read_text("utf-8").split("<score-partwise", 1)[0]
    target.write_text(header + ET.tostring(tree.getroot(), encoding="unicode"), "utf-8")
    (KEPT / name / "applied.json").write_text(json.dumps(
        {"changed": changed, "confirmed": kept, "left": left, "homr_only": data["homr_only"]}, indent=1))
    print(f"{name}: {len(changed)} bars changed, {kept} confirmed, {len(left)} left over")


if __name__ == "__main__":
    command, names = sys.argv[1], sys.argv[2:]
    for each in names:
        {"items": make_items, "run": run, "retry": retry, "apply": apply}[command](each)
