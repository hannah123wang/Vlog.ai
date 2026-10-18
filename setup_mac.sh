#!/usr/bin/env bash
# One-time setup for macOS / Linux.  Run:  bash setup_mac.sh
set -e
cd "$(dirname "$0")"
echo "▸ creating virtualenv…"
python3 -m venv .venv
source .venv/bin/activate
echo "▸ installing dependencies (this also fetches a private copy of ffmpeg)…"
pip install -q --upgrade pip
pip install -q -r requirements.txt
echo "▸ linking ffmpeg/ffprobe into the virtualenv…"
python - <<'PY'
import pathlib, sys
try:
    from static_ffmpeg.run import get_or_fetch_platform_executables_else_raise as get
except ImportError:
    sys.exit(0)
ff, fp = get()
bin_dir = pathlib.Path(sys.prefix) / "bin"
for src, name in ((ff, "ffmpeg"), (fp, "ffprobe")):
    dst = bin_dir / name
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    dst.symlink_to(src)
    print("  linked", dst.name)
PY
echo
echo "✓ done.  Next:"
echo "    source .venv/bin/activate"
echo "    export ANTHROPIC_API_KEY=sk-ant-..."
echo "    python -m vlogai.studio --port 8900"
