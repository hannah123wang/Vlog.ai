import sys

if hasattr(sys.stdout, "reconfigure"):      # Windows 控制台默认 GBK，中文日志会报错
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from .cli import main
from .llm import FatalError

try:
    main()
except FatalError as e:          # Key 不对、余额不足：一句话说清楚，不打印 Python 堆栈
    print(f"\n[vlogai] ✗ {e}", file=sys.stderr)
    sys.exit(1)
