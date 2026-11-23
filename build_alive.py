#!/usr/bin/env python3
"""build_alive.py — 从已筛过的 config.json 生成纯 CMS 版 alive_cms.json。

config.json 里 type=1 的站点已经被 filter_sources.py 探活过了，
所以这里只做「提取 + 按 host/path 去重」，不再发请求。

用法:
    python3 build_alive.py [源文件] [目标文件]
"""

import json
import sys
import urllib.parse

MIN_SOURCES = 10


def normalize(api):
    """同一个采集站的不同线路（带 ?ac=list 等）归一到 host+path。"""
    parts = urllib.parse.urlsplit(api)
    return (parts.netloc.lower(), parts.path.rstrip("/").lower())


def main():
    src_path = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    dst_path = sys.argv[2] if len(sys.argv) > 2 else "alive_cms.json"

    try:
        with open(src_path, encoding="utf-8") as fh:
            src = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"读不了 {src_path}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    seen = set()
    sites = []
    for item in src.get("sites") or []:
        if str(item.get("type")) != "1":
            continue
        api = item.get("api") or ""
        if not api.startswith(("http://", "https://")):
            continue
        key = normalize(api)
        if key in seen:
            continue
        seen.add(key)
        sites.append(
            {
                "key": f"cms_{len(sites) + 1}",
                "name": (item.get("name") or "").strip(),
                "type": 1,
                "api": api,
                "searchable": 1,
                "quickSearch": 1,
                "filterable": 1,
            }
        )

    if len(sites) < MIN_SOURCES:
        print(
            f"只有 {len(sites)} 个存活 CMS 源，低于阈值 {MIN_SOURCES}，"
            "疑似探测环境异常，不写出",
            file=sys.stderr,
        )
        return 1

    config = {
        "sites": sites,
        "parses": src.get("parses") or [],
        "flags": src.get("flags") or [],
    }

    try:
        with open(dst_path, "w", encoding="utf-8") as fh:
            json.dump(config, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        print(f"写不出去 {dst_path}: {exc}", file=sys.stderr)
        return 1

    print(f"{dst_path}: {len(sites)} 个 CMS 源，{len(config['parses'])} 个解析")
    return 0


if __name__ == "__main__":
    sys.exit(main())
