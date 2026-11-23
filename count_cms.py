#!/usr/bin/env python3
"""count_cms.py — 统计配置里的站点数量。

用法:
    python3 count_cms.py config.json            # 人类可读：CMS 40 / 总 721
    python3 count_cms.py config.json --number   # 只打印 CMS 数量
"""

import json
import sys


def main():
    if len(sys.argv) < 2:
        print("用法: count_cms.py <config.json> [--number]", file=sys.stderr)
        return 2

    try:
        with open(sys.argv[1], encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"读不了 {sys.argv[1]}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    sites = data.get("sites") or []
    cms = sum(1 for s in sites if str(s.get("type")) == "1")

    if "--number" in sys.argv:
        print(cms)
    else:
        print(f"CMS {cms} / 总 {len(sites)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
