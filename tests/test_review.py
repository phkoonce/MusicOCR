"""The review tool: bar rewriting, alignment, and one line's round trip through a stand-in for Claude."""
import json
import stat
import threading
import urllib.error
import urllib.request
import zipfile
from fractions import Fraction

import pytest
from PIL import Image, ImageDraw

from musicocr.review import claude, images, score
from musicocr.review.server import make_server
from musicocr.review.session import ReviewError, Session


def note(pitch, value="quarter"):
    return f"<Chord><eid>e{pitch}</eid><durationType>{value}</durationType><Note><pitch>{pitch}</pitch></Note></Chord>"


def measure(*items, extra=""):
    return f"<Measure><eid>m</eid>{extra}<voice>{''.join(items)}</voice></Measure>"


FOUR = [note(60), note(62), note(64), note(65)]
BREAK = "<LayoutBreak><subtype>line</subtype></LayoutBreak>"


def make_mscz(path, bars):
    mscx = f'<museScore><Score><Staff id="1">{"".join(bars)}</Staff></Score></museScore>'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(f"{path.stem}.mscx", mscx)
        z.writestr("score_style.mss", "<museScore><Style></Style></museScore>")


def test_check_replacement():
    old = measure(*FOUR, extra=BREAK)
    bar = Fraction(1)
    assert score.check_replacement(old, old.replace("<pitch>62<", "<pitch>63<"), bar) is None
    assert "add up" in score.check_replacement(old, measure(*FOUR[:3], extra=BREAK), bar)
    assert "LayoutBreak" in score.check_replacement(old, measure(*FOUR), bar)
    assert "well-formed" in score.check_replacement(old, "<Measure>", bar)


def test_voice_lengths_counts_dots_tuplets_and_grace_notes():
    triplet = ("<Tuplet><normalNotes>2</normalNotes><actualNotes>3</actualNotes></Tuplet>"
               + note(60, "eighth") * 3 + "<endTuplet/>")
    dotted = "<Chord><dots>1</dots><durationType>quarter</durationType></Chord>"
    grace = "<Chord><acciaccatura/><durationType>eighth</durationType></Chord>"
    assert score.voice_lengths(measure(triplet, grace, dotted), Fraction(1)) == [Fraction(5, 8)]


def test_match_barlines_prefers_near_columns_and_fills_a_missing_one():
    assert images.match_barlines([100, 200, 300], [20, 108, 150, 297], sp=5) == [108, 200, 297]


def test_rendered_staves_ignore_a_beam_between_lines():
    img = page_with_staves([100, 300])
    ImageDraw.Draw(img).rectangle((200, 104, 420, 110), fill=0)
    assert [(s.top, s.bottom, s.left, s.right) for s in images.rendered_staves(img, 0)] == [
        (100, 148, 50, 951), (300, 348, 50, 951)]


def test_bar_at():
    assert images.bar_at(0.75, 400, [0, 200, 400]) == (1, 50)


def test_parse_reply_skips_prose_and_fences():
    reply = claude.parse_reply('Here:\n```json\n{"changes": [], "summary": "same"}\n```')
    assert reply["summary"] == "same"
    with pytest.raises(claude.ClaudeError):
        claude.parse_reply('{"changes": [{"bar": "1"}]}')


def test_command_gives_claude_read_only():
    cmd = claude.command("claude", "some-model")
    assert cmd[cmd.index("--tools") + 1] == "Read"
    assert "--safe-mode" in cmd and cmd[-1] == "some-model"


def test_run_job_signed_out_waits(tmp_path):
    tool = tmp_path / "claude"
    tool.write_text("#!/bin/sh\necho '{\"is_error\": true, \"result\": \"Not logged in\"}'\nexit 1\n")
    tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    (tmp_path / "prompt.md").write_text("hi")
    with pytest.raises(claude.ClaudeUnavailable):
        claude.run_job(tmp_path, str(tool))


def page_with_staves(tops, bars=2):
    img = Image.new("L", (1000, 600), 255)
    draw = ImageDraw.Draw(img)
    for top in tops:
        for k in range(5):
            draw.line((50, top + 12 * k, 950, top + 12 * k), fill=0, width=2)
        for i in range(1, bars + 1):
            x = 50 + 900 * i // bars
            draw.line((x, top, x, top + 48), fill=0, width=2)
    return img


@pytest.fixture
def session(tmp_path, monkeypatch):
    """Two lines of two bars, with drawn pages instead of pdftoppm and MuseScore."""
    mscz = tmp_path / "tune.mscz"
    make_mscz(mscz, [measure(*FOUR), measure(*FOUR, extra=BREAK), measure(*FOUR), measure(*FOUR)])
    (tmp_path / "tune.pdf").write_bytes(b"%PDF")
    page = page_with_staves([100, 300])
    monkeypatch.setattr(images, "render_original",
                        lambda *a: ([page], images._find_staves(page, 0)))

    def renderer(*a):
        bars = [images.RenderedBar(0, y, x, x + 450) for y in (100, 300) for x in (50, 500)]
        return [page], images._find_staves(page, 0), bars

    return Session(mscz, tmp_path / "tune.pdf", "musescore", renderer=renderer)


def reply_for(job, bar, old, new):
    sent = {m["bar"]: m["xml"] for m in json.loads((job / "sent.json").read_text())}
    return {"changes": [{"staff": 1, "bar": bar, "measure": sent[bar - 1].replace(old, new)}], "summary": "fixed"}


def bars_of(session):
    with zipfile.ZipFile(session.mscz) as z:
        mscx = z.read("tune.mscx").decode()
    return [mscx[a:b] for a, b in score.measure_spans(mscx)[1]]


def test_send_fix_undo(session):
    assert [(line["first"], line["last"]) for line in session.view()["lines"]] == [(1, 2), (3, 4)]
    original = bars_of(session)
    session.send(0, [{"staff": 0, "x": 0.8, "y": 0.5, "note": "wrong note"}])
    assert not session.work_once()                      # no `claude`: the line waits
    assert session.view()["lines"][0]["status"] == "queued"
    job = session.prepare(0)
    prompt = (job / "prompt.md").read_text()
    assert "mark 1 on staff 1, bar 2" in prompt and "wrong note" in prompt
    assert (job / "staff1.png").exists() and (job / "staff1-tags.png").exists()

    (job / "result.json").write_text(json.dumps(reply_for(job, 2, "<pitch>62<", "<pitch>63<")))
    assert session.work_once()
    line = session.view()["lines"][0]
    assert line["status"] == "check" and line["canUndo"] and line["summary"] == "fixed"
    assert "<pitch>63<" in bars_of(session)[1]
    assert session.mscz.with_suffix(".before-review.mscz").exists()

    session.done(0)
    assert session.view()["lines"][0]["status"] == "done"
    session.reset(0)
    session.state["lines"]["0"]["undo"] = [{"staff": 1, "bar": 1, "before": original[1], "after": bars_of(session)[1]}]
    session.undo(0)
    assert bars_of(session) == original


def test_bad_or_stale_replies_change_nothing(session):
    original = bars_of(session)
    session.send(0, [])
    job = session.prepare(0)
    (job / "result.json").write_text(json.dumps(reply_for(job, 1, note(65), "")))
    session.work_once()
    line = session.view()["lines"][0]
    assert line["status"] == "failed" and "add up" in line["error"]
    assert bars_of(session) == original

    session.send(0, [])
    job = session.prepare(0)
    reply = reply_for(job, 1, "<pitch>62<", "<pitch>63<")
    with zipfile.ZipFile(session.mscz) as z:           # the user edits the same bar meanwhile
        mscx, style = z.read("tune.mscx").decode(), z.read("score_style.mss").decode()
    with zipfile.ZipFile(session.mscz, "w") as z:
        z.writestr("tune.mscx", mscx.replace("<pitch>64<", "<pitch>66<", 1))
        z.writestr("score_style.mss", style)
    with pytest.raises(ReviewError, match="changed while"):
        session.apply(0, job, reply)


def test_done_line_that_changes_is_flagged_and_progress_survives(session):
    session.done(1)
    mscx = zipfile.ZipFile(session.mscz).read("tune.mscx").decode()
    head, tail = mscx.rsplit("<pitch>65<", 1)
    with zipfile.ZipFile(session.mscz, "w") as z:
        z.writestr("tune.mscx", head + "<pitch>67<" + tail)
        z.writestr("score_style.mss", "<museScore><Style></Style></museScore>")
    session.try_refresh()
    assert session.view()["lines"][1]["status"] == "changed"
    assert json.loads(session.state_path.read_text())["lines"]["1"]["status"] == "done"


def test_server_serves_and_refuses_outsiders(session):
    server = make_server(session, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def status(path, headers=None, data=None):
        try:
            with urllib.request.urlopen(urllib.request.Request(base + path, data=data, headers=headers or {})) as r:
                return r.status
        except urllib.error.HTTPError as exc:
            return exc.code

    try:
        assert status("/") == 200 and status("/api/state") == 200 and status("/img/0/0/overlay.png") == 200
        assert status("/img/0/0/../x.png") == 404
        assert status("/api/state", {"Host": "evil.example"}) == 403
        assert status("/api/line/0/done", data=b"{}") == 403
        assert status("/api/line/0/done", {"X-Review": "1"}, b"{}") == 200
        assert status("/api/line/9/done", {"X-Review": "1"}, b"{}") == 400
    finally:
        server.shutdown()
        server.server_close()
