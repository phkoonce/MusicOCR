"""Find bars the transcription lost or both engines misread alike, line by line against the print.

    .venv/bin/python workflows/bach-suites/audit.py count NAME   # model counts the bars on every line
    .venv/bin/python workflows/bach-suites/audit.py read  NAME   # re-read the suspect lines in full
    .venv/bin/python workflows/bach-suites/audit.py apply NAME   # after correct.py apply, before finish.py

A change to an existing bar is taken only where that bar was already suspect or the second engine reads
it the same way; a bar is inserted only where the count and the full reading agree.
"""
from __future__ import annotations

import concurrent.futures
import difflib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))
from correct import (KEPT, OUT, ROOT, add_beams, bar_length, baseline, build, claude, load_baseline,  # noqa: E402
                     measures, open_session, parse, quarters, read, rewrite, tripletize, triplet_bars, work)

PER_SHEET = 7
COUNT = """Read the image {image} in this folder (use the Read tool once). It shows lines of a printed horn part, each
labelled L<n>. For each line count the bars: stretches of music separated by bar lines. A short pickup before the first
bar line (or after the last) counts as a bar; so does a bar holding only a repeat-bar sign (slash with dots). The clef
and key signature alone are not a bar. Bar lines are the thin full-height strokes, not note stems: look carefully.
Answer with one JSON object and nothing else: {{"changes": [], "counts": {{"<n>": <bars>, ...}}}}"""

READ = """Read the image {image} in this folder (use the Read tool once): one printed line of a horn part (Bach cello
suites). {clef} clef, key signature {key:+d} fifths, time {time}. A program read it as these bars, in order; it may
have misread notes, shown unreadable bars as rests, or lost or added whole bars:
{readings}

Transcribe EVERY bar of the printed line, left to right, from the image. Notation, notes separated by spaces: written
pitch with the accidental as it SOUNDS under the key signature and bar (F#4; C4 = middle C), then value w h q e s t x,
'.' dotted, '(3-tuplet)' (or 6-tuplet), '~' tie, '+' chord, 'grace:' prefix, r = rest. Notes in parentheses count.
A pickup before a bar line is its own bar. Slanted strokes standing for beats mean "repeat the previous beat" and
"//" after half a bar means "play the first half again": write those notes out. A bar holding only a slash-and-dots
sign repeats the previous bar: give "R" for it. A staff marked Ossia is not part of the line.
Answer with one JSON object and nothing else:
{{"changes": [], "bars": ["...", "..."], "unsure": [<positions from 1 of bars you are not confident of>]}}"""


def call(prompt, cwd, model="sonnet"):
    done = subprocess.run(claude.command(claude.find_claude(), model), input=prompt, cwd=cwd, capture_output=True,
                          text=True, timeout=900)
    out = json.loads(done.stdout)
    if out.get("is_error"):
        raise RuntimeError(str(out.get("result"))[:300])
    text = out["result"]
    return json.loads(text[text.index("{"):text.rindex("}") + 1]), out.get("total_cost_usd", 0)


def session_for(name):
    return open_session(name, work(name) / f"{name}.mscz")


def count(name):
    w, store = work(name), KEPT / name / "line_counts.json"
    if store.exists():
        return
    s = session_for(name)
    shots = [Image.open(s.image_path(n, 0, "orig")).convert("L") for n in range(len(s.lines))]

    def sheet(start):
        batch = shots[start:start + PER_SHEET]
        page = Image.new("L", (max(i.width for i in batch), sum(i.height + 34 for i in batch)), 255)
        draw, y = ImageDraw.Draw(page), 0
        for n, image in enumerate(batch, start):
            draw.line((0, y, page.width, y), fill=0, width=4)
            draw.text((8, y + 6), f"L{n}", fill=0, font_size=28)
            page.paste(image, (0, y + 34))
            y += image.height + 34
        page.save(w / f"count{start:03d}.png")
        return call(COUNT.format(image=f"count{start:03d}.png"), w)

    counts, cost = {}, 0.0
    with concurrent.futures.ThreadPoolExecutor(3) as pool:
        for reply, c in pool.map(sheet, range(0, len(shots), PER_SHEET)):
            counts.update({str(k).lstrip("L"): v for k, v in reply["counts"].items()})
            cost += c
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps(counts))
    print(f"{name}: counted {len(counts)} lines, ${cost:.2f} (list price)")


def spec_for(name):
    path = Path(__file__).parent / "structure.json"
    return json.loads(path.read_text()).get(name, {}) if path.exists() else {}


def misread_keys(name):
    """``{bar index: right key}`` for the stretches structure.json says were read under a wrong key."""
    spec, bars = spec_for(name), read(measures(baseline(name)))
    wanted, out, right = spec.get("keys", {}), {}, 0
    for i, bar in enumerate(bars):
        number = str(i + 1)
        if number in wanted and wanted[number] is not None:
            right = wanted[number]
        elif number not in wanted and (i == 0 or bar["key"] != bars[i - 1]["key"]):
            right = bar["key"]
        if bar["key"] != right:
            out[i] = right
    return out


def current(name):
    return read(measures(OUT / name / f"{name}.musicxml"))


def has_long_rest(bar):
    return any(w.startswith(("rw", "rh", "rq")) for w in bar["text"].split())


def suspects(name, s):
    bars, base = current(name), read(measures(baseline(name)))
    counts = json.loads((KEPT / name / "line_counts.json").read_text())
    applied = json.loads((KEPT / name / "applied.json").read_text())
    left = {int(line.split()[1]) - 1 for line in applied["left"] if line.startswith("bar ")}
    homr = read(measures(OUT / "ttl=30d/homr" / name / f"{name}.musicxml"))
    matcher = difflib.SequenceMatcher(None, [x["sig"] for x in base], [x["sig"] for x in homr], autojunk=False)
    more = set()
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if j2 - j1 > i2 - i1:
            more.update(range(max(0, i1 - 1), min(len(base), max(i2, i1 + 1))))
    keys = misread_keys(name)
    out = []
    for n, line in enumerate(s.lines):
        numbers = [c[0] for c in line["cells"]]
        if (counts.get(str(n)) != len(numbers) or any(i in left or i in more or i in keys or has_long_rest(bars[i])
                                                      for i in numbers)):
            out.append(n)
    return out


def read_lines(name):
    w, store = work(name), KEPT / name / "line_reads.json"
    s = session_for(name)
    done = json.loads(store.read_text()) if store.exists() else {}
    bars, keys = current(name), misread_keys(name)
    todo = [n for n in suspects(name, s) if str(n) not in done]

    def one(n):
        numbers = [c[0] for c in s.lines[n]["cells"]]
        image = f"line{n:03d}.png"
        Image.open(s.image_path(n, 0, "orig")).save(w / image)
        first = bars[numbers[0]]
        prompt = READ.format(image=image, clef=first["clef"], key=keys.get(numbers[0], first["key"]),
                             time=first["time"], readings="\n".join(f"  {bars[i]['text']}" for i in numbers))
        try:
            reply, c = call(prompt, w)
        except Exception as exc:  # noqa: BLE001
            return n, None, str(exc)[:200]
        return n, reply, c

    cost = 0.0
    store.parent.mkdir(parents=True, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(3) as pool:
        for n, reply, c in pool.map(one, todo):
            if reply is None:
                print("line", n, "failed:", c)
                continue
            cost += c
            done[str(n)] = {"bars": reply["bars"], "unsure": reply.get("unsure", []),
                            "numbers": [x[0] for x in s.lines[n]["cells"]]}
            store.write_text(json.dumps(done))
    print(f"{name}: read {len(todo)} of {len(s.lines)} lines, ${cost:.2f} (list price)")


def fits(text, time, triplets=False):
    notes = parse(text) if text else None
    if triplets:
        notes = tripletize(notes, bar_length(time))
    if not notes:
        return None, False
    return notes, sum(map(quarters, notes), Fraction(0)) == bar_length(time)


def new_measure(notes, divisions, key, time):
    if any((quarters(dict(n, chord=False)) * divisions).denominator != 1 for n in notes if not n["grace"]):
        return None
    measure = ET.Element("measure")
    built = [build(None, n, divisions) for n in notes]
    add_beams(built, notes, [], time)
    measure.extend(built)
    # accidentals are worked out the same way as for a rewritten bar
    rewrite(measure, notes, divisions, key, time)
    return measure


def signature(text):
    notes = parse(text) if text else None
    return tuple((n["pitch"], n["value"], n["dots"]) for n in notes if not n["grace"]) if notes else text


def apply(name):
    reads = json.loads((KEPT / name / "line_reads.json").read_text())
    counts = json.loads((KEPT / name / "line_counts.json").read_text())
    applied = json.loads((KEPT / name / "applied.json").read_text())
    left = {int(line.split()[1]) - 1 for line in applied["left"] if line.startswith("bar ")}
    target = OUT / name / f"{name}.musicxml"
    tree = ET.parse(target)
    part = tree.getroot().find("part")
    bars = part.findall("measure")
    info, keys = read(bars), misread_keys(name)
    homr = {signature(b["text"]) for b in read(measures(OUT / "ttl=30d/homr" / name / f"{name}.musicxml"))}
    divisions, per_bar = 1, []
    for n, m in enumerate(bars, 1):
        divisions = int(m.findtext("attributes/divisions") or divisions)
        per_bar.append(divisions)
        m.set("number", str(n))
    changes, inserts, doubts = [], [], []
    for line, got in sorted(reads.items(), key=lambda kv: int(kv[0])):
        numbers = got["numbers"]
        seen = []
        for text in got["bars"]:
            seen.append(seen[-1] if text == "R" and seen else
                        info[numbers[0] - 1]["text"] if text == "R" and numbers[0] else text)
        unsure = {u - 1 for u in got["unsure"] if isinstance(u, int)}
        mine = [info[i]["text"] for i in numbers]
        agreed = counts.get(line) == len(seen)
        # pair our bars with the model's by likeness
        a, b = [signature(t) for t in mine], [signature(t) for t in seen]

        def like(x, y):
            return difflib.SequenceMatcher(None, x, y, autojunk=False).ratio()

        n, m = len(a), len(b)
        best = [[0.0] * (m + 1) for _ in range(n + 1)]
        for i in range(n, -1, -1):
            for j in range(m, -1, -1):
                options = []
                if i < n and j < m:
                    options.append(like(a[i], b[j]) + 0.05 + best[i + 1][j + 1])
                if i < n:
                    options.append(best[i + 1][j])
                if j < m:
                    options.append(best[i][j + 1])
                best[i][j] = max(options, default=0.0)
        i = j = 0
        where = f"line {line} (bars {numbers[0] + 1}-{numbers[-1] + 1})"
        while i < n or j < m:
            # the same number of bars: they correspond in order, however badly one of ours was read
            if i < n and j < m and (n == m or best[i][j] == like(a[i], b[j]) + 0.05 + best[i + 1][j + 1]):
                index, text = numbers[i], seen[j]
                if a[i] != b[j]:
                    notes, full = fits(text, info[index]["time"], index in triplet_bars(name))
                    suspect = index in left or index in keys or has_long_rest(info[index])
                    short = notes and sum(map(quarters, notes), Fraction(0)) < bar_length(info[index]["time"])
                    pickup = short and sum(map(quarters, parse(mine[i]) or []), Fraction(0)) < bar_length(
                        info[index]["time"])
                    words = mine[i].split()
                    extra = len(words) - len(text.split())
                    padded = extra > 0 and words[:-extra] == text.split() and all(
                        w.startswith("r") for w in words[-extra:])         # only padding rests to drop
                    if notes and j not in unsure and (padded or (full or pickup) and (
                            suspect or signature(text) in homr)):
                        try:
                            rewrite(bars[index], notes, per_bar[index], keys.get(index, info[index]["key"]),
                                    info[index]["time"])
                            changes.append({"bar": index + 1, "was": mine[i], "now": text})
                        except (AssertionError, KeyError) as exc:
                            doubts.append(f"bar {index + 1}: could not write ({exc!r}): {text}")
                    else:
                        doubts.append(f"bar {index + 1}: kept '{mine[i]}', a second look read '{text}'")
                i, j = i + 1, j + 1
            elif j < m and best[i][j] == best[i][j + 1]:
                # a bar we do not have
                after = numbers[i - 1] if i else numbers[0] - 1
                ref = max(after, 0)
                notes = fits(seen[j], info[ref]["time"], ref in triplet_bars(name))[0]
                length = sum(map(quarters, notes), Fraction(0)) if notes else Fraction(0)
                whole = bar_length(info[ref]["time"])
                if agreed and notes and 0 < length <= whole and not all(x["pitch"] == "r" for x in notes):
                    made = new_measure(notes, per_bar[ref], keys.get(ref, info[ref]["key"]), info[ref]["time"])
                    if made is None:
                        doubts.append(f"{where}: a bar is missing, not representable: {seen[j]}")
                    else:
                        inserts.append({"after": after + 1, "first_on_line": i == 0, "text": seen[j],
                                        "xml": ET.tostring(made, encoding="unicode"), "line": int(line)})
                else:
                    doubts.append(f"{where}: a bar may be missing: {seen[j]}")
                j += 1
            else:
                doubts.append(f"bar {numbers[i] + 1}: may not be in the print (kept): {mine[i]}")
                i += 1
    # insert from the end so that earlier positions stay valid
    for k, item in reversed(list(enumerate(inserts))):
        made = ET.fromstring(item["xml"])
        made.set("number", f"x{k}")
        at = part.findall("measure").index(bars[item["after"] - 1]) + 1 if item["after"] else 0
        if item["first_on_line"] and at < len(part.findall("measure")):
            follower = part.findall("measure")[at]
            marker = follower.find("print")
            if marker is not None:
                follower.remove(marker)
                made.insert(0, marker)
        part.insert(list(part).index(part.findall("measure")[at]) if at < len(part.findall("measure")) else len(part), made)
    header = target.read_text("utf-8").split("<score-partwise", 1)[0]
    target.write_text(header + ET.tostring(tree.getroot(), encoding="unicode"), "utf-8")
    (KEPT / name / "audit.json").write_text(json.dumps(
        {"changed": changes, "inserts": inserts, "doubts": doubts}, indent=1))
    print(f"{name}: {len(changes)} bars changed, {len(inserts)} inserted, {len(doubts)} doubts")


if __name__ == "__main__":
    command, names = sys.argv[1], sys.argv[2:]
    for each in names:
        {"count": count, "read": read_lines, "apply": apply}[command](each)
