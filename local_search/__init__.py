"""离线本地文本检索（最小功能）。

仅使用 Python 3 标准库：扫描指定目录下的 .txt/.md 普通文件，
按字面文本、区分大小写在单行内匹配关键词，输出 JSON 结果。
不建立索引、不修改源文件、不跟随符号链接。
"""

from .core import SearchResult, main, search

__all__ = ["SearchResult", "main", "search"]
