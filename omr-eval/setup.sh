#!/usr/bin/env bash
# Sets up isolated environments to trial pretrained OMR models against Audiveris.
# Both need their own Python versions, hence separate venvs via uv (independent of the main project venv).
set -euo pipefail
cd "$(dirname "$0")"

command -v uv >/dev/null || { echo "install uv first: brew install uv"; exit 1; }

echo "== homr =="
uv venv homr/.venv --python 3.12
uv pip install --python homr/.venv/bin/python "homr[cpu]"

echo "== copisteria =="
# Needs Python 3.12 and runs from its own repo root (it finds models/ relative to cwd).
[ -d copisteria/repo ] || git clone --depth 1 https://github.com/kobimusic/copisteria copisteria/repo
uv venv copisteria/.venv --python 3.12
uv pip install --python copisteria/.venv/bin/python -e copisteria/repo huggingface_hub
copisteria/.venv/bin/python - <<'PY'
from huggingface_hub import snapshot_download
snapshot_download("kobimusic/copista-28m", allow_patterns=["*.pt"], local_dir="copisteria/repo/models")
PY

echo "== legato2 =="
# Weights (guangyangmusic/legato-2, Apache-2.0) are GATED: run `huggingface-cli login` and accept the
# terms at https://huggingface.co/guangyangmusic/legato-2 first. The inference code
# (legato.pipeline.Legato2) is not in the public guang-yng/legato main branch as of 2026-10-26, so this
# step installs the deps, then checks for it and says what is missing instead of failing the whole script.
[ -d legato2/repo ] || git clone --depth 1 https://github.com/guang-yng/legato legato2/repo
uv venv legato2/.venv --python 3.12
uv pip install --python legato2/.venv/bin/python torch transformers==4.54.0 safetensors pillow \
    "numpy<2" ultralytics huggingface_hub pdf2image fire
if [ ! -f legato2/repo/legato/pipeline.py ]; then
  echo "  !! legato/pipeline.py not found in legato2/repo -- put the LEGATO 2 inference code there"
  echo "     (or point [omr.legato2] repo in config.toml at it) before using --engine legato2"
fi
if legato2/.venv/bin/python -c "from huggingface_hub import hf_hub_download as d; d('guangyangmusic/legato-2','config.json')" 2>/dev/null; then
  legato2/.venv/bin/python -c "from huggingface_hub import snapshot_download as s; s('guangyangmusic/legato-2')"
else
  echo "  !! cannot download guangyangmusic/legato-2 (gated: log in and accept the terms), skipping weights"
fi

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
