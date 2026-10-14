#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
browser_session.py —— 有头浏览器会话工具（给人用的过验证环节）

用途
    有些站点挂了 Cloudflare 人机验证，纯 requests 请求
    只会拿到「Just a moment...」挑战页。本脚本打开一个**看得见**的真实浏览器，
    由你本人完成验证；脚本检测到验证通过后，把该会话的 cookies + User-Agent
    存成 browser-state.json，供爬虫复用（爬虫在包文件夹或根目录找到它就会自动加载）。

边界（重要）
    本脚本不做任何绕过：不解验证码、不伪造 cf_clearance、不用 TLS 指纹伪装，
    也不隐藏浏览器自动化特征。它只做两件事——开浏览器、把**你本人通过验证后**
    的会话存下来。验证这一步始终由人完成。

用法（直接双击根目录的 browser-session.cmd 最省事）
    python browser_session.py "https://<站点域名>/"
    python browser_session.py "https://<站点域名>/" -o state.json
    python browser_session.py "<目录页 URL>" --browser chrome   # 指定浏览器
    python browser_session.py "<目录页 URL>" --profile          # 持久化 profile，下次免验证

    默认把会话写到本文件所在目录（= 根目录）的 browser-state.json。

依赖
    pip install playwright
    （默认用系统已装的 Edge，不需要 `playwright install` 下载浏览器）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Cloudflare 挑战页特征：出现这些说明还没过验证
CHALLENGE_MARKERS = (
    "just a moment",
    "challenge-platform",
    "_cf_chl",
    "cf-chl-",
    "checking your browser",
    "enable javascript and cookies",
    "cf-mitigated",
    "请稍候",
    "正在验证",
)

# 会话与 profile 都放在本脚本旁边（根目录），爬虫会自动找到 browser-state.json
APP_DIR = Path(__file__).resolve().parent
DEFAULT_STATE = str(APP_DIR / "browser-state.json")
DEFAULT_PROFILE = str(APP_DIR / "_browser-profile")


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def looks_like_challenge(status: int, text: str) -> bool:
    """判断当前页面是不是仍在 CF 验证/拦截页。"""
    head = (text or "")[:8000].lower()
    if status in (403, 429, 503):
        return True
    if "cf-mitigated" in head:
        return True
    hits = sum(marker in head for marker in CHALLENGE_MARKERS)
    # 真实小说页不太可能同时命中多个挑战特征；命中即视为未通过
    return hits >= 2


def probe(page) -> tuple[int, str]:
    """读取当前页面的状态码和正文（用最稳的方式）。"""
    status = 0
    try:
        resp = page.request.get(page.url, timeout=15000)
        status = resp.status
        text = resp.text()
        if text:
            return status, text
    except Exception as exc:  # 网络抖动等，退回到 content()
        log(f"    (探测请求失败，改用页面内容判断：{exc})")
    try:
        return status or 200, page.content()
    except Exception:
        return status, ""


def wait_for_pass(page, timeout_s: float) -> bool:
    """轮询等待人工完成验证。返回是否通过。"""
    import time

    deadline = time.monotonic() + timeout_s
    last_status = None
    reported = False
    while time.monotonic() < deadline:
        try:
            page.wait_for_load_state("domcontentloaded", timeout=2000)
        except Exception:
            pass

        status, text = probe(page)
        if status != last_status:
            log(f"    当前状态码：{status}")
            last_status = status

        if not looks_like_challenge(status, text):
            return True

        if not reported:
            log("    页面仍是验证/拦截页，请在浏览器窗口里手动完成验证……")
            reported = True
        time.sleep(2.0)

    return False


def save_state(context, page, out_path: Path, url: str) -> dict:
    """把 cookies + User-Agent 存成 novel_scraper.py 能读的格式。"""
    user_agent = page.evaluate("() => navigator.userAgent")
    cookies = context.cookies()
    names = [c.get("name", "") for c in cookies]
    state = {
        "url": url,
        "user_agent": user_agent,
        "cookies": [
            {
                "name": c.get("name"),
                "value": c.get("value"),
                "domain": c.get("domain"),
                "path": c.get("path"),
            }
            for c in cookies
        ],
    }
    out_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"[ok] 会话已保存：{out_path}（{len(cookies)} 个 cookie）")
    log(f"     User-Agent：{user_agent}")
    if "cf_clearance" in names:
        log("[ok] 拿到 cf_clearance —— 这就是通过验证的凭证。")
    else:
        log("[!] 没有 cf_clearance：这个站可能不用 CF 挑战，或验证还没真正通过。")
    return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="打开有头浏览器，由你本人通过人机验证，再把会话保存下来给爬虫复用。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("url", help="要打开的页面 URL（站点首页或目录页）")
    parser.add_argument("-o", "--out", default=DEFAULT_STATE, help=f"会话输出文件（默认 {DEFAULT_STATE}）")
    parser.add_argument("--browser", default="msedge",
                        help="浏览器：msedge（默认）/ chrome / chromium")
    parser.add_argument("--timeout", type=float, default=300.0,
                        help="等待人工完成验证的秒数（默认 300）")
    parser.add_argument("--profile", action="store_true",
                        help="使用持久化 profile 目录，cookie 跨次保留（过了就不用再过）")
    parser.add_argument("--profile-dir", default=DEFAULT_PROFILE, help="profile 目录名")
    parser.add_argument("--keep-open", action="store_true",
                        help="保存后不关闭浏览器，方便你自己接着翻页查看结构")
    args = parser.parse_args(argv)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("[x] 没装 playwright。请先运行：\n    python -m pip install playwright")
        return 2

    out_path = Path(args.out).expanduser()
    profile_dir = Path(args.profile_dir).expanduser()
    if args.profile:
        profile_dir.mkdir(parents=True, exist_ok=True)
        log(f"[i] 使用持久化 profile：{profile_dir.resolve()}")

    log("=" * 62)
    log("有头浏览器会话工具")
    log("=" * 62)
    log(f"[i] 即将打开 {args.browser}，访问：{args.url}")
    log("[i] 浏览器里如果出现 Cloudflare 验证，请**你本人**点一下完成它。")
    log("    （本工具不会替你绕过验证，也不隐藏自动化特征）")
    log("")

    with sync_playwright() as p:
        launch_kwargs: dict = {"headless": False, "args": ["--start-maximized"]}
        if args.browser and args.browser != "chromium":
            launch_kwargs["channel"] = args.browser
        if args.profile:
            launch_kwargs["user_data_dir"] = str(profile_dir)
            # 持久化上下文不能再用 channel 之外的额外 launch 参数差异
            context = p.chromium.launch_persistent_context(**launch_kwargs)
            page = context.pages[0] if context.pages else context.new_page()
        else:
            browser = p.chromium.launch(**launch_kwargs)
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            page = context.new_page()

        try:
            page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
            log(f"[i] 已打开，标题：{page.title()!r}")

            if not wait_for_pass(page, args.timeout):
                log("[x] 等待超时，验证似乎没有通过。")
                log("    可以：确认浏览器窗口里页面已正常显示；或加大 --timeout；")
                log("    或加 --profile 保留 profile 下次接着来。")
                if not args.keep_open:
                    return 3
            else:
                log("[ok] 验证通过，页面已正常返回内容。")

            state = save_state(context, page, out_path, page.url)

            log("")
            log("下一步（先确认身份/结构，再正式抓取）：")
            log(f'  python "novel-scraper\\tools\\diag_site.py" "{args.url}" --state "{out_path}"')
            log(f'  python "novel-scraper\\novel_scraper.py" --state "{out_path}" '
                f'--url "{args.url}" -o output --format both')
            log("便携版：把 browser-state.json 放到 novel-scraper.exe 旁边，或用问答模式启动，")
            log("        爬虫会自动加载（包文件夹和根目录都会找）。")

            if args.keep_open:
                log("[i] --keep-open：浏览器保持打开，按回车关闭。")
                input()
            return 0
        finally:
            try:
                if not args.keep_open:
                    context.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
