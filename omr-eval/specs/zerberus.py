"""Spec for 'Zerberus' (Lake Brass), non-percussion parts: 12 horns, 3 euphoniums, tuba.

Used by omr-eval/align_systems.py, which (unlike align_combine.py) derives each part's
bar structure *automatically* from the part PDFs' text layer (system-start bar numbers and
multi-rest counts) and homr's per-system measure counts; this file only carries the
score-wide facts read off the full score (pages 1-18 of the score+parts PDF).

Bar numbering = the score's: bar 1 is the 1-beat pickup, 210 bars in total.
Assumption: all parts are written at concert pitch (the score shows the same key signature
for horns and tuba), so no <transpose> is emitted.
"""
TITLE = 'Zerberus'
COMPOSER = ''
TOTAL_BARS = 210
_IN = '/Users/philip/Dev/MusicOCR/input/zerberus'
_OUT = '/Users/philip/Dev/MusicOCR/output'
FIRST_BAR_BEATS = 1          # bar 1 is a one-beat pickup (in 4/4)

def _p(name, abbr, key, program, clef):
    return dict(name=name, abbr=abbr, key=key, program=program, clef=clef,
                pdf=f'{_IN}/Zerberus_{key}.pdf', dir=f'{_OUT}/Zerberus_{key}')

PARTS = {}
for i in range(1, 13):
    PARTS[i] = _p(f'Horn in F {i}', f'Hn {i}', f'hn{i:02d}', 61, 'treble')
for i in range(1, 4):
    PARTS[12 + i] = _p(f'Euphonium {i}', f'Euph {i}', f'eu{i}', 58, 'bass')
PARTS[16] = _p('Tuba', 'Tba', 'tuba', 59, 'bass')

def meter(bar):
    return (4, 4) if bar <= 34 else (6, 8)

def key_fifths(bar):
    if bar <= 34: return 1
    if bar <= 45: return 2
    if bar <= 114: return 3
    if bar <= 170: return 4
    if bar <= 186: return 3
    return 0

# (kind, *args) inserted at the start of a bar; 'all' = every part, else part id
MARKS = {
    1: {1: [('tempo', '♩ = 68', 68)], 57: [('rehearsal', 'A')], 115: [('rehearsal', 'D')]},
}
