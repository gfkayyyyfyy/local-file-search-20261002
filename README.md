# 本地文件内容检索台

建设面向本地资料目录的文件检索产品，逐步覆盖目录登记、文本索引、关键词查询、命中片段、文件属性筛选、增量更新、重复文件提示和结果导出。

计划采用：Python 3 标准库 / sqlite3 / pathlib / re / argparse。

当前已交付**文本检索最小功能**：无需安装任何第三方包，离线运行，只读扫描源文件，查询结束后不留下索引文件。

## 环境要求

- Python 3（仅使用标准库）
- 无需联网，无需 `pip install`

## 使用方式

在项目根目录下执行：

```bash
python -m local_search <目录> <关键词> [--path-contains <路径片段>] [--ignore-case] [--all-lines]
```

- `<目录>`：要扫描的目录，支持绝对路径或相对于当前工作目录的相对路径，可包含中文或空格（记得加引号）。
- `<关键词>`：非空的**字面文本**，默认区分大小写；不解释正则表达式，不拆分多个词，关键词中的空格（含首尾空格）按原样保留，匹配不跨行。为空或全为空白时报错。
- `--ignore-case`：可选的无取值开关，只能写在两个位置参数之后，可与 `--path-contains`、`--all-lines` 以任意先后顺序同用。启用后**仅将 ASCII 的 A–Z 与 a–z 视为同一字符**：`TARGET` 可命中 `Target`、`target`；其他字符仍精确比较，例如 `É` 不匹配 `é`、`ß` 不匹配 `ss`。关键词仍按连续字面子串匹配，不拆词、不解释正则或通配符；`snippet` 始终取自源文本，保留原始大小写。未指定时完全保留区分大小写的原有行为。第二个位置参数即使恰好写作 `--ignore-case`，也仍是关键词。重复指定该开关时报错。
- `--all-lines`：可选的无取值开关，只能写在两个位置参数之后，可与 `--path-contains`、`--ignore-case` 以任意先后顺序同用。启用后，**同一文件的每个命中行分别返回一项**，结果按行号递增；同一行多次出现关键词仍只返回一项，并以最左侧命中生成片段。未指定时保留默认行为：每个文件只返回按行号、行内位置确定的首个命中。第二个位置参数或 `--path-contains` 的取值即使恰好写作 `--all-lines`，也仍按字面文本处理，不会开启该模式。重复指定该开关时报错。
- 只扫描该目录及其子目录中的普通文件，扩展名（大小写不敏感）为 `.txt` 或 `.md`；不跟随符号链接。
- 匹配限定在单行内；默认每个文件只返回按行号、行内位置确定的**首个命中**，同一文件多个命中不重复返回；指定 `--all-lines` 后每个命中行各返回一项（同一行多次出现仍只算一项）。
- 标准输出是一个 JSON 数组，每项仅含：
  - `path`：相对于选定目录的路径，统一使用正斜杠 `/`
  - `line`：从 1 开始的行号
  - `snippet`：完整关键词及同一行前后各至多 30 个 Unicode 字符，不含换行符，不添加省略号
- 结果按 `path` 的区分大小写字典序（Unicode 码点序）排列。
- 没有命中、目录为空或只含不支持的文件时输出 `[]`，退出码为 0。

## 本地使用示例

### 1. 准备示例文件

仓库内已附带同名资料目录；如需自行准备，可执行：

```bash
mkdir -p 资料/notes
printf 'header\nTarget note\n' > 资料/a.txt
printf 'target again\n'       > 资料/notes/b.md
```

### 2. 查询小写关键词 target

```bash
$ python -m local_search "资料" "target"
[{"path": "a.txt", "line": 2, "snippet": "Target note"}, {"path": "notes/b.md", "line": 1, "snippet": "target again"}]
$ echo $?
0
```

`a.txt` 命中在第 2 行，`notes/b.md` 命中在第 1 行；结果按路径排列。

### 3. 查询大写 TARGET（默认区分大小写，得到空数组）

```bash
$ python -m local_search "资料" "TARGET"
[]
$ echo $?
0
```

### 4. 启用 --ignore-case：大小写不同的 ASCII 命中也会返回

```bash
$ python -m local_search "资料" "TARGET" --ignore-case
[{"path": "a.txt", "line": 2, "snippet": "Target note"}, {"path": "notes/b.md", "line": 1, "snippet": "target again"}]
$ echo $?
0
```

`a.txt` 的 `Target note` 与 `notes/b.md` 的 `target again` 都被命中；`snippet` 保留源文本原始大小写。每个文件仍只返回按行号、行内位置确定的首个命中——较早的大小写不同命中不会让位于较晚的完全同大小写命中。非 ASCII 字符不受影响：`É` 不匹配 `é`，`ß` 不匹配 `ss`。

### 5. 启用 --all-lines：同一文件的每个命中行分别返回一项

仓库内附带 `演示/` 目录，其中只有 `a.txt`，三行依次为 `target target`、`other`、`Target later`。

```bash
$ python -m local_search 演示 TARGET --ignore-case --all-lines
[{"path": "a.txt", "line": 1, "snippet": "target target"}, {"path": "a.txt", "line": 3, "snippet": "Target later"}]
$ echo $?
0
```

第 1 行与第 3 行各返回一项；第 1 行内出现两次关键词仍只算一项，并以最左侧命中生成片段（该行短于 30 字符上下文，片段就是整行）。去掉 `--all-lines` 后保留默认行为，只返回第 1 行，同一行的两次命中不增加条目：

```bash
$ python -m local_search 演示 TARGET --ignore-case
[{"path": "a.txt", "line": 1, "snippet": "target target"}]
```

`--all-lines` 与 `--path-contains`、`--ignore-case` 可在两个位置参数之后以任意顺序同用；结果先按路径的区分大小写 Unicode 码点序排列，同一路径按行号递增。

### 6. 未支持的文件不会进入结果

再放一个非 `.txt`/`.md` 的文件（即使内容包含关键词也不扫描）：

```bash
$ printf 'target in log\n' > 资料/other.log
$ python -m local_search "资料" "target"
[{"path": "a.txt", "line": 2, "snippet": "Target note"}, {"path": "notes/b.md", "line": 1, "snippet": "target again"}]
```

扩展名大小写不敏感，例如 `notes/c.MD` 同样会被纳入；符号链接（无论指向文件还是目录）一律不跟随。

### 7. 无法读取或非 UTF-8 的文件被跳过并告警

构造一个包含非法 UTF-8 字节的文件：

```bash
$ printf 'bad \xff\xff bytes\n' > 资料/broken.txt
$ python -m local_search "资料" "target"
警告: 跳过文件 broken.txt: 无法按 UTF-8 解码 ('utf-8' codec can't decode byte 0xff in position 4: invalid start byte)
[{"path": "a.txt", "line": 2, "snippet": "Target note"}, {"path": "notes/b.md", "line": 1, "snippet": "target again"}]
$ echo $?
0
```

告警写到**标准错误**，JSON 结果仍写到**标准输出**；该文件被跳过，其余文件正常参与查询，退出码仍为 0。无法读取（如权限问题）的候选文件同样按此方式跳过。

### 8. 路径与关键词的边界情况

```bash
# 包含空格的目录与关键词（关键词中的首尾空格原样保留，不拆词）
python -m local_search "我的 资料" "target note"

# 正则元字符按字面文本处理，例如下面只匹配连续的 a.b*c? 六个字符
python -m local_search "资料" "a.b*c?"

# --ignore-case、--all-lines 与 --path-contains 可在两个位置参数之后
# 以任意顺序同用；路径筛选仍区分大小写，被排除的文件不读取、不告警
python -m local_search "资料" "TARGET" --ignore-case --path-contains "notes/"
python -m local_search "资料" "TARGET" --path-contains "notes/" --ignore-case
python -m local_search "资料" "TARGET" --all-lines --ignore-case --path-contains "notes/"

# --path-contains 的取值即使写作 --ignore-case 或 --all-lines 也仍是字面片段，而不是开关
python -m local_search "资料" "target" --path-contains "--ignore-case"
python -m local_search "资料" "target" --path-contains "--all-lines"
```

### 9. 错误情形（退出码 2，标准输出为空，原因写入标准错误）

```bash
$ python -m local_search "不存在的目录" target
错误: 目录不存在: 不存在的目录
$ echo $?
2

$ python -m local_search "资料/a.txt" target
错误: 路径不是目录: 资料/a.txt
$ echo $?
2

$ python -m local_search "资料" "   "
错误: 关键词为空或全为空白
$ echo $?
2

$ python -m local_search "资料" target --ignore-case --ignore-case
错误: 选项 --ignore-case 只能指定一次
$ echo $?
2

$ python -m local_search "资料" target --all-lines --all-lines
错误: 选项 --all-lines 只能指定一次
$ echo $?
2
```

关键词为空或全为空白、重复指定 `--ignore-case` 或 `--all-lines`、出现无法识别的多余参数（注意选项只能写在两个位置参数之后，`--ignore-case`、`--all-lines` 出现在关键词之前会被当作多余参数；第二个位置参数即使恰好写作 `--all-lines` 也仍是关键词，`--path-contains` 的下一个参数即使写作 `--all-lines` 也仍是路径片段）、目录不存在、路径不是目录或目录遍历失败时，均退出码 2、标准输出为空并在标准错误说明原因。
