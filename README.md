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
python -m local_search <目录> <关键词> [--path-contains <路径片段>]
```

- `<目录>`：要扫描的目录，支持绝对路径或相对于当前工作目录的相对路径，可包含中文或空格（记得加引号）。
- `<关键词>`：非空的**字面文本**，区分大小写；不解释正则表达式，不拆分多个词，关键词中的空格按原样保留，以连字符开头也仍按字面文本处理。为空或全为空白时报错。
- `--path-contains <路径片段>`：可选，且只能在两个位置参数之后出现一次。先按路径筛选、再检索内容：只有相对于所选目录、以 `/` 分隔的相对路径（不含根目录自身的名字）**区分大小写地连续包含**该片段的文件才会被打开检查；片段可匹配子目录名或文件名，中文、空格与标点原样参与比较，不解释通配符或正则，也不按目录是否存在判断片段有效性。片段非空时其前后空格不做裁剪；片段为空或全为空白时报参数错误。被路径条件排除的文件不会被读取，因此即使无法读取或含非法 UTF-8 字节也不会产生告警。
- 只扫描该目录及其子目录中的普通文件，扩展名（大小写不敏感）为 `.txt` 或 `.md`；不跟随符号链接。
- 匹配限定在单行内，每个文件只返回按行号、行内位置确定的**首个命中**；同一文件多个命中不重复返回。
- 标准输出是一个 JSON 数组，每项仅含：
  - `path`：相对于选定目录的路径，统一使用正斜杠 `/`
  - `line`：从 1 开始的行号
  - `snippet`：完整关键词及同一行前后各至多 30 个 Unicode 字符，不含换行符，不添加省略号
- 结果按 `path` 的区分大小写字典序（Unicode 码点序）排列。
- 没有命中、目录为空或只含不支持的文件时输出 `[]`，退出码为 0。

## 本地使用示例

### 1. 准备示例文件

```bash
mkdir -p 资料目录/notes
printf 'alpha\ntarget note\n' > 资料目录/a.txt
printf 'target again\n'       > 资料目录/notes/b.md
```

### 2. 查询小写关键词 target

```bash
$ python -m local_search "资料目录" "target"
[{"path": "a.txt", "line": 2, "snippet": "target note"}, {"path": "notes/b.md", "line": 1, "snippet": "target again"}]
$ echo $?
0
```

`a.txt` 命中在第 2 行，`notes/b.md` 命中在第 1 行；结果按路径排列。

### 3. 查询大写 TARGET（区分大小写，得到空数组）

```bash
$ python -m local_search "资料目录" "TARGET"
[]
$ echo $?
0
```

### 4. 未支持的文件不会进入结果

再放一个非 `.txt`/`.md` 的文件（即使内容包含关键词也不扫描）：

```bash
$ printf 'target in log\n' > 资料目录/other.log
$ python -m local_search "资料目录" "target"
[{"path": "a.txt", "line": 2, "snippet": "target note"}, {"path": "notes/b.md", "line": 1, "snippet": "target again"}]
```

扩展名大小写不敏感，例如 `notes/c.MD` 同样会被纳入；符号链接（无论指向文件还是目录）一律不跟随。

### 5. 用 --path-contains 先按相对路径筛选

只有相对路径（使用 `/`，不含所选根目录自身名字）区分大小写地连续包含该片段的文件才参与内容检索：

```bash
$ printf 'target root\n' > 资料目录/a.txt
$ mkdir -p 资料目录/notes 资料目录/Notes
$ printf 'alpha\ntarget note\n' > 资料目录/notes/b.md
$ printf 'target upper\n'        > 资料目录/Notes/c.md
$ python -m local_search "资料目录" target --path-contains notes/
[{"path": "notes/b.md", "line": 2, "snippet": "target note"}]
$ echo $?
0
```

片段 `notes/` 只匹配子目录 `notes/`，不匹配 `Notes/`（区分大小写），根目录下的 `a.txt` 也不含该片段；因此只检索并返回 `notes/b.md`。去掉该选项则三个文件都会参与检索。片段同样可以匹配文件名（如 `--path-contains b.md`）；中文、空格与标点原样比较，不解释通配符或正则。

### 6. 无法读取或非 UTF-8 的文件被跳过并告警

构造一个包含非法 UTF-8 字节的文件：

```bash
$ printf 'bad \xff\xff bytes\n' > 资料目录/broken.txt
$ python -m local_search "资料目录" "target"
警告: 跳过文件 broken.txt: 无法按 UTF-8 解码 ('utf-8' codec can't decode byte 0xff in position 4: invalid start byte)
[{"path": "a.txt", "line": 2, "snippet": "target note"}, {"path": "notes/b.md", "line": 1, "snippet": "target again"}]
$ echo $?
0
```

告警写到**标准错误**，JSON 结果仍写到**标准输出**；该文件被跳过，其余文件正常参与查询，退出码仍为 0。无法读取（如权限问题）的候选文件同样按此方式跳过。

### 7. 路径与关键词的边界情况

```bash
# 包含空格的目录与关键词（关键词中的空格原样保留，不拆词）
python -m local_search "我的 资料" "target note"

# 正则元字符按字面文本处理，例如下面只匹配连续的 a.b*c? 六个字符
python -m local_search "资料目录" "a.b*c?"
```

### 8. 错误情形（退出码 2，标准输出为空，原因写入标准错误）

```bash
$ python -m local_search "不存在的目录" target
错误: 目录不存在: 不存在的目录
$ echo $?
2

$ python -m local_search "资料目录/a.txt" target
错误: 路径不是目录: 资料目录/a.txt
$ echo $?
2

$ python -m local_search "资料目录" "   "
错误: 关键词为空或全为空白
$ echo $?
2

# --path-contains 的片段为空或全为空白、缺少值、选项重复或存在多余参数
$ python -m local_search "资料目录" target --path-contains "   "
错误: 路径片段为空或全为空白
$ echo $?
2
```

`--path-contains` 只能在两个位置参数之后出现一次；选项缺少值、重复出现或存在任何额外参数时，同样退出码 2、标准输出为空并在标准错误说明参数错误。

目录遍历无法完成（例如某个子目录无读取权限）时同样退出码为 2，并在标准错误说明原因。
