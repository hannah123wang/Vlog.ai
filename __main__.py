import sys

if hasattr(sys.stdout, "reconfigure"):      # Windows 控制台默认 GBK，中文日志会报错
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from .cli import main

main()
