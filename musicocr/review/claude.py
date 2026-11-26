"""Ask Claude to correct one line, through the ``claude`` command line tool.

A job is a folder with the line's images, the XML of its bars and ``prompt.md``. Claude may only
read in that folder; it answers with replacement bars as JSON, which the app checks and applies.
A job can also be answered by hand: put the JSON in ``result.json``.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

_TIMEOUT = 600

PROMPT = """You are proofreading one line of a score that was produced by optical music recognition,
and correcting it so that it matches the original print.

Look at these images in the current folder with the Read tool:
{images}
In the overlay, red ink is only in the original, blue ink only in our version, black is in both. The
two are aligned bar line by bar line; MuseScore spaces notes differently, so small sideways offsets
between a red and a blue copy of the same symbol are not differences. Compare symbol by symbol:
pitches, accidentals, note values, dots, rests, tuplets, beams, ties, which note each slur starts and
ends on, hairpins and where they start and end, dynamics, articulations, text.

The images and XML show one part only. This line holds bars {first} to {last}. At its start: {context}.
{tags}
`measures.xml` holds the MuseScore XML of these bars and their neighbours, each preceded by a comment
naming its staff and bar. Rules for your replacement bars:
- Return a whole <Measure>...</Measure> for every bar you change, and only for bars you change.
- Change only what is needed to match the original. Keep every other element as it is, including its
  <eid>. Leave <eid> out of elements you add. Do not touch <LayoutBreak>, <stretch> or the len attribute.
- A slur, hairpin or tie is a pair of <Spanner> elements: <next> at its start, <prev> at its end, each
  <location> giving the offset to the other end in <measures> and <fractions> (of a whole note; omit
  whichever is zero). Moving either end means updating both, so return both bars.
- <pitch> is the sounding MIDI pitch and <tpc> its spelling; follow how the existing notes encode them.
- The notes of a bar must add up to its time signature. If the time signature itself was misread,
  correct it in the bar where it appears and return every bar it governs among those given.
- The only bars you may change are those in measures.xml.

If you cannot tell what the original shows, leave it and say so. If our version already matches, change
nothing.

Answer with one JSON object and nothing else:
{{"changes": [{{"staff": 1, "bar": 12, "measure": "<Measure>...</Measure>"}}],
  "summary": "one line per change, in plain musical terms, e.g. 'bar 12: slur now starts on the B'",
  "unresolved": ["anything you could not fix or are unsure of"]}}
"""


class ClaudeError(Exception):
    pass


class ClaudeUnavailable(ClaudeError):
    """The tool is missing or not signed in; the job should wait, not fail."""


def find_claude(explicit: Optional[str] = None) -> Optional[str]:
    if explicit:
        return explicit if Path(explicit).expanduser().exists() else shutil.which(explicit)
    found = shutil.which("claude")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / "claude"
    return str(local) if local.exists() else None


def parse_reply(text: str) -> dict:
    """The JSON object in a reply, which may come with a code fence or a few words around it."""
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start >= 0:
        try:
            value, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict) and "changes" in value:
            changes = value["changes"]
            if not isinstance(changes, list) or not all(
                    isinstance(c, dict) and isinstance(c.get("measure"), str)
                    and isinstance(c.get("staff"), int) and isinstance(c.get("bar"), int) for c in changes):
                raise ClaudeError("the reply's changes are not in the asked form")
            return value
        start = text.find("{", start + 1)
    raise ClaudeError("no JSON answer in the reply")


def command(tool: str, model: str = "") -> List[str]:
    # --safe-mode and no MCP servers: none of the user's hooks, plugins or connectors; Read only.
    cmd = [tool, "-p", "--safe-mode", "--strict-mcp-config", "--tools", "Read",
           "--no-session-persistence", "--output-format", "json"]
    return cmd + (["--model", model] if model else [])


def run_job(job: Path, tool: Optional[str], model: str = "") -> dict:
    """Claude's answer for the job folder ``job``, also saved there as ``result.json``."""
    result = job / "result.json"
    if result.exists():
        return parse_reply(result.read_text("utf-8"))
    if not tool:
        raise ClaudeUnavailable("the `claude` command was not found")
    try:
        done = subprocess.run(command(tool, model), input=(job / "prompt.md").read_text("utf-8"),
                              cwd=job, capture_output=True, text=True, timeout=_TIMEOUT)
    except FileNotFoundError as exc:
        raise ClaudeUnavailable(str(exc)) from exc
    except subprocess.TimeoutExpired as exc:
        raise ClaudeError(f"Claude took more than {_TIMEOUT // 60} minutes") from exc
    (job / "claude.log").write_text(done.stdout + done.stderr, "utf-8")
    try:
        out = json.loads(done.stdout)
        text, failed = str(out.get("result", "")), bool(out.get("is_error"))
    except (json.JSONDecodeError, AttributeError):
        text, failed = done.stdout + done.stderr, done.returncode != 0
    if failed or done.returncode != 0:
        if any(s in text.lower() for s in ("not logged in", "/login", "invalid api key", "authenticat")):
            raise ClaudeUnavailable("`claude` is not signed in: run it once in a terminal and use /login")
        raise ClaudeError(text.strip()[:300] or f"claude exited with {done.returncode}")
    reply = parse_reply(text)
    result.write_text(json.dumps(reply, indent=2), "utf-8")
    return reply
