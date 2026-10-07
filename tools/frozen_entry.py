"""PyInstaller 打包用的入口脚本。

为什么不直接拿 ``src/stellar_mod_manager/__main__.py`` 当入口：
那个文件要同时兼容「以文件路径直接运行」的情况，里面有一段
``sys.path.insert(...)`` 的兜底分支。冻结之后那段的路径计算没有意义，
用一个显式的入口更干净——它只需要在正常的包上下文里调用 ``main()``。
"""

from __future__ import annotations

import multiprocessing
import sys

from stellar_mod_manager.__main__ import main

if __name__ == "__main__":
    # 冻结后如果代码里用到 multiprocessing，没有这句会在子进程里重新执行整个 GUI
    multiprocessing.freeze_support()
    sys.exit(main())
