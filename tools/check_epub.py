# -*- coding: utf-8 -*-
"""EPUB 成品自检：只读，不解压到磁盘。逐个校验 ZIP 结构、容器、OPF、每页 XML 是否合法。

用法：
    python check_epub.py 书名.epub                 # 结构自检
    python check_epub.py 书名.epub --dir 书名目录   # 再核对「插图是否在正文原位」
（--dir 不给则默认用 EPUB 所在的目录；那里没有分章 TXT 就跳过位置核对。）
"""
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

argv = sys.argv[1:]
path = Path(argv[0] if argv and not argv[0].startswith("--")
            else "output/书名/epub/书名.epub")
src_dir = None
if "--dir" in argv:
    src_dir = Path(argv[argv.index("--dir") + 1])
print(f"文件: {path}")
if not path.exists():
    print("[x] 文件不存在"); sys.exit(1)
print(f"大小: {path.stat().st_size} 字节 ({path.stat().st_size/1024:.0f} KB)")

z = zipfile.ZipFile(path)
names = z.namelist()
bad = z.testzip()
print(f"ZIP 完整性: {'[x] 损坏于 '+str(bad) if bad else '[+] 所有条目 CRC 校验通过'}")

# 1) mimetype 必须是第一个条目且不压缩
info = z.infolist()
first = info[0]
print(f"\n[1] mimetype")
print(f"    第一个条目      : {first.filename}  {'[+]' if first.filename=='mimetype' else '[x] 必须是第一个'}")
print(f"    压缩方式        : {first.compress_type}  {'[+] STORED(0)' if first.compress_type==zipfile.ZIP_STORED else '[x] 必须 STORED'}")
print(f"    内容            : {z.read('mimetype').decode('ascii').strip()!r}")
ok_mime = first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED \
    and z.read("mimetype").decode("ascii").strip() == "application/epub+zip"

# 2) 所有 XML 条目是否可解析
print(f"\n[2] XML 合法性 ({sum(1 for n in names if n.endswith(('.xhtml','.opf','.ncx','.xml')))} 个文件)")
xml_files = [n for n in names if n.endswith((".xhtml", ".opf", ".ncx", ".xml"))]
ok_xml = True
for n in xml_files:
    try:
        ET.fromstring(z.read(n))
    except ET.ParseError as e:
        print(f"    [x] {n}: {e}")
        ok_xml = False
if ok_xml:
    print(f"    [+] 全部 {len(xml_files)} 个 XML 解析通过")

# 3) container.xml -> rootfile 路径必须存在
print(f"\n[3] container.xml 指向")
rootfile = None
if "META-INF/container.xml" in names:
    try:
        c = ET.fromstring(z.read("META-INF/container.xml"))
        for el in c.iter():
            if el.tag.endswith("rootfile"):
                rootfile = el.get("full-path")
        print(f"    rootfile: {rootfile}  {'[+] 存在' if rootfile in names else '[x] 不存在！'}")
    except ET.ParseError as e:
        print(f"    [x] container.xml 无法解析: {e}")
else:
    print("    [x] 缺 META-INF/container.xml")

# 4) OPF：manifest 里每个 href 是否存在；spine 引用的 id 是否都在 manifest
print(f"\n[4] OPF manifest / spine 交叉引用")
ok_opf, n_doc, n_css = True, 0, 0
if rootfile and rootfile in names:
    opf = ET.fromstring(z.read(rootfile))
    base = str(Path(rootfile).parent)
    ids, hrefs = set(), []
    for el in opf.iter():
        if el.tag.endswith("item") and el.get("id"):
            ids.add(el.get("id"))
            hrefs.append((el.get("id"), el.get("href"), el.get("media-type")))
    missing = []
    for i, h, mt in hrefs:
        full = f"{base}/{h}" if base != "." else h
        if mt == "application/xhtml+xml":
            n_doc += 1
        if mt == "text/css":
            n_css += 1
        if full not in names:
            missing.append(f"{i} -> {full}")
    print(f"    manifest 条目   : {len(hrefs)}（正文文档 {n_doc}，CSS {n_css}）")
    if missing:
        ok_opf = False
        for m in missing:
            print(f"    [x] 文件缺失: {m}")
    else:
        print(f"    [+] 所有 manifest 指向的文件都存在")
    spine = [el.get("idref") for el in opf.iter() if el.tag.endswith("itemref")]
    dangling = [s for s in spine if s not in ids]
    print(f"    spine 条目      : {len(spine)}  {'[+] 引用全部有效' if not dangling else '[x] 悬空: '+str(dangling)}")
    if dangling:
        ok_opf = False
    # 元数据
    for el in opf.iter():
        if el.tag.endswith("title") and el.text:
            print(f"    标题            : {el.text}")
            break
    for el in opf.iter():
        if el.tag.endswith("language") and el.text:
            print(f"    语言            : {el.text}")
            break
    ok_opf = ok_opf and not dangling and not missing
else:
    ok_opf = False

# 5) 正文文档是否有实际文字
print(f"\n[5] 正文内容抽查")
text_docs = [n for n in names if n.endswith(".xhtml") and "nav" not in n.lower()]
if text_docs:
    total = 0
    for n in text_docs:
        s = z.read(n).decode("utf-8", "replace")
        body = s.split("<body", 1)[-1]
        # 粗略去掉标签后计中文/字符数
        import re
        plain = re.sub(r"<[^>]+>", "", body)
        total += len(plain.strip())
        print(f"    {n:<40} {len(plain.strip()):>6} 字")
    print(f"    合计 {len(text_docs)} 篇 / {total} 字")
else:
    print("    [x] 没有正文文档")

# 6) 图片等资源（如果有）
media = [n for n in names if n.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"))]
if media:
    print(f"\n[6] 内嵌资源 {len(media)} 个，最大 {max(z.getinfo(n).file_size for n in media)} 字节")

# 7) 插图位置：EPUB 各章的「块序列」必须与分章 TXT 一致（TXT 里 [插图 n] 行就是图在正文中的真实位置）
print(f"\n[7] 插图位置核对（EPUB 章节 vs 分章 TXT）")
ok_pos = True
src = src_dir or path.parent
if not src_dir and not any(src.glob("[0-9]*.txt")):
    # 新布局：电子书在 <书名>\epub\ 下，分章文本在同级的 <书名>\txt\ 下
    for cand in (src / "txt", src.parent / "txt"):
        if cand.is_dir():
            src = cand
            break
txts = sorted(p for p in src.glob("[0-9]*.txt") if p.is_file())
if not txts:
    print(f"    跳过：{src} 下没有分章 TXT（用 --dir 指定分章目录即可启用核对）")
else:
    from collections import defaultdict, deque
    from html import unescape

    SEC_RE = re.compile(r"<section\b[^>]*>(.*?)</section>", re.S)
    H1_RE = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.S)
    BLOCK_RE = re.compile(r"<img\b[^>]*>|<p\b[^>]*>(.*?)</p>", re.S)
    SRC_RE = re.compile(r'src="([^"]+)"')
    IMG_LINE_RE = re.compile(r"^\[插图\s*(\d+)\]\s*(.*)$")

    def plain(markup: str) -> str:
        return unescape(re.sub(r"<[^>]+>", "", markup)).strip()

    def pos_anchors(blocks) -> list:
        """每张插图前面最近的那段正文 —— 用来说明图在正文里的确切位置。"""
        out, last = [], None
        for kind, val in blocks:
            if kind == "text":
                last = val
            else:
                out.append(last)
        return out

    # EPUB 侧：按文档顺序取每章 h1 与块序列
    epub_chaps = []
    for n in names:
        low = n.lower()
        if not low.endswith(".xhtml") or any(k in low for k in ("nav", "titlepage", "cover")):
            continue
        doc = z.read(n).decode("utf-8", "replace")
        for sec in SEC_RE.findall(doc):
            hm = H1_RE.search(sec)
            blocks = []
            for m in BLOCK_RE.finditer(sec):
                tok = m.group(0)
                if tok[:4].lower() == "<img":
                    sm = SRC_RE.search(tok)
                    blocks.append(("img", Path(unescape(sm.group(1))).name if sm else ""))
                else:
                    blocks.append(("text", plain(m.group(1) or "")))
            epub_chaps.append((plain(hm.group(1)) if hm else "", blocks))

    # TXT 侧：第一行标题，其余非空行按序成块
    by_title = defaultdict(deque)
    for p in txts:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        title = lines[0].strip() if lines else ""
        blocks = []
        for ln in lines[1:]:
            ln = ln.strip()
            if not ln:
                continue
            mm = IMG_LINE_RE.match(ln)
            blocks.append(("img", Path(mm.group(2)).name) if mm else ("text", ln))
        by_title[title].append(blocks)

    # 图片身份核对：EPUB 里图片会被重命名成 0001.jpg，所以不能比文件名，
    # 要比「内容」—— TXT 记的是站点原始文件名，正文用图就在 images/<原始名> 里。
    import hashlib

    img_dir = src / "images"
    hash_cache = {}

    def disk_hash(name: str):
        key = ("d", name)
        if key not in hash_cache:
            fp = img_dir / name
            hash_cache[key] = hashlib.sha256(fp.read_bytes()).hexdigest() if fp.is_file() else None
        return hash_cache[key]

    def zip_hash(name: str, doc: str = ""):
        key = ("z", name)
        if key not in hash_cache:
            cands = []
            if doc:
                cands.append(f"{Path(doc).parent.as_posix()}/{name}")
            cands.append(f"OEBPS/{name}")
            cands.append(name)
            hit = next((c for c in cands if c in names), None)
            if hit is None:  # 兜底：任何以该文件名结尾的条目
                hit = next((n for n in names if n.endswith("/" + name)), None)
            hash_cache[key] = hashlib.sha256(z.read(hit)).hexdigest() if hit else None
        return hash_cache[key]

    can_hash = img_dir.is_dir()
    n_hash_ok = 0
    n_img = 0
    compared = 0
    problems = []
    for title, blocks in epub_chaps:
        if not by_title.get(title):
            problems.append(f"EPUB 章节在 TXT 里找不到同名文件: {title!r}")
            continue
        ref = by_title[title].popleft()
        compared += 1
        imgs = [b[1] for b in blocks if b[0] == "img"]
        n_img += len(imgs)
        r_imgs = [b[1] for b in ref if b[0] == "img"]
        why = []
        if [b[0] for b in blocks] != [b[0] for b in ref]:
            why.append("段落/插图顺序")
        if [b for b in blocks if b[0] == "text"] != [b for b in ref if b[0] == "text"]:
            why.append("段落文字")
        if len(imgs) != len(r_imgs):
            why.append(f"插图数量 {len(imgs)}≠{len(r_imgs)}")
        elif can_hash:
            for a, b in zip(imgs, r_imgs):
                ha, hb = zip_hash(a), disk_hash(b)
                if ha and hb and ha == hb:
                    n_hash_ok += 1
                elif ha and hb:
                    why.append("插图内容不符")
                    break
        if pos_anchors(blocks) != pos_anchors(ref):
            why.append("插图前后正文")
        if why:
            problems.append(f"{title}: {'/'.join(dict.fromkeys(why))}")
    leftovers = sum(len(v) for v in by_title.values())
    print(f"    比对章节        : {compared} 章（有插图 {sum(1 for t, b in epub_chaps if any(x[0] == 'img' for x in b))} 章 / 插图 {n_img} 张）")
    if can_hash and n_img:
        print(f"    图片内容逐张核对: {n_hash_ok}/{n_img} 张与 images\\ 里的原图字节一致"
              + ("" if n_hash_ok == n_img else "  [x]"))
    elif n_img:
        print(f"    [i] 没找到 images\\ 目录，跳过了图片内容核对（只比了顺序与数量）")
    if leftovers:
        print(f"    [i] TXT 里未被 EPUB 收录: {leftovers} 章（可能按卷/范围过滤，属正常）")
    if problems:
        ok_pos = False
        for msg in problems[:8]:
            print(f"    [x] {msg}")
        if len(problems) > 8:
            print(f"    ... 另有 {len(problems) - 8} 条")
    elif compared:
        print(f"    [+] 所有章节的段落顺序、插图位置与插图内容都一致")

allok = ok_mime and ok_xml and ok_opf and bool(text_docs) and ok_pos
print(f"\n[结论] {'EPUB 结构合法，可直接拖进阅读器' if allok else '存在问题，见上面 [x] 项'}")
sys.exit(0 if allok else 4)
