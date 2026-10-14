"""诊断一个页面到底是被拦了，还是选择器写错了。

用法：
    python diag_site.py https://<站点域名>/
    python diag_site.py <目录页 URL> --cookies cookies.json
    python diag_site.py <url> --html out.html      # 顺手把原始 HTML 存下来核对选择器

cookies.json 格式（浏览器导出，Netscape JSON 或 EditThisCookie 都行）：
    [{"name": "cf_clearance", "value": "...", "domain": ".<站点域名>"}]
也支持 {"cf_clearance": "..."} 这种简写。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import requests

CF_MARKERS = ("Just a moment", "challenge-platform", "_cf_chl", "cf-chl-", "Enable JavaScript and cookies")
BLOCK_HINTS = {
    403: "被拒绝：通常是 Cloudflare/WAF 人机验证或封 IP",
    429: "被限流：请求太快，加大 --delay",
    503: "服务不可用：可能是站点过载或风控拦截",
    521: "源站挂了（Cloudflare 521）",
}


def load_cookies(path: str) -> dict[str, str]:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if isinstance(data, dict):
        # 支持 {"cookies": [...]} 或 {"name": "value"}
        if "cookies" in data and isinstance(data["cookies"], list):
            data = data["cookies"]
        else:
            return {str(k): str(v) for k, v in data.items()}
    out: dict[str, str] = {}
    for item in data:  # type: ignore[union-attr]
        if isinstance(item, dict) and item.get("name"):
            out[str(item["name"])] = str(item.get("value", ""))
    return out


def load_state(path: str) -> tuple[dict[str, str], str | None]:
    """读取 browser_session.py 的会话文件：cookies + User-Agent 一起取。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        print(f"[x] 会话文件不存在：{path}")
        print("    先过验证：双击工作区根目录的 browser-session.cmd"
              "（或 python ..\\browser_session.py <该站URL>）")
        raise SystemExit(3)
    except json.JSONDecodeError as exc:
        print(f"[x] 会话文件不是合法 JSON：{path}（{exc}）")
        raise SystemExit(3)
    cookies: dict[str, str] = {}
    for item in data.get("cookies", []) if isinstance(data, dict) else []:
        if isinstance(item, dict) and item.get("name"):
            cookies[str(item["name"])] = str(item.get("value", ""))
    ua = data.get("user_agent") if isinstance(data, dict) else None
    return cookies, (str(ua) if ua else None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="站点可抓取性诊断")
    parser.add_argument("url")
    parser.add_argument("--cookies", help="浏览器导出的 cookie JSON")
    parser.add_argument("--state", help="browser_session.py 写出的会话文件（含 cookie + User-Agent）")
    parser.add_argument("--html", help="把原始 HTML 写到该文件")
    parser.add_argument("--timeout", type=float, default=25.0)
    parser.add_argument(
        "--ua",
        default=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
    )
    args = parser.parse_args(argv)

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": args.ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
    )
    if args.cookies:
        jar = load_cookies(args.cookies)
        session.cookies.update(jar)
        print(f"[i] 载入 {len(jar)} 个 cookie：{', '.join(jar)}")
    if args.state:
        jar2, ua2 = load_state(args.state)
        session.cookies.update(jar2)
        if ua2:
            session.headers["User-Agent"] = ua2  # cf_clearance 绑定 UA，必须一致
        print(f"[i] 从会话文件载入 {len(jar2)} 个 cookie：{', '.join(jar2)}")
        if ua2:
            print(f"[i] 同步会话 User-Agent：{ua2[:60]}...")

    jar = {c.name: c.value for c in session.cookies}

    try:
        resp = session.get(args.url, timeout=args.timeout, allow_redirects=True)
    except requests.RequestException as exc:
        print(f"[x] 请求失败：{type(exc).__name__}: {exc}")
        print("    连接层就失败的话，检查网络/代理，而不是选择器。")
        return 2

    body = resp.text
    print(f"URL      : {resp.url}")
    print(f"状态码   : {resp.status_code}")
    print(f"服务器   : {resp.headers.get('Server')}")
    print(f"cf-RAY   : {resp.headers.get('CF-RAY')}")
    print(f"cf-mitigated: {resp.headers.get('cf-mitigated')}")
    print(f"内容类型 : {resp.headers.get('Content-Type')}")
    print(f"字节数   : {len(resp.content)}")
    print(f"编码     : {resp.encoding} (apparent: {resp.apparent_encoding})")

    found = [m for m in CF_MARKERS if m in body]
    if found:
        print(f"验证标记 : {found}")
    print(f"cf_clearance cookie: {'有' if 'cf_clearance' in session.cookies else '无'}")

    if args.html:
        Path(args.html).write_text(body, encoding="utf-8")
        print(f"[i] 原始 HTML 已写入 {args.html}")

    behind_cf = (resp.headers.get("Server", "").lower() == "cloudflare"
                 or "cf-ray" in {k.lower() for k in resp.headers})
    print()
    if found or resp.headers.get("cf-mitigated") == "challenge":
        print("[结论] Cloudflare 人机验证页，拿到的不是真实内容。")
        print("       两条路：")
        print("       A) 浏览器导出 cookie 后重跑：python diag_site.py <url> --cookies cookies.json")
        print("       B) 用有头浏览器让本人过验证再接管会话（脚本在上一层根目录）：")
        print(f"          双击  ..\\browser-session.cmd   或   python ..\\browser_session.py \"{args.url}\"")
        print(f"          python diag_site.py \"{args.url}\" --state ..\\browser-state.json")
        print("       注意 cf_clearance 绑定 IP + User-Agent，且几十分钟后会过期。")
        return 3
    if resp.status_code in BLOCK_HINTS:
        print(f"[结论] HTTP {resp.status_code} — {BLOCK_HINTS[resp.status_code]}")
        return 3
    if resp.status_code == 200:
        print("[结论] 页面正常返回，可以开始核对选择器了。")
        if behind_cf and "cf_clearance" not in session.cookies:
            print("[i] 站点在 Cloudflare 后面，但本次没有触发挑战 —— 说明对你的 IP/UA 放行了。")
            print("    这种情况直接抓即可，不必折腾 cf_clearance；")
            print("    若之后出现 403 challenge，再用 browser_session.py 让浏览器过验证。")
        return 0
    print(f"[结论] HTTP {resp.status_code}，未识别，请人工看原始 HTML。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
