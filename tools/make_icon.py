"""生成应用图标资源（``src/stellar_mod_manager/assets/``）。

产出两个文件：

* ``icon.png`` —— 256×256，运行时用 ``QIcon`` 加载；
* ``icon.ico`` —— 多尺寸（16/24/32/48/64/128/256），给 Windows 任务栏、标题栏、
  文件管理器各取所需。

用法::

    python tools/make_icon.py --from 我的图标.png   # 用一张现成的方形图
    python tools/make_icon.py                       # 已有 icon.png 时重新打包 ico

源图**不必是正方形**，也不必正好 256×256：会等比缩放后居中贴到透明方底上。
这一步不能省——直接等比缩放会产出 255×256 这种非方形条目，而 ico 目录里声明的
是方形尺寸，两者对不上时 Windows 渲染会出问题。
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src" / "stellar_mod_manager" / "assets"

#: ico 里包含的尺寸；Windows 会按显示场景挑合适的一档
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def build_ico(source: Path, destination: Path, sizes: tuple[int, ...] = ICO_SIZES) -> None:
    """把一张图片打包成多尺寸 .ico。

    Vista 以后的 ico 允许每一项直接放 PNG 数据，不必再转成 BMP + 掩码，
    所以这里就是「缩放 + 居中贴到方底 + 拼一张目录表」。

    源图会先等比缩放到能放进 ``size × size``，再居中贴到透明方底上——
    这样即使源图不是正方形（比如 255×256），每一项也仍是严格的方形，
    与 ico 目录里声明的尺寸一致。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])

    original = QImage(str(source))
    if original.isNull():
        raise SystemExit(f"读不出图片：{source}")

    blobs: list[bytes] = []
    for size in sizes:
        scaled = original.scaled(
            size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation
        ).convertToFormat(QImage.Format_ARGB32)

        canvas = QImage(size, size, QImage.Format_ARGB32)
        canvas.fill(Qt.transparent)
        painter = QPainter(canvas)
        painter.drawImage((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
        painter.end()

        temp = destination.with_suffix(f".{size}.tmp.png")
        if not canvas.save(str(temp), "PNG"):
            raise SystemExit(f"缩放 {size}px 失败")
        blobs.append(temp.read_bytes())
        temp.unlink()

    count = len(blobs)
    header = struct.pack("<HHH", 0, 1, count)
    offset = len(header) + count * 16
    directory = bytearray()
    for size, blob in zip(sizes, blobs, strict=True):
        # 256 在 ico 里用 0 表示
        dimension = 0 if size >= 256 else size
        directory += struct.pack(
            "<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(blob), offset
        )
        offset += len(blob)

    destination.write_bytes(header + bytes(directory) + b"".join(blobs))
    print(f"  已生成 {destination.name}（{count} 档：{', '.join(map(str, sizes))}）")


def normalize(source: Path, destination: Path, size: int = 256) -> None:
    """把任意尺寸的源图规整成 ``size × size`` 的透明底 PNG。

    等比缩放后居中贴到方底上，保证 ``icon.png`` 本身也是严格的方形——
    运行时的 ``QIcon`` 与侧边栏的圆角裁切都按方形处理。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])

    original = QImage(str(source))
    if original.isNull():
        raise SystemExit(f"读不出图片：{source}")

    scaled = original.scaled(
        size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation
    ).convertToFormat(QImage.Format_ARGB32)
    canvas = QImage(size, size, QImage.Format_ARGB32)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    painter.drawImage((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
    painter.end()

    destination.parent.mkdir(parents=True, exist_ok=True)
    if not canvas.save(str(destination), "PNG"):
        raise SystemExit(f"写入失败：{destination}")
    note = "" if scaled.size() == canvas.size() else f"（源图 {original.width()}×{original.height()}，已居中补边）"
    print(f"  已生成 {destination.name}（{size}×{size}）{note}")


def main() -> int:
    parser = argparse.ArgumentParser(description="生成应用图标资源")
    parser.add_argument("--from", dest="source", type=Path, help="源图；省略则用现有的 icon.png")
    options = parser.parse_args()

    ASSETS.mkdir(parents=True, exist_ok=True)
    png = ASSETS / "icon.png"

    if options.source:
        if not options.source.is_file():
            raise SystemExit(f"找不到源图：{options.source}")
        normalize(options.source, png)
    elif not png.is_file():
        print("没有现成的 icon.png，请用 --from 指定一张源图")
        return 1
    else:
        print(f"  使用现有的 {png.name}")

    build_ico(png, ASSETS / "icon.ico")
    return 0


if __name__ == "__main__":
    sys.exit(main())
