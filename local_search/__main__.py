"""支持 ``python -m local_search <目录> <关键词>`` 调用。"""

import sys

from .core import main

if __name__ == "__main__":
    sys.exit(main())
