# 本地文件内容检索台

建设面向本地资料目录的文件检索产品，逐步覆盖目录登记、文本索引、关键词查询、命中片段、文件属性筛选、增量更新、重复文件提示和结果导出。

计划采用：Python 3 标准库 / sqlite3 / pathlib / re / argparse。

当前已交付**最小文本检索功能**：离线扫描指定目录下的 `.txt` / `.md` 文件，按字面关键词在单行内匹配，以 JSON 输出每个文件的首个命中。全程仅使用 Python 3 标准库，无需安装第三方包，不建立索引、不修改源文件、不留下索引文件。

## 运行方式

需要 Python 3.8+，在项目根目录执行：

```bash
python -m local_search "<资料目录>" "<关键词>"
```

- 目录参数支持绝对路径，也支持相对于当前工作目录的路径；包含中文或空格时按常规加引号即可。
- 每次调用只扫描传入的目录及其子目录，仅纳入扩展名 `.txt` / `.md` 的普通文件，扩展名大小写不敏感（如 `.TXT`、`.Md` 也会纳入）。
- 扫描不跟随任何符号链接（包括指向目录的链接）。
- 关键词按**字面文本**处理：区分大小写、不解释正则表达式、不拆分多个词，内部空格原样保留。
- 匹配限定在单行内；每个文件只返回按行号、行内位置确定的**首个命中**，同文件多个命中不重复返回。
- 标准输出为一个 JSON 数组，每项仅含 `path`、`line`、`snippet`：
  - `path`：相对于所选目录的路径，统一使用正斜杠 `/`；
  - `line`：从 1 开始的行号；
  - `snippet`：完整关键词加上同一行前后各至多 30 个 Unicode 字符，不含换行符，不添加省略号。
- 结果按 `path` 的区分大小写字典序排列。

退出码：

| 退出码 | 含义 |
| --- | --- |
| `0` | 查询正常完成（没有命中、目录为空或只有不支持的文件时输出 `[]`） |
| `2` | 目录不存在、目录参数指向文件、无法完成目录遍历，或关键词为空/全为空白；此时标准输出为空，原因写入标准错误 |

单个候选文件无法读取或不能按 UTF-8 解码时会跳过该文件：标准错误报告其相对路径与失败原因，其余文件照常参与查询，本次查询退出码仍为 `0`。

## 本地使用示例

以下命令可直接逐条复制核对（示例目录名 `资料目录` 含中文；换用含空格的目录名如 `"my notes"` 同样可行）。

### 1. 准备示例文件

```bash
mkdir -p "资料目录/notes"
printf 'alpha\ntarget note\n' > "资料目录/a.txt"
printf 'target again\n' > "资料目录/notes/b.md"
```

`资料目录/a.txt` 第一行为 `alpha`、第二行为 `target note`；`资料目录/notes/b.md` 第一行为 `target again`。

### 2. 查询 `target`：两个结果按路径排列

```bash
python -m local_search "资料目录" "target"
```

标准输出（为便于阅读此处折行，实际为单行 JSON）：

```json
[
  {"path": "a.txt", "line": 2, "snippet": "target note"},
  {"path": "notes/b.md", "line": 1, "snippet": "target again"}
]
```

- `a.txt` 命中在第二行，`notes/b.md` 命中在第一行；
- 两项按 `path` 字典序排列（`a.txt` 在前，`notes/b.md` 在后）；
- 退出码为 `0`（可用 `echo $?` 核对）。

### 3. 查询 `TARGET`：区分大小写，结果为空

```bash
python -m local_search "资料目录" "TARGET"
```

标准输出：

```json
[]
```

退出码为 `0`。

### 4. 不支持的文件不会进入结果

放入一个非 `.txt`/`.md` 文件，即使内容包含关键词也不会被检索：

```bash
printf 'target in csv\n' > "资料目录/data.csv"
mkdir -p "资料目录/pics"
printf 'target in png\n' > "资料目录/pics/x.png"
python -m local_search "资料目录" "target"
```

输出与第 2 步完全相同，`data.csv` 与 `pics/x.png` 均不在结果中：

```json
[
  {"path": "a.txt", "line": 2, "snippet": "target note"},
  {"path": "notes/b.md", "line": 1, "snippet": "target again"}
]
```

扩展名大小写不影响纳入范围，例如将文件命名为 `UPPER.MD` 仍会被扫描。

### 5. 无效 UTF-8 的文件被跳过，其余文件照常返回

制造一个无法按 UTF-8 解码的 `.txt` 文件：

```bash
printf 'target before\n\xff\xfe bad bytes\n' > "资料目录/broken.txt"
python -m local_search "资料目录" "target"
echo "exit=$?"
```

- 标准输出仍是 `a.txt` 与 `notes/b.md` 两个结果（`broken.txt` 不出现）：

```json
[
  {"path": "a.txt", "line": 2, "snippet": "target note"},
  {"path": "notes/b.md", "line": 1, "snippet": "target again"}
]
```

- 标准错误额外报告被跳过的文件（具体措辞中的解码说明由 Python 给出）：

```text
跳过文件 broken.txt: 'utf-8' codec can't decode byte ...
```

- `exit=0`：存在被跳过的文件不影响查询成功。

### 6. 错误输入：退出码 2，标准输出为空

```bash
# 目录不存在
python -m local_search "不存在的目录" "target"; echo "exit=$?"

# 目录参数指向一个文件
python -m local_search "资料目录/a.txt" "target"; echo "exit=$?"

# 关键词为空
python -m local_search "资料目录" ""; echo "exit=$?"

# 关键词全为空白（其中的空格不会被当作分隔符）
python -m local_search "资料目录" "   "; echo "exit=$?"
```

以上每条命令均满足：标准输出为空，标准错误写明对应原因，退出码为 `2`。

### 7. 片段长度与空格关键词

`snippet` 只取命中行上关键词前后各至多 30 个字符。若要检索带空格的字面短语，直接整体加引号：

```bash
python -m local_search "资料目录" "target note"
```

```json
[
  {"path": "a.txt", "line": 2, "snippet": "target note"}
]
```

> 接着第 5 步的环境执行时，标准错误仍会出现 `broken.txt` 的跳过提示；上方 JSON 是标准输出，二者互不影响。
