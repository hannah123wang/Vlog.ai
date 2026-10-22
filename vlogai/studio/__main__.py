import sys

if hasattr(sys.stdout, "reconfigure"):      # Windows 控制台默认 GBK，中文日志会报错
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import argparse

from .server import serve

ap = argparse.ArgumentParser(prog="vlogai.studio", description="vlogai studio 本地应用")
ap.add_argument("--port", type=int, default=8765)
ap.add_argument("--no-open", action="store_true", help="不要自动打开浏览器")
a = ap.parse_args()
serve(port=a.port, open_browser=not a.no_open)
