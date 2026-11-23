#!/usr/bin/env python3
"""
filter_sources.py — 把影视仓 / TVBox 接口配置里已经死掉的采集站筛掉。

用法:
    python3 filter_sources.py --url https://[配置地址]/tvbox.json
    python3 filter_sources.py --file ./tvbox.json
    python3 filter_sources.py --file ./tvbox.json --out tvbox.json --keep-slow

做的事:
    1. 下载 / 读取接口 JSON（支持单仓 dict 与多仓 list 两种格式）
    2. 取出所有 type==1（CMS 采集站）且 api 为 http(s) 的站点
    3. 并发请求 api?ac=list，按 HTTP 状态 + 返回体特征判定 alive / slow / dead
    4. --out 写出一份只含存活站点的新配置（其余字段原样保留）

不做的事:
    - 不测试 type==3 的 csp_*.jar（本地 jar，探测无意义）
    - 不下载任何影片内容，只打一次 list 请求探活
"""

import argparse
import json
import random
import ssl
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 多套 UA 轮换，避免所有探测请求带同一个指纹
UA_POOL = (
    UA,
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Linux; Android 12; TV) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
    "okhttp/3.15",
)

# 探活成功时返回体里应该出现的东西（CMS10 返回 xml 或 json）
ALIVE_MARKERS = ("<rss", "<list", '"list"', '"class"', '"data"', "vod_")

ALLOWED_SCHEMES = ("http://", "https://")

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def ensure_http_url(url):
    """只放行 http/https，挡住 file:// 与自定义 scheme。"""
    if not isinstance(url, str) or not url.startswith(ALLOWED_SCHEMES):
        raise ValueError(f"不支持的地址协议: {url!r}")
    return url


def http_get(url, timeout):
    # ensure_http_url 已把 scheme 限制为 http/https，这里豁免通用 scheme 审计规则
    ua_header = {"User-Agent": random.choice(UA_POOL)}
    req = urllib.request.Request(ensure_http_url(url), headers=ua_header)  # noqa: S310
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:  # noqa: S310
        body = resp.read(8192).decode("utf-8", "ignore").lower()
        return resp.status, body


def fetch_json(source, timeout):
    """下载或读取一份 JSON。失败时抛 ValueError，由调用方决定怎么处理。"""
    try:
        if source.startswith(ALLOWED_SCHEMES):
            _, body = http_get(source, timeout)
            return json.loads(body)
        with open(source, encoding="utf-8") as fh:
            return json.load(fh)
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"读不到配置 {source}: {type(exc).__name__}: {exc}") from exc


def probe(api, timeout, jitter=0.0):
    """返回 (state, detail, elapsed)。state ∈ alive / slow / dead"""
    if jitter:
        time.sleep(random.uniform(0, jitter))
    sep = "&" if "?" in api else "?"
    started = time.time()
    try:
        code, body = http_get(api + sep + "ac=list", timeout)
    except urllib.error.HTTPError as exc:
        return "dead", f"HTTP {exc.code}", time.time() - started
    except Exception as exc:  # noqa: BLE001 - 任何异常都等价于站点不可用
        return "dead", type(exc).__name__, time.time() - started

    elapsed = time.time() - started
    if code != 200:
        return "dead", f"HTTP {code}", elapsed
    if not any(marker in body for marker in ALIVE_MARKERS):
        return "dead", "body 不像 CMS 接口", elapsed
    if elapsed > 5.0:
        return "slow", "HTTP 200 但慢", elapsed
    return "alive", "HTTP 200", elapsed


def load_config(source, timeout):
    """读配置。返回 (label, dict) 列表；多仓会被展开成多个单仓。"""
    raw = fetch_json(source, timeout)
    if isinstance(raw, dict):
        return [("(single)", raw)]

    out = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict) or "url" not in item:
            continue
        label = item.get("name") or f"repo{idx}"
        try:
            out.append((label, fetch_json(item["url"], timeout)))
        except ValueError as exc:
            print(f"  [warn] 仓库 {label} 拉取失败: {exc}", file=sys.stderr)
    return out


def is_cms_site(site):
    api = site.get("api") or ""
    return str(site.get("type")) == "1" and api.startswith(ALLOWED_SCHEMES)


def main():
    parser = argparse.ArgumentParser(description="筛掉影视仓配置里失效的采集站")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", help="接口配置的远程地址")
    group.add_argument("--file", help="接口配置的本地路径")
    parser.add_argument("--out", help="把只含存活站点的配置写到这个文件")
    parser.add_argument(
        "--keep-slow",
        action="store_true",
        help="把响应慢但还活着的站点也保留 (默认丢弃)",
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="单次探测超时秒数")
    parser.add_argument("--workers", type=int, default=16, help="并发数")
    parser.add_argument(
        "--jitter",
        type=float,
        default=0.0,
        help="每个请求前随机等待 0~N 秒，降低脚本特征 (批量时建议 0.5)",
    )
    args = parser.parse_args()

    source = args.url or args.file
    print(f"读取配置: {source}")
    try:
        repos = load_config(source, args.timeout)
    except ValueError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    if not repos:
        print("没读到任何可解析的配置", file=sys.stderr)
        return 1

    total_sites = sum(len(repo.get("sites") or []) for _, repo in repos)
    targets = [
        (label, site)
        for label, repo in repos
        for site in (repo.get("sites") or [])
        if is_cms_site(site)
    ]
    print(f"站点总数 {total_sites}，其中 CMS 型(type=1) 可探测 {len(targets)} 个\n")
    if not targets:
        print("没有可探测的 CMS 站点，无需处理")
        return 0

    state_by_api = {}
    started = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(probe, site["api"], args.timeout, args.jitter): site
            for _, site in targets
        }
        for future in as_completed(futures):
            site = futures[future]
            state, detail, elapsed = future.result()
            state_by_api[site["api"]] = state
            tag = {"alive": "OK  ", "slow": "SLOW", "dead": "DEAD"}[state]
            name = (site.get("name") or "?")[:18]
            print(f"  {tag}  {elapsed:6.2f}s  {name:<18} {detail:<22} {site['api']}")

    buckets = {"alive": [], "slow": [], "dead": []}
    for _, site in targets:
        buckets[state_by_api.get(site["api"], "dead")].append(site)
    alive, slow, dead = buckets["alive"], buckets["slow"], buckets["dead"]
    print(
        f"\n结果: alive {len(alive)} / slow {len(slow)} / dead {len(dead)}"
        f"   ({time.time() - started:.1f}s)"
    )

    if not args.out:
        print("\n(加 --out tvbox.json 可生成只含存活站点的配置)")
        return 0

    keep_apis = {site["api"] for site in alive}
    if args.keep_slow:
        keep_apis |= {site["api"] for site in slow}

    if len(repos) == 1:
        config = dict(repos[0][1])
        config["sites"] = [
            site
            for site in (config.get("sites") or [])
            if not is_cms_site(site) or site.get("api") in keep_apis
        ]
    else:
        # 多仓：合并成单仓，key 冲突时加序号后缀，parses / flags 按 url / 值去重
        config = {"sites": [], "parses": [], "flags": []}
        seen_keys, seen_parse, seen_flag = set(), set(), set()
        for _, repo in repos:
            for raw_site in repo.get("sites") or []:
                if is_cms_site(raw_site) and raw_site.get("api") not in keep_apis:
                    continue
                site = dict(raw_site)
                base = str(site.get("key") or site.get("name") or "site")
                n = 1
                while site.get("key") in seen_keys:
                    n += 1
                    site["key"] = f"{base}_{n}"
                seen_keys.add(site.get("key"))
                config["sites"].append(site)
            for parse in repo.get("parses") or []:
                url = parse.get("url")
                if url in seen_parse:
                    continue
                seen_parse.add(url)
                config["parses"].append(parse)
            for flag in repo.get("flags") or []:
                if flag in seen_flag:
                    continue
                seen_flag.add(flag)
                config["flags"].append(flag)

    try:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(config, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        print(f"写不出去 {args.out}: {exc}", file=sys.stderr)
        return 1

    print(
        f"已写出: {args.out}  (sites: {total_sites} -> {len(config['sites'])})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
