# -*- coding: utf-8 -*-
"""交互入口：只问「网址」和「格式（含只挑几卷）」，其余走配置默认值。

被两个地方共用：
  · `start.py`（源码方式，双击 run.cmd）
  · 打包后的 `novel-scraper.exe`（无参数启动时，PyInstaller 冻结）

冻结环境下不再 `subprocess` 调起 Python，而是直接调用 novel_scraper 的
`main(argv)`——exe 内部没有独立的 python.exe，同进程调用最稳。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# 便携版要在任何机器上都不乱码：强制 UTF-8 输出，并让子进程也继承
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

FORMATS = {
    "1": ("both", "TXT + EPUB（推荐）"),
    "2": ("epub", "只要 EPUB"),
    "3": ("txt", "只要 TXT"),
}


def _ask(prompt: str, default: str = "") -> str:
    tail = f"（直接回车 = {default}）" if default else ""
    try:
        ans = input(f"{prompt}{tail}\n> ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n已取消。")
        sys.exit(1)
    return ans or default


def _pause(msg: str = "按回车键关闭窗口…") -> None:
    """收尾时等一次回车，别让双击启动的控制台窗口一闪就消失。

    非交互环境（管道、重定向、CI）直接跳过，否则脚本会被挂住；设
    `NOVEL_SCRAPER_NO_PAUSE=1` 也能强制跳过。
    """
    if os.environ.get("NOVEL_SCRAPER_NO_PAUSE"):
        return
    try:
        if not sys.stdin or not sys.stdin.isatty():
            return
    except (AttributeError, ValueError):
        return
    try:
        input(f"\n{msg}")
    except (EOFError, KeyboardInterrupt):
        print()


def _find_any(app_dir: Path, name: str) -> Path | None:
    """会话/ cookie 文件可能在包文件夹里，也可能在根目录（browser_session.py 那里）。"""
    for base in (app_dir, app_dir.parent):
        p = base / name
        if p.is_file():
            return p
    return None


def _start_log(app_dir: Path, argv: list[str]) -> Path | None:
    """和抓取进程共用同一份日志：父进程先建文件，再通过环境变量传给子进程。"""
    try:
        import novel_scraper
        return novel_scraper.start_logging(None, argv)
    except Exception:
        return None


# ---------------------------------------------------------------- 分卷选择
VOL_CFG = ".last-volumes"       # {"url":…, "tokens":[…], "names":[…]}，选完自动存下来


def _vol_cfg_path(app_dir: Path) -> Path:
    return app_dir / VOL_CFG


def _load_vol_cfg(app_dir: Path, url: str) -> dict:
    """读回上次选的分卷；换了书（URL 不同）就当没选过。"""
    try:
        data = json.loads(_vol_cfg_path(app_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("url") != url:
        return {}
    return data


def _save_vol_cfg(app_dir: Path, url: str, tokens: list[str], names: list[str]) -> None:
    try:
        _vol_cfg_path(app_dir).write_text(
            json.dumps({"url": url, "tokens": tokens, "names": names},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def _clear_vol_cfg(app_dir: Path) -> None:
    try:
        _vol_cfg_path(app_dir).unlink()
    except OSError:
        pass


def _describe_vols(names: list[str]) -> str:
    if not names:
        return "全部卷"
    if len(names) <= 3:
        return "、".join(names)
    return "、".join(names[:3]) + f" 等 {len(names)} 卷"


def _resolve_tokens(vols: list[dict], tokens: list[str]) -> tuple[list[dict], list[str]]:
    """把用户输入的卷号/卷名对上列表里的卷；对不上的原样返回。"""
    picked: list[dict] = []
    missing: list[str] = []
    for tok in tokens:
        hit = None
        if tok.isdigit():
            n = int(tok)
            if 1 <= n <= len(vols):
                hit = vols[n - 1]
        else:
            for v in vols:
                if v.get("name") == tok:
                    hit = v
                    break
            if hit is None:
                for v in vols:
                    if tok and tok in (v.get("name") or ""):
                        hit = v
                        break
        if hit is None:
            missing.append(tok)
        elif hit not in picked:
            picked.append(hit)
    return picked, missing


def _list_volumes(app_dir: Path, scraper: Path | None, base_argv: list[str]) -> list[dict] | None:
    """调子进程只读目录列出分卷（表格直接打到屏幕，卷数据用文件回传）。"""
    tmp = app_dir / ".volumes.json"
    try:
        tmp.unlink()
    except OSError:
        pass
    argv = [*base_argv, "--list-volumes", str(tmp)]
    cmd = ([sys.executable, *argv] if getattr(sys, "frozen", False)
           else [sys.executable, str(scraper), *argv])
    print()
    print("-" * 58)
    print("正在读取目录并识别分卷（只读目录，不抓正文）…")
    print("-" * 58)
    try:
        rc = subprocess.call(cmd, cwd=str(app_dir))
    except KeyboardInterrupt:
        print("\n已取消。")
        return None
    if rc != 0 or not tmp.is_file():
        print("[x] 没取到分卷列表：目录页打不开，或这个站点没有配置。")
        print("    可以先选 1/2/3 试抓；要加站点见《使用说明》的 sites.json 一节。")
        return None
    try:
        data = json.loads(tmp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    try:
        tmp.unlink()
    except OSError:
        pass
    vols = data.get("volumes") or []
    if not vols:
        print("[x] 这本书没识别出分卷。")
        return None
    print(f"\n共 {len(vols)} 卷（上面表格里的序号就是卷号）。")
    return vols


def _ask_volumes(app_dir: Path, url: str, vols: list[dict], current: dict) -> dict:
    """让用户按空格分隔挑卷；回车=保持当前，0/all/全部=清空回全书。"""
    cur_tokens = current.get("tokens") or []
    default = " ".join(cur_tokens) if cur_tokens else "all"
    print()
    print('输入要下载的卷：卷号或卷名都行，多个用空格隔开，例如  1 9  或  "第一卷 第九卷"')
    print("  回车 = 保持当前选择；输入  all（或 0 / 全部）= 抓整本，不分卷。")
    ans = _ask("选择分卷：", default).strip()
    low = ans.lower()
    if low in ("all", "0", "全部", "全书", "*"):
        _clear_vol_cfg(app_dir)
        print("[i] 已改为「全部卷」（下载时不再按卷挑选）。")
        return {}
    tokens = [t for t in re.split(r"[,，、;；\s　]+", ans) if t]
    if not tokens:
        return current
    picked, missing = _resolve_tokens(vols, tokens)
    if missing:
        print(f"[x] 没找到：{'、'.join(missing)}")
        print("    卷号看上面表格；卷名可以直接写「第三卷」这种前缀。原来的选择没变。")
        return current
    names = [str(v.get("name")) for v in picked]
    _save_vol_cfg(app_dir, url, [t for t in tokens], names)
    print(f"[+] 已选 {len(names)} 卷：{'、'.join(names)}")
    print("    已保存，回到主选择菜单 ✓")
    return {"tokens": tokens, "names": names}


def run_interactive(app_dir: Path, scraper: Path | None = None) -> int:
    """问网址 + 格式（可选分卷），然后抓取。返回退出码。"""
    last_url_file = app_dir / ".last-url"
    log_path = _start_log(app_dir, ["(交互模式)"])

    print("=" * 58)
    print("  小说爬虫 —— 输入网址即可，串行限速抓取")
    print("=" * 58)
    print("支持整本抓取，也可以只挑几卷；输出 TXT / EPUB（EPUB 一卷一个文件，插图按原位内嵌）。")
    print()

    try:
        last = last_url_file.read_text(encoding="utf-8").strip()
    except OSError:
        last = ""

    url = _ask("① 请粘贴小说目录页网址：", last)
    if not url:
        print("[x] 没有网址，退出。")
        return 1
    if not url.lower().startswith(("http://", "https://", "file://")):
        url = "https://" + url

    state_file = _find_any(app_dir, "browser-state.json")
    cookies_file = _find_any(app_dir, "cookies.json")
    base_argv = ["--url", url]
    if state_file:
        base_argv += ["--state", str(state_file)]
    if cookies_file:
        base_argv += ["--cookies", str(cookies_file)]

    # ② 主选择菜单：格式 1/2/3，或 4 = 挑分卷（挑完回到这个菜单）
    vol_cfg = _load_vol_cfg(app_dir, url)
    chosen_names = list(vol_cfg.get("names") or [])
    vols_cache: list[dict] | None = None
    while True:
        print()
        print("② 要什么格式？（4 只挑分卷，可以反复改）")
        for key in sorted(FORMATS):
            print(f"   {key}. {FORMATS[key][1]}")
        print(f"   4. 选择分卷下载（当前：{_describe_vols(chosen_names)}）")
        choice = _ask("请输入序号：", "1").strip()

        if choice == "4":
            if vols_cache is None:
                vols_cache = _list_volumes(app_dir, scraper, base_argv)
            if not vols_cache:
                continue
            vol_cfg = _ask_volumes(app_dir, url, vols_cache, vol_cfg)
            chosen_names = list(vol_cfg.get("names") or [])
            continue

        if choice not in FORMATS:
            print(f"[x] 不认识的序号：{choice}（可选 1 / 2 / 3 / 4）")
            continue
        fmt, fmt_label = FORMATS[choice]
        break

    # 成果直接落到「包文件夹的上一层」，即工作区根目录 —— 便携文件夹本身保持干净
    out_dir = str(app_dir.parent / "output")
    argv = [*base_argv, "-o", out_dir, "--format", fmt]
    if chosen_names:
        argv += ["--volumes", " ".join(chosen_names)]
        fmt_label += f"（只抓 {'、'.join(chosen_names)}）"
    if log_path:
        argv += ["--log-dir", str(log_path.parent)]


    print()
    print("-" * 58)
    print(f"开始抓取：{fmt_label}")
    print(f"网址：{url}")
    print(f"输出：{out_dir}")
    if log_path:
        print(f"日志：{log_path}")
    print("想中途停止直接按 Ctrl+C，已经抓好的章节会保留，下次自动续传。")
    print("-" * 58)
    print()

    try:
        if getattr(sys, "frozen", False):
            # 冻结环境没有独立的 python.exe，交给打包时指定的启动脚本
            cmd = [sys.executable, *argv]
            rc = subprocess.call(cmd, cwd=str(app_dir))
        else:
            # 源码方式：独立进程，cwd 必须是脚本所在目录（要读 sites.json / 写 output）
            cmd = [sys.executable, str(scraper), *argv]
            rc = subprocess.call(cmd, cwd=str(app_dir))
    except KeyboardInterrupt:
        print("\n[!] 已中断。已抓章节保留在输出目录里，重跑会自动跳过。")
        _pause()
        return 130

    if rc == 0:
        try:
            last_url_file.write_text(url, encoding="utf-8")
        except OSError:
            pass
        print()
        print("-" * 58)
        print(f"完成！成品在：{app_dir / out_dir}")
        print("  epub/  书名.epub       —— 整本电子书（可直接拖进阅读器）")
        print("         书名 - 01 …epub —— 分卷电子书：一卷一个文件，各自带该卷插图")
        print("         书名.txt        —— 全书总览文本")
        print("  txt/   00001_….txt     —— 分章文本（续传依据）")
        print("         书名 - 01 ….txt —— 各卷合并文本")
        print("  images/                 —— 插图原图")
        if chosen_names:
            print(f"  本次只抓了：{'、'.join(chosen_names)}")
            print("  想抓其它卷：重跑本程序，选 4 改分卷（回车保持、输入 all 恢复整本）")
        if log_path:
            print(f"  运行日志        —— {log_path}")
        print("-" * 58)
    else:
        print()
        print(f"[x] 抓取失败（退出码 {rc}）。常见原因：")
        print("    · 站点不在 sites.json 里 → 按 README 添加选择器配置")
        print("    · 被 Cloudflare 拦 → 用浏览器过一次验证，导出 cookie 后加 --cookies 重试")
        print("    · 网址不是目录页 → 需要书目页/目录页地址，不是某一章的地址")
        if log_path:
            print(f"    · 详细报错见日志：{log_path}")
    # 双击启动时不自动关窗 —— 让用户看清结果，自己按回车退出。
    _pause()
    return rc
