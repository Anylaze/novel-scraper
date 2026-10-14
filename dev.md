# 开发指南（novel-scraper）

给接手维护这套代码的人看。假设你懂 Python，但不了解这个项目。

**先记住三句话：**

1. **真正在跑的代码只有三处**：`novel_scraper.py`（爬虫 + EPUB 引擎，单文件 1700+ 行）、`tools/launcher.py`（交互问答）、`tools/build_entry.py`（打包入口）。其它 `.py` 都是工具或历史遗留。
2. **`tools/novel_scraper/` 是上一版（v1.0.0）的包，当前链路不 import 它**（`grep -r "novel_scraper" ` 只命中它自己）。别在里面改功能，改完不会有任何效果。
3. **`sites.json` 是配置不是代码**：加站点、改选择器都在这里；只有解析逻辑本身变了才动 `.py`。

---

## 1. 目录结构

```
novel-scraper/                    源码树
├─ novel_scraper.py               ★ 主程序：抓取 + 清洗 + TXT/EPUB 生成 + CLI 参数
├─ sites.json                     站点配置（hosts 表），随 exe 一起分发
├─ start.py                       源码方式双击入口（只做 sys.path 处理后调 launcher）
├─ run.cmd                        源码方式双击的 cmd 外壳（检查 python、UTF-8、pause）
├─ README.md                      面向使用者/命令行的说明
├─ demo-book/                     离线夹具（list_1/2.html + chapter_1..3.html）
└─ tools/
   ├─ launcher.py                 ★ 交互问答（exe 与源码共用同一份）
   ├─ build_entry.py              ★ PyInstaller 入口：无参数=交互，有参数=命令行
   ├─ diag_site.py                站点体检：请求、编码、选择器、CF 判定
   ├─ check_epub.py               EPUB 自检 + 与分章 TXT 逐张比对插图（回归用）
   ├─ run_build.cmd               打包便携版 exe
   └─ novel_scraper/              ⚠ 遗留 v1.0.0 包（python -m novel_scraper ...），未被引用
```

工作区根目录里另有：

| 路径 | 作用 |
| --- | --- |
| `browser_session.py` | 用 Playwright 打开浏览器人工过 Cloudflare，把 cookie+UA 写成 `browser-state.json` |
| `browser-session.cmd` | 上面那个的双击外壳 |
| `browser-state.json` / `_browser-profile/` | 会话文件与浏览器配置（**不进包、不进 git**） |
| `log/` | 运行日志（见 §5.7） |
| `output/<书名>/` | 抓取成果，见下面「输出目录布局」 |
| `../new/` | 分发的成品目录（exe + `sites.json` + `使用说明.md` + `demo-book/`）；源码和构建缓存不许放进来 |

### 运行时目录约定（容易踩）

`APP_DIR` = `novel_scraper.py` 所在目录（打包后 = exe 所在目录）。约定：

- 输出：`APP_DIR.parent / "output"` —— 也就是**包文件夹的上一层**；
- 日志：`APP_DIR.parent / "log"`；
- 配置：`APP_DIR / "sites.json"`，找不到就在上一层找（`find_hosts_file()`，`novel_scraper.py:152`）。

所以便携版解压后：`<解压目录>\novel-scraper.exe` 跑完，成果在 `<解压目录>\..\output\`、日志在 `<解压目录>\..\log\`，不会污染放 exe 的那个文件夹本身。

**输出目录布局（`main()` 里 `out_dir` 的构造，`:1828` 起）** —— 文本和电子书**分两个子文件夹**，不要把 `_emit_txt` / `epub_path` 的落点改回 `out_dir` 根：

```
output/<书名>/
├─ txt/     各卷 TXT、分章 TXT（00001_<标题>.txt）—— `load_scraped_chapters()` 续传与
│           `--rebuild-epub` 都从这里读
├─ epub/    <书名>.epub、分卷 <书名> - NN <卷名>.epub、整本「总览」<书名>.txt
└─ images/  插图原图
```

- 三个变量是 `txt_dir = out_dir / "txt"`、`epub_dir = out_dir / "epub"`、`img_dir = out_dir / "images"`；合并/总览文本 `merged_path = epub_dir / (book_stem + ".txt")`（跟电子书放一起），分卷 TXT 的命名基准 `vol_txt_base = txt_dir / (book_stem + ".txt")`。
- `load_scraped_chapters(out_dir)` 先扫 `out_dir/"txt"`、再扫 `out_dir` 根（去重），兼容旧输出目录。
- `--rebuild-epub <目录>` 的落点：该目录下有 `txt/` 就写进 `epub/`，否则写回原目录（`rebuild_dir`，`:1700`）。
- `tools/check_epub.py` 也会自动探测 `txt/` 子目录（不传 `--dir` 时）。
- 改任何一处都要同步：`failed.txt` 仍留在 `out_dir` 根。

---

## 2. 环境与运行

| 项目 | 值 |
| --- | --- |
| Python | 3.14.6（`H:\Anaconda\python.exe`），实际只需 3.10+ |
| 依赖 | `requests`、`beautifulsoup4`；**没有 lxml**，所以只能用 BeautifulSoup 的 CSS 选择器（`select_one`/`select`） |
| 过 CF | `playwright` + 一个 Chromium（只有 `browser_session.py` 需要，不进 exe） |
| 校验 | EPUBCheck 5.2.1（需要 Java）：`java -jar <epubcheck.jar> <epub>` |

```powershell
# 源码方式交互（等价于双击 run.cmd）
python start.py

# 打包方式（等价于双击 run.cmd 的 exe）
python tools\build_entry.py

# 直接命令行（调试主力）
python novel_scraper.py --url "https://site/book/123" -o output --format both
```

**PowerShell 陷阱**：Windows 下 `pwsh` 里跑 python 前先设好编码，并把输出重定向到文件再读，否则 stderr 的中文会触发 `NativeCommandError`，把 `$LASTEXITCODE` 污染成 1，让你误判失败：

```powershell
$env:PYTHONUTF8='1'; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
python novel_scraper.py ... > _dbg.log 2>&1
$LASTEXITCODE; Get-Content _dbg.log -Tail 30
```

---

## 3. 数据流

```
URL ──► pick_config()        按域名挑 sites.json 配置
    ──► Fetcher.get()        目录页 HTML（串行限速 / 重试 / 编码 / CF 判定）
    ──► resolve_book_name()  书名（配置 book_name → 页面选择器 → <title> → URL 末段）
    ──► parse_list_pages()   [(标题, URL), ...]，支持分页模板
    ──► group_by_volume()    [卷名, [章节...]]（供 --volumes / 分卷 EPUB 用）
    ──► 逐章 parse_chapter()
    │      └─ extract_blocks()  → blocks = ["段落", ("img", url), "段落", ...]
    │      └─ _lines_to_paragraphs()  清洗广告/导航/重复标题
    │      └─ 插图章（标题含「插图」）只留图
    ├─► 分章 TXT  output/<书名>/txt/00007_第三卷 第一章.txt
    ├─► 插图原图   output/<书名>/images/00007_01_第三卷 第一章.jpg
    └─► write_epub() → output/<书名>/epub/<书名> - 01 第一卷 ….epub（一卷一个）+ <书名>.epub（整本）
```

`main()` 的组织（`novel_scraper.py:1622`）：解析参数 → `start_logging()` → 若 `--rebuild-epub` 走离线重建分支并 `return` → 配置/会话/cookies → `[1/3]` 解析目录 → `--list-volumes` 分支可提前 `return 0` → `--dry-run` 分支 → `[2/3]` 抓正文循环 → `[3/3]` 汇总 → `write_epub()`。

---

## 4. 核心机制

### 4.1 Fetcher（`novel_scraper.py:419`）

- `_wait()` 保证两次请求间隔 ≥ `delay`（默认取站点配置，多数站 2.0s）——**串行限速是刻意设计，不要为了快改成并发**。
- `get(url, referer)` 取 HTML；`get_bytes(url)` 取图片，返回 `(bytes, 建议扩展名)`。
- `file://` 开头直接读本地文件 → 离线夹具全靠它，`pick_config()` 用路径里的目录名匹配 `fixture_dir`。
- 命中 Cloudflare 验证页（`_is_cf_challenge`，`:348`）时**抛 RuntimeError 且不重试**，错误信息里直接给出补救步骤（跑 `browser-session.cmd`）。
- 普通网络异常重试 `retry`（默认 3）次，退避 `1.5*attempt + random()`。
- 编码三段回退：配置 `encoding` → `resp.encoding` → `apparent_encoding`。

### 4.2 正文到 blocks：**本项目最重要的契约**

`extract_blocks(node, page_url)`（`:749`）在**干净的 DOM** 上按文档顺序产出混合列表：

```python
["……第 1 段……", ("img", "https://…/a.jpg"), "……第 2 段……"]
```

实现要点（改这里前务必读懂）：

1. 先把 `<img>` 换成**可打印占位符** `@@DSHIMG{序号}@@`（常量 `IMG_PLACEHOLDER`/`IMG_PLACEHOLDER_RE`，`:665`），而不是先取文本再找图 —— 文本一取出来 DOM 结构就没了。
2. 图片 URL 的候选属性顺序是 `IMG_ATTRS`（`:178`，含 `data-src`/`data-original` 等懒加载属性），并用 `IMG_SKIP_RE`（`:181`）滤掉 logo/icon/占位图。
3. `<br>` → `\n`，块级标签（p/div/h1..h3/li/td/tr）末尾补 `\n`，然后 `get_text("", strip=False)`。
4. 用 `IMG_PLACEHOLDER_RE.finditer(raw)` **按 match 位置切片**还原顺序 —— **不要改回 `re.split`**：带捕获组的 split 返回的占位符也是字符串，`isinstance` 区分不出「这是占位符还是正文」。
5. 后续 `_lines_to_paragraphs()`（`:785`）按行整理：丢掉空行、与标题完全相同的行、`上一章/下一章/返回目录` 开头的导航行；**只输出纯文本，不加任何缩进**。
   - 段首缩进是**写文件时的排版决定**，不是正文内容：TXT 走 `"\n".join(body)`（**一段一行、段间不空行**，章与章之间才空一行 —— 这类站点靠 `<p>` 分段，`#content p` 上没有 `text-indent`）；EPUB 走 `EPUB_CSS`（`:1120`）里的 `p { … text-indent: 2em; }`。**不要把 `\u3000\u3000` 写回段落文本**，否则 EPUB 里会双层缩进。
   **注意：屏蔽词（垃圾词）过滤已整体移除**。原来这里有一张内置垃圾词表，并会合并站点的 `extra_junk_words`，任何含这些词的行都被整行删除；该功能按用户要求删掉了。现在站点配置里写 `extra_junk_words` **不会生效**（代码不再读取该键），正文里出现什么都不再被自动删除。改回旧行为前请先确认用户要的是「自动删内容」。

### 4.2.1 反爬：乱序正文 + 诱饵段落（`deobfuscate_content()`，`:678`）

有些站不在 HTML 里给出正文顺序，而是：

- 把段落全部塞进 `<p style="order:N">` —— **`order` 只决定视觉顺序，是幌子**；
- 每个 `<p>` 再挂四个属性，真正生效的属性名由父节点的 `data-a` 指定，另外三个是干扰项；
- 混入若干**诱饵段**（把真句截断后插进假话，并染上 `data-c` 指定的那个类名，页面 CSS 把它们 `position:absolute;left:-9999px;opacity:0` 藏掉）；
- 靠页面**底部**一段脚本在**空闲 7 秒**（scroll/wheel/touch/mousemove/keydown 会重置计时）后重排 DOM、删掉诱饵。

`deobfuscate_content(node)` 把这段脚本的算法原样搬进了 Python，**在解析 DOM 阶段就还原好**，不依赖 JS、不依赖等待：

```js
var tt = en ^ (sd % 256);              // sd=data-r, en=data-t, ek=data-k, mo=data-c, an=data-a
function dp(h){ var tg=parseInt(h,16)>>>0; var mx=tt+50; if(mx<2000)mx=2000;
  for(var p=0;p<mx;p++){ var v=(p*31+ek)|0; v=v^((sd>>3)&0xFFFF);
    v=Math.imul(v,2654435761)>>>0; if(v===tg)return p; } return -1; }
// 无该属性的 <p> → 序号 -1（排最前）；op<0 或 op>=tt 或带 mo 类 → 诱饵，删；否则按 op 升序
```

实现要点：

1. **触发条件是 DOM 属性，不是域名** —— `#content` 上少 `data-r`/`data-t`/`data-a`/`data-k` 任一个就直接 `return False`。所以普通站点零影响，也**不需要在 `sites.json` 里配置任何东西**。新站遇到同类反爬但算法不同，就在这个函数里加分支，判据仍放在 DOM 属性上。
2. `_js_imul()`（`:672`）复刻 `Math.imul` 的 32 位有符号回绕；`Math.imul(v, 2654435761)>>>0` 等价于先算 64 位乘积再取低 32 位。Python 侧按 `& 0xFFFFFFFF` 取模即可。
3. **预建 `hash → p` 的 `table`**，不要对每个 `<p>` 重跑那最多 2000 次的循环 —— 章节动辄近千段。
4. 顺序值用 `int(raw, 16) & 0xFFFFFFFF` 归一到和 JS 的 `>>>0` 一致。
5. 收尾 `node.clear()` 后按序回填，并把五个 `data-*` 属性 `pop` 掉（避免下游把它们当正文属性）。
6. 命中时 `global DEOBFUSCATED` 计数，`main()` 结束时打一行 `（其中 N 章做了乱序/诱饵段落还原）`。**看不见这一行 = 这本书没被这样处理过，不是失败。**
7. 判定基准：与浏览器「查看源代码」拿到的同章文本逐段比对，**106/106 完全一致**（`_jtest\_verify_5483.py`）。


`parse_chapter()`（`:826`）返回 `{title, paragraphs, images, blocks, is_illustration}`：

- `chapter_junk` 里的选择器会先 `decompose()` 掉（广告、分页条等）；
- `chapter_title` 命中时以页面标题为准（站点常带「第一章 xxx-《书名》」后缀，可自行选择是否配）；
- **插图章**（`chapter_is_illustration`，`:816`，默认关键词「插图/插画/彩图/扉页/口绘」，可被站点 `illustration_title_words` 覆盖）只保留 tuple 块 —— 因为这类页面里的文字往往是站点塞的填充句，整段丢弃、只留图。
- 若清洗后段落与图片都为空 → 抛 `RuntimeError`，不算抓成功。

### 4.3 卷的识别与选择

- `VOLUME_RE`（`:905`）匹配 `第X卷/部/篇/集`，**小数点在正则里是可选组**（`第\s*[0-9一二三…]+(?:\s*[.．]\s*[0-9]+)?\s*[卷部篇集]`），所以「第8.5卷」能独立成卷、不会被漏掉也不会并进第八卷；`volume_label()`（`:970`）取标记后第一段作为候选卷名。
- `group_by_volume()`（`:984`）**按卷标记本身分组**；候选标签只有在整卷里重复出现比例 ≥ `VOLUME_LABEL_SHARE = 0.6`（`:981`）时才当卷名，否则退化成标记本身。
  - 为什么：真实站点里标题形如「第一卷 ～第一败～某某女主 的惨烈败相」，每章后缀都不同；早期按「标记后第一段」分组会把一本 234 章的书**碎成 118 个假卷**。整卷后缀一致的站则能拿到「第十四卷 水都市的圣女」这种好名字。
- **短篇集合并**：`_merge_short_story_volumes()`（`:1015`）在 `group_by_volume()` 末尾调用。候选 = 「整卷每一章的 `volume_label()` 都含 `SHORT_STORY_WORD = "短篇"`」（`:910`）**且**「卷名里不含 `.` / `．`」的组；候选 < 2 个就原样返回（只剩一卷时保留原卷名，信息更多），否则把它们的章节按目录顺序合成一个名为 `SHORT_STORY_VOLUME = "短篇集"`（`:911`）的卷，插回第一个候选原来的位置。**「不含小数点」这个条件是必需的**：只按「标签含短篇」判断会把 `第8.5卷 短篇集` 一起并走，而用户明确要求小数点卷自成独立卷。
- 分卷选择相关：`VolumeItem`（`:1043`）、`volume_items()`（`:1057`）、`parse_volume_tokens()`（`:1061`，按空格/逗号/顿号/分号拆）、`match_volumes()`（`:1070`，依次尝试 纯数字=序号 → 卷名全等 → 只看卷标记 → 前缀 → 包含，返回「命中的下标 + 没命中的 token」）、`volume_table()`（`:1104`，打印 `  1. 第一卷 起始之章（9 章，第 1~203 章）`）。
- **不变量：`VolumeItem.index` 必须保持「完整目录里的 1 基序号」**，分章 TXT 也用这个序号命名。这样「只抓第九卷」和「整本抓」能续传同一批文件、不会错位。筛选时不要重新编号。

### 4.4 EPUB 写入器

`write_epub(out_path, book_name, chapters, source_url="", author="", image_loader=None, on_progress=None, split="both", vol_numbering=None) -> list[Path]`（`:1489`）

- 打包单位是**卷**：一卷一个 XHTML（`_epub_vol_doc`，`:1172`），卷内章节用 `<section epub:type="chapter">` 分隔；`nav` 是 卷→章 两级（`_epub_nav`，`:1192`），章节数超过 `_TOC_MAX = 150`（`:1137`）只列卷。
- `split`：`"both"`（默认，分卷 + 整本）/ `"volume"`（只分卷，**哪怕只有一卷也出 `书名 - 01 卷名.epub`**）/ `"single"`（只整本）。返回值是写出的文件列表，分卷在前、整本最后。
- `vol_numbering = {卷名: 完整目录里的卷序号}` —— 只抓部分卷时靠它保住「第九卷 → 09」的编号；不传就按本次顺序编号（会变成 `- 1`）。
- 封面：`_epub_cover()`（`:1234`）用**该卷正文顺序里第一张「竖版」插图**生成 `cover.xhtml`；同时写 `<meta name="cover" content="imgN"/>`（EPUB2 兼容）+ `properties="cover-image"`。判定在 `_write_epub_file`（`:1312`）内部：从前到后扫 `ch.images`，遇到 `_is_portrait()`（`:334`，即 `h > w`，等价于 width:height < 1）就选中，一张竖版都没有才退回第一张 —— **不要改回「无条件取第一张」**，不少书把跨页彩图/扉页横幅排在真封面前面。尺寸由 `image_size(data)`（`:255`）读文件头得到（PNG/GIF/BMP/WebP(VP8/VP8L/VP8X)/SVG/JPEG），**不能依赖 Pillow**（打包命令带 `--exclude-module PIL`），所以它是手写解析 + 逐段长度守卫，纯解码失败一律返回 `None`（当成非竖版、不覆盖已选中的封面）。整本 EPUB 同样用这条规则在全书插图里选。卷内无图则不生成封面页，正常退化。
- 图片只下载一次（`_download_images`，`:1289`），打包时统一重命名为 `0001.jpg` 顺序号，分卷文件各自引用自己那一卷的图。**所以 EPUB 里的文件名与 `images/` 下的原名必然不同 —— 任何比对都必须按顺序或按内容哈希，不能按文件名。**
- EPUB 3 是**纯标准库手工打包**，五条硬约束（注释在 `:885` 附近）：mimetype 必须是 ZIP 首个条目且 STORED 不压缩；`META-INF/container.xml` 指向 OPF；manifest 的 href 都要真实存在、spine 的 idref 都要有 item；`nav.xhtml` 要带 `properties="nav"`；正文必须是合法 XHTML。改完一定要跑 EPUBCheck。

### 4.5 续传与补抓

- 分章文件命名 `f"{序号:05d}_{safe_name(标题)}.txt"`（`safe_name`，`:339`，替换 Windows 非法字符）。
- 已存在且非 `--no-resume` → 读回该文件重建 blocks：首行是标题，其余按序解析，`IMG_MARK_RE = re.compile(r"^\[插图\s*(\d+)\]\s*(.+)$")识别插图行。
  - `.+` 不能改成 `\S*`：章节名里的空格很常见（`第一卷 起始之章 插图.jpg`），用 `\S*` 会整行匹配失败，插图行被当正文读回去。
- 特殊情况：某章是插图章、分章文件里却一张图都没有（上次图源挂了）→ 触发「补抓插图」，此时代码会把 `FETCHER.delay` 抬到站点 `delay` 再抓（`:1924` 附近）。
- 失败章节收集到 `failed.txt`，`main()` 返回 1。

### 4.6 分卷输出（TXT 与 EPUB 的两种「分卷」）

选了几卷（`--volumes` / 菜单 `4`）时：`vol_mode = True`，**不写整本合并文件**，每卷一个 TXT（`_vol_file_name`，`:1482`：`txt/书名 - 09 第九卷 双月的舞会.txt`）和每卷一个 EPUB（`epub/`）；实现上用 `contextlib.ExitStack()` + `_emit_txt(index, text)`（`:1884`）按卷惰性开文件。序号补零宽度取决于总卷数。

### 4.7 日志

`start_logging(log_dir=None, argv=None)`（`:105`）：

- 目录优先级：`--log-dir` 参数 → 环境变量 `NOVEL_SCRAPER_LOG_DIR` → `APP_DIR.parent / "log"`；
- 环境变量 `NOVEL_SCRAPER_LOG` 里已有路径就**追加到同一文件**（父进程 launcher 传给子进程 exe，交互模式下一份日志看全程）；
- `_Tee`（`:66`）把 stdout/stderr 同时写屏幕与文件；`atexit` 注册关闭。

### 4.8 交互层（`tools/launcher.py`）

`run_interactive(app_dir, scraper=None) -> int`（`:222`）是一个 `while True:` 主菜单循环：

- `4. 选择分卷下载（当前：…）` 是**修饰项不是格式项** —— 选完 `continue` 回菜单，1/2/3 才 `break` 去执行；选项 4 的标签动态显示当前选择（`_describe_vols`，`:120`）。
- 分卷列表用「**子进程 + JSON 文件回传**」拿：`_list_volumes()`（`:155`）跑 `scraper --list-volumes <临时 JSON>`，表格由子进程直接打到终端，父进程只读 JSON 解析序号→卷名。
  - 为什么不捕获 stdout：一是沙箱环境下管道会被拒，二是表格本来就该原样给用户看。
- 选择存 `app_dir/.last-volumes`（`VOL_CFG`，`:84`，JSON：`{"url","tokens","names"}`），URL 不同视为没选过（`_load_vol_cfg`，`:91`）；回车 = 保持，`all`/`0`/`全部`/`*` = 清空（`_ask_volumes`，`:194`、`_clear_vol_cfg`，`:113`）。
- 传给子进程的命令行是 `--volumes "第一卷 第九卷"`（走卷名，不依赖序号）。
- **跑完不自动关窗口**：`_pause(msg="按回车键关闭窗口…")`（`:46`）。用在 `run_interactive` 的**两个出口** —— `except KeyboardInterrupt` 分支 `return 130` 之前，以及函数末尾正常返回之前。**不要改成无条件 `input()`**：它必须先看环境变量 `NOVEL_SCRAPER_NO_PAUSE`（设了就跳过）和 `sys.stdin.isatty()`（管道/重定向/CI 下直接返回），否则脚本调用会永久挂住。跑自动化时设 `NOVEL_SCRAPER_NO_PAUSE=1`。

---

## 5. 扩展怎么做

### 5.1 加一个站点（最常见，不用改代码）

1. 先在 `sites.json` 的 `hosts` 里加一条，键是**域名**（`host` 等于它或以 `.` + 它结尾都能匹配，最长后缀优先）。
2. 必填 `list_link`、`chapter_content`；其余见《使用说明》的字段表。
3. 三步验证，**别跳**：
   ```powershell
   python tools\diag_site.py <目录页URL>                    # 先体检：能否直连、编码、选择器命中情况
   python novel_scraper.py --url <URL> --dry-run            # 目录解析对不对、书名对不对
   python novel_scraper.py --url <URL> --start 1 --end 1 --format both -o _one
   ```
4. 最后一章检查 `_one\<书名>\00001_*.txt` 的内容与图片，再放开整本。
5. 站点改版后选择器会失效 —— 配置里的 `note` 字段请写清「什么时候核对的、核对了什么」。

### 5.2 加输出格式 / 命令行参数

1. `main()` 的 argparse 块（`:1623` 起）加参数；
2. `[2/3]` 抓取循环与 `[3/3]` 汇总里接上（TXT 走 `_emit_txt`，EPUB 走 `write_epub`）；
3. 如果交互模式也该问，就同步改 `tools/launcher.py` 的菜单与 `FORMATS`（`:29`）；
4. 同步更新 `README.md`、`../new/使用说明.md`；
5. 视参数性质决定是否进 `build_entry.py`（通常不用，它只做分发）。

### 5.3 改 EPUB 外观

只改 `EPUB_CSS`（`:1120`）。结构相关（卷/章/封面/nav）改 `_epub_vol_doc`、`_epub_nav`、`_epub_cover`、`_epub_title_page`（`:1255`）。改完务必 `check_epub.py` + EPUBCheck。

### 5.4 新增一个交互问题

在 `run_interactive` 的循环里加编号项，注意约定：**格式项（1/2/3）结束循环，修饰项（如 4）`continue`**。用户输入走 `_ask(prompt, default)`（`:36`，回车取默认）。

---

## 6. 踩坑清单（都是真踩过的）

| 坑 | 症状 | 正确做法 |
| --- | --- | --- |
| `--rebuild-epub` 不传 `image_loader` | 重建出的 EPUB 静默丢光所有插图，大小正常只是变小 | 离线重建必须传 `local_loader`（读分章 TXT 同目录的 `images/`），见 `main()` 分支（`:1691`） |
| 按文件名比对插图位 | 误报「插图文件序列不一致」（实测 385 张全报错） | 打包会重命名图片；按顺序或**内容哈希**比对 |
| `re.split` 拆占位符 | 占位符与正文混在一起，图位置全乱 | `finditer` + 位置切片（`:772`） |
| 站点把正文乱序 + 插诱饵段 | 段落顺序错乱、混进「说不出的难受」这类假句 | `<p style="order:N">` 只是幌子；用 `deobfuscate_content()`（`:678`）按 `data-r/t/a/k` 原地还原，**不要读 `order`，也不要等页面脚本**（它要空闲 7 秒才跑） |
| 把缩进写进正文文本 | 网页上本没有 `text-indent`（靠 `<p>` 分段），TXT 里出现多余全角空格，EPUB 里双层缩进 | 段落只存纯文本；缩进交给 TXT 的 `\n\n` 分段与 EPUB 的 CSS |
| `IMG_MARK_RE` 用 `\S*` | 带空格的插图文件名整行匹配失败，图变正文 | 必须 `.+`（`:1547`） |
| 进度用章节序号当计数 | 只抓第九卷时打印 `[80/12]` | 用 `enumerate(picked_entries, 1)` 的 `_pos` |
| 卷标签整卷不一致 | 一本 234 章的书碎成 118 个假卷 | `VOLUME_LABEL_SHARE = 0.6` 阈值 + 按卷标记分组 |
| 分卷筛选后重新编号 | 按卷抓与整本抓的分章文件对不上，续传错位 | `VolumeItem.index` 保持全局 1 基序号 |
| 打包后 `__file__` 指向临时解包目录 | 配置/输出目录找错 | 打包入口用 `Path(sys.executable).parent` 当 `app_dir`（`build_entry.py:20`） |
| 交互模式日志分成两份 | 父进程一份、exe 子进程一份 | 父进程先 `start_logging()` 再用 `NOVEL_SCRAPER_LOG` + `--log-dir` 传递 |
| 临时文件混进 zip | 包里出现 `.last-url` / `.last-volumes` / `.volumes.json` | 打包前清理 `distpath`，zip 里只留 exe + sites.json + 使用说明 + demo-book |
| `run_build.cmd` 挂住 | 非交互执行卡在末尾 `pause` | 自动化时直接用等价的 `python -m PyInstaller …` 命令 |
| pwsh 里误判失败 | 中文 stderr 触发 NativeCommandError，`$LASTEXITCODE` 变 1 | 设 `PYTHONUTF8`，输出重定向到文件再读 |
| exe 首启被 SmartScreen 拦 | 用户以为坏了 | 见《使用说明》常见问题（未签名程序的正常提示） |

---

## 7. 测试与发布

### 7.1 三层验证

```powershell
# ① 离线夹具（不联网，跑 3 章）：证明主流程与参数没被改坏
python novel_scraper.py --url "file:///<源码目录>/demo-book/list_1.html" `
  -o _fx --format both --no-resume

# ② 真实站小范围：证明选择器与正文清洗还对（先过验证拿到会话文件）
python novel_scraper.py --state ..\browser-state.json `
  --url "<书目页 URL>" `
  --start 1 --end 2 --format epub -o _one

# ③ 结构与插图回归
python tools\check_epub.py _one\书名\epub\书名.epub --dir _one\书名
java -jar "%TEMP%\epubcheck\epubcheck-5.2.1\epubcheck.jar" _one\书名\epub\书名.epub
```

`check_epub.py` 会：查 mime/XML/OPF/spine 完整性，并把 EPUB 里每张图与分章目录 `images/` 下的原图**逐张 SHA-256 比对**，再核对「段落序列 + 每张图前面那段正文」的位置锚点。退出码 0 = 全过；`4` = 位置/内容对不上。不传 `--dir` 时它会自动找旁边的 `txt/`。

### 7.2 打包与分发

```powershell
# 等价于 tools\run_build.cmd（注意它末尾有 pause）
cd <源码目录>
python -m PyInstaller --noconfirm --clean --onefile --console --log-level=WARN `
  --name novel-scraper `
  --distpath "..\new" --workpath "_build" --specpath "_build" `
  --add-data "$((Get-Location).Path)\sites.json;." --hidden-import tools.launcher `
  --exclude-module tkinter --exclude-module matplotlib --exclude-module numpy `
  --exclude-module pandas --exclude-module PIL --exclude-module pytest `
  --exclude-module IPython tools\build_entry.py
```

约定：

- **成品**去 `..\new\`（exe + `sites.json` + `使用说明.md` + `demo-book\`）；
- **源码与构建缓存 `_build\` 留在源码树里**，不要丢进成品目录；
- 打完必须在**干净目录**实测一次（拷 exe + `sites.json` + `demo-book\` 到 `%TEMP%`），确认：命令行模式能跑、交互模式能跑、`output\` 与 `log\` 落在 exe 的上一层；
- 生成 zip：`Add-Type -AssemblyName System.IO.Compression.FileSystem` 后 `[System.IO.Compression.ZipFile]::CreateFromDirectory(src, zip, Optimal, $false)`；
- `sites.json` 改过就要重新打包（它作为 `--add-data` 打进 exe 内部，同时也在包外放一份可直接编辑）；
- **`--exclude-module PIL` 是有意的**：图片尺寸/格式判定必须靠 `image_size()` / `sniff_ext()` 自己读文件头，任何新功能都不要引入 Pillow，否则打包后就崩。

---

## 8. 已知限制与 TODO

- **每章分多页的站抓不全**（正文页带 `第(1/3)页`，第 2/3 页是 `..._2.html`）。当前只取第一页 → **每章静默丢 2/3 正文**。要做就得加「章节分页」支持：读到页码标记 → 顺链抓完 → 合并 blocks。
- **JS 渲染的目录/正文抓不到**（`requests` 只拿到空壳），需要浏览器渲染 —— 没做，配置上也解决不了。
- 行内图（`<p>文字<img>文字</p>`）会把前后文字切成两个独立段落（可接受，但不完美）。
- 分卷 EPUB 目前按**站点目录顺序**编号（短篇集可能夹在中间），没有按卷号重排。
- 无图版（`--no-images` 打包）没做，仍是同一个 exe。

---

## 9. 提交前自查

- [ ] `python start.py` 交互模式能走通「网址 → 4 选卷 → 2 只要 EPUB」并真的只出所选卷
- [ ] 夹具 + 真实站小范围两轮都过（§7.1）
- [ ] `check_epub.py --dir` 显示插图「逐张字节一致」
- [ ] EPUBCheck `0 errors`
- [ ] 新增/改动的参数、字段已写进 `README.md` 与《使用说明》
- [ ] 没有临时脚本、`_dbg.log`、`.last-volumes` 之类文件混进交付包
