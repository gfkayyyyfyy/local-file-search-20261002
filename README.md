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
python -m local_search <目录> <关键词> [--path-contains <路径片段>] [--path-excludes <路径片段>] [--ignore-case] [--all-lines] [--context-chars <0-200>] [--file-type <txt|md>] [--and-keyword <附加关键词>]
```

- `<目录>`：要扫描的目录，支持绝对路径或相对于当前工作目录的相对路径，可包含中文或空格（记得加引号）。
- `<关键词>`：非空的**字面文本**，默认区分大小写；不解释正则表达式，不拆分多个词，关键词中的空格（含首尾空格）按原样保留，匹配不跨行。为空或全为空白时报错。
- `--ignore-case`：可选的无取值开关，只能写在两个位置参数之后，可与 `--path-contains`、`--all-lines` 以任意先后顺序同用。启用后**仅将 ASCII 的 A–Z 与 a–z 视为同一字符**：`TARGET` 可命中 `Target`、`target`；其他字符仍精确比较，例如 `É` 不匹配 `é`、`ß` 不匹配 `ss`。关键词仍按连续字面子串匹配，不拆词、不解释正则或通配符；`snippet` 始终取自源文本，保留原始大小写。未指定时完全保留区分大小写的原有行为。第二个位置参数即使恰好写作 `--ignore-case`，也仍是关键词。重复指定该开关时报错。
- `--all-lines`：可选的无取值开关，只能写在两个位置参数之后，可与另外两个选项以任意先后顺序同用。启用后**每个命中行各返回一项**，同一行多次出现关键词仍只返回一项，片段以该行最左侧命中为中心；不指定时保留默认行为——每个文件只返回按行号、行内位置确定的首个命中。第二个位置参数或 `--path-contains` 的取值即使恰好写作 `--all-lines`，也仍按字面文本处理，不会开启该模式。重复指定该开关时报错。
- `--context-chars <0-200>`：可选的带取值选项，只能写在两个位置参数之后，可与其他选项以任意先后顺序同用。指定命中关键词**前后各保留多少个 Unicode 码点**（中文与补充平面字符各算一个码点，关键词自身长度不计入额度），到行首或行尾停止，不借用相邻行也不添加省略号；`snippet` 仍取自源文本，保留原始大小写。取值只接受非空的 ASCII 十进制数字串，允许前导零（如 `007`），范围 0 至 200；首尾空白、正负号、小数及非 ASCII 数字均不接受。传入 `0` 时片段只保留完整命中关键词；不指定该选项时仍为前后各 30 个码点。第二个位置参数或 `--path-contains` 的取值即使恰好写作 `--context-chars`，也仍按字面文本处理。缺少取值、重复指定、取值格式不合要求或超出范围时报错，且不开始目录扫描。
- `--path-excludes <路径片段>`：可选的带取值选项，只能写在两个位置参数之后，可与其他选项（含 `--path-contains`）以任意先后顺序同用。文件**相对于选定目录、统一使用正斜杠**的路径中只要**区分大小写地连续包含该字面子串**，该文件即被排除、不参与内容检索：不读取文件内容，因此即使文件无法读取或含非法 UTF-8 字节也不告警。不解释正则或通配符，片段首尾空格按原样比较，匹配**不受 `--ignore-case` 影响**，选定目录本身的名称不参与比较。与 `--path-contains` 同用时，文件须符合包含条件且不符合排除条件才参与检索；两个片段相同时所有文件都被排除，输出 `[]`。取值即使看似开关（如 `--ignore-case`）也按字面片段处理，不启用任何开关；第二个位置参数或其他选项的取值即使恰好写作 `--path-excludes`，也仍按字面文本处理。缺少取值、重复指定、值为空或全为空白时在扫描前报错（退出码 2、标准输出为空）。
- `--file-type <txt|md>`：可选的带取值选项，只能写在两个位置参数之后，可与其他选项以任意先后顺序同用，每次最多指定一次。取值**仅接受小写字面值 `txt` 或 `md`**：`txt` 只选择扩展名为 `.txt` 的普通文件，`md` 只选择 `.md`，扩展名比较仍忽略大小写（如 `notes/b.MD` 计入 md）。空值、纯空白、首尾空白、大写值（如 `TXT`、`Md`）及其他格式（如 `markdown`、`log`、`.txt`）均为非法取值。未传该选项时继续同时检索两种格式。格式条件与路径包含、排除条件共同生效，只有满足全部条件的文件才参与内容匹配；被格式条件排除的文件不读取、不告警。被选中的不可读或非法 UTF-8 文件仍按既有规则告警后跳过，其余文件继续检索，退出码为 0。紧随该选项的参数即使看似开关（如 `--ignore-case`）也作为其值校验，不开启该开关；第二个位置参数或其他路径选项的取值即使恰好写作 `--file-type`，也仍按字面文本处理。缺值、重复指定或非法取值均在扫描前报错（退出码 2、标准输出为空）。
- `--and-keyword <附加关键词>`：可选的带取值选项，只能写在两个位置参数之后，可与其他选项以任意先后顺序同用，每次最多指定一次。指定后**只有同一行分别包含主关键词与该附加关键词才算命中**：两个关键词分处不同行不能合并，两者在行内出现顺序不限，相同关键词或重叠匹配可共用同一段文字。附加关键词与主关键词一样按**连续字面子串**匹配，首尾空格原样保留，不拆词、不解释正则或通配符；默认区分大小写，`--ignore-case` 同时作用于二者且仍只折叠 ASCII 字母。返回规则不变：默认每个文件只返回行号最小的合格行，`--all-lines` 时每个合格行各返回一项（行内重复出现不增加结果）；`snippet` 始终围绕**主关键词**在该行最左侧的命中，保留源文本大小写，`--context-chars` 沿用既有规则，不为展示附加关键词而扩大片段。紧随该选项的参数即使看似开关（如 `--all-lines`）也作为其值按字面文本处理，不开启该开关；第二个位置参数或其他选项的取值即使恰好写作 `--and-keyword`，也仍按字面文本处理。缺少取值、重复指定、值为空或全为空白时在扫描前报错（退出码 2、标准输出为空）。未提供该选项时，既有命令的结果与错误行为完全不变。
- 只扫描该目录及其子目录中的普通文件，扩展名（大小写不敏感）为 `.txt` 或 `.md`；不跟随符号链接。
- 匹配限定在单行内；默认每个文件只返回按行号、行内位置确定的**首个命中**，同一文件多个命中不重复返回；指定 `--all-lines` 后同一文件的每个命中行各返回一项（同一行的多次出现仍只算一项）。
- 标准输出是一个 JSON 数组，每项仅含：
  - `path`：相对于选定目录的路径，统一使用正斜杠 `/`
  - `line`：从 1 开始的行号
  - `snippet`：完整关键词及同一行前后各至多 30 个 Unicode 字符（可用 `--context-chars` 调整），不含换行符，不添加省略号
- 结果先按 `path` 的区分大小写字典序（Unicode 码点序）排列，同一路径再按 `line` 从小到大排列。
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

### 5. 启用 --all-lines：同一文件的每个命中行各返回一项

仓库内附带仅含 `a.txt` 的演示目录 `演示`，三行依次为 `target target`、`other`、`Target later`。

```bash
$ python -m local_search "演示" "TARGET" --ignore-case --all-lines
[{"path": "a.txt", "line": 1, "snippet": "target target"}, {"path": "a.txt", "line": 3, "snippet": "Target later"}]
$ echo $?
0
```

第 1 行与第 3 行各返回一项，按行号递增；第 1 行中关键词出现两次，但片段只以最左侧命中为中心，该行不重复返回。片段不含换行或省略号，也不借用相邻行（第 2 行 `other` 不进入任何片段）。去掉 `--all-lines` 后恢复默认行为，只返回第 1 行：

```bash
$ python -m local_search "演示" "TARGET" --ignore-case
[{"path": "a.txt", "line": 1, "snippet": "target target"}]
```

### 6. 调整片段上下文长度 --context-chars

构造一个两行文件，第一行含中文与命中词：

```bash
$ mkdir -p /tmp/demo && printf '甲乙丙Target丁戊己\nTarget again\n' > /tmp/demo/a.txt
$ python -m local_search /tmp/demo "TARGET" --ignore-case --context-chars 2
[{"path": "a.txt", "line": 1, "snippet": "乙丙Target丁戊"}]
$ python -m local_search /tmp/demo "TARGET" --ignore-case --context-chars 0 --all-lines
[{"path": "a.txt", "line": 1, "snippet": "Target"}, {"path": "a.txt", "line": 2, "snippet": "Target"}]
```

`--context-chars 2` 让命中词前后各保留 2 个码点（中文每字算一个）；`--context-chars 0` 时片段只保留完整命中关键词。不指定该选项时仍为前后各 30 个码点。

### 7. 未支持的文件不会进入结果

再放一个非 `.txt`/`.md` 的文件（即使内容包含关键词也不扫描）：

```bash
$ printf 'target in log\n' > 资料/other.log
$ python -m local_search "资料" "target"
[{"path": "a.txt", "line": 2, "snippet": "Target note"}, {"path": "notes/b.md", "line": 1, "snippet": "target again"}]
```

扩展名大小写不敏感，例如 `notes/c.MD` 同样会被纳入；符号链接（无论指向文件还是目录）一律不跟随。

### 8. 无法读取或非 UTF-8 的文件被跳过并告警

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

### 9. 路径与关键词的边界情况

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

### 10. 用 --path-excludes 排除路径片段

仓库内附带验收用的 `demo` 目录：`a.txt` 内容为 `target root`；`notes/b.md` 三行依次为 `Target note`、`other`、`target later`；`notes/private/c.txt` 仅含一个非法 UTF-8 字节。

```bash
$ python -m local_search demo TARGET --ignore-case --all-lines --path-contains notes/ --path-excludes private/ --context-chars 0
[{"path": "notes/b.md", "line": 1, "snippet": "Target"}, {"path": "notes/b.md", "line": 3, "snippet": "target"}]
$ echo $?
0
```

只有同时满足“路径包含 `notes/`”且“路径不包含 `private/`”的文件才参与检索：`notes/private/c.txt` 在读取前即被排除，因此既不参与匹配也不产生解码告警，标准错误为空。去掉 `--path-excludes private/` 后，坏文件会保留为候选并按第 8 节的方式告警跳过。排除匹配同样区分大小写、按字面子串比较，`--ignore-case` 不影响它。

### 11. 用 --file-type 只检索一种格式

准备一个同时含三种扩展名的目录 `sample`：`a.txt` 内容为 `target root`，`notes/b.MD` 内容为 `Target note`，`other.log` 内容为 `target log`。

```bash
$ python -m local_search sample TARGET --ignore-case --file-type txt --context-chars 0
[{"path": "a.txt", "line": 1, "snippet": "target"}]
$ echo $?
0

$ python -m local_search sample TARGET --ignore-case --file-type md --context-chars 0
[{"path": "notes/b.MD", "line": 1, "snippet": "Target"}]
$ echo $?
0
```

`--file-type txt` 只返回 `a.txt`，`--file-type md` 只返回 `notes/b.MD`（扩展名大小写不敏感，`.MD` 计入 md）；`other.log` 从不参与。未传该选项时两种格式仍同时检索。格式条件与路径包含、排除条件共同生效：不满足格式条件的文件在读取前即被排除，既不读取也不告警，因此目录中无法读取或含非法 UTF-8 字节的另一种格式文件不会产生任何告警；而被格式选中的坏文件仍按第 8 节告警跳过、退出码为 0。无符合格式的命中文件时输出 `[]`。

### 12. 用 --and-keyword 要求同一行同时命中两个关键词

仓库内附带验收目录 `sample`，其中仅有 `a.txt`，四行依次为 `target`、`budget`、`Target budget`、`budget target target`。

```bash
$ python -m local_search sample target --and-keyword budget --ignore-case --context-chars 0
[{"path": "a.txt", "line": 3, "snippet": "Target"}]
$ echo $?
0

$ python -m local_search sample target --and-keyword budget --ignore-case --context-chars 0 --all-lines
[{"path": "a.txt", "line": 3, "snippet": "Target"}, {"path": "a.txt", "line": 4, "snippet": "target"}]
$ echo $?
0
```

只有第 3、4 行同时包含 `target` 与 `budget`（`--ignore-case` 对两个关键词同时生效）；第 1、2 行各只含其一，不能跨行合并。默认只返回行号最小的第 3 行，追加 `--all-lines` 后第 4 行也返回。`snippet` 只围绕主关键词 `target` 在该行最左侧的命中（第 4 行有两次出现，仍只取最左侧一次），`--context-chars 0` 时片段就是完整命中词本身，不为展示附加关键词 `budget` 而扩大。

### 13. 错误情形（退出码 2，标准输出为空，原因写入标准错误）

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

$ python -m local_search "资料" target --context-chars
错误: 选项 --context-chars 缺少值
$ echo $?
2

$ python -m local_search "资料" target --context-chars 2.5
错误: 选项 --context-chars 的值必须是非空的 ASCII 十进制数字串: '2.5'
$ echo $?
2

$ python -m local_search "资料" target --context-chars 201
错误: 选项 --context-chars 的值超出范围 0-200: '201'
$ echo $?
2

$ python -m local_search "资料" target --path-excludes
错误: 选项 --path-excludes 缺少值
$ echo $?
2

$ python -m local_search "资料" target --path-excludes "   "
错误: --path-excludes 的路径片段为空或全为空白
$ echo $?
2

$ python -m local_search "资料" target --path-excludes a --path-excludes b
错误: 选项 --path-excludes 只能指定一次
$ echo $?
2

$ python -m local_search "资料" target --file-type
错误: 选项 --file-type 缺少值
$ echo $?
2

$ python -m local_search "资料" target --file-type TXT
错误: 选项 --file-type 的值必须是小写的 txt 或 md: 'TXT'
$ echo $?
2

$ python -m local_search "资料" target --file-type txt --file-type md
错误: 选项 --file-type 只能指定一次
$ echo $?
2

$ python -m local_search "资料" target --and-keyword
错误: 选项 --and-keyword 缺少值
$ echo $?
2

$ python -m local_search "资料" target --and-keyword "   "
错误: --and-keyword 的附加关键词为空或全为空白
$ echo $?
2

$ python -m local_search "资料" target --and-keyword a --and-keyword b
错误: 选项 --and-keyword 只能指定一次
$ echo $?
2
```

关键词为空或全为空白、重复指定任何选项（`--ignore-case`、`--all-lines`、`--context-chars`、`--path-contains`、`--path-excludes`、`--file-type`、`--and-keyword`）、带值选项缺少取值或取值格式/范围不合要求（此类错误在目录扫描开始前即报出）、`--path-contains`/`--path-excludes` 的路径片段为空或全为空白、`--and-keyword` 的附加关键词为空或全为空白、`--file-type` 的取值为空、纯空白、含首尾空白、大写或非 `txt`/`md` 的其他格式、出现无法识别的多余参数（注意选项只能写在两个位置参数之后，`--ignore-case`、`--all-lines` 出现在关键词之前会被当作多余参数）、目录不存在、路径不是目录或目录遍历失败时，均退出码 2、标准输出为空并在标准错误说明原因。
