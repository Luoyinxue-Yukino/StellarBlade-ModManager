"""矢量图标工厂。

不引入图标字体或图片资源：全部用 ``QPainter`` 在 24×24 的逻辑坐标系里现画，
再按需缩放。好处是任意尺寸都清晰、颜色跟随主题、打包时零额外文件。
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from .theme import DARK, Palette

_CANVAS = 24.0
_Drawer = Callable[[QPainter, QColor], None]


# ---------------------------------------------------------------------------
# 绘制辅助
# ---------------------------------------------------------------------------


def _setup(painter: QPainter, color: QColor, width: float = 1.8) -> QPen:
    pen = QPen(color)
    pen.setWidthF(width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    return pen


def _fill(painter: QPainter, color: QColor) -> None:
    painter.setPen(Qt.NoPen)
    painter.setBrush(color)


def _round_rect(x: float, y: float, w: float, h: float, r: float = 2.0) -> QRectF:
    return QRectF(x, y, w, h)


# ---------------------------------------------------------------------------
# 各图标绘制函数（逻辑坐标 0..24）
# ---------------------------------------------------------------------------


def _draw_library(p: QPainter, c: QColor) -> None:
    _setup(p, c, 1.7)
    for x, y in ((3.5, 3.5), (13.5, 3.5), (3.5, 13.5), (13.5, 13.5)):
        p.drawRoundedRect(_round_rect(x, y, 7, 7), 2.0, 2.0)


def _draw_import(p: QPainter, c: QColor) -> None:
    _setup(p, c, 1.8)
    p.drawLine(QPointF(12, 3.5), QPointF(12, 14.5))
    path = QPainterPath(QPointF(7, 10))
    path.lineTo(12, 15)
    path.lineTo(17, 10)
    p.drawPath(path)
    p.drawLine(QPointF(4, 19.5), QPointF(20, 19.5))


def _draw_archive(p: QPainter, c: QColor) -> None:
    _setup(p, c, 1.7)
    p.drawRoundedRect(_round_rect(3.5, 5.5, 17, 14), 2.5, 2.5)
    p.drawLine(QPointF(3.5, 10), QPointF(20.5, 10))
    p.drawLine(QPointF(10.5, 13.5), QPointF(13.5, 13.5))


def _draw_extract(p: QPainter, c: QColor) -> None:
    _setup(p, c, 1.8)
    # 盒子
    path = QPainterPath(QPointF(3.5, 9.5))
    path.lineTo(3.5, 19)
    path.lineTo(20.5, 19)
    path.lineTo(20.5, 9.5)
    p.drawPath(path)
    p.drawLine(QPointF(3.5, 9.5), QPointF(20.5, 9.5))
    # 向上的箭头（取出）
    p.drawLine(QPointF(12, 16), QPointF(12, 4))
    arrow = QPainterPath(QPointF(7.5, 8.5))
    arrow.lineTo(12, 4)
    arrow.lineTo(16.5, 8.5)
    p.drawPath(arrow)


def _draw_settings(p: QPainter, c: QColor) -> None:
    _setup(p, c, 1.8)
    rows = ((5.5, 9.0), (12.0, 15.5), (18.5, 7.0))
    for y, knob in rows:
        p.drawLine(QPointF(3.5, y), QPointF(20.5, y))
    _fill(p, c)
    for y, knob in rows:
        p.drawEllipse(QPointF(knob, y), 2.3, 2.3)


def _draw_search(p: QPainter, c: QColor) -> None:
    _setup(p, c, 1.9)
    p.drawEllipse(QPointF(10.5, 10.5), 6.2, 6.2)
    p.drawLine(QPointF(15.2, 15.2), QPointF(20, 20))


def _draw_folder(p: QPainter, c: QColor) -> None:
    _setup(p, c, 1.7)
    path = QPainterPath(QPointF(3.5, 19))
    path.lineTo(3.5, 6)
    path.lineTo(9.5, 6)
    path.lineTo(11.5, 8.5)
    path.lineTo(20.5, 8.5)
    path.lineTo(20.5, 19)
    path.closeSubpath()
    p.drawPath(path)


def _draw_refresh(p: QPainter, c: QColor) -> None:
    _setup(p, c, 1.8)
    rect = QRectF(4.5, 4.5, 15, 15)
    p.drawArc(rect, 60 * 16, 260 * 16)
    _fill(p, c)
    head = QPainterPath(QPointF(14.2, 2.4))
    head.lineTo(19.6, 5.2)
    head.lineTo(15.0, 8.6)
    head.closeSubpath()
    p.drawPath(head)


def _draw_trash(p: QPainter, c: QColor) -> None:
    _setup(p, c, 1.7)
    p.drawLine(QPointF(4, 6.5), QPointF(20, 6.5))
    p.drawLine(QPointF(9.5, 6.5), QPointF(9.5, 4))
    p.drawLine(QPointF(9.5, 4), QPointF(14.5, 4))
    p.drawLine(QPointF(14.5, 4), QPointF(14.5, 6.5))
    path = QPainterPath(QPointF(6, 6.5))
    path.lineTo(7.2, 20)
    path.lineTo(16.8, 20)
    path.lineTo(18, 6.5)
    p.drawPath(path)
    p.drawLine(QPointF(10.3, 10), QPointF(10.6, 16.6))
    p.drawLine(QPointF(13.7, 10), QPointF(13.4, 16.6))


def _draw_play(p: QPainter, c: QColor) -> None:
    _fill(p, c)
    path = QPainterPath(QPointF(8, 5))
    path.lineTo(19, 12)
    path.lineTo(8, 19)
    path.closeSubpath()
    p.drawPath(path)


def _draw_stop(p: QPainter, c: QColor) -> None:
    _fill(p, c)
    p.drawRoundedRect(_round_rect(6.5, 6.5, 11, 11), 2.2, 2.2)


def _draw_pause(p: QPainter, c: QColor) -> None:
    _fill(p, c)
    p.drawRoundedRect(_round_rect(7, 5.5, 3.8, 13), 1.4, 1.4)
    p.drawRoundedRect(_round_rect(13.2, 5.5, 3.8, 13), 1.4, 1.4)


def _draw_check(p: QPainter, c: QColor) -> None:
    _setup(p, c, 2.2)
    path = QPainterPath(QPointF(4.5, 12.5))
    path.lineTo(9.8, 18)
    path.lineTo(19.5, 6.5)
    p.drawPath(path)


def _draw_close(p: QPainter, c: QColor) -> None:
    _setup(p, c, 2.0)
    p.drawLine(QPointF(6, 6), QPointF(18, 18))
    p.drawLine(QPointF(18, 6), QPointF(6, 18))


def _draw_alert(p: QPainter, c: QColor) -> None:
    _setup(p, c, 1.7)
    path = QPainterPath(QPointF(12, 3.4))
    path.lineTo(21.6, 20.2)
    path.lineTo(2.4, 20.2)
    path.closeSubpath()
    p.drawPath(path)
    _setup(p, c, 1.9)
    p.drawLine(QPointF(12, 9.4), QPointF(12, 14.6))
    _fill(p, c)
    p.drawEllipse(QPointF(12, 17.4), 1.05, 1.05)


def _draw_copy(p: QPainter, c: QColor) -> None:
    _setup(p, c, 1.7)
    p.drawRoundedRect(_round_rect(8.5, 3.5, 12, 12), 2.5, 2.5)
    path = QPainterPath(QPointF(15.5, 18.0))
    path.lineTo(5.5, 18.0)
    path.lineTo(3.5, 16.0)
    path.lineTo(3.5, 6.5)
    path.lineTo(5.5, 6.5)
    p.drawPath(path)


def _draw_warning(p: QPainter, c: QColor) -> None:
    _draw_alert(p, c)


def _draw_plus(p: QPainter, c: QColor) -> None:
    _setup(p, c, 2.0)
    p.drawLine(QPointF(12, 5), QPointF(12, 19))
    p.drawLine(QPointF(5, 12), QPointF(19, 12))


def _draw_chevron_down(p: QPainter, c: QColor) -> None:
    _setup(p, c, 2.0)
    path = QPainterPath(QPointF(6, 9.5))
    path.lineTo(12, 15.5)
    path.lineTo(18, 9.5)
    p.drawPath(path)


def _draw_chevron_right(p: QPainter, c: QColor) -> None:
    _setup(p, c, 2.0)
    path = QPainterPath(QPointF(9.5, 6))
    path.lineTo(15.5, 12)
    path.lineTo(9.5, 18)
    p.drawPath(path)


def _draw_folder_open(p: QPainter, c: QColor) -> None:
    """打开的文件夹：分组展开时用，和折叠态形成区别。"""
    _setup(p, c, 1.7)
    # 后板
    back = QPainterPath(QPointF(3, 19))
    back.lineTo(3, 5.5)
    back.lineTo(9, 5.5)
    back.lineTo(11, 8)
    back.lineTo(18.5, 8)
    back.lineTo(18.5, 10.5)
    p.drawPath(back)
    # 前板（打开的盖子）
    front = QPainterPath(QPointF(3, 19))
    front.lineTo(6.2, 11)
    front.lineTo(21.5, 11)
    front.lineTo(18.3, 19)
    front.closeSubpath()
    p.drawPath(front)


def _draw_more(p: QPainter, c: QColor) -> None:
    """三个横点：更多操作。分组行右侧用它，避免和左侧的折叠箭头混淆。"""
    _fill(p, c)
    for x in (6.5, 12.0, 17.5):
        p.drawEllipse(QPointF(x, 12.0), 1.5, 1.5)


def _draw_tag(p: QPainter, c: QColor) -> None:
    """标签：用于分组相关的操作。"""
    _setup(p, c, 1.7)
    path = QPainterPath(QPointF(11.5, 3.5))
    path.lineTo(20.5, 3.5)
    path.lineTo(20.5, 12.5)
    path.lineTo(11.5, 21.5)
    path.lineTo(2.5, 12.5)
    path.closeSubpath()
    p.drawPath(path)
    _fill(p, c)
    p.drawEllipse(QPointF(16.6, 7.4), 1.5, 1.5)


def _draw_game(p: QPainter, c: QColor) -> None:
    _setup(p, c, 1.7)
    p.drawRoundedRect(_round_rect(2.5, 7, 19, 10.5), 4.5, 4.5)
    p.drawLine(QPointF(7.5, 10.5), QPointF(7.5, 14.5))
    p.drawLine(QPointF(5.5, 12.5), QPointF(9.5, 12.5))
    _fill(p, c)
    p.drawEllipse(QPointF(16, 11.2), 1.25, 1.25)
    p.drawEllipse(QPointF(18.2, 13.8), 1.25, 1.25)


def _draw_external(p: QPainter, c: QColor) -> None:
    _setup(p, c, 1.8)
    path = QPainterPath(QPointF(13.5, 4.5))
    path.lineTo(5.5, 4.5)
    path.lineTo(5.5, 19.5)
    path.lineTo(19.5, 19.5)
    path.lineTo(19.5, 11.5)
    p.drawPath(path)
    p.drawLine(QPointF(12.5, 11.5), QPointF(20.5, 3.5))
    head = QPainterPath(QPointF(14.5, 3.5))
    head.lineTo(20.5, 3.5)
    head.lineTo(20.5, 9.5)
    p.drawPath(head)


def _draw_info(p: QPainter, c: QColor) -> None:
    _setup(p, c, 1.7)
    p.drawEllipse(QPointF(12, 12), 8.5, 8.5)
    _fill(p, c)
    p.drawEllipse(QPointF(12, 7.8), 1.15, 1.15)
    _setup(p, c, 1.9)
    p.drawLine(QPointF(12, 11), QPointF(12, 16.5))


def _draw_shield(p: QPainter, c: QColor) -> None:
    _setup(p, c, 1.7)
    path = QPainterPath(QPointF(12, 3))
    path.lineTo(20, 6.2)
    path.lineTo(20, 12)
    path.cubicTo(20, 17, 16.5, 19.6, 12, 21)
    path.cubicTo(7.5, 19.6, 4, 17, 4, 12)
    path.lineTo(4, 6.2)
    path.closeSubpath()
    p.drawPath(path)


_DRAWERS: dict[str, _Drawer] = {
    "library": _draw_library,
    "import": _draw_import,
    "archive": _draw_archive,
    "extract": _draw_extract,
    "settings": _draw_settings,
    "search": _draw_search,
    "folder": _draw_folder,
    "refresh": _draw_refresh,
    "trash": _draw_trash,
    "play": _draw_play,
    "stop": _draw_stop,
    "pause": _draw_pause,
    "check": _draw_check,
    "close": _draw_close,
    "plus": _draw_plus,
    "chevron-down": _draw_chevron_down,
    "chevron-right": _draw_chevron_right,
    "folder-open": _draw_folder_open,
    "tag": _draw_tag,
    "more": _draw_more,
    "game": _draw_game,
    "external": _draw_external,
    "info": _draw_info,
    "shield": _draw_shield,
    "alert": _draw_alert,
    "copy": _draw_copy,
}

#: 可用图标名，供界面做校验。
ICON_NAMES: frozenset[str] = frozenset(_DRAWERS)


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------


@lru_cache(maxsize=512)
def icon(name: str, color: str | None = None, size: int = 18) -> QIcon:
    """取得图标。``name`` 见 :data:`ICON_NAMES`。

    结果按 ``(name, color, size)`` 缓存——重复调用不会重复绘制。
    """
    drawer = _DRAWERS.get(name)
    if drawer is None:
        return QIcon()

    tint = QColor(color) if color else DARK.qcolor("text_muted")

    # 2 倍超采样后标记 devicePixelRatio，保证高分屏下依然锐利。
    scale = 2
    pixmap = QPixmap(size * scale, size * scale)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.scale(size * scale / _CANVAS, size * scale / _CANVAS)
        drawer(painter, tint)
    finally:
        painter.end()

    pixmap.setDevicePixelRatio(scale)
    return QIcon(pixmap)


def themed_icon(name: str, palette: Palette, role: str = "text_muted", size: int = 18) -> QIcon:
    """按主题语义色取图标。"""
    return icon(name, palette.qcolor(role).name(), size)


@lru_cache(maxsize=1)
def app_icon() -> QIcon:
    """应用图标——取自《剑星》游戏程序自己的图标。

    打包的 ``icon.ico`` 里有多档尺寸，Windows 会按场景（任务栏、Alt+Tab、
    标题栏、文件管理器）各自挑合适的一档，所以优先用它而不是单张 PNG。
    """
    from ..core import paths

    ico = paths.asset_path("icon.ico")
    if ico.is_file():
        return QIcon(str(ico))
    png = paths.asset_path("icon.png")
    if png.is_file():
        return QIcon(str(png))
    return QIcon()


@lru_cache(maxsize=8)
def app_icon_pixmap(size: int = 34, radius: int = 9) -> QPixmap:
    """圆角化的应用图标，用在侧边栏品牌位。

    游戏图标本身是方图，直接摆上去和界面里其它圆角块不搭；这里按圆角矩形
    裁一次，顺带按 devicePixelRatio 超采样，高分屏不发虚。
    """
    source = app_icon().pixmap(size, size)
    if source.isNull():
        return source

    scale = 2
    canvas = QPixmap(size * scale, size * scale)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    path = QPainterPath()
    path.addRoundedRect(
        QRectF(0, 0, size * scale, size * scale), radius * scale, radius * scale
    )
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, size * scale, size * scale, source)
    painter.end()
    canvas.setDevicePixelRatio(scale)
    return canvas


def clear_cache() -> None:
    """主题切换后调用（颜色已编进缓存键，通常不必清）。"""
    icon.cache_clear()
