"""单文件小说爬虫样板（TXT + EPUB 输出）。

用法：
    python novel_scraper.py https://demo.example.com/book/123/            # 指定目录页
    python novel_scraper.py https://demo.example.com/book/123/ --start 1 --end 20
    python novel_scraper.py https://demo.example.com/book/123/ --dry-run  # 只看目录
    python novel_scraper.py https://demo.example.com/book/123/ --epub     # 同时导出 EPUB
    python novel_scraper.py --rebuild-epub output/书名                    # 不联网，从已抓章节重建 EPUB
    python novel_scraper.py --url file:///.../toc.html                    # 本地文件调试（离线）

离线自测（不需要联网，也不需要任何第三方服务）：
    python verify_sample.py

依赖：pip install requests beautifulsoup4（EPUB 为纯标准库实现，无额外依赖）
"""
from __future__ import annotations

import argparse
import atexit
import contextlib
import html
import json
import os
import random
import re
import sys
import time
import uuid
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

try:
    from bs4 import BeautifulSoup as _BS  # noqa: F401
except ImportError:  # pragma: no cover
    sys.exit("缺少依赖：pip install requests beautifulsoup4")

# 便携版/重定向输出时也要保证中文不乱码
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# ============================================================ 站点配置（改这里）
# 适配新站只需要：在 sites.json 里加一段 hosts 配置 + 用 --dry-run/--inspect 核对选择器。
if getattr(sys, "frozen", False):  # PyInstaller 打包后，配置在 exe 旁边
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent
HOSTS_FILE = APP_DIR / "sites.json"

# ============================================================ 运行日志
# 每次运行都在「根目录」（便携包文件夹的上一层，与 output/ 同级）的 log/ 下留一份，
# 方便事后查「哪一章失败、被哪个站点拦了」。日志只做记录，写不进去也不影响抓取。
LOG_PATH: Path | None = None
LOG_PATH_ENV = "NOVEL_SCRAPER_LOG"          # 父进程 → 子进程传递同一个日志文件
LOG_DIR_ENV = "NOVEL_SCRAPER_LOG_DIR"       # 自定义日志目录


class _Tee:
    """把写到 stdout/stderr 的内容顺带写一份进日志文件。"""

    def __init__(self, stream, fp):
        self._stream = stream
        self._fp = fp

    def write(self, data):
        try:
            self._stream.write(data)
        except (ValueError, OSError):
            pass
        try:
            self._fp.write(data)
        except (ValueError, OSError):
            pass
        return len(data)

    def flush(self):
        for target in (self._stream, self._fp):
            try:
                target.flush()
            except Exception:
                pass

    def __getattr__(self, name):        # isatty/encoding/fileno 等交给原流
        return getattr(self._stream, name)


def resolve_log_dir(log_dir: str | os.PathLike | None = None) -> Path:
    """日志目录：--log-dir > 环境变量 > 根目录/log（APP_DIR 的上一层）。"""
    if log_dir:
        return Path(log_dir).expanduser()
    env = os.environ.get(LOG_DIR_ENV)
    if env:
        return Path(env).expanduser()
    return APP_DIR.parent / "log"


def start_logging(log_dir: str | os.PathLike | None = None,
                  argv: list[str] | None = None) -> Path | None:
    """开始记录日志，返回日志文件路径（失败返回 None，绝不因此中断抓取）。"""
    global LOG_PATH
    if LOG_PATH is not None:
        return LOG_PATH

    env_path = os.environ.get(LOG_PATH_ENV)
    if env_path:
        # 交互启动时父进程已经建好了日志，父子共写同一份
        path = Path(env_path)
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = resolve_log_dir(log_dir) / f"novel-scraper-{stamp}.log"

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fp = path.open("a", encoding="utf-8", buffering=1)
    except OSError:
        return None

    LOG_PATH = path
    os.environ[LOG_PATH_ENV] = str(path)
    sys.stdout = _Tee(sys.stdout, fp)
    sys.stderr = _Tee(sys.stderr, fp)
    atexit.register(_close_log, fp)

    if not env_path:
        print(f"===== novel-scraper {datetime.now():%Y-%m-%d %H:%M:%S} =====", flush=True)
        print(f"程序：{sys.executable if getattr(sys, 'frozen', False) else __file__}", flush=True)
        print(f"目录：{Path.cwd()}", flush=True)
        print(f"参数：{' '.join(argv if argv is not None else sys.argv[1:])}", flush=True)
    return path


def _close_log(fp) -> None:
    try:
        print(f"===== 结束 {datetime.now():%Y-%m-%d %H:%M:%S} =====", flush=True)
    except Exception:
        pass
    try:
        fp.flush()
        fp.close()
    except Exception:
        pass


def find_hosts_file() -> Path:
    """定位 sites.json：优先用 exe/脚本旁边的（用户可改），否则用打包进包里的。

    打包版把 sites.json 也塞进了 exe，所以便携文件夹里即使没有这个文件也能跑；
    想让用户能改，就把 sites.json 放在 exe 旁边，这里会优先用它。
    """
    if HOSTS_FILE.is_file():
        return HOSTS_FILE
    bundled = Path(getattr(sys, "_MEIPASS", APP_DIR)) / "sites.json"
    return bundled if bundled.is_file() else HOSTS_FILE

# 部分站点的目录不是一页，而是 /list_1.html /list_2.html …；这里配置页数上限
MAX_LIST_PAGES = 200

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# ============================================================ 工具函数
BAD_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# 插图可能用 src，也可能用懒加载属性
IMG_ATTRS = ("src", "data-src", "data-original", "data-lazy-src",
             "data-echo", "data-url", "data-actualsrc")
# 明显不是正文插图的（站点 logo、图标、占位图）
IMG_SKIP_RE = re.compile(r"(logo|icon|avatar|blank|placeholder|spacer|/template/)", re.I)
IMG_EXT_BY_CT = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
    "image/gif": ".gif", "image/webp": ".webp", "image/bmp": ".bmp",
    "image/svg+xml": ".svg", "image/avif": ".avif",
}


def guess_ext(url: str, content_type: str | None = None) -> str:
    """按 Content-Type 或 URL 后缀判断图片格式。"""
    if content_type:
        ct = content_type.split(";")[0].strip().lower()
        if ct in IMG_EXT_BY_CT:
            return IMG_EXT_BY_CT[ct]
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".avif", ".svg"):
        return ".jpg" if suffix == ".jpeg" else suffix
    return ".jpg"


# 图片文件头：用来识别“下载回来的其实是报错页/防盗链页”这种情况。
# 有原图就内嵌，没有原图（404、返 HTML、空文件）就跳过——绝不把坏图塞进 EPUB。
IMG_SIGNATURES = (
    b"\xff\xd8\xff",          # JPEG
    b"\x89PNG\r\n\x1a\n",     # PNG
    b"GIF87a", b"GIF89a",     # GIF
    b"BM",                    # BMP
    b"II*\x00", b"MM\x00*",   # TIFF
)


def sniff_ext(data: bytes, default: str = ".jpg") -> str:
    """按文件头判断真实图片格式。

    站点常把 PNG 挂在 `.jpg` 后缀下；只信 URL 后缀会让 EPUB 里的
    media-type 与实际内容不匹配，EPUBCheck 报 OPF-029 / PKG-022，
    所以扩展名一律以文件头为准，拿不准才退回 default。
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if data.startswith(b"BM"):
        return ".bmp"
    if data.startswith((b"II*\x00", b"MM\x00*")):
        return ".tiff"
    if len(data) > 12 and data[4:8] == b"ftyp":
        brand = data[8:12].lower()
        return ".avif" if brand in (b"avif", b"avis") else ".heic"
    if b"WEBP" in data[:16]:
        return ".webp"
    if b"<svg" in data[:2048].lower():
        return ".svg"
    return default


def looks_like_image(data: bytes, ext: str = "") -> bool:
    """按文件头判断是不是真图片。拿不准就返回 False（宁可跳过也不内嵌坏图）。"""
    if not data or len(data) < 12:
        return False
    if any(data.startswith(sig) for sig in IMG_SIGNATURES):
        return True
    head = data[:64]
    if head[4:8] == b"ftyp":  # AVIF / HEIC
        return True
    if ext == ".webp" and b"WEBP" in head:
        return True
    if ext == ".svg":
        return b"<svg" in data[:2048].lower()
    return False


def image_size(data: bytes) -> tuple[int, int] | None:
    """只读文件头，返回图片的 (宽, 高)；认不出格式或数据不全时返回 None。

    打包时排除了 Pillow，所以这里手写各格式的头部解析 —— 只需要尺寸，不解码像素。
    支持 PNG / JPEG / GIF / BMP / WebP（VP8、VP8L、VP8X）与 SVG（读 viewBox）。
    """
    if not data or len(data) < 10:
        return None
    try:
        # PNG：IHDR 紧跟在 8 字节签名 + 4 字节长度 + 4 字节类型之后
        if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR" and len(data) >= 24:
            return (int.from_bytes(data[16:20], "big"),
                    int.from_bytes(data[20:24], "big"))
        # GIF：逻辑屏幕宽高，小端 16 位
        if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
            return (int.from_bytes(data[6:8], "little"),
                    int.from_bytes(data[8:10], "little"))
        # BMP：BITMAPINFOHEADER 的宽高（有符号，负高度表示自上而下）
        if data[:2] == b"BM" and len(data) >= 26:
            w = int.from_bytes(data[18:22], "little", signed=True)
            h = int.from_bytes(data[22:26], "little", signed=True)
            return (abs(w), abs(h))
        # WebP：三种子格式的尺寸位置各不相同
        if len(data) >= 30 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            fmt = data[12:16]
            if fmt == b"VP8 ":
                # 关键帧头：3 字节起始码 9d 01 2a，再 2 字节各 14 位宽高
                return (int.from_bytes(data[26:28], "little") & 0x3FFF,
                        int.from_bytes(data[28:30], "little") & 0x3FFF)
            if fmt == b"VP8L" and len(data) >= 25:
                # 1 字节签名 0x2f，随后 28 位里塞了 14 位宽 + 14 位高（都要 +1）
                bits = int.from_bytes(data[21:25], "little")
                return ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
            if fmt == b"VP8X":
                # 扩展格式用 24 位小端存「宽-1」「高-1」
                return (int.from_bytes(data[24:27], "little") + 1,
                        int.from_bytes(data[27:30], "little") + 1)
        # SVG：没有像素，退而求其次读 viewBox / width / height 的数值
        if b"<svg" in data[:2048].lower():
            head = data[:2048].decode("utf-8", "ignore")
            m = re.search(r'viewBox\s*=\s*["\']\s*[-\d.]+[ ,]+[-\d.]+[ ,]+'
                          r'([\d.]+)[ ,]+([\d.]+)', head)
            if m:
                return (int(float(m.group(1))), int(float(m.group(2))))
            w = re.search(r'\bwidth\s*=\s*["\']([\d.]+)', head)
            h = re.search(r'\bheight\s*=\s*["\']([\d.]+)', head)
            if w and h:
                return (int(float(w.group(1))), int(float(h.group(1))))
            return None
        # JPEG：逐段跳过，直到 SOFn 段（里面才有真实尺寸）
        if data[:2] == b"\xff\xd8":
            i, n = 2, len(data)
            while i + 4 <= n:
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker == 0xFF:
                    i += 1
                    continue
                # 无载荷的标记 / 结束标记
                if marker == 0x01 or 0xD0 <= marker <= 0xD9:
                    i += 2
                    continue
                seg_len = int.from_bytes(data[i + 2:i + 4], "big")
                # SOF0~SOF15（跳过 DHT=C4 / JPG=C8 / DAC=CC 这几个非 SOFn）
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    if i + 9 > n:
                        return None
                    return (int.from_bytes(data[i + 7:i + 9], "big"),
                            int.from_bytes(data[i + 5:i + 7], "big"))
                if seg_len < 2:
                    break
                i += 2 + seg_len
    except (IndexError, ValueError):
        return None
    return None


def _is_portrait(size: tuple[int, int] | None) -> bool:
    """宽:高 < 1（竖版）—— 封面几乎都是竖版，横版的多半是插页/海报。"""
    return bool(size) and size[1] > size[0]


def safe_name(text: str, limit: int = 60, fallback: str = "untitled") -> str:
    name = BAD_FILENAME.sub("_", text)
    name = re.sub(r"[\s\u3000]+", " ", name).strip(" .")
    return (name[:limit].rstrip(" .") or fallback)


CF_CHALLENGE_MARKERS = ("Just a moment", "challenge-platform", "_cf_chl", "cf-chl-")


def _is_cf_challenge(resp) -> bool:
    """判断响应是不是 Cloudflare 人机验证页（而不是真实内容）。"""
    if resp.headers.get("cf-mitigated") == "challenge":
        return True
    if resp.status_code != 403:
        return False
    head = resp.text[:6000] if resp.text else ""
    return sum(m in head for m in CF_CHALLENGE_MARKERS) >= 2


def load_cookies(path: str) -> dict[str, str]:
    """读取浏览器导出的 cookie：支持 cookies 数组、顶层对象、{"cookies": [...]}。"""
    try:
        # utf-8-sig 兼容记事本/PowerShell 写出的 BOM
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        sys.exit(f"[x] cookie 文件不存在：{path}")
    except json.JSONDecodeError as exc:
        sys.exit(
            f"[x] cookie 文件不是合法 JSON：{path}（{exc}）\n"
            "    正确格式：[{\"name\":\"cf_clearance\",\"value\":\"...\"}]"
        )
    if isinstance(data, dict):
        if isinstance(data.get("cookies"), list):
            data = data["cookies"]
        else:
            return {str(k): str(v) for k, v in data.items()}
    out: dict[str, str] = {}
    for item in data:  # type: ignore[union-attr]
        if isinstance(item, dict) and item.get("name"):
            out[str(item["name"])] = str(item.get("value", ""))
    return out


def load_ua(path: str) -> str | None:
    """从浏览器导出文件里取 User-Agent（cf_clearance 与 UA 绑定，必须一致）。"""
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    if path.lower().endswith(".json"):
        try:
            data = json.loads(text)
            if isinstance(data, dict) and data.get("user_agent"):
                return str(data["user_agent"])
        except json.JSONDecodeError:
            pass
    match = re.search(r"^(?:#\s*)?(?:User-Agent|user_agent)\s*[:=]\s*(\S.*)$",
                      text, re.MULTILINE | re.IGNORECASE)
    return match.group(1).strip() if match else None


def load_state(path: str) -> tuple[dict[str, str], str | None]:
    """读取 browser_session.py 写出的会话文件，返回 (cookies, user_agent)。

    cf_clearance 与本机 IP + User-Agent 绑定，所以 User-Agent 必须一起带上。
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        sys.exit(f"[x] 会话文件不存在：{path}\n"
                 f"    先过验证：双击工作区根目录的 browser-session.cmd"
                 f"（或 python ..\\browser_session.py <该站URL>）")
    except json.JSONDecodeError as exc:
        sys.exit(f"[x] 会话文件不是合法 JSON：{path}（{exc}）")

    cookies: dict[str, str] = {}
    for item in data.get("cookies", []) if isinstance(data, dict) else []:
        if isinstance(item, dict) and item.get("name"):
            cookies[str(item["name"])] = str(item.get("value", ""))
    user_agent = data.get("user_agent") if isinstance(data, dict) else None
    return cookies, (str(user_agent) if user_agent else None)


class Fetcher:
    """串行限速 + 重试 + 编码处理的取页器。"""
    def __init__(self, encoding: str | None = None, delay: float = 1.0,
                 retry: int = 3, timeout: float = 20.0,
                 user_agent: str | None = None,
                 cookies: dict[str, str] | None = None):
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        if user_agent:
            self.session.headers["User-Agent"] = user_agent
        if cookies:
            self.session.cookies.update(cookies)
        self.encoding = encoding
        self.delay = max(0.0, delay)
        self.retry = max(1, retry)
        self.timeout = timeout
        self._last = 0.0
        self.count = 0

    @property
    def user_agent(self) -> str:
        return self.session.headers.get("User-Agent", "")

    def _wait(self) -> None:
        gap = self._last + self.delay - time.monotonic()
        if gap > 0:
            time.sleep(gap)
        self._last = time.monotonic()

    def get(self, url: str, referer: str | None = None) -> str:
        """取一页 HTML；支持 file:// 便于本地调试。"""
        if url.startswith("file://"):
            from urllib.request import url2pathname

            parts = urlparse(url)
            raw = url2pathname(parts.path)
            if parts.netloc and not raw.startswith("\\\\"):
                raw = f"//{parts.netloc}{raw}"
            local = Path(raw)
            if not local.exists():
                raise FileNotFoundError(f"本地文件不存在：{local}")
            return local.read_text(encoding=self.encoding or "utf-8", errors="replace")

        last_error: Exception | None = None
        for attempt in range(1, self.retry + 1):
            self._wait()
            try:
                resp = self.session.get(
                    url, timeout=self.timeout,
                    headers={"Referer": referer} if referer else {},
                )
                if _is_cf_challenge(resp):
                    raise RuntimeError(
                        f"被 Cloudflare 人机验证拦截 (HTTP {resp.status_code}, "
                        f"cf-mitigated={resp.headers.get('cf-mitigated')})。\n"
                        "    页面返回的是验证页，不是小说内容。\n"
                        "    解决：先过验证，让浏览器把会话存下来。\n"
                        "         最省事：双击工作区根目录的 browser-session.cmd\n"
                        "         或命令行：python ..\\browser_session.py <该站URL>\n"
                        "         验证通过后它会写出 browser-state.json（爬虫会自动加载），也可以显式：\n"
                        "         python novel_scraper.py --state ..\\browser-state.json --url <URL> ...\n"
                        "         （cf_clearance 绑定 IP + User-Agent，别单独手改 UA）"
                    )
                resp.raise_for_status()
                self.count += 1
                if self.encoding:
                    return resp.content.decode(self.encoding, errors="replace")
                if resp.encoding:
                    return resp.text
                return resp.content.decode(resp.apparent_encoding or "utf-8", "replace")
            except RuntimeError:
                raise  # 验证/风控类问题不重试，重试也没用
            except requests.RequestException as exc:
                last_error = exc
                if attempt < self.retry:
                    wait = 1.5 * attempt + random.random()
                    print(f"    重试 {attempt}/{self.retry - 1}：{exc}；{wait:.1f}s 后重试",
                          file=sys.stderr)
                    time.sleep(wait)
        raise RuntimeError(f"抓取失败 {url}：{last_error}")

    def get_bytes(self, url: str, referer: str | None = None) -> tuple[bytes, str]:
        """取二进制内容（插图等），返回 (字节, 建议扩展名)。"""
        last_error: Exception | None = None
        for attempt in range(1, self.retry + 1):
            self._wait()
            try:
                resp = self.session.get(
                    url, timeout=self.timeout,
                    headers={"Referer": referer, "Accept": "image/*,*/*;q=0.8"}
                    if referer else {"Accept": "image/*,*/*;q=0.8"},
                )
                resp.raise_for_status()
                self.count += 1
                return resp.content, guess_ext(url, resp.headers.get("Content-Type"))
            except requests.RequestException as exc:
                last_error = exc
                if attempt < self.retry:
                    time.sleep(1.5 * attempt + random.random())
        raise RuntimeError(f"抓取图片失败 {url}：{last_error}")


def load_hosts() -> dict:
    f = find_hosts_file()
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8-sig")).get("hosts", {})
    return {}


def pick_config(url: str, hosts: dict) -> dict:
    """按域名或目录名匹配站点配置。"""
    if url.startswith("file://"):
        # 本地夹具：用路径里的目录名当站点名
        parts = Path(urlparse(url).path).parts
        for key, cfg in hosts.items():
            fixture = cfg.get("fixture_dir") or key
            if fixture and fixture in parts:
                return cfg
        return next(iter(hosts.values())) if hosts else {}

    host = (urlparse(url).netloc or "").lower()
    best: dict = {}
    for key, cfg in hosts.items():
        key = key.lower()
        if host == key or host.endswith("." + key):
            if len(key) > len(best.get("_key", "")):
                best = dict(cfg, _key=key)
    if not best:
        sys.exit(
            f"[x] 没有匹配 {host} 的配置。请在 {HOSTS_FILE.name} 的 hosts 里加一条，"
            "选择器可用 --dry-run 和浏览器 F12 核对。"
        )
    return best


# ============================================================ 目录解析
def parse_list_pages(cfg: dict, entry_url: str,
                     first_html: str | None = None) -> list[tuple[str, str]]:
    """返回 [(章节标题, 章节URL), ...]，保持网站顺序。"""
    template = cfg.get("list_url_template")
    link_sel = cfg.get("list_link")
    if not link_sel:
        sys.exit("[x] 配置缺少 list_link")

    pages: list[str] = []
    if template:
        # 目录页目录：/book/123/  ->  /book/123
        path = urlparse(entry_url).path.rstrip("/")
        base_dir = entry_url.rstrip("/") if not path or path.endswith(".html") else entry_url
        if base_dir.endswith(".html") or base_dir.endswith(".htm"):
            base_dir = base_dir.rsplit("/", 1)[0]
        for page in range(1, MAX_LIST_PAGES + 1):
            pages.append(
                template.replace("${BASE}", base_dir).format(
                    base_dir=base_dir, page=page
                )
            )
    else:
        pages = [entry_url]

    chapters: list[tuple[str, str]] = []
    seen: set[str] = set()
    for index, page_url in enumerate(pages, 1):
        try:
            # 第一页可能已由调用方取过（用于读书名），复用避免重复请求
            html = first_html if (index == 1 and first_html is not None) \
                else FETCHER.get(page_url, referer=entry_url)
        except FileNotFoundError:
            # 本地调试时用文件缺失表示“分页已结束”
            if template and index > 1:
                print(f"  目录第 {index} 页不存在，视为结束", file=sys.stderr)
                break
            raise
        soup = BeautifulSoup(html, "html.parser")
        found = 0
        for anchor in soup.select(link_sel):
            href = anchor.get("href") or ""
            title = anchor.get_text(" ", strip=True)
            if not href or not title or href.startswith(("javascript:", "#")):
                continue
            full = urljoin(page_url, href)
            if full in seen:
                continue
            seen.add(full)
            chapters.append((title, full))
            found += 1
        print(f"  目录第 {index} 页：拿到 {found} 章（累计 {len(chapters)}）", file=sys.stderr)
        if not template:
            break
        if found == 0:  # 分页已到末尾
            break

    if not chapters:
        sys.exit(
            "[x] 目录没解析到章节：\n"
            "    1) 如果日志里出现“Cloudflare 人机验证拦截”，先解决验证（见 --cookies）\n"
            "    2) 否则是选择器不对：检查 list_link / list_url_template"
        )
    return chapters


def _name_from_html(html: str, selector: str | None) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    if selector:
        node = soup.select_one(selector)
        if node and node.get_text(strip=True):
            return node.get_text(" ", strip=True)
    if soup.title and soup.title.get_text(strip=True):
        # 回退：站点标题通常是「书名-站点名」
        first = re.split(r"[-_|]", soup.title.get_text(strip=True))[0].strip()
        return first or None
    return None


def resolve_book_name(cfg: dict, entry_url: str, html: str | None = None) -> str:
    """书名优先级：配置写死 > 页面选择器 > 页面 title > URL 末段。"""
    if cfg.get("book_name"):
        return str(cfg["book_name"])
    selector = cfg.get("book_name_selector")
    if selector and html is None:
        try:
            html = FETCHER.get(entry_url, referer=entry_url)
        except Exception as exc:
            print(f"[!] 取书目页失败（{exc}），书名改用 URL 推断", file=sys.stderr)
    if html:
        try:
            name = _name_from_html(html, selector)
            if name:
                return name
        except Exception as exc:
            print(f"[!] 解析书名失败（{exc}），改用 URL 推断", file=sys.stderr)
    return re.sub(r"[\\/]+$", "", urlparse(entry_url).path).strip("/").replace("/", "_") or "novel"


# ============================================================ 正文解析
# 一章解析出来是一串「块」（blocks），按页面里的顺序排列：
#   "　　……"                       → 一个正文段落（已带全角缩进）
#   ("img", "https://…/31319.jpg") → 一张插图
# 这样插图能落在它在正文里的真实位置，而不是被堆到章首或章尾。
#
# 实现要点：不能一边遍历 descendants 一边 get_text（顺序会乱）。
# 做法是先把 <img> 换成唯一占位符、把块级边界换成换行，再一次取全文，
# 然后按占位符把文本切开 —— 顺序天然正确，也不受嵌套结构影响。

# 占位符用可打印字符，别用 \x00 这类控制字符：网页文本里可能出现任何字节，
# 而且可打印标记在调试打印时看得见。
IMG_PLACEHOLDER = "@@DSHIMG{}@@"
IMG_PLACEHOLDER_RE = re.compile(r"@@DSHIMG(\d+)@@")

# 本次运行里有多少章做了乱序/诱饵段落还原，收尾时汇报一句
DEOBFUSCATED = 0


def _js_imul(a: int, b: int) -> int:
    """复刻 JS 的 Math.imul：32 位有符号乘法（会溢出回绕）。"""
    r = ((a & 0xFFFFFFFF) * (b & 0xFFFFFFFF)) & 0xFFFFFFFF
    return r - 0x100000000 if r >= 0x80000000 else r


def deobfuscate_content(node) -> bool:
    """还原「打乱 DOM 顺序 + 塞诱饵段落」型反爬，返回是否真的动过。

    一类站点的做法：正文 <p> 的 DOM 顺序被打乱，靠行内 style="order:N"
    制造视觉顺序；再混进若干诱饵段（把真句子截断后接一句语义不通的假话），
    并用一段 CSS 把诱饵隐藏（`#content .<data-c>{height:0;opacity:0;
    position:absolute;left:-9999px}`）。页面空闲 7 秒后由一段 JS 重排 +
    删除诱饵 + 去掉 order。它的算法是纯函数，这里原样复刻：

        hash(p) = imul((((p*31+k)|0) ^ ((r>>3)&0xFFFF)), 2654435761) >>> 0

    段落的 `data-a` 属性值是 hash 的十六进制。在 p ∈ [0, max(t+50, 2000))
    里找到 hash 相等的那个 p，就是该段的真实序号；找不到、或序号 ≥ t，
    都是诱饵。t = data-t ^ (data-r % 256) 即正文段数。

    属性名与参数全部来自页面本身，缺任何一个就原样返回 —— 站点不玩这套时
    这里自然不生效，不是硬编码某个站。
    """
    sd = node.get("data-r")
    en = node.get("data-t")
    an = node.get("data-a")
    ek = node.get("data-k")
    if not (sd and en and an and ek):
        return False
    try:
        sd, en, ek = int(str(sd)), int(str(en)), int(str(ek))
    except ValueError:
        return False
    fc = node.get("data-c")
    total = en ^ (sd % 256)              # 正文段数：序号 ≥ total 的一律是诱饵
    if total <= 0:
        return False
    span = max(total + 50, 2000)         # 站点 JS 的搜索上限
    shift = (sd >> 3) & 0xFFFF

    table: dict[int, int] = {}
    for p in range(span):
        v = (p * 31 + ek) & 0xFFFFFFFF
        if v >= 0x80000000:
            v -= 0x100000000
        table.setdefault(_js_imul(v ^ shift, 2654435761) & 0xFFFFFFFF, p)

    kept: list[tuple[int, object]] = []
    dropped: list = []
    for para in node.find_all("p"):
        raw = para.get(an)
        if not raw:                      # 没带标记的段落照旧保留，排最前
            kept.append((-1, para))
            continue
        try:
            target = int(str(raw), 16) & 0xFFFFFFFF
        except ValueError:
            dropped.append(para)
            continue
        order = table.get(target, -1)
        if order < 0 or order >= total or (fc and fc in (para.get("class") or [])):
            dropped.append(para)
        else:
            kept.append((order, para))

    for para in dropped:
        para.decompose()
    kept.sort(key=lambda item: item[0])
    node.clear()
    for _, para in kept:
        node.append(para)
    for attr in ("data-r", "data-t", "data-c", "data-a", "data-k"):
        node.attrs.pop(attr, None)
    return True


def extract_blocks(node, page_url: str) -> list:
    """DOM 顺序产出 段落字符串 / ("img", url) 的混合列表。"""
    img_urls: list[str] = []
    for img in node.find_all("img"):
        raw = ""
        for attr in IMG_ATTRS:
            val = (img.get(attr) or "").strip()
            if val and not val.startswith("data:"):
                raw = val
                break
        if raw and not IMG_SKIP_RE.search(raw):
            img_urls.append(urljoin(page_url, raw))
            img.replace_with(IMG_PLACEHOLDER.format(len(img_urls) - 1))
        else:
            img.decompose()

    for br in node.find_all("br"):
        br.replace_with("\n")
    for block in node.find_all(["p", "div", "h1", "h2", "h3", "li", "td", "tr"]):
        block.append("\n")
    raw = node.get_text("", strip=False)

    blocks: list = []
    # 用 finditer 而不是 split：split 带分组时捕获组也是字符串，靠 isinstance
    # 根本区分不出「这是占位符」。按 match 位置切才是可靠的。
    pos = 0
    for m in IMG_PLACEHOLDER_RE.finditer(raw):
        if m.start() > pos:
            blocks.append(raw[pos:m.start()])
        blocks.append(("img", img_urls[int(m.group(1))]))
        pos = m.end()
    if pos < len(raw):
        blocks.append(raw[pos:])
    return blocks


def _lines_to_paragraphs(raw: str, title: str, cfg: dict) -> list[str]:
    """把原始文本切成正文段落，只做机械整理，不做任何屏蔽词过滤。

    这里曾经有一张「垃圾词」（屏蔽词）黑名单：内置词表加上站点
    `extra_junk_words`，任何含这些词的行都会被整行丢掉。该功能已整体移除
    （2026-xx，用户要求）—— 正文里出现什么都不再被自动删除，
    站点配置里写 `extra_junk_words` 也不会生效。

    现在只剩三种「非正文」行的处理，都不是屏蔽词匹配：
      * 空行；
      * 与本章标题完全相同的行（标题被页面重复排版了一次）；
      * 「上一章 / 下一章 / 返回目录」开头的导航行。

    段落一律原样返回，**不加段首缩进、不拼接**：这类站点的正文 CSS 里
    没有 text-indent，`<p>` 开头的两个空格在浏览器里会被折叠掉，所以
    「缩进」不是页面的排版。段与段之间怎么分隔交给写文件的环节决定
    （TXT 是一段一行、段间不空行，EPUB 靠 CSS 的 text-indent）。
    """
    paragraphs: list[str] = []
    for line in raw.replace("\r", "\n").split("\n"):
        line = re.sub(r"[\u00a0\u3000\s]+", " ", line).strip()
        if not line:
            continue
        if line == title:
            continue
        if line.startswith(("上一章", "下一章", "返回目录")):
            continue
        paragraphs.append(line)
    return paragraphs


def chapter_is_illustration(title: str, cfg: dict) -> bool:
    """标题命中插图关键词 → 该章以图为准（文本可能只是站点塞的填充句）。

    关键词可站点覆盖：hosts.json 里写 `illustration_title_words: ["插图", ...]`。
    默认值只是常见叫法，不是硬编码规则 —— 站点不用这类标题时，这一路自然不生效。
    """
    words = cfg.get("illustration_title_words") or ["插图", "插画", "彩图", "扉页", "口绘"]
    return any(w in (title or "") for w in words if w)


def parse_chapter(cfg: dict, title: str, url: str) -> dict:
    """抓一章，返回 {title, paragraphs, images, blocks, is_illustration}。"""
    global DEOBFUSCATED
    content_sel = cfg.get("chapter_content")
    if not content_sel:
        sys.exit("[x] 配置缺少 chapter_content")

    html = FETCHER.get(url)
    soup = BeautifulSoup(html, "html.parser")
    node = soup.select_one(content_sel)
    if node is None:
        raise RuntimeError(f"正文选择器未命中 {content_sel} @ {url}")

    # 先还原乱序/诱饵段落，后面的选择器与取文都在还原后的 DOM 上做
    if deobfuscate_content(node):
        DEOBFUSCATED += 1

    if cfg.get("chapter_title"):
        node_title = soup.select_one(cfg["chapter_title"])
        if node_title and node_title.get_text(strip=True):
            title = node_title.get_text(" ", strip=True)

    for junk_sel in cfg.get("chapter_junk", []):
        if not junk_sel or junk_sel == ".":
            continue
        for junk in node.select(junk_sel):
            junk.decompose()

    # 干净 DOM 上的顺序遍历：段落与插图混排
    blocks: list = []
    for item in extract_blocks(node, url):
        if isinstance(item, tuple):
            if not blocks or blocks[-1] != item:
                blocks.append(item)
            continue
        for para in _lines_to_paragraphs(item, title, cfg):
            blocks.append(para)

    is_illustration = chapter_is_illustration(title, cfg)
    if is_illustration:
        # 插图页的文本是站点塞的填充句子，只保留图片
        blocks = [b for b in blocks if isinstance(b, tuple)]
        paragraphs: list[str] = []
    else:
        paragraphs = [b for b in blocks if isinstance(b, str)]
    images = [b[1] for b in blocks if isinstance(b, tuple)]

    if not paragraphs and not images:
        raise RuntimeError(f"正文清洗后为空：{url}")
    return {
        "title": title,
        "paragraphs": paragraphs,
        "images": images,
        "blocks": blocks,
        "is_illustration": is_illustration,
    }


# ============================================================ EPUB 生成
# 纯标准库实现 EPUB 3，不引入第三方依赖。
#
# 打包单位是「卷」：每一卷一个 XHTML（若干章按顺序排在同一个文档里），
# 卷内章节用 <section> + 标题分隔，目录（nav）是 卷 → 章 的两级结构。
# 这样阅读器里一个文件对应一卷，翻页与定位都更接近实体书。
#
# 插图：标题命中「插图」的章按其页面里的 <img> 下载原图，内嵌到对应位置。
#
# 关键约束（写错阅读器就打不开）：
#   1) mimetype 必须是 ZIP 里的第一个条目，且必须以 STORED（不压缩）方式存储；
#   2) META-INF/container.xml 指向 OPF；
#   3) OPF 里 manifest 的每个 href 都要真实存在，spine 的 idref 都要有对应 item；
#   4) nav.xhtml 要带 properties="nav"，正文文档要是合法 XHTML；
#   5) 图片要在 manifest 里声明 media-type，href 相对 OPF 所在目录。
#
# 标题一律原样保留（含「第一卷 起始之章 (1)」这类前缀）—— 前缀是卷次信息，
# 删掉会让「第一章」这种标题在阅读器目录里分不清是哪一卷。

# 支持「第8.5卷」这类小数卷号（短篇集常插在两卷之间）。小数点必须单独可选，
# 否则「第8.5卷」不匹配、整卷章节会掉进「正文」桶里凭空多出一个卷文件。
VOLUME_RE = re.compile(
    r"第\s*[0-9一二三四五六七八九十百千零两]+(?:\s*[.．]\s*[0-9]+)?\s*[卷部篇集]"
)

# 「短篇集」的识别标志：整卷章节的卷名标签都带这个词时，这些卷属于同一部短篇集。
SHORT_STORY_WORD = "短篇"
SHORT_STORY_VOLUME = "短篇集"

EPUB_MIME_BY_EXT = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    ".svg": "image/svg+xml", ".avif": "image/avif",
}


class EpubChapter:
    """一章：标题 + 块序列（段落与插图按原顺序混排）。

    blocks 是权威字段；paragraphs / images 是为兼容旧调用留的派生视图。
    """

    __slots__ = ("title", "blocks", "is_illustration", "_derived")

    def __init__(self, title: str, paragraphs: list[str] | None = None,
                 images: list[str] | None = None, is_illustration: bool = False,
                 blocks: list | None = None):
        self.title = title
        self.is_illustration = is_illustration
        if blocks is not None:
            self.blocks = list(blocks)
        else:
            # 旧调用只给了段落/图片：没有顺序信息，就“图在前、文在后”
            self.blocks = [("img", u) for u in (images or [])]
            self.blocks += [p for p in (paragraphs or []) if isinstance(p, str)]
        self._derived = None

    @property
    def paragraphs(self) -> list[str]:
        return [b for b in self.blocks if isinstance(b, str)]

    @property
    def images(self) -> list[str]:
        return [b[1] for b in self.blocks if isinstance(b, tuple)]

    @classmethod
    def coerce(cls, ch):
        """兼容旧的 (标题, 段落) 二元组、dict、以及 EpubChapter。"""
        if isinstance(ch, cls):
            return ch
        if isinstance(ch, dict):
            return cls(ch.get("title", ""), ch.get("paragraphs"),
                       ch.get("images"), ch.get("is_illustration", False),
                       ch.get("blocks"))
        title, paragraphs = ch
        return cls(title, paragraphs)


def volume_mark(title: str) -> str:
    """章节标题里的「卷标记」：第一卷 / 第3部 / 第2篇 / 第一集；没有就是「正文」。"""
    m = VOLUME_RE.search(title or "")
    if not m:
        return "正文"
    return re.sub(r"\s+", "", m.group(0))


def volume_label(title: str) -> str:
    """卷标记后面紧跟的那一小段（可能是卷名，也可能只是章节名的一部分）。"""
    m = VOLUME_RE.search(title or "")
    if not m:
        return ""
    rest = (title or "")[m.end():].strip()
    seg = re.split(r"\s+", rest, maxsplit=1)[0].strip()
    seg = seg.strip("　·-—～~【】[]（）()").strip()
    return seg


VOLUME_LABEL_SHARE = 0.6   # 标签至少在这么多章节里重复出现，才当成“卷名”


def group_by_volume(chapters: list["EpubChapter"]) -> list[tuple[str, list]]:
    """按「卷标记」把章节分组，返回 [(卷名, 章节列表), ...]（保持目录顺序）。

    卷名 = 卷标记（如「第一卷」），仅当标记后那一段在整卷里反复出现（≥60%）才
    附上，作为真正的卷名。这样：整卷后缀一致的站能拿到「第十四卷 水都市的圣女」
    这种好名字，而每章后缀都不同的站不会裂成上百个假卷。
    """
    order: list[str] = []
    buckets: dict[str, list] = {}
    for ch in chapters:
        mark = volume_mark(ch.title)
        if mark not in buckets:
            buckets[mark] = []
            order.append(mark)
        # 目录顺序可能把同名卷拆开（如某卷后记被排到最后）——并入已有同名卷，
        # 但不改变其在书中的实际位置，只让“卷”这个打包单位完整。
        buckets[mark].append(ch)

    groups: list[tuple[str, list]] = []
    for mark in order:
        ch_list = buckets[mark]
        labels = Counter(volume_label(ch.title) for ch in ch_list)
        name = mark
        if labels:
            top_label, top_n = labels.most_common(1)[0]
            if top_label and top_n / len(ch_list) >= VOLUME_LABEL_SHARE:
                name = f"{mark} {top_label}".strip()
        groups.append((name, ch_list))
    return _merge_short_story_volumes(groups)


def _merge_short_story_volumes(groups: list[tuple[str, list]]) -> list[tuple[str, list]]:
    """把「短篇集」拆出来的多个卷并回一卷，位置取第一个短篇集卷的位置。

    有些书把短篇集标成「第一集 短篇故事」「第二集 短篇故事」…，按卷标记分组就会
    得到好几个小卷。这里只要某一卷的**每一章**标签都带「短篇」，就认定它是短篇集，
    多于一卷时合并成单一的「短篇集」卷。

    两条例外：
      · 带小数点的卷号（「第8.5卷 短篇集」）**不参与合并** —— 它自带完整卷号，
        自成一卷，不该被卷进一个笼统的「短篇集」里；
      · 只有一卷时保持原卷名（「第8.5卷 短篇集」这类名字信息量比「短篇集」大）。
    """
    ss_idx = [i for i, (name, ch_list) in enumerate(groups)
              if ch_list and "." not in name and "．" not in name
              and all(SHORT_STORY_WORD in volume_label(ch.title)
                      for ch in ch_list)]
    if len(ss_idx) < 2:
        return groups
    merged: list = []
    for i in ss_idx:
        merged.extend(groups[i][1])
    drop = set(ss_idx)
    rest = [g for i, g in enumerate(groups) if i not in drop]
    rest.insert(ss_idx[0], (SHORT_STORY_VOLUME, merged))
    return rest


# ------------------------------------------------------------ 分卷选择
class VolumeItem:
    """只有 title / index 两字段的轻量章节：给 group_by_volume 分组用。

    index 是该章在完整目录里的 1 基序号 —— 分章 TXT 的文件名用的就是它，
    所以按卷筛选时绝不能重新编号，否则续传和插图锚点都会错位。
    """

    __slots__ = ("title", "index")

    def __init__(self, title: str, index: int):
        self.title = title
        self.index = index


def volume_items(chapters: list[tuple[str, str]]) -> list[VolumeItem]:
    return [VolumeItem(t, i) for i, (t, _) in enumerate(chapters, 1)]


def parse_volume_tokens(spec: str) -> list[str]:
    """把「第一卷 第九卷」/「1,9」/「1 9」拆成 token 列表。"""
    return [t for t in re.split(r"[,，、;；\s　]+", spec or "") if t]


def _norm_vol_key(text: str) -> str:
    return re.sub(r"[\s　]+", "", (text or "")).strip("·-—–~～【】[]（）()。,")


def match_volumes(groups: list, tokens: list[str]) -> tuple[list[int], list[str]]:
    """用户输入的卷名/序号 → groups 下标（0 基）。返回 (命中的下标, 没命中的 token)。

    匹配顺序：纯数字当序号 → 卷名完全相同 → 只看卷标记（「第一卷」）→ 前缀 → 包含。
    """
    picked: list[int] = []
    missing: list[str] = []
    for tok in tokens:
        hit: int | None = None
        if tok.isdigit():
            idx = int(tok) - 1
            if 0 <= idx < len(groups):
                hit = idx
        if hit is None:
            key = _norm_vol_key(tok)
            for test in (
                lambda name: _norm_vol_key(name) == key,
                lambda name: _norm_vol_key(volume_mark(name)) == key,
                lambda name: _norm_vol_key(name).startswith(key),
                lambda name: bool(key) and key in _norm_vol_key(name),
            ):
                for idx, (name, _) in enumerate(groups):
                    if test(name):
                        hit = idx
                        break
                if hit is not None:
                    break
        if hit is None:
            missing.append(tok)
        elif hit not in picked:
            picked.append(hit)
    return picked, missing


def volume_table(groups: list, indent: str = "  ") -> str:
    """编号列出所有卷：序号、卷名、章数、首末章序号。"""
    width = len(str(len(groups)))
    lines: list[str] = []
    for i, (name, items) in enumerate(groups, 1):
        first = items[0].index if items else 0
        last = items[-1].index if items else 0
        lines.append(f"{indent}{i:>{width}}. {name}"
                     f"（{len(items)} 章，第 {first}~{last} 章）")
    return "\n".join(lines)


def _xesc(text: str) -> str:
    return html.escape(text or "", quote=True)


EPUB_CSS = """\
html, body { margin: 0; padding: 0; }
body { margin: 0 6%; line-height: 1.7; font-family: serif; }
h1 { font-size: 1.3em; margin: 1.4em 0 1em; text-align: center; font-weight: normal; }
h2 { font-size: 1.1em; margin: 1.6em 0 0.8em; text-align: center; font-weight: normal; }
p { margin: 0.45em 0; line-height: 1.7; text-indent: 2em; }
figure { margin: 1em 0; text-align: center; page-break-inside: avoid; }
figure img { max-width: 100%; max-height: 95vh; height: auto; }
.toc a { text-decoration: none; }
.toc ol { margin: 0.2em 0 0.2em 1.2em; }
body.cover-page { margin: 0; padding: 0; }
section.cover { margin: 0; padding: 0; text-align: center; }
section.cover img { max-width: 100%; max-height: 100vh; height: auto; }
p.src { font-size: 0.7em; word-break: break-all; color: #666; }
p.meta { font-size: 0.85em; color: #444; }
"""

_TOC_MAX = 150


def _chapter_blocks(ch_list: list, offset: int, img_names: dict) -> list[str]:
    """把若干章渲染成同一文档里的多个 <section>。offset 是该卷首章的全局序号。

    img_names 只包含“确实下载到并校验通过的图” —— 取不到图的插图章节直接
    不产生任何 <figure>，不留占位文字（这是内容缺失，不是需要提示的错误）。
    """
    parts: list[str] = []
    for k, ch in enumerate(ch_list):
        gidx = offset + k + 1
        parts.append(f'  <section epub:type="chapter" id="c{gidx}">')
        parts.append(f"  <h1>{_xesc(ch.title)}</h1>")
        n_img = len(ch.images)
        for block in (ch.blocks or ch.paragraphs):
            if isinstance(block, tuple):
                name = img_names.get(block[1])
                if not name:
                    continue
                alt = f"{ch.title} 插图" if n_img <= 1 else f"{ch.title} 插图{n_img}"
                parts.append("  <figure>")
                # 图片与卷文档同在 OEBPS/ 下，用纯文件名引用：
                # 不要写成 ../images/xxx —— EPUBCheck 解析父目录相对路径时会
                # 报 RSC-007「Referenced resource ... could not be found」，
                # 明明文件就在包里的 images/ 下。同目录纯文件名则 0 errors。
                parts.append(f'    <img src="{name}" alt="{_xesc(alt)}"/>')
                parts.append("  </figure>")
            elif block.strip():
                parts.append(f"  <p>{_xesc(block)}</p>")
        # 只含图片的插图页：若一张都没取到，保留一个空 section 让目录锚点不落空
        parts.append("  </section>")
    return parts


def _epub_vol_doc(book_name: str, vol_name: str, ch_list: list,
                  offset: int, img_names: dict) -> str:
    body = "\n".join(_chapter_blocks(ch_list, offset, img_names))
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<!DOCTYPE html>\n"
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="zh">\n'
        "<head>\n"
        '  <meta charset="utf-8"/>\n'
        f"  <title>{_xesc(vol_name)}</title>\n"
        '  <link rel="stylesheet" type="text/css" href="style.css"/>\n'
        "</head>\n"
        "<body>\n"
        f"{body}\n"
        "</body>\n"
        "</html>\n"
    )


def _epub_nav(book_name: str, groups: list, offsets: list[int]) -> str:
    """两级目录：卷（指向卷文件）→ 章（指向卷内锚点）。超长则每卷只列出卷。"""
    items: list[str] = []
    for v, (vol_name, ch_list) in enumerate(groups, 1):
        off = offsets[v - 1]
        head = f'<a href="vol{v:02d}.xhtml">{_xesc(vol_name)}</a>'
        if not ch_list:
            items.append(f"      <li>{head}</li>")
            continue
        if len(ch_list) > _TOC_MAX:
            items.append(f"      <li>{head}</li>")
            continue
        subs = "\n".join(
            f'          <li><a href="vol{v:02d}.xhtml#c{off + k + 1}">'
            f"{_xesc(ch.title)}</a></li>"
            for k, ch in enumerate(ch_list)
        )
        items.append(
            f"      <li>{head}\n        <ol>\n{subs}\n        </ol>\n      </li>"
        )
    body = "\n".join(items)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<!DOCTYPE html>\n"
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="zh">\n'
        "<head>\n"
        '  <meta charset="utf-8"/>\n'
        f"  <title>{_xesc(book_name)} - 目录</title>\n"
        "</head>\n"
        "<body>\n"
        '  <nav epub:type="toc" id="toc">\n'
        f"    <h1>{_xesc(book_name)}</h1>\n"
        '    <ol class="toc">\n'
        f"{body}\n"
        "    </ol>\n"
        "  </nav>\n"
        "</body>\n"
        "</html>\n"
    )


def _epub_cover(book_name: str, img_name: str) -> str:
    """封面页：直接用书里第一张「插图」原图，没有插图时这一页不存在。"""
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<!DOCTYPE html>\n"
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="zh">\n'
        "<head>\n"
        '  <meta charset="utf-8"/>\n'
        f"  <title>{_xesc(book_name)}</title>\n"
        '  <link rel="stylesheet" type="text/css" href="style.css"/>\n'
        "</head>\n"
        '<body class="cover-page">\n'
        '  <section epub:type="cover" class="cover">\n'
        f'    <img src="{img_name}" alt="{_xesc(book_name)}"/>'
        "\n  </section>\n"
        "</body>\n"
        "</html>\n"
    )


def _epub_title_page(book_name: str, source_url: str, created: str,
                     n_ch: int, n_vol: int, n_img: int,
                     vol_label: str = "", vol_index: int = 0,
                     total_vols: int = 0) -> str:
    """扉页。vol_label 非空 = 这是「分卷」出来的单卷文件，扉页写明是第几卷。"""
    src = f'  <p class="src">{_xesc(source_url)}</p>\n' if source_url else ""
    if vol_label:
        book_name = f"{book_name} {vol_label}"
        stats = f"第 {vol_index}/{total_vols} 卷 · 本卷 {n_ch} 章"
    else:
        stats = f"共 {n_vol} 卷 / {n_ch} 章"
    if n_img:
        stats += f" · 内嵌插图 {n_img} 张"
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<!DOCTYPE html>\n"
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="zh">\n'
        "<head>\n"
        '  <meta charset="utf-8"/>\n'
        f"  <title>{_xesc(book_name)}</title>\n"
        '  <link rel="stylesheet" type="text/css" href="style.css"/>\n'
        "</head>\n"
        "<body>\n"
        '  <section epub:type="titlepage">\n'
        f"  <h1>{_xesc(book_name)}</h1>\n"
        f"  <p class=\"meta\">{stats} · 抓取于 {_xesc(created)}</p>\n"
        f"{src}"
        "  </section>\n"
        "</body>\n"
        "</html>\n"
    )


def _download_images(chs: list, image_loader, on_progress=None) -> dict:
    """把书里所有插图的字节取齐，返回 {来源: (bytes, ext)}。单张失败只跳过。"""
    img_data: dict[str, tuple[bytes, str]] = {}
    want: list[str] = []
    for ch in chs:
        for u in ch.images:
            if u not in img_data and u not in want:
                want.append(u)
    if image_loader is not None:
        for idx, u in enumerate(want, 1):
            try:
                data, ext = image_loader(u)
            except Exception as exc:  # 单张图失败不该毁掉整本书
                if on_progress:
                    on_progress(f"    [!] 插图下载失败（跳过）：{exc}")
                continue
            if data:
                img_data[u] = (data, ext)
            if on_progress and (idx % 10 == 0 or idx == len(want)):
                on_progress(f"    插图 {idx}/{len(want)}")
    return img_data


def _write_epub_file(
    out_path: Path,
    book_name: str,
    chapters: list,
    source_url: str = "",
    author: str = "",
    img_data: dict | None = None,
    vol_label: str = "",
    vol_index: int = 0,
    total_vols: int = 0,
) -> Path:
    """把 chapters（整本，或者分卷后的某一卷）写成一个自洽的 EPUB 3 文件。

    图片按「本文件内的出现顺序」重新编号（0001.jpg…），所以分卷出来的每个
    .epub 都是独立完整的：自己的图、自己的封面、自己的目录与卷文档。
    """
    chs = [EpubChapter.coerce(c) for c in chapters]
    if not chs:
        raise ValueError("EPUB 至少需要一个章节")
    img_data = img_data or {}

    now = datetime.now(timezone.utc)
    created = now.astimezone().strftime("%Y-%m-%d %H:%M")
    modified = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    book_title = f"{book_name} {vol_label}".strip() if vol_label else book_name
    # 分卷文件必须用不同的 identifier，否则同一本书的几卷在书架上是同一个 UUID
    uid_src = f"{source_url or book_name}|{vol_label}"
    book_id = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, uid_src)}"

    groups = group_by_volume(chs)
    offsets: list[int] = []
    acc = 0
    for _, ch_list in groups:
        offsets.append(acc)
        acc += len(ch_list)

    # ---- 图片命名（顺序编号，方便阅读器与解包后查看）----
    # 注意：不能用 len(img_names)+1 当序号 —— 那么算的话，扩展名不管真实格式
    # 都会被写死；这里按本文件内的出现次序编号，扩展名取真实格式。
    img_names: dict[str, str] = {}
    ext_by_name: dict[str, str] = {}
    slot = 0
    for ch in chs:
        for u in ch.images:
            if u in img_names or u not in img_data:
                continue
            slot += 1
            _, ext = img_data[u]
            name = f"{slot:04d}{ext}"
            img_names[u] = name
            ext_by_name[name] = ext

    # ---- 封面：优先挑「竖版」的图（宽:高 < 1）----
    # 只取出现顺序第一张并不可靠：不少书把跨页彩图或扉页横幅摆在最前面，真正的
    # 封面反而排在后面。所有图都认不出尺寸（或都不是竖版）时才退回第一张。
    cover_url = ""
    first_url = ""
    for ch in chs:
        for u in ch.images:
            if u not in img_data:
                continue
            if not first_url:
                first_url = u
            if _is_portrait(image_size(img_data[u][0])):
                cover_url = u
                break
        if cover_url:
            break
    cover_url = cover_url or first_url
    cover_name = img_names.get(cover_url, "")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w") as z:
        # (1) mimetype 第一条 + STORED —— 固定时间戳保证可复现
        z.writestr(
            zipfile.ZipInfo("mimetype", date_time=(1980, 1, 1, 0, 0, 0)),
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        # (2) 容器
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
            "  <rootfiles>\n"
            '    <rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/>\n'
            "  </rootfiles>\n"
            "</container>\n",
        )
        # (3) 样式 / 封面页 / 扉页 / 目录 / 各卷文档
        z.writestr("OEBPS/style.css", EPUB_CSS)
        if cover_name:
            z.writestr("OEBPS/cover.xhtml", _epub_cover(book_name, cover_name))
        z.writestr(
            "OEBPS/titlepage.xhtml",
            _epub_title_page(book_name, source_url, created,
                             len(chs), len(groups), len(img_names),
                             vol_label=vol_label, vol_index=vol_index,
                             total_vols=total_vols),
        )
        z.writestr("OEBPS/nav.xhtml", _epub_nav(book_name, groups, offsets))
        for v, (vol_name, ch_list) in enumerate(groups, 1):
            z.writestr(
                f"OEBPS/vol{v:02d}.xhtml",
                _epub_vol_doc(book_name, vol_name, ch_list, offsets[v - 1], img_names),
            )
        for name, (data, _ext) in ((img_names[u], img_data[u]) for u in img_names):
            z.writestr(f"OEBPS/{name}", data)

        # (4) OPF：manifest 与 spine 必须自洽
        manifest = [
            '    <item id="nav" href="nav.xhtml" '
            'media-type="application/xhtml+xml" properties="nav"/>',
            '    <item id="titlepage" href="titlepage.xhtml" '
            'media-type="application/xhtml+xml"/>',
            '    <item id="css" href="style.css" media-type="text/css"/>',
        ]
        spine: list[str] = []
        if cover_name:
            # 封面是独立一页（书里第一张插图），排在所有内容之前
            manifest.append('    <item id="cover" href="cover.xhtml" '
                            'media-type="application/xhtml+xml"/>')
            spine.append('    <itemref idref="cover"/>')
        spine += ['    <itemref idref="titlepage"/>', '    <itemref idref="nav"/>']
        for v in range(1, len(groups) + 1):
            manifest.append(
                f'    <item id="v{v}" href="vol{v:02d}.xhtml" '
                'media-type="application/xhtml+xml"/>'
            )
            spine.append(f'    <itemref idref="v{v}"/>')
        cover_img_id = ""
        for n, name in enumerate(img_names.values()):
            mt = EPUB_MIME_BY_EXT.get(ext_by_name[name], "image/jpeg")
            props = ""
            if name == cover_name:
                # 阅读器靠这个 properties 认封面（缩略图/锁屏）
                props = ' properties="cover-image"'
                cover_img_id = f"img{n}"
            manifest.append(
                f'    <item id="img{n}" href="{name}" media-type="{mt}"{props}/>'
            )

        creator = f"    <dc:creator>{_xesc(author)}</dc:creator>\n" if author else ""
        source = f"    <dc:source>{_xesc(source_url)}</dc:source>\n" if source_url else ""
        cover_meta = (f'    <meta name="cover" content="{cover_img_id}"/>\n'
                      if cover_img_id else "")

        z.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
            'unique-identifier="bookid" xml:lang="zh">\n'
            '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
            f'    <dc:identifier id="bookid">{book_id}</dc:identifier>\n'
            f"    <dc:title>{_xesc(book_title)}</dc:title>\n"
            "    <dc:language>zh</dc:language>\n"
            f"{creator}"
            f"{source}"
            f"{cover_meta}"
            f'    <meta property="dcterms:modified">{modified}</meta>\n'
            "  </metadata>\n"
            "  <manifest>\n" + "\n".join(manifest) + "\n  </manifest>\n"
            "  <spine>\n" + "\n".join(spine) + "\n  </spine>\n"
            "</package>\n",
        )
    return out_path


def _vol_file_name(out_path: Path, index: int, total: int, vol_name: str) -> Path:
    """分卷文件名：<书名> - 01 第一卷 起始之章.epub（编号前缀保证排序正确）。"""
    book = out_path.stem.rstrip(" -—–~～·")   # 站点书名常以「-」结尾，先去掉再接编号
    base = f"{book} - {index:0{len(str(total))}d} {safe_name(vol_name, 40, 'volume')}"
    return out_path.with_name(base + out_path.suffix)


def write_epub(
    out_path: Path,
    book_name: str,
    chapters: list,
    source_url: str = "",
    author: str = "",
    image_loader=None,
    on_progress=None,
    split: str = "both",
    vol_numbering: dict | None = None,
) -> list[Path]:
    """按「卷」打包写出 EPUB 3，一卷一个文件。

    split 取值：
      "volume" —— 只写分卷文件，一卷一个（哪怕书里只有一卷，也是「书名 - 01 卷名.epub」）；
      "single" —— 只写整本一个文件 out_path；
      "both"   —— 两种都写（默认；书里只有一卷时退化成只剩整本）。
    vol_numbering —— {卷名: 目录里的卷序号}，只抓了部分卷时用它保住「第九卷 → 09」这种编号；
                     不给就按本次 chapters 的第几卷来编。
    插图只下载一次，分卷文件各自引用自己那一卷的图。
    返回值：写出的文件列表，分卷在前、整本最后（只写分卷时列表里没有整本）。
    """
    chs = [EpubChapter.coerce(c) for c in chapters]
    if not chs:
        raise ValueError("EPUB 至少需要一个章节")

    img_data = _download_images(chs, image_loader, on_progress)
    groups = group_by_volume(chs)
    written: list[Path] = []

    if split == "volume" or (split == "both" and len(groups) > 1):
        numbering = vol_numbering or {}
        total_vols = max(len(groups), max(numbering.values(), default=0))
        if on_progress:
            on_progress(f"    按卷打包：共 {len(groups)} 卷")
        for v, (vol_name, ch_list) in enumerate(groups, 1):
            num = numbering.get(vol_name, v)
            vol_path = _vol_file_name(out_path, num, total_vols, vol_name)
            _write_epub_file(
                vol_path, book_name, ch_list,
                source_url=source_url, author=author, img_data=img_data,
                vol_label=vol_name, vol_index=num, total_vols=total_vols,
            )
            written.append(vol_path)

    if split != "volume":
        _write_epub_file(
            out_path, book_name, chs,
            source_url=source_url, author=author, img_data=img_data,
        )
        written.append(out_path)

    return written


# 分章 TXT 里插图的占位标记（重建 EPUB 时据此找本地图片）。
# 文件名必须用 .+ 匹配 —— 章节名里常有空格（“第一卷 起始之章 插图.jpg”），
# 用 \S* 会整行匹配不上，插图行就被当成正文读回去了。
IMG_MARK_RE = re.compile(r"^\[插图\s*(\d+)\]\s*(.+)$")


def _epub_image_loader(chapters: list):
    """给 write_epub 用的取图函数：本地已下好的直接读，还没下的才联网。

    单张失败会被 write_epub 捕获并跳过 —— 没图就不内嵌，绝不写死。
    """
    cache: dict[str, tuple] = {}

    def loader(src: str):
        if src in cache:
            return cache[src]
        if src.startswith(("http://", "https://")):
            data, ext = FETCHER.get_bytes(src)
        else:
            path = Path(src)
            if not path.is_file():
                raise FileNotFoundError(f"插图文件不存在：{path}")
            data, ext = path.read_bytes(), path.suffix.lower() or ".jpg"
        if not looks_like_image(data, ext):
            raise ValueError(f"不是有效图片，已跳过：{src[:80]}")
        # 扩展名以文件头为准：站点常把 PNG 挂在 .jpg 后缀下
        ext = sniff_ext(data, ext)
        cache[src] = (data, ext)
        return data, ext

    return loader


def load_scraped_chapters(out_dir: Path) -> list[EpubChapter]:
    """从已抓好的分章 TXT 读回章节，供离线重建 EPUB。

    格式：第一行章节标题，其余是正文段落；插图行形如 `[插图 1] 0001.jpg`，
    图片存放在书目录的 images/ 下 —— 行序就是正文里的真实位置。
    分章文本在 `txt/` 子目录里（旧版本直接散在书目录，两处都找）。
    老版本写的纯文本也能读（没有插图行就是没图）。
    """
    files: list[Path] = []
    seen: set[str] = set()
    for d in (out_dir / "txt", out_dir):
        if not d.is_dir():
            continue
        for f in sorted(d.glob("[0-9][0-9][0-9][0-9][0-9]_*.txt")):
            if f.name not in seen:
                seen.add(f.name)
                files.append(f)
    result: list[EpubChapter] = []
    for f in files:
        lines = f.read_text(encoding="utf-8-sig").splitlines()
        if not lines:
            continue
        title = lines[0].strip() or f.stem
        blocks: list = []
        for ln in lines[1:]:
            m = IMG_MARK_RE.match(ln.strip())
            if m:
                rel = (m.group(2) or "").strip()
                if not rel:
                    continue
                local = out_dir / "images" / rel
                if local.is_file():  # 图片已经不在了就跳过，不写死占位
                    blocks.append(("img", str(local)))
                continue
            if ln.strip():
                blocks.append(ln)
        if blocks:
            result.append(EpubChapter(title, blocks=blocks))
    return result


# ============================================================ 主流程
FETCHER: Fetcher  # 延迟到配置加载后再建


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="单文件小说爬虫样板（TXT + EPUB 输出）")
    parser.add_argument("url", nargs="?", help="目录页 URL（也支持 --url 传参）")
    parser.add_argument("--url", dest="url_opt", help="目录页 URL")
    parser.add_argument("-o", "--output", default="output", help="输出目录（默认 output/）")
    parser.add_argument("--start", type=int, default=1, help="起始章节序号（1 基）")
    parser.add_argument("--end", type=int, default=0, help="结束章节序号（0=全部）")
    parser.add_argument("--delay", type=float, help="请求间隔秒数（覆盖站点配置）")
    parser.add_argument(
        "--cookies",
        help="浏览器导出的 cookie JSON（Cloudflare 站需要，关键是 cf_clearance）",
    )
    parser.add_argument(
        "--cookies-from-browser",
        help="浏览器导出文件，从里面自动读 User-Agent（cookie 与 UA 绑定）",
    )
    parser.add_argument(
        "--state",
        help="browser_session.py 写出的会话文件（推荐：浏览器替你过验证后复用它）",
    )
    parser.add_argument("--user-agent", help="覆盖 User-Agent（必须与导出 cookie 的浏览器一致）")
    parser.add_argument("--dry-run", action="store_true", help="只解析目录，不抓正文")
    parser.add_argument("--no-resume", action="store_true", help="忽略已下载章节")
    parser.add_argument(
        "--format", default="epub", choices=("txt", "epub", "both"),
        help="输出格式：epub（默认，含插图）/ txt / both",
    )
    parser.add_argument(
        "--no-images", action="store_true",
        help="不下载插图（EPUB 里就自然没有图）",
    )
    parser.add_argument(
        "--epub", action="store_true",
        help="（旧参数，等价于 --format epub）",
    )
    parser.add_argument(
        "--rebuild-epub",
        metavar="目录",
        help="不联网：从已抓好的分章 TXT 重建 EPUB（书名著者按目录名猜，可用 --book-name 覆盖）",
    )
    parser.add_argument("--book-name", help="覆盖书名（重建 EPUB 时用）")
    parser.add_argument("--author", default="", help="EPUB 里的作者署名（可选）")
    parser.add_argument(
        "--epub-split", default="both", choices=("both", "volume", "single"),
        help="EPUB 打包方式：volume=一卷一个 .epub 文件 / single=整本一个文件 / "
             "both=分卷与整本都出（默认）",
    )
    parser.add_argument(
        "--volumes",
        help="只抓这些卷：卷名或 --list-volumes 给出的序号，空格/逗号分隔，"
             "例如 --volumes \"第一卷 第九卷\" 或 --volumes \"1 9\"。"
             "给了它就不写整本合并文件，每个选中的卷各自一个文件",
    )
    parser.add_argument(
        "--list-volumes", nargs="?", const="", metavar="JSON文件",
        help="只列出这本书有哪些卷（不抓正文）。给了路径就把卷列表写成 JSON，"
             "交互菜单用它来展示可选的卷",
    )
    parser.add_argument(
        "--log-dir",
        help="日志目录（默认：根目录/log，即 APP_DIR 上一层的 log/）",
    )
    args = parser.parse_args(argv)

    log_path = start_logging(args.log_dir, sys.argv[1:] if argv is None else argv)
    if log_path and not os.environ.get("NOVEL_SCRAPER_LOG_QUIET"):
        print(f"[i] 日志：{log_path}", file=sys.stderr)

    # ---------- 离线模式：只重建 EPUB，不联网 ----------
    if args.rebuild_epub:
        src = Path(args.rebuild_epub)
        if not src.is_dir():
            sys.exit(f"[x] 目录不存在：{src}")
        chapters = load_scraped_chapters(src)
        if not chapters:
            sys.exit(f"[x] {src} 里没有找到分章文件（形如 00001_第一章.txt）")
        book = args.book_name or src.name
        # 新布局的书目录里成品在 epub/ 下；旧目录就直接写在原地。
        rebuild_dir = src / "epub" if (src / "txt").is_dir() else src
        rebuild_dir.mkdir(parents=True, exist_ok=True)
        epub_path = rebuild_dir / f"{safe_name(book, 80, 'novel')}.epub"

        def local_loader(path_str: str):
            """离线重建：分章 TXT 里的插图路径就是本地文件，直接读字节。"""
            p = Path(path_str)
            ext = p.suffix.lower()
            if ext == ".jpeg":
                ext = ".jpg"
            return p.read_bytes(), (ext or ".jpg")

        n_imgs = sum(1 for c in chapters for _ in c.images)
        print(f"[rebuild] {len(chapters)} 章 / {n_imgs} 张插图，正在打包…", file=sys.stderr)
        paths = write_epub(epub_path, book, chapters, author=args.author,
                           image_loader=local_loader, split=args.epub_split,
                           on_progress=lambda msg: print(msg, file=sys.stderr))
        for p in paths:
            print(f"[rebuild] {p.name}（{p.stat().st_size/1024:.0f} KB）", file=sys.stderr)
        n_vol = len(paths) - (1 if epub_path in paths else 0)
        print(f"[rebuild] 完成：{len(paths)} 个 EPUB"
              + (f"（分卷 {n_vol} 个 + 整本 1 个）" if n_vol else "")
              + f" → {src}", file=sys.stderr)
        return 0

    entry_url = args.url_opt or args.url
    if not entry_url:
        parser.error("需要目录页 URL")

    global FETCHER
    cfg = pick_config(entry_url, load_hosts())
    cookies: dict[str, str] = {}
    user_agent = args.user_agent or cfg.get("user_agent")

    if args.state:
        state_cookies, state_ua = load_state(args.state)
        cookies.update(state_cookies)
        user_agent = state_ua or user_agent
        print(f"[i] 已从会话文件载入 {len(state_cookies)} 个 cookie："
              f"{', '.join(state_cookies)}", file=sys.stderr)
    if args.cookies:
        cookies.update(load_cookies(args.cookies))
        print(f"[i] 已载入 {len(cookies)} 个 cookie：{', '.join(cookies)}", file=sys.stderr)
    if args.cookies_from_browser:
        user_agent = user_agent or load_ua(args.cookies_from_browser)
    # 不在这里预判 Cloudflare：很多站在 CF 后面但根本不触发挑战，
    # 提前警告只会误导。真被拦时 get() 会给出带解决步骤的明确错误。
    FETCHER = Fetcher(
        encoding=cfg.get("encoding"),
        delay=args.delay if args.delay is not None else cfg.get("delay", 1.0),
        user_agent=user_agent,
        cookies=cookies,
    )

    print(f"[1/3] 解析目录 {entry_url}", file=sys.stderr)
    # 目录页只取一次：书名和章节列表都从这份 HTML 里出
    try:
        entry_html = FETCHER.get(entry_url, referer=entry_url)
    except RuntimeError as exc:
        sys.exit(f"[x] {exc}")
    book_name = resolve_book_name(cfg, entry_url, html=entry_html)
    try:
        chapters = parse_list_pages(cfg, entry_url, first_html=entry_html)
    except RuntimeError as exc:
        sys.exit(f"[x] {exc}")

    # ---------- 分卷：列出 / 筛选 ----------
    # 一律按「完整目录」分组，分章 TXT 的序号（i）保持全目录里的原始位置，
    # 这样按卷下载和整本下载可以续传同一批文件，不会互相错位。
    vol_tokens = parse_volume_tokens(args.volumes) if args.volumes else []
    groups = group_by_volume(volume_items(chapters))
    if args.list_volumes is not None or vol_tokens:
        print(f"[i] 目录：共 {len(chapters)} 章，分为 {len(groups)} 卷", file=sys.stderr)
        print(volume_table(groups, indent="  "), file=sys.stderr)
        print("  用法：--volumes \"1 9\" 或 --volumes \"第一卷 第九卷\"（空格/逗号分隔）",
              file=sys.stderr)
    if args.list_volumes is not None:
        if args.list_volumes:
            data = [{"index": i, "name": name, "chapters": len(items),
                     "first": items[0].index if items else 0,
                     "last": items[-1].index if items else 0}
                    for i, (name, items) in enumerate(groups, 1)]
            Path(args.list_volumes).write_text(
                json.dumps({"book": book_name, "total": len(chapters),
                            "volumes": data}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return 0

    vol_names: list[str] = []
    if vol_tokens:
        picked_idx, missing = match_volumes(groups, vol_tokens)
        if missing:
            sys.exit("[x] 没找到这些卷：" + "、".join(missing)
                     + "\n" + volume_table(groups))
        vol_names = [groups[i][0] for i in picked_idx]
        keep = {it.index for i in picked_idx for it in groups[i][1]}

    start = max(1, args.start if args.start > 0 else len(chapters) + args.start + 1)
    end = args.end if args.end > 0 else len(chapters)
    end = min(end, len(chapters))
    picked_entries = [
        (i, title, url)
        for i, (title, url) in enumerate(chapters, 1)
        if start <= i <= end and (not vol_tokens or i in keep)
    ]
    if not picked_entries:
        sys.exit(f"[x] 参数与目录不匹配：共 {len(chapters)} 章，取 {start}..{end}"
                 + ("（已按分卷筛选）" if vol_tokens else ""))

    if args.dry_run:
        for i, title, _url in picked_entries:
            print(f"{i:>5}  {title}")
        print(f"[dry-run] 书名「{book_name}」，共 {len(picked_entries)} 章"
              + (f"，分卷：{'、'.join(vol_names)}" if vol_names else "")
              + "（未抓正文）", file=sys.stderr)
        return 0

    fmt = "epub" if args.epub and args.format == "epub" else args.format
    want_txt = fmt in ("txt", "both")
    want_epub = fmt in ("epub", "both")
    want_images = want_epub and not args.no_images
    # 选了分卷：不打包，每个卷各自一个文件（EPUB 与 TXT 都一样）
    vol_mode = bool(vol_tokens)
    if vol_mode and args.epub_split == "both":
        args.epub_split = "volume"

    # 书目录下分成两个子目录：txt/ 放各卷（及各章）文本，epub/ 放全部电子书 + 整本总览文本。
    out_dir = Path(args.output) / safe_name(book_name, 80, "novel")
    book_stem = safe_name(book_name, 80, "novel")
    txt_dir = out_dir / "txt"
    epub_dir = out_dir / "epub"
    img_dir = out_dir / "images"
    out_dir.mkdir(parents=True, exist_ok=True)
    txt_dir.mkdir(parents=True, exist_ok=True)
    epub_dir.mkdir(parents=True, exist_ok=True)
    merged_path = epub_dir / f"{book_stem}.txt"     # 整本「总览」文本，跟电子书放一起
    vol_txt_base = txt_dir / f"{book_stem}.txt"     # 分卷文本的命名基准（落在 txt/ 下）

    # 章节序号 → 卷名（分卷模式写各自文件时用），以及卷名 → 输出路径
    vol_of_index: dict[int, str] = {}
    vol_txt_paths: dict[str, Path] = {}
    if vol_mode:
        for v, (name, items) in enumerate(groups, 1):
            for it in items:
                vol_of_index[it.index] = name
            vol_txt_paths[name] = _vol_file_name(vol_txt_base, v, len(groups), name)

    if want_images:
        img_dir.mkdir(parents=True, exist_ok=True)

    print(f"[2/3] 抓取正文 → {out_dir}（{len(picked_entries)} 章，格式 {fmt}"
          + (f"，仅 {'、'.join(vol_names)}" if vol_names else "") + "）", file=sys.stderr)
    ok, failed = 0, []
    collected: list[dict] = []        # 供 EPUB 用
    collected_blocks: dict[int, list] = {}
    collected_titles: dict[int, str] = {}
    img_urls: list[str] = []
    img_count = 0

    def _chapter_txt(title_: str, blocks_: list) -> str:
        """分章 TXT：第一行标题，其余按顺序写段落与 `[插图 n] 文件名`。

        一段一行、段间不空行 —— 与网页上每个 `<p>` 一一对应，也方便再读回来
        （`load_scraped_chapters()` 按「非空行 = 一个段」解析）。
        """
        body: list[str] = [title_]
        k = 0
        for b in blocks_:
            if isinstance(b, tuple):
                k += 1
                body.append(f"[插图 {k}] {Path(b[1]).name}")
            else:
                body.append(b)
        return "\n".join(body) + "\n"

    txt_handles: dict[str, object] = {}

    with contextlib.ExitStack() as stack:
        merged = None
        if not vol_mode:
            merged = stack.enter_context(merged_path.open("w", encoding="utf-8"))
            merged.write(book_name + "\n\n")

        def _emit_txt(index_: int, text: str) -> None:
            """分卷模式写「这一卷」的合并文本；否则写整本合并文本。"""
            if merged is not None:
                merged.write(text)
                return
            name = vol_of_index.get(index_, "正文")
            fp = txt_handles.get(name)
            if fp is None:
                path = vol_txt_paths.get(name) or (txt_dir / f"{book_stem} - {name}.txt")
                fp = stack.enter_context(path.open("w", encoding="utf-8"))
                fp.write(f"{book_name} {name}\n\n")
                txt_handles[name] = fp
                print(f"      分卷文本：{path.name}", file=sys.stderr)
            fp.write(text)

        for _pos, (i, title, url) in enumerate(picked_entries, 1):
            single = txt_dir / f"{i:05d}_{safe_name(title)}.txt"
            if single.exists() and not args.no_resume:
                lines = single.read_text(encoding="utf-8-sig").splitlines()
                blocks: list = []
                pending: list[str] = []
                for ln in lines[1:]:
                    m = IMG_MARK_RE.match(ln.strip())
                    if m:
                        rel = (m.group(2) or "").strip()
                        if rel:
                            blocks.append(("img", str(img_dir / rel)))
                            pending.append(rel)
                        continue
                    if ln.strip():
                        blocks.append(ln)
                real_title = (lines[0].strip() if lines else "") or title
                if blocks:
                    collected_blocks[i] = blocks
                    collected_titles[i] = real_title
                    ok += 1
                needs_images = (want_images and not pending
                                and chapter_is_illustration(real_title, cfg))
                if needs_images:
                    # 上次这一章一张图都没下到（图源挂了），要重抓时得限速
                    FETCHER.delay = max(FETCHER.delay, cfg.get("delay", 0) or 0)
                if not needs_images:
                    print(f"  [{_pos}/{len(picked_entries)}] 已存在，跳过 {real_title}",
                          file=sys.stderr)
                    if want_txt:
                        _emit_txt(i, single.read_text(encoding="utf-8-sig") + "\n")
                    continue
                print(f"  [{_pos}/{len(picked_entries)}] 补抓插图 {real_title}",
                      file=sys.stderr)
            try:
                info = parse_chapter(cfg, title, url)
            except (RuntimeError, requests.RequestException) as exc:
                failed.append((title, url, str(exc).splitlines()[0]))
                print(f"  [{_pos}/{len(picked_entries)}] [!] 失败 {title}：{exc}",
                      file=sys.stderr)
                continue
            real_title, blocks = info["title"], info["blocks"]
            # 图片下载：能下就下，下不了/不是图片就跳过，绝不写死
            if want_images and info["images"]:
                for pos, b in enumerate(blocks):
                    if not isinstance(b, tuple):
                        continue
                    u = b[1]
                    fn = f"{i:05d}_{len(img_urls) + 1:02d}_{safe_name(real_title, 24)}"
                    try:
                        data, ext = FETCHER.get_bytes(u, referer=url)
                        if not looks_like_image(data, ext):
                            print(f"    [i] 跳过非图片内容：{u[:80]}", file=sys.stderr)
                            continue
                        ext = sniff_ext(data, ext)  # 以文件头为准，别信 URL 后缀
                        path = img_dir / f"{fn}{ext}"
                        path.write_bytes(data)
                        blocks[pos] = ("img", str(path))
                        if u not in img_urls:
                            img_urls.append(u)
                        img_count += 1
                    except Exception as exc:
                        print(f"    [!] 插图失败（跳过）：{str(exc).splitlines()[0]}",
                              file=sys.stderr)
                blocks = [b for b in blocks
                          if not isinstance(b, tuple) or not b[1].startswith("http")]
            collected_blocks[i] = blocks
            collected_titles[i] = real_title
            single.write_text(_chapter_txt(real_title, blocks), encoding="utf-8")
            if want_txt:
                # 一章一段一行，章与章之间空一行（空行只用来分章，不夹在段与段之间）
                parts = [real_title + "\n"]
                for b in blocks:
                    if isinstance(b, tuple):
                        parts.append(f"[插图] {Path(b[1]).name}\n")
                    else:
                        parts.append(b + "\n")
                parts.append("\n")
                _emit_txt(i, "".join(parts))
            n_chars = sum(len(b) for b in blocks if isinstance(b, str))
            n_img = sum(1 for b in blocks if isinstance(b, tuple))
            tail = f"  +{n_img} 图" if n_img else ""
            ok += 1
            print(f"  [{_pos}/{len(picked_entries)}] {real_title[:30]:<30}"
                  f" {n_chars:>6} 字{tail}", file=sys.stderr)

    for i in sorted(collected_blocks):
        collected.append({
            "title": collected_titles[i],
            "blocks": collected_blocks[i],
            "is_illustration": chapter_is_illustration(collected_titles[i], cfg),
        })

    print(f"[3/3] 完成：成功 {ok} 章，失败 {len(failed)} 章"
          + (f"，插图 {img_count} 张" if want_images else ""), file=sys.stderr)
    if DEOBFUSCATED:
        print(f"      （其中 {DEOBFUSCATED} 章做了乱序/诱饵段落还原）", file=sys.stderr)
    if want_txt:
        if vol_mode:
            for name, path in vol_txt_paths.items():
                if name in txt_handles:
                    print(f"      分卷文本：{path.name}"
                          f"（{path.stat().st_size/1024:.0f} KB）", file=sys.stderr)
        else:
            print(f"      合并文件：{merged_path}", file=sys.stderr)
    print(f"      分章文本：{txt_dir}", file=sys.stderr)

    if want_epub and collected:
        epub_path = epub_dir / f"{safe_name(book_name, 80, 'novel')}.epub"
        paths = write_epub(
            epub_path, book_name, collected,
            source_url=entry_url, author=args.author,
            image_loader=_epub_image_loader(collected),
            on_progress=lambda msg: print(msg, file=sys.stderr),
            split=args.epub_split,
            vol_numbering={name: idx for idx, (name, _items) in enumerate(groups, 1)},
        )
        n_img = sum(1 for ch in collected for b in ch["blocks"]
                    if isinstance(b, tuple) and not b[1].startswith("http"))
        vols = [p for p in paths if p != epub_path]
        if vols:
            print(f"      分卷 EPUB：{len(vols)} 个文件（一卷一个）", file=sys.stderr)
            for p in vols:
                print(f"        {p.name}（{p.stat().st_size/1024:.0f} KB）", file=sys.stderr)
        if epub_path in paths:
            size = epub_path.stat().st_size
            print(f"      EPUB（整本）：{epub_path}"
                  f"（{size/1024:.0f} KB，{len(collected)} 章"
                  + (f"，内嵌插图 {n_img} 张" if n_img else "")
                  + "）", file=sys.stderr)
        if len(collected) < len(chapters) and not vol_mode:
            print(f"      [提示] EPUB 只含本次的 {len(collected)}/{len(chapters)} 章；"
                  f"要整本可跑完所有章节，或用 --rebuild-epub {out_dir} 从已抓章节重建",
                  file=sys.stderr)

    if failed:
        fail_path = out_dir / "failed.txt"
        fail_path.write_text(
            "\n".join(f"{t}\t{u}\t{e}" for t, u, e in failed), encoding="utf-8"
        )
        print(f"      失败清单：{fail_path}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
