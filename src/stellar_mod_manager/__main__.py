"""程序入口。

支持三种启动方式，效果完全一致：

1. ``python -m stellar_mod_manager``        推荐
2. ``stellar-mod-manager``                  安装时生成的命令
3. ``python src/stellar_mod_manager/__main__.py``   直接跑文件（IDE 的 Run 按钮）

第 3 种方式下 Python 不会建立包上下文，``__package__`` 为空，相对导入会报
``attempted relative import with no known parent package``；因此这里显式把 ``src``
目录补进 ``sys.path``，再改用绝对导入。
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    # 以文件路径直接运行时走到这里：把 src 目录加入搜索路径
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from stellar_mod_manager.app import run
else:
    from .app import run


def main() -> int:
    """启动管理器。"""
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
