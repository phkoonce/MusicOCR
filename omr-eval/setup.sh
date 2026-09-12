#!/usr/bin/env bash
# Sets up isolated environments to trial pretrained OMR models against Audiveris.
# Both need newer Python than the main project's 3.9 venv, hence separate venvs via uv.
set -euo pipefail
cd "$(dirname "$0")"

command -v uv >/dev/null || { echo "install uv first: brew install uv"; exit 1; }

echo "== homr =="
uv venv homr/.venv --python 3.12
uv pip install --python homr/.venv/bin/python "homr[cpu]"

echo "== oemer =="
uv venv oemer/.venv --python 3.11
uv pip install --python oemer/.venv/bin/python oemer

# oemer (last released 0.1.5) is unmaintained and breaks on current numpy/opencv.
# Patch its installed source rather than pin ancient transitive deps.
OEMER_PKG="oemer/.venv/lib/python3.11/site-packages/oemer"

# 1. np.int was removed in numpy>=1.24.
sed -i '' 's/dtype=np\.int)/dtype=int)/' "$OEMER_PKG/staffline_extraction.py"
sed -i '' 's/np\.int(unit_size\/\/2)/int(unit_size\/\/2)/' "$OEMER_PKG/symbol_extraction.py"

# 2. cv2.HoughLinesP return shape assumption breaks (IndexError) on sparse results,
#    which single-staff monophonic pages reliably trigger.
python3 - "$OEMER_PKG/bbox.py" <<'PY'
import sys
path = sys.argv[1]
src = open(path).read()
old = '''    lines = cv2.HoughLinesP(data.astype(np.uint8), 1, np.pi/180, 50, None, min_len, max_gap)
    new_line = []
    for line in lines:
        line = line[0]'''
new = '''    lines = cv2.HoughLinesP(data.astype(np.uint8), 1, np.pi/180, 50, None, min_len, max_gap)
    new_line = []
    if lines is None:
        return new_line
    for line in lines:
        line = np.array(line).reshape(-1)'''
assert old in src, "bbox.py source has changed upstream; patch no longer applies"
open(path, "w").write(src.replace(old, new))
PY

echo "Done. Run with:"
echo "  homr/.venv/bin/homr <image.png>"
echo "  oemer/.venv/bin/oemer <image.png> -o <outdir>"
