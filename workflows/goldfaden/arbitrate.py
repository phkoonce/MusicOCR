"""Settle the bars where the Goldfaden score and the part books were read differently, cheaply.

Both are OMR readings of the same music. Where they disagree, a model looks at that one bar of the
printed score and says which reading matches. Many bars share one image and one call.

    .venv/bin/python workflows/goldfaden/arbitrate.py items                 # free: list and crop
    .venv/bin/python workflows/goldfaden/arbitrate.py run --model haiku [--batches 0,1]
    .venv/bin/python workflows/goldfaden/arbitrate.py apply --model haiku [--dry-run]
"""
from __future__ import annotations

import argparse
import collections
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

from PIL import Image, ImageDraw  # noqa: E402

from musicocr.review import claude, score  # noqa: E402
from musicocr.scoreops.layout import read_mscz, without_rest_placeholders, write_mscz  # noqa: E402

SCORE = ROOT / "output/goldfaden-score/Goldfaden_score/Goldfaden_score"
PART = ROOT / "output/goldfaden-flatbed/Goldfaden_h{k}/Goldfaden_h{k}"
WORK = ROOT / "output/goldfaden-score/ttl=30d/arbitration"
KEPT = Path(__file__).resolve().parent            # answers cost quota: keep them where nothing expires
MUSESCORE = "/Applications/MuseScore 4.app/Contents/MacOS/mscore"
PER_BATCH = 24
CELL = 430
SIZES = {"whole": "w", "half": "h", "quarter": "q", "eighth": "e", "16th": "s", "32nd": "t", "breve": "br"}


def read_part(part: ET.Element):
    """Per bar: ``(compare key, compact notation, key signature)``."""
    out, divisions, key, clef = [], 1, 0, "treble"
    for m in part.findall("measure"):
        divisions = int(m.findtext("attributes/divisions") or divisions)
        key = int(m.findtext("attributes/key/fifths") or key)
        sign = m.findtext("attributes/clef/sign")
        clef = {"G": "treble", "F": "bass"}.get(sign, clef)
        sig, words = [], []
        for n in m.findall("note"):
            if n.find("cue") is not None:
                continue
            rest = n.find("rest") is not None
            pitch = "r" if rest else (n.findtext("pitch/step") + {"-2": "bb", "-1": "b", "1": "#", "2": "x"}.get(
                str(int(float(n.findtext("pitch/alter") or 0))), "") + n.findtext("pitch/octave"))
            word = pitch + SIZES.get(n.findtext("type") or "", "") + "." * len(n.findall("dot"))
            if n.find("grace") is not None:
                words.append("grace:" + word)
                continue
            if n.find("chord") is not None:
                word = "+" + word
            if n.find("time-modification") is not None:
                word += "(%s-tuplet)" % n.findtext("time-modification/actual-notes")
            if any(t.get("type") == "start" for t in n.findall("tie")):
                word += "~"
            words.append(word)
            sig.append((pitch, str(Fraction(int(n.findtext("duration") or 0), divisions)),
                        n.find("chord") is not None))
        if sig and all(s[0] == "r" for s in sig):
            sig, words = [("R",)], ["rest bar"]
        out.append((tuple(sig), " ".join(words) or "(empty)", key, clef))
    return out


def collapse(bars):
    """Indices of ``bars`` with each run of rest bars counted once (a part prints a multi-bar rest)."""
    keep = []
    for i, bar in enumerate(bars):
        if not (bar[0] == (("R",),) and keep and bars[keep[-1]][0] == (("R",),)):
            keep.append(i)
    return keep


def differences():
    """``[{id, horn, bar, part_bar, a, b, key}]`` (bars 0-based) and the unpaired stretches."""
    parts = ET.parse(f"{SCORE}.musicxml").getroot().findall("part")
    items, unpaired = [], []
    for k in range(1, 5):
        a = read_part(parts[k - 1])
        b = read_part(ET.parse(str(PART).format(k=k) + ".musicxml").getroot().find("part"))
        ia, ib = collapse(a), collapse(b)
        ops = difflib.SequenceMatcher(None, [a[i][0] for i in ia], [b[j][0] for j in ib],
                                      autojunk=False).get_opcodes()
        for tag, i1, i2, j1, j2 in ops:
            if tag == "equal":
                continue
            if i2 - i1 != j2 - j1:
                unpaired.append({"horn": k, "bars": [ia[i] + 1 for i in range(i1, i2)],
                                 "part_bars": [ib[j] + 1 for j in range(j1, j2)]})
                continue
            for i, j in zip(range(i1, i2), range(j1, j2)):
                x, y = a[ia[i]], b[ib[j]]
                items.append({"horn": k, "bar": ia[i], "part_bar": ib[j], "a": x[1], "b": y[1], "key": x[2], "clef": x[3]})
    items.sort(key=lambda d: (d["bar"], d["horn"]))
    for n, item in enumerate(items, 1):
        item["id"] = n
    return items, unpaired


def make_items():
    from musicocr.review.session import Session

    WORK.mkdir(parents=True, exist_ok=True)
    items, unpaired = differences()
    copy = WORK / "Goldfaden_score.mscz"         # a copy: the review page may be using the real one's cache
    shutil.copy2(f"{SCORE}.mscz", copy)
    session = Session(copy, ROOT / "input/goldfaden-score/Goldfaden_score.review-source.pdf", MUSESCORE)
    where = {}
    for n, line in enumerate(session.lines):
        for c, cell in enumerate(line["cells"]):
            for bar in cell:
                where[bar] = (n, c)
    placed = []
    for item in items:
        if item["bar"] not in where:
            continue
        n, c = where[item["bar"]]
        part = session.lines[n]["staves"][item["horn"] - 1]
        image = Image.open(session.image_path(n, item["horn"] - 1, "orig")).convert("L")
        edges = part["edges"]                    # bar line columns: left end of the line first
        left, right = edges[min(c, len(edges) - 2)], edges[min(c + 1, len(edges) - 1)]
        crop = image.crop((max(0, left - 25), 0, min(image.width, right + 25), image.height))
        if crop.width > CELL - 20:
            crop = crop.resize((CELL - 20, crop.height * (CELL - 20) // crop.width))
        crop.save(WORK / f"bar{item['id']:03d}.png")
        item["page"] = session.lines[n]["page"] + 1
        placed.append(item)
    for start in range(0, len(placed), PER_BATCH):
        batch = placed[start:start + PER_BATCH]
        crops = [Image.open(WORK / f"bar{d['id']:03d}.png") for d in batch]
        heights = [max(c.height for c in crops[i:i + 4]) + 30 for i in range(0, len(crops), 4)]
        sheet = Image.new("L", (CELL * 4, sum(heights)), 255)
        draw, y = ImageDraw.Draw(sheet), 0
        for row, h in enumerate(heights):
            for col, (d, crop) in enumerate(zip(batch[row * 4:row * 4 + 4], crops[row * 4:row * 4 + 4])):
                draw.text((col * CELL + 6, y + 6), f"#{d['id']}", fill=0, font_size=22)
                sheet.paste(crop, (col * CELL + 10, y + 28))
                draw.line((col * CELL, y, col * CELL, y + h), fill=120)
            y += h
            draw.line((0, y - 1, CELL * 4, y - 1), fill=120)
        sheet.save(WORK / f"batch{start // PER_BATCH:02d}.png")
    (WORK / "items.json").write_text(json.dumps({"items": placed, "unpaired": unpaired}, indent=1))
    print(f"{len(placed)} differing bars in {-(-len(placed) // PER_BATCH)} batches; "
          f"{len(unpaired)} stretches could not be paired")


PROMPT = """Read the image {image} in this folder (use the Read tool once). It shows single bars cut from a printed
horn quartet score, each labelled #n, one staff each (the clef in force is given below). Parts of the
neighbouring staves or bars may show at the edges: judge only the middle staff, between its bar lines.

Two programs read each bar differently. Notation: written pitch with accidental as sounding in the key
(F#4 = F sharp in octave 4, C4 = middle C), then the value: w h q e s(16th) t(32nd), '.' dotted, '~' tied to the
next note, '+' stacked on the previous note, r = rest. The key signature in fifths is given (+ sharps, - flats).
Ignore slurs, dynamics and articulations.

{items}

For every #n decide which reading matches the print. Answer with one JSON object and nothing else:
{{"changes": [], "answers": [{{"id": 1, "v": "A"}}, ...]}}
v is "A", "B", "N" (neither: add "fix" with the correct reading in the same notation) or "?" (cannot tell, or the
crop does not show this bar). Do not guess: use "?" when unsure."""


def run(model: str, batches):
    data = json.loads((WORK / "items.json").read_text())
    store = KEPT / f"answers-{model}.json"
    answers = json.loads(store.read_text()) if store.exists() else {}
    tool = claude.find_claude()
    total = -(-len(data["items"]) // PER_BATCH)
    for b in batches if batches is not None else range(total):
        batch = data["items"][b * PER_BATCH:(b + 1) * PER_BATCH]
        if all(str(d["id"]) in answers for d in batch):
            continue
        listing = "\n".join(f"#{d['id']} ({d['clef']} clef, key {d['key']:+d})\n  A: {d['a']}\n  B: {d['b']}" for d in batch)
        prompt = PROMPT.format(image=f"batch{b:02d}.png", items=listing)
        done = subprocess.run(claude.command(tool, model), input=prompt, cwd=WORK, capture_output=True,
                              text=True, timeout=900)
        (WORK / f"batch{b:02d}.{model}.log").write_text(done.stdout + done.stderr)
        out = json.loads(done.stdout)
        if out.get("is_error"):
            print(f"batch {b}: {str(out.get('result'))[:300]}")
            return 1
        text = out["result"]
        reply = json.loads(text[text.index("{"):text.rindex("}") + 1])
        wanted = {d["id"] for d in batch}
        for a in reply["answers"]:
            if a.get("id") in wanted:
                answers[str(a["id"])] = {"v": a.get("v", "?"), "fix": a.get("fix", "")}
        store.write_text(json.dumps(answers, indent=1))
        print(f"batch {b}: {len(reply['answers'])} answers, ${out.get('total_cost_usd', 0):.3f} (list price)")
    return 0


# ---------------------------------------------------------------- applying

_VOICE = re.compile(r"<voice>.*?</voice>", re.S)
_FIRST = re.compile(r"[ \t]*<(Chord|Rest|Tuplet|Beam)\b")


_NOTE = re.compile(r"<Note>.*?</Note>", re.S)
_PITCH = re.compile(r"<pitch>-?\d+</pitch>\s*<tpc>-?\d+</tpc>(\s*<tpc2>-?\d+</tpc2>)?")
_ACCIDENTAL = re.compile(r"[ \t]*<Accidental>.*?</Accidental>\n?", re.S)


def _shape(measure: str):
    """Rhythm of a one-voice bar: every chord and rest with its value and number of notes."""
    root = ET.fromstring(measure)
    return [(e.tag, e.findtext("durationType"), e.findtext("dots"), len(e.findall("Note")),
             any(c.tag.startswith(("acciaccatura", "appoggiatura", "grace")) for c in e))
            for v in root.findall("voice") for e in v if e.tag in ("Chord", "Rest", "Tuplet", "endTuplet")]


def repitch(target: str, source: str):
    """``target`` with ``source``'s pitches, if only pitches differ: slurs and dynamics stay untouched."""
    if _shape(target) != _shape(source):
        return None
    new_notes = _NOTE.findall(source)

    def swap(match, it=iter(new_notes)):
        old, new = match.group(), next(it)
        a, b = _PITCH.search(old), _PITCH.search(new)
        if not a or not b or a.group().split() == b.group().split():
            return old
        old = old.replace(a.group(), b.group())
        old = _ACCIDENTAL.sub("", old)
        acc = _ACCIDENTAL.search(new)
        if acc:
            old = old.replace("<Note>", "<Note>" + score._EID.sub("", acc.group()).strip(), 1)
        return old

    return _NOTE.sub(swap, target)


def transplant(target: str, source: str, length: Fraction):
    """``target``'s bar with ``source``'s notes, or ``(None, why not)``."""
    same = repitch(target, source)
    if same is not None:
        if same == target:
            return None, "the files already agree here"
        why = score.check_replacement(target, same, length)
        return (None, why) if why else (same, None)
    tv, sv = _VOICE.findall(target), _VOICE.findall(source)
    if len(tv) != 1 or len(sv) != 1:
        return None, "more than one voice"
    for text in (tv[0], sv[0]):
        if re.search(r"<(?:measures|fractions)>-?\d", text) and "<measures>" in text:
            return None, "a slur or hairpin crosses the bar line"
    a, b = _FIRST.search(tv[0]), _FIRST.search(sv[0])
    if not a or not b:
        return None, "no notes to move"
    voice = tv[0][:a.start()] + score._EID.sub("", sv[0][b.start():])
    new = target.replace(tv[0], voice, 1)
    why = score.check_replacement(target, new, length)
    return (None, why) if why else (new, None)


def apply(model: str, dry_run: bool):
    data = json.loads((WORK / "items.json").read_text())
    answers = json.loads((KEPT / f"answers-{model}.json").read_text())
    files = {0: Path(f"{SCORE}.mscz")}
    files.update({k: Path(str(PART).format(k=k) + ".mscz") for k in range(1, 5)})
    docs = {}
    for k, path in files.items():
        mscx, style = read_mscz(path)
        docs[k] = {"mscx": mscx, "style": style, "spans": score.measure_spans(mscx), "new": {}}

    def bar_of(k, staff, i):
        d = docs[k]
        return score.get_measure(d["mscx"], d["spans"], (staff, i))

    done, left = {"score": 0, "part": 0}, []
    for item in data["items"]:
        a = answers.get(str(item["id"]))
        if not a:
            continue
        k, name = item["horn"], f"horn {item['horn']} score bar {item['bar'] + 1} (page {item['page']})"
        if a["v"] not in ("A", "B"):
            left.append(f"{name}: " + (f"neither reading, print has: {a['fix']}" if a["v"] == "N" else "unclear"))
            continue
        score_staff = sorted(docs[0]["spans"])[k - 1]
        part_staff = sorted(docs[k]["spans"])[0]
        if a["v"] == "B":          # the part was read right: fix the score
            doc, key, source, which = 0, (score_staff, item["bar"]), bar_of(k, part_staff, item["part_bar"]), "score"
        else:                      # the score was read right: fix the part
            doc, key, source, which = k, (part_staff, item["part_bar"]), bar_of(0, score_staff, item["bar"]), "part"
        d = docs[doc]
        around = [bar_of(doc, key[0], i) for i in (key[1] - 1, key[1] + 1) if 0 <= i < len(d["spans"][key[0]])]
        if "<Chord>" not in bar_of(doc, *key) and any("<Chord>" not in m for m in around):
            left.append(f"{name}: {which} has a rest here that may be part of a longer rest; check it")
            continue
        new, why = transplant(bar_of(doc, *key), source, score.time_at(d["mscx"], d["spans"], *key))
        if why:
            left.append(f"{name}: {which} is wrong but was not changed ({why})")
        else:
            d["new"][key] = new
            done[which] += 1
    if not dry_run:
        for k, d in docs.items():
            if not d["new"]:
                continue
            baseline = files[k].with_suffix(".pipeline-baseline.mscz")
            if not baseline.exists():
                shutil.copy2(files[k], baseline)
            out = score.replace_measures(d["mscx"], d["new"])
            tmp = files[k].with_suffix(".writing")
            write_mscz(files[k], tmp, without_rest_placeholders(out), d["style"])
            tmp.replace(files[k])
        (KEPT / "left-over.md").write_text("# Bars still to check by hand\n\n"
                                           + "".join(f"- {line}\n" for line in left))
    if dry_run:
        reasons = collections.Counter(re.sub(r"^.*?: ", "", line).split(", print has")[0] for line in left)
        print("\n".join(f"  {n:3d}  {r}" for r, n in reasons.most_common()))
    print(f"fixed {done['score']} score bars and {done['part']} part bars; {len(left)} left over")


# ---------------------------------------------------------------- second pass: rewrite single bars

REWRITE = """Read the images {images} in this folder (Read tool). Each shows single bars cut from a printed horn
quartet score, labelled #n; judge only the middle staff between its bar lines. For each task below, correct the
MuseScore 4 XML of that bar so that its pitches and rhythm match the print.
Rules: change only what is needed. Keep every <Spanner> (slur, tie, hairpin) element, <Dynamic>, <KeySig>,
<TimeSig>, <LayoutBreak> and text exactly as it is. <pitch> is the sounding MIDI pitch (a perfect fifth below
the written note) and <tpc> its spelling; follow how the existing notes encode them. The notes must fill the
bar's length. If you cannot tell, leave the task out.
Answer with one JSON object and nothing else:
{{"answers": [{{"task": 1, "measure": "<Measure>...</Measure>"}}]}}

{tasks}"""


def _marks(measure: str):
    return sorted(score.canonical(m) for m in re.findall(r"<(?:Spanner|Dynamic|KeySig|TimeSig|LayoutBreak)\b.*?"
                                                         r"</(?:Spanner|Dynamic|KeySig|TimeSig|LayoutBreak)>",
                                                         measure, re.S) if "<Spanner" not in m or "<next>" in m
                  or "<prev>" in m)


def rewrite(model: str, per_call: int = 8):
    data = json.loads((WORK / "items.json").read_text())
    answers = json.loads((KEPT / "answers-sonnet.json").read_text())
    files = {0: Path(f"{SCORE}.mscz")}
    files.update({k: Path(str(PART).format(k=k) + ".mscz") for k in range(1, 5)})
    docs = {}
    for k, path in files.items():
        mscx, style = read_mscz(path)
        docs[k] = {"mscx": mscx, "style": style, "spans": score.measure_spans(mscx), "new": {}}
    tasks = []
    for item in data["items"]:
        a = answers.get(str(item["id"]), {})
        k = item["horn"]
        places = {0: (sorted(docs[0]["spans"])[k - 1], item["bar"]), k: (sorted(docs[k]["spans"])[0], item["part_bar"])}
        bad = {"A": [(k, item["a"])], "B": [(0, item["b"])], "N": [(0, a.get("fix")), (k, a.get("fix"))]}
        for doc, right in bad.get(a.get("v"), []):
            d = docs[doc]
            key = places[doc]
            text = score.get_measure(d["mscx"], d["spans"], key)
            if "<Chord>" not in text or not right:
                continue
            other = 0 if doc else k          # skip what the first pass already made equal
            o = docs[other]
            twin = score.get_measure(o["mscx"], o["spans"], places[other])
            if a["v"] != "N" and _shape(twin) == _shape(text) and repitch(text, twin) == text:
                continue
            length = score.time_at(d["mscx"], d["spans"], *key)
            tasks.append({"doc": doc, "key": key, "item": item["id"], "right": right, "old": text,
                          "length": str(length)})
    print(len(tasks), "bars to rewrite")
    tool = claude.find_claude()
    done = []
    store = KEPT / "rewrites.json"
    for start in range(0, len(tasks), per_call):
        chunk = tasks[start:start + per_call]
        crops = [Image.open(WORK / f"bar{t_['item']:03d}.png") for t_ in chunk]
        sheet = Image.new("L", (CELL * 4, sum(max(c.height for c in crops[i:i + 4]) + 30
                                              for i in range(0, len(crops), 4))), 255)
        draw, y = ImageDraw.Draw(sheet), 0
        for i in range(0, len(crops), 4):
            h = max(c.height for c in crops[i:i + 4]) + 30
            for col, crop in enumerate(crops[i:i + 4]):
                draw.text((col * CELL + 6, y + 6), f"#{start + i + col + 1}", fill=0, font_size=22)
                sheet.paste(crop, (col * CELL + 10, y + 28))
            y += h
        name = f"rewrite{start // per_call:02d}.png"
        sheet.save(WORK / name)
        listing = "\n\n".join(
            f"Task {start + n + 1} (image bar #{start + n + 1}; bar length {t_['length']} of a whole note). Another "
            f"reading says the print shows: {t_['right']}\n{score._EID.sub('', t_['old'])}"
            for n, t_ in enumerate(chunk))
        proc = subprocess.run(claude.command(tool, model), input=REWRITE.format(images=name, tasks=listing),
                              cwd=WORK, capture_output=True, text=True, timeout=900)
        (WORK / f"rewrite{start // per_call:02d}.log").write_text(proc.stdout + proc.stderr)
        out = json.loads(proc.stdout)
        if out.get("is_error"):
            print(str(out.get("result"))[:300])
            break
        text = out["result"]
        for ans in json.loads(text[text.index("{"):text.rindex("}") + 1])["answers"]:
            n = int(ans.get("task", 0)) - 1 - start
            if not 0 <= n < len(chunk):
                continue
            task, new = chunk[n], ans.get("measure", "").strip()
            d = docs[task["doc"]]
            old = task["old"]
            if not score.is_measure(new) or score.check_replacement(old, new, Fraction(task["length"])) \
                    or _marks(old) != _marks(new):
                continue
            if score.canonical(score._EID.sub("", new)) != score.canonical(old):
                d["new"][task["key"]] = new
                done.append({"doc": task["doc"], "bar": task["key"][1] + 1, "staff": task["key"][0],
                             "item": task["item"]})
        print(f"call {start // per_call}: ${out.get('total_cost_usd', 0):.3f} (list price), {len(done)} changes so far")
    for k, d in docs.items():
        if d["new"]:
            tmp = files[k].with_suffix(".writing")
            write_mscz(files[k], tmp, score.replace_measures(d["mscx"], d["new"]), d["style"])
            tmp.replace(files[k])
    store.write_text(json.dumps({"changed": done, "tasks": len(tasks)}, indent=1))
    print(f"rewrote {len(done)} of {len(tasks)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["items", "run", "apply", "rewrite"])
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--batches")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.cmd == "items":
        make_items()
    elif args.cmd == "run":
        return run(args.model, None if args.batches is None else [int(x) for x in args.batches.split(",")])
    elif args.cmd == "rewrite":
        rewrite("sonnet")
    else:
        apply(args.model, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
