from __future__ import annotations

import xml.etree.ElementTree as ET

from musicocr.retouch import RetouchOptions, octavize_bass_clefs, retouch_tree

DOC = """<score-partwise version="3.1"><movement-title>x.musicxml</movement-title>
<identification><creator type="composer">Music21</creator></identification>
<part-list><score-part id="P1"/></part-list>
<part id="P1">
<measure number="1"><attributes><clef><sign>F</sign><line>4</line></clef></attributes>
<direction><direction-type><words>check: Heinz Liebert (geb.1918)</words></direction-type></direction>
<note><pitch><step>C</step><octave>2</octave></pitch><duration>4</duration></note></measure>
<measure number="2"><attributes><clef><sign>G</sign><line>2</line></clef></attributes>
<direction><direction-type><words>meno</words></direction-type></direction>
<note><pitch><step>C</step><octave>4</octave></pitch><duration>4</duration></note></measure>
<measure number="3"><note><pitch><step>D</step><octave>4</octave></pitch><duration>4</duration></note>
<attributes><clef><sign>F</sign><line>4</line></clef></attributes>
<note><pitch><step>E</step><octave>2</octave></pitch><duration>4</duration></note></measure>
</part></score-partwise>"""


def _octaves(root):
    return [n.findtext("pitch/octave") for n in root.iter("note")]


def test_bass_clef_octavized_and_notes_raised():
    root = ET.fromstring(DOC)
    assert octavize_bass_clefs(root) == 2
    assert _octaves(root) == ["3", "4", "4", "3"]       # treble notes untouched, mid-bar clef change respected
    clefs = [c.findtext("clef-octave-change") for c in root.iter("clef")]
    assert clefs == ["1", None, "1"]


def test_octavize_is_idempotent():
    root = ET.fromstring(DOC)
    octavize_bass_clefs(root)
    assert octavize_bass_clefs(root) == 0
    assert _octaves(root) == ["3", "4", "4", "3"]


def test_title_composer_set_and_misread_direction_dropped():
    root = ET.fromstring(DOC)
    res = retouch_tree(root, RetouchOptions(title="Kleine Jagdfantasie", composer="Heinz Liebert (geb. 1918)"))
    assert res["dropped"] == 1
    assert root.findtext("work/work-title") == "Kleine Jagdfantasie"
    assert root.findtext("movement-title") == "Kleine Jagdfantasie"
    assert [c.text for c in root.findall("identification/creator")] == ["Heinz Liebert (geb. 1918)"]
    assert [w.text for w in root.iter("words")] == ["meno"]      # real expression text stays


def test_running_head_misread_dropped():
    root = ET.fromstring(DOC.replace("meno", "check: Horn IV in"))
    assert retouch_tree(root, RetouchOptions(title="T"))["dropped"] == 1
