"""核心检索逻辑与命令行入口。

用法：
    python -m local_search <目录> <关键词>

退出码：
    0 查询正常完成（包括无命中、部分文件被跳过）
    2 参数或目录无效（标准输出为空，原因写入标准错误）
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass
from typing import Iterator, List, Optional

SUPPORTED_SUFFIXES = (".txt", ".md")
SNIPPET_CONTEXT = 30  # 命中关键词同一行前后各保留的 Unicode 字符数


@dataclass(frozen=True)
class SearchResult:
    """单个文件的首个命中。"""

    path: str  # 相对于查询目录、使用正斜杠的路径
    line: int  # 从 1 开始的行号
    snippet: str  # 命中关键词及同一行前后至多 30 个字符，不含换行


def _is_supported_file(name: str) -> bool:
    """扩展名是否为 .txt/.md，大小写不敏感。"""
    return name.lower().endswith(SUPPORTED_SUFFIXES)


def _to_forward_slash(path: str) -> str:
    """把相对路径统一为正斜杠形式。"""
    return path.replace(os.sep, "/")


def _iter_candidate_files(root: str) -> Iterator[str]:
    """生成 root 下所有受支持普通文件的相对路径（正斜杠形式）。

    基于 os.scandir 的显式栈遍历：任何符号链接（无论指向文件还是
    目录）都不跟随；os.scandir 失败时抛出 OSError，由调用方按
    “无法完成目录遍历”处理为退出码 2。
    """
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(entry.path)
                    elif entry.is_file(follow_symlinks=False) and _is_supported_file(
                        entry.name
                    ):
                        rel = os.path.relpath(entry.path, root)
                        yield _to_forward_slash(rel)
                except OSError:
                    # 个别条目的类型无法判别：无法确认它是普通文件，
                    # 跳过该条目，不影响其余文件参与查询。
                    continue


def _make_snippet(line: str, match_index: int, keyword: str) -> str:
    """构造命中片段：关键词同一行前后各至多 30 个 Unicode 字符。"""
    start = max(0, match_index - SNIPPET_CONTEXT)
    end = min(len(line), match_index + len(keyword) + SNIPPET_CONTEXT)
    return line[start:end].replace("\r", "").replace("\n", "")


def _search_file(root: str, rel_path: str, keyword: str) -> Optional[SearchResult]:
    """在单个文件内查找首个命中。

    整文件按 UTF-8 读入后逐行匹配；无法读取或不能按 UTF-8 解码时
    抛出 OSError/UnicodeError，由调用方跳过并报告。
    """
    abs_path = os.path.join(root, rel_path)
    with open(abs_path, "r", encoding="utf-8", newline="") as handle:
        text = handle.read()

    for line_no, line in enumerate(text.splitlines(), start=1):
        index = line.find(keyword)
        if index != -1:
            return SearchResult(
                path=rel_path,
                line=line_no,
                snippet=_make_snippet(line, index, keyword),
            )
    return None


def search(root: str, keyword: str) -> List[SearchResult]:
    """执行一次查询，返回按 path 字典序（区分大小写）排列的结果。

    目录遍历失败向上抛出 OSError；单个候选文件读取或解码失败时，
    将其相对路径与原因写入标准错误并跳过该文件，其余文件照常查询。
    """
    results: List[SearchResult] = []
    for rel_path in _iter_candidate_files(root):
        try:
            hit = _search_file(root, rel_path, keyword)
        except (OSError, UnicodeError) as exc:
            print(
                "跳过文件 {path}: {reason}".format(path=rel_path, reason=exc),
                file=sys.stderr,
            )
            continue
        if hit is not None:
            results.append(hit)
    results.sort(key=lambda item: item.path)
    return results


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m local_search",
        description="在本地目录的 .txt/.md 文件中按字面文本检索关键词。",
    )
    parser.add_argument("directory", help="要扫描的目录（绝对路径或相对当前工作目录）")
    parser.add_argument(
        "keyword", help="非空关键词（字面文本、区分大小写，不解释正则，保留空格）"
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    keyword = args.keyword
    if not keyword or keyword.strip() == "":
        print("错误：关键词不能为空或全为空白。", file=sys.stderr)
        return 2

    root = os.path.abspath(args.directory)
    if not os.path.isdir(root):
        if os.path.lexists(root):
            print("错误：参数指向的不是目录：{0}".format(args.directory), file=sys.stderr)
        else:
            print("错误：目录不存在：{0}".format(args.directory), file=sys.stderr)
        return 2

    try:
        results = search(root, keyword)
    except OSError as exc:
        print("错误：无法完成目录遍历：{0}".format(exc), file=sys.stderr)
        return 2

    print(
        json.dumps(
            [
                {"path": item.path, "line": item.line, "snippet": item.snippet}
                for item in results
            ],
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
