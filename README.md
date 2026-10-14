用 ai 写的，自用，下面那堆人机发言不想改了


# 小说爬虫（TXT + EPUB）

一个单文件小说爬虫：贴上网址就能整本抓下来，输出 **TXT + EPUB**。
串行限速、断点续传、EPUB **一卷一个 .epub 文件**、章节里的原插图按原位内嵌进 EPUB。

## 最快用法：双击 `run.cmd`

```
双击 run.cmd  →  粘贴小说目录页网址  →  选格式（1/2/3，或 4 先挑分卷）  →  回车等它跑完
```

只问网址和格式，其余走默认值。
中途 `Ctrl+C` 可以停，已抓的章节保留，下次重跑自动续传。
跑完在**本文件夹的上一层** `output\<书名>\` 下得到（文本和电子书分开放）：

```
<书名>\
├─ txt\                              各卷（及各章）文本
│   ├─ <书名> - 01 第一卷 起始之章.txt
│   └─ 00001_第一章 xxx.txt          分章文本（--rebuild-epub 从这里读）
├─ epub\                             全部电子书 + 整本总览文本
│   ├─ <书名>.epub                    整本电子书
│   ├─ <书名> - 01 第一卷 起始之章.epub 分卷电子书（一卷一个文件，编号保证排序）
│   ├─ <书名> - 02 第二卷 风之亚尔比昂.epub
│   └─ <书名>.txt                     全书合并文本（「总览」）
└─ images\                           插图原图
```

分卷 EPUB 每本都自带该卷的图与封面（封面 = **该卷**正文顺序里第一张竖版插图，
没有竖版图时退回第一张），单独发给别人、单独扔进阅读器都完整；
不想要分卷就加 `--epub-split single`。
插图按原位内嵌（正文夹图、插图章多图都不会错位）。
下载结束**不会自动关窗口**（交互入口会停住等回车），方便看结果和报错；
要用脚本调用可以设 `NOVEL_SCRAPER_NO_PAUSE=1` 跳过暂停。

### 只抓某几卷（交互菜单的 `4`）

菜单里的 `4. 选择分卷下载` 是**修饰项**，不是格式项：选完会回到主菜单，
`4` 那行显示 `（当前：第一卷、第九卷）` 供确认，再选 `1/2/3` 决定格式。

- 挑卷时输入**按空格分隔**的卷号或卷名（`1 9 15` 或 `第一卷 第九卷`），
  逗号/顿号/分号也行；卷号就是列表里显示的序号。
- 回车 = 保持当前选择；输入 `all`（或 `0`/`全部`）= 抓整本，不分卷。
- 选择会被记住（`app_dir\.last-volumes`，按网址区分），下次同一本书直接沿用。
- **选了分卷就不合并**：每个卷各自出 `- NN 卷名.epub` 和 `- NN 卷名.txt`，一卷一个文件。
- 命令行等价物：`--volumes "1 9"`；只想看有哪些卷用 `--list-volumes`（可加文件路径导出 JSON）。

日志默认落在**上一层**的 `log\novel-scraper-<日期>-<时间>.log`（每次运行一份，含失败章节与原因）；
要换位置用 `--log-dir 目录`，或设环境变量 `NOVEL_SCRAPER_LOG_DIR`。

## 过 Cloudflare 验证（browser_session）

被 CF 拦的站点，先由你本人在真实浏览器里过验证：

```
双击 根目录\browser-session.cmd   → 输入网址 → 手动点过验证 → 自动存 browser-state.json
```

`browser-session.cmd` / `browser_session.py` 就在**工作区根目录**（包文件夹的上一层），
生成的 `browser-state.json` 也在那里；爬虫会在**包文件夹与上一层两处**找它并自动加载。

## 便携版（别人电脑上用，不用装 Python）

工作区根目录的 **`..\new\`** 是打包好的绿色版（`novel-scraper.exe` 加同目录的
`sites.json`、`使用说明.md`、`demo-book\`）：
把它整个复制到任意目录，双击 `novel-scraper.exe` 就能用，Python 和依赖都在里面。
打包脚本是 `tools\run_build.cmd`（开发者用）；使用教程见 `..\new\使用说明.md`。

## 文件

| 文件 | 作用 |
|---|---|
| `run.cmd` | **双击入口**（Windows，源码方式） |
| `start.py` | 交互入口的薄壳，逻辑在 `tools\launcher.py`（和便携版 exe 共用） |
| `novel_scraper.py` | 爬虫本体，只依赖 `requests` + `beautifulsoup4`（EPUB 是纯标准库实现） |
| `sites.json` | 域名 → 选择器配置。源码方式下**必须和脚本放同一目录** |
| `cookies.json` | 可选，浏览器导出的 cookie（需要时才有用） |
| `browser-state.json` | 可选，`browser_session.py` 跑出来的会话（cookie + UA） |
| `demo-book\` | 离线夹具，用来不联网自测 |
| `tools\` | 辅助脚本（见下） |
| `dev.md` | **开发指南**：架构、数据流、扩展与踩坑、测试发布流程（接手维护的人先看这个） |

## 命令行用法（不想用交互入口时）

```powershell
$env:PYTHONUTF8='1'          # Windows 下防中文乱码
cd <本目录>

# 1) 先看目录解析对不对（不下载正文）
python novel_scraper.py --url "<书目页 URL>" --dry-run

# 2) 整本抓，TXT + EPUB 都要
python novel_scraper.py --url "<书目页 URL>" --format both

# 3) 只抓一段试试
python novel_scraper.py --url "..." --start 1 --end 20 --format epub

# 4) 已经抓好了 TXT，想单独重建 EPUB（不联网）
python novel_scraper.py --rebuild-epub "output\书名" --book-name "书名"

# 5) 看看这本书有哪些卷 / 只抓第 1、9 卷（按卷各出文件，不合并）
python novel_scraper.py --url "..." --list-volumes
python novel_scraper.py --url "..." --volumes "1 9" --format both
```

常用参数：

| 参数 | 说明 |
|---|---|
| `--format txt\|epub\|both` | 输出格式（默认 `epub`） |
| `--start` / `--end` | 章节范围，1 基，`--end 0` = 到最后一章 |
| `--volumes "1 9"` | **只抓这些卷**（卷号或卷名，空格/逗号分隔）。给了它就不写整本合并文件，每卷各自一个 `.epub`/`.txt` |
| `--list-volumes [JSON文件]` | 只列出分卷、不抓正文；给了路径就写成 JSON `{book,total,volumes:[{index,name,chapters,first,last}]}` |
| `--delay` | 请求间隔秒数，覆盖站点配置（默认 2.0） |
| `--no-images` | 不下载插图 |
| `--rebuild-epub 目录` | 离线：从分章 TXT + `images\` 重建 EPUB（会带上插图与封面） |
| `--epub-split both\|volume\|single` | EPUB 打包方式：`volume`=一卷一个文件 / `single`=整本一个文件 / `both`=两者都出（**默认**） |
| `--log-dir 目录` | 日志目录（默认：上一层 `log\`） |
| `--state` | 载入 `browser_session.py` 的会话文件（cookie + UA 一起载入） |
| `--dry-run` | 只解析目录 |
| `--no-resume` | 忽略已下载章节，重新抓 |

断点续传默认开启：已存在的 `00001_xxx.txt` 会跳过。插图章若上次没抓到图，这次会自动补抓。

## EPUB 说明

纯标准库实现 EPUB 3，用官方 **EPUBCheck 5.2.1** 校验过：`0 fatals / 0 errors / 0 warnings / 0 infos`。

- **封面自动取「插图」第一张** —— 按正文顺序找到第一张真的下到的图，生成 `cover.xhtml`（spine 最前、`properties="cover-image"` + `<meta name="cover">` 兼容 EPUB2 阅读器）；这本书没有图就自然没有封面页。分卷时每卷的封面 = **该卷**的第一张插图。
- **插图严格按原位插入** —— 解析阶段就把正文切成「段落 / 插图」的块序列（`@@DSHIMG{n}@@` 占位符），因此
  插图章里的多张图、正文中间夹的图都不会错位。`tools\check_epub.py --dir <分章目录>` 会逐章比对
  段落顺序、插图位置与**插图内容（与 `images\` 原图逐张比对 SHA-256）**。

- **一卷一个 .epub 文件** —— `--epub-split both`（默认）同时给出分卷与整本：文件名形如
  `<书名> - 01 第一卷 起始的章.epub`（编号前缀保证在资源管理器里排序正确）。每个分卷文件都是
  **自洽**的：自己的 `nav.xhtml`（卷 → 章两级目录）、自己的 `vol01.xhtml`、自己那一卷的插图
  （在本文件内从 `0001.jpg` 重新编号）、自己的封面、自己的 `dc:identifier`（几卷不会在阅读器里
  互相顶掉）。只写分卷用 `--epub-split volume`，只要整本用 `--epub-split single`。
- **卷的判定** —— 按标题里的「第X卷 / 第X部 / 第X篇 / 第X集」分组，**支持小数点**（「第8.5卷」自成一卷，不会并进第八卷）；只有当标记后面那段在整卷里反复出现（≥60%）才当作卷名，所以「第十四卷 水都市的圣女」能拿到卷名，而每章各不相同的「第一卷 ～第一败～…」不会裂成上百个假卷。
- **短篇集合成一卷** —— 若有多于一个卷的每一章都带「短篇」标签（例如「第一集 短篇故事」「第二集 短篇故事」），它们会合并成一个名为「短篇集」的卷；带小数点的卷号（如「第8.5卷 短篇集」）不参与合并，保持独立。没有卷标记的章节仍归入「正文」卷。
- **有原图就内嵌** —— 标题带「插图 / 插画 / 彩图 / 扉页 / 口绘」的章节，把该页 `#content` 里的原图按原位放进 `<figure>`；下载失败或不是真图片就跳过，不写死占位。这类页面的文字通常是站点填充的废话，整段丢弃、只留图。
- **封面自动挑竖版图** —— 从前到后扫本文件里的插图，取第一张「高 > 宽」的当封面（不少书会把跨页彩图/扉页横幅排在真封面前面），一张竖版都没有时才退回第一张。尺寸靠读文件头解析（PNG/JPEG/GIF/BMP/WebP/SVG），不依赖 Pillow（打包时 `--exclude-module PIL`）。
- 插图页的图是直接取 `src` 下载，跟随章节的限速与重试。
- **图片扩展名按文件头判定，不信 URL 后缀** —— 站点会把 PNG 挂在 `.jpg` 下，只信后缀会让 OPF 的 `media-type` 和实际内容对不上，EPUBCheck 报 `OPF-029` + `PKG-022`。`sniff_ext()` 负责纠正。
- **图源挂掉是正常的** —— 有些老书的插图指向早已失效的图床，会连接超时。这种情况按设计跳过并打日志 `[!] 插图失败（跳过）`，EPUB 里就没有那几张图，不是 bug。
- **带图的 EPUB 会很大** —— 插图页常是高清大图，一本几百章的书带图可以到几十 MB。嫌大可以加 `--no-images`。

## 正文排版与「乱序正文」还原

- **段落排版**：TXT 是「标题 / 段 / 段 / …」，**一段一行、段间不空行**、段首不加全角缩进 —— 与页面上的实际排版一致（这类小说站靠 `<p>` 分段，CSS 里没有 `text-indent`）；章与章之间空一行。EPUB 的缩进由 CSS 负责，阅读器里仍是标准的中文首行缩进。
- **乱序 + 诱饵段落自动还原**：部分站点不直接给出正文顺序，而是把打乱顺序的段落塞进 `<p style="order:N">`，再混入若干「诱饵段」，靠页面底部一段脚本在**空闲 7 秒后**重排 DOM、删掉诱饵。程序内置了这套算法的复刻（`deobfuscate_content()`），**在解析 DOM 时就还原好，不依赖 JS、不依赖等待**：
  - 触发条件是 `#content` 上存在 `data-r` / `data-t` / `data-a` / `data-k` 四个属性，**无需在 `sites.json` 里配置**，没这组属性时该函数直接返回 `False`，对普通站点零影响；
  - 还原成功时运行结束会多打一行 `（其中 N 章做了乱序/诱饵段落还原）`，看到它说明生效了；没看到就说明此书没被这样处理过。
- 判据来自浏览器「查看源代码」得到的同一章文本，逐段比对 **106/106 完全一致**。

几个容易写错、写错阅读器就打不开的点，代码里都处理了：

1. `mimetype` 必须是 ZIP 的**第一个**条目，且以 **STORED（不压缩）** 方式存储；
2. `META-INF/container.xml` 指向 OPF，OPF 里 manifest 的每个 href 必须真实存在；
3. spine 的每个 `idref` 都要有对应的 manifest item；
4. `nav.xhtml` 要带 `properties="nav"`（EPUB 3 目录）；
5. 图片和卷文档都放在 `OEBPS/` 根下、用纯文件名引用 —— **不要用 `../images/xxx`**：EPUBCheck 解析父目录相对路径会误报 `RSC-007 Referenced resource could not be found`；
6. 正文段落的文本里**不带**缩进（`_lines_to_paragraphs()` 只输出纯文本），首行缩进由 CSS 的 `p { text-indent: 2em }` 提供 —— 所以**不要**在段落文本里再加 `\u3000\u3000`，否则双层缩进；
7. 章节标题原样保留（含「第一卷 起始之章 (1)」这类前缀）—— 前缀是卷次信息，删掉会让「第一章」这种标题在阅读器目录里分不清是哪一卷；
8. 图片的 `media-type` 要和文件头一致（见上一条 `sniff_ext()`）。

## 站点适配

改 `sites.json` 加一段即可，不用动代码：

```json
"example.com": {
  "name": "示例站",
  "book_name_selector": "h1",
  "list_link": "div#chapterlist ul li a",
  "chapter_content": "#content",
  "chapter_title": "h1",
  "chapter_junk": ["script", "style", ".ads"],
  "delay": 2.0,
  "encoding": "utf-8"
}
```

- 目录分页时加 `list_url_template`，用 `{page}` 占位；不分页（单页目录）就不要写这一项。
- **没有屏蔽词/垃圾词过滤**：正文行不会被自动删除（旧版的 `extra_junk_words` 已移除，写上也不会生效）。要减内容只能靠 `chapter_junk` 选择器删掉整块 DOM。
- **乱序/诱饵段落还原是自动的**：无需配置（见上文「正文排版与『乱序正文』还原」）。若新站用了同类反爬但算法不同，可在 `deobfuscate_content()` 里按同样的思路加分支 —— 判据始终放在 DOM 属性上，不要按域名硬编码。
- 插图章判定用 `illustration_title_words`（默认 `["插图","插画","彩图","扉页","口绘"]`）。
- 选择器支持 `xpath:` 前缀和 `@attr` 后缀（需要 `lxml`，未装则用 CSS）。
- 换新站先用 `tools\diag_site.py` 存下真实 HTML (`--html`)，核对选择器再加配置。

## tools\ 里的辅助脚本

| 脚本 | 作用 |
|---|---|
| `browser_session.py` | 在**工作区根目录**（上一层）：打开有头浏览器让你本人过验证，存下 `browser-state.json`（cookie + UA） |
| `diag_site.py` | 诊断站点：看状态码 / 是否被 CF 拦 / 存原始 HTML、核对选择器 |
| `check_epub.py` | 离线自检 EPUB（ZIP / mimetype / XML / manifest 交叉引用 + `--dir` 核对插图原位与内容） |
| `launcher.py` | 交互问答逻辑（`start.py` 与便携版 exe 共用） |
| `build_entry.py` | PyInstaller 打包入口：无参数 = 问答，带参数 = 命令行 |
| `run_build.cmd` | 重新打便携版 exe |
| `novel_scraper\` | ⚠ **遗留 v1.0.0 包**（`python -m novel_scraper …`），当前链路不引用它，改这里不会有任何效果；只作参考 |

## 合规

抓取前请确认目标站允许抓取。本脚本默认串行 + 限速，不并发压站。
不包含任何绕过 Cloudflare 验证的功能 —— 被拦时用工作区根目录的 `browser_session.py` 让自己在浏览器里过验证，脚本复用你本人已通过的会话。
