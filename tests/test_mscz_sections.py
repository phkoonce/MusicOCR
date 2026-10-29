"""Section breaks for multi-piece scores (musicocr/assemble/mscz_sections.py)."""
from __future__ import annotations

import zipfile

from musicocr.assemble.mscz_sections import add_section_breaks, patch_mscx, section_ends

MSCX = """<museScore><Score>
    <Staff id="1">
      <Measure>
        <eid>a</eid>
        <noOffset>3</noOffset>
        <voice><Rest/></voice>
        </Measure>
      <Measure>
        <eid>b</eid>
        <LayoutBreak>
          <eid>x</eid>
          <subtype>line</subtype>
          </LayoutBreak>
        <voice><Rest/></voice>
        </Measure>
      <Measure>
        <eid>c</eid>
        <voice><Rest/></voice>
        </Measure>
    </Staff>
    <Staff id="2">
      <Measure><eid>d</eid><voice><Rest/></voice></Measure>
      <Measure><eid>e</eid><voice><Rest/></voice></Measure>
      <Measure><eid>f</eid><voice><Rest/></voice></Measure>
    </Staff>
</Score></museScore>"""


def test_patch_puts_section_breaks_on_the_first_staff_and_drops_offsets_and_line_breaks():
    out = patch_mscx(MSCX, [0, 1])
    first, second = out.split('<Staff id="2">')
    assert first.count("<subtype>section</subtype>") == 2 and "<subtype>line</subtype>" not in first
    assert "<noOffset>" not in out and "<LayoutBreak>" not in second
    assert first.count("<startWithMeasureOne>1</startWithMeasureOne>") == 2
    # the break sits in the bar it ends, not the next one
    assert first.index("<eid>a</eid>") < first.index("section") < first.index("<eid>b</eid>")


def test_section_ends_are_the_bars_before_each_new_system(tmp_path):
    x = tmp_path / "s.musicxml"
    x.write_text("<score-partwise><part id='P1'><measure number='1'/><measure number='2'/>"
                 "<measure number='1'><print new-system='yes'/></measure><measure number='2'/>"
                 "<measure number='1'><print new-system='yes'/></measure></part></score-partwise>")
    assert section_ends(x) == [1, 3]


def test_add_section_breaks_rewrites_the_mscz(tmp_path):
    z = tmp_path / "s.mscz"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("s.mscx", MSCX)
        f.writestr("META-INF/container.xml", "<container/>")
    assert add_section_breaks(z, [0]) == 1 and add_section_breaks(z, []) == 0
    with zipfile.ZipFile(z) as f:
        assert "<subtype>section</subtype>" in f.read("s.mscx").decode()
        assert f.read("META-INF/container.xml") == b"<container/>"
