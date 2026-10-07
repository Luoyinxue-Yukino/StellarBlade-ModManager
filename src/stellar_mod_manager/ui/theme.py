"""界面配色与全局样式表。

设计取向
--------
**纯色为主、靠层次说话**：不用图片、不用渐变填充，只用「背景 → 卡片 → 悬浮」
三档明度拉开层次，配一条强调色点缀。深色底略偏冷，长时间看不累。

所有颜色集中在这里，页面与组件只引用语义化的名字（``accent``、``surface``…），
换肤时不必翻遍代码。

间距节奏
--------
统一用 4 的倍数：``4 / 8 / 12 / 16 / 24 / 32``。页面外边距 24，卡片内边距 16，
控件之间 8。改版式时照这个尺子走，界面才不会越改越乱。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap

from ..core import paths

#: 间距刻度，供页面与组件统一引用。
SPACE_XS = 4
SPACE_SM = 8
SPACE_MD = 12
SPACE_LG = 16
SPACE_XL = 24
SPACE_2XL = 32

#: 圆角刻度。
RADIUS_SM = 6
RADIUS_MD = 10
RADIUS_LG = 14


@dataclass(frozen=True, slots=True)
class Palette:
    """一套完整配色。"""

    name: str

    bg: str
    """窗口底色，最暗的一层。"""
    bg_alt: str
    """侧边栏 / 状态栏等次级底色。"""
    surface: str
    """卡片、面板底色。"""
    surface_alt: str
    """卡片内嵌区域、悬浮态底色。"""
    elevated: str
    """输入框、下拉、菜单等「浮起来」的控件底色。"""

    border: str
    """常规描边。"""
    border_strong: str
    """强调描边，同时用作卡片顶部的受光边。"""

    text: str
    text_muted: str
    text_faint: str

    accent: str
    accent_hover: str
    accent_pressed: str
    accent_soft: str
    """强调色的低明度变体，用于选中背景。"""

    accent2: str
    """次要强调色（《剑星》的霓虹粉），用于点缀。"""

    success: str
    warning: str
    danger: str
    danger_soft: str

    shadow: str
    """投影颜色（带透明度）。"""

    def qcolor(self, field: str) -> QColor:
        return QColor(getattr(self, field))

    def rgba(self, field: str, alpha: int) -> str:
        """把某个颜色转成带透明度的 CSS 颜色，用于内联样式。"""
        color = self.qcolor(field)
        return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"


#: 深色主题（默认）——贴近《剑星》的黑 + 霓虹青蓝。
DARK = Palette(
    name="dark",
    bg="#0c0e13",
    bg_alt="#11141b",
    surface="#171b24",
    surface_alt="#1d222d",
    elevated="#232936",
    border="#282f3d",
    border_strong="#394254",
    text="#e9edf6",
    text_muted="#98a3ba",
    text_faint="#69738a",
    accent="#4aa8ff",
    accent_hover="#69b9ff",
    accent_pressed="#3a8fdf",
    accent_soft="#16324d",
    accent2="#ff5f8d",
    success="#3fd99a",
    warning="#ffb95e",
    danger="#ff6470",
    danger_soft="#3a1c22",
    shadow="rgba(0, 0, 0, 150)",
)

#: 浅色主题：与深色共享结构，只换颜色。
LIGHT = Palette(
    name="light",
    bg="#f2f4f9",
    bg_alt="#e9edf5",
    surface="#ffffff",
    surface_alt="#f6f8fc",
    elevated="#ffffff",
    border="#dce2ee",
    border_strong="#c2cbdd",
    text="#161b26",
    text_muted="#57617a",
    text_faint="#8791a6",
    accent="#1f7ae0",
    accent_hover="#3390f2",
    accent_pressed="#1666c4",
    accent_soft="#dbeafc",
    accent2="#e0447a",
    success="#0f9d68",
    warning="#b9760f",
    danger="#d63a47",
    danger_soft="#fbe4e6",
    shadow="rgba(15, 23, 42, 38)",
)

PALETTES: dict[str, Palette] = {DARK.name: DARK, LIGHT.name: LIGHT}


def get_palette(name: str) -> Palette:
    return PALETTES.get(name, DARK)


def banner_style(palette: Palette, role: str, *, radius: int = RADIUS_MD) -> str:
    """提示条样式：**中性底 + 左侧实心色条**。

    不用「同色淡底 + 同色描边」——淡色块铺在大片纯色界面上会切出一块块补丁，
    显得很碎；而且淡底并没有比纯描边传达更多信息。颜色放在左侧实心条和图标上
    就够了，读起来干净，也和卡片的受光边是同一套语言。
    """
    color = palette.qcolor(role)
    return (
        f"QFrame {{ background-color: {palette.surface_alt};"
        f" border: 1px solid {palette.border};"
        f" border-left: 3px solid {color.name()};"
        f" border-radius: {radius}px; }}"
    )


@lru_cache(maxsize=8)
def checkmark_icon(color: str, size: int = 18) -> str:
    """生成勾号 PNG，返回可直接写进 QSS ``image: url()`` 的路径。

    为什么必须生成一张图：QSS 一旦给 ``::indicator`` 设了 ``background-color``，
    Qt 就不再绘制原生勾号了；而 QSS 本身没有画线能力，也没有内联 SVG。
    所以只能先用 QPainter 画好存盘，再让样式表引用它。
    """
    try:
        paths.ensure_app_dirs()
        target: Path = paths.app_data_dir() / f"checkmark-{color.lstrip('#')}-{size}.png"
        if not target.is_file():
            scale = 2  # 高分屏下不发虚
            side = size * scale
            pixmap = QPixmap(side, side)
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing, True)
            pen = QPen(QColor(color))
            pen.setWidthF(side * 0.15)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            tick = QPainterPath()
            tick.moveTo(side * 0.24, side * 0.52)
            tick.lineTo(side * 0.43, side * 0.71)
            tick.lineTo(side * 0.78, side * 0.29)
            painter.drawPath(tick)
            painter.end()
            pixmap.save(str(target), "PNG")
        return target.as_posix()
    except Exception:  # noqa: BLE001 - 拿不到勾号也不该拦住界面
        return ""


def stylesheet(palette: Palette) -> str:
    """生成全局 QSS。"""
    p = palette
    # 勾号压在实心强调色上，用最深的底色画才能看清（深色主题近黑、浅色主题近白）
    check_image = checkmark_icon(p.bg)
    check_rule = f"image: url({check_image});" if check_image else ""
    return f"""
/* ============================== 基础 ============================== */
* {{
    outline: none;
}}
QWidget {{
    color: {p.text};
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif;
    font-size: 13px;
}}
/* 背景只设在顶层窗口上。
   不能写成 ``QWidget {{ background-color: bg }}``——QSS 没有 CSS 那种继承语义，
   那条规则会**打到每一个子 widget 上**，于是卡片里的普通容器会被涂上窗口底色，
   看起来就是一块块突兀的浅色方块。子控件默认透明，自然透出父级背景。 */
QMainWindow, QDialog {{
    background-color: {p.bg};
}}
/* 标签一律透明底。上面给 QWidget 设了背景色，QSS 不做 CSS 那种继承，
   这条规则会**连带作用到每个子标签**——卡片里的文字就会顶着一条窗口底色的
   横带，看起来像渲染坏了。需要底色的标签（徽标、图标块）自己内联设置。 */
QLabel {{
    background: transparent;
}}
QToolTip {{
    background-color: {p.elevated};
    color: {p.text};
    border: 1px solid {p.border_strong};
    border-radius: {RADIUS_SM}px;
    padding: 6px 9px;
}}

/* ============================== 文本 ============================== */
QLabel[role="title"] {{
    font-size: 23px;
    font-weight: 600;
    color: {p.text};
}}
QLabel[role="subtitle"] {{
    font-size: 13px;
    color: {p.text_muted};
}}
QLabel[role="section"] {{
    font-size: 11px;
    font-weight: 700;
    color: {p.text_faint};
    letter-spacing: 1.4px;
    padding: 2px 0;
}}
QLabel[role="muted"] {{
    color: {p.text_muted};
}}
QLabel[role="faint"] {{
    color: {p.text_faint};
}}
QLabel[role="metric"] {{
    font-size: 27px;
    font-weight: 600;
    color: {p.text};
}}
QLabel[role="brand"] {{
    font-size: 15px;
    font-weight: 700;
    color: {p.text};
}}
QLabel[role="badge"] {{
    font-size: 11px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 9px;
}}

/* ============================== 容器 ============================== */
/* 卡片顶边略亮一线：深色界面里这一点「受光感」能让卡片明显浮起来 */
QFrame[role="card"] {{
    background-color: {p.surface};
    border: 1px solid {p.border};
    border-top-color: {p.border_strong};
    border-radius: {RADIUS_LG}px;
}}
QFrame[role="panel"] {{
    background-color: {p.bg_alt};
    border: none;
    border-right: 1px solid {p.border};
}}
QFrame[role="toolbar"] {{
    background-color: {p.bg};
    border-bottom: 1px solid {p.border};
}}
QFrame[role="divider"] {{
    background-color: {p.border};
    max-height: 1px;
    border: none;
}}
QFrame[role="dropzone"] {{
    background-color: {p.surface_alt};
    border: 2px dashed {p.border_strong};
    border-radius: {RADIUS_LG}px;
}}
QFrame[role="dropzone"][dragActive="true"] {{
    background-color: {p.elevated};
    border-color: {p.accent};
}}
QFrame[role="stat"] {{
    background-color: {p.surface};
    border: 1px solid {p.border};
    border-top-color: {p.border_strong};
    border-radius: {RADIUS_LG}px;
}}

/* ========================== Mod 列表项 ========================== */
/* 悬停与选中必须能区分开：悬停只是「可点」，选中是「当前」。
   两者用同一个底色就等于没有悬停态。 */
QFrame[role="modcard"] {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: {RADIUS_LG}px;
}}
QFrame[role="modcard"]:hover {{
    background-color: {p.surface};
    border-color: {p.border_strong};
}}
QFrame[role="modcard"][selected="true"] {{
    background-color: {p.surface_alt};
    border-color: {p.accent};
}}
QFrame[role="rowcard"] {{
    background-color: {p.surface_alt};
    border: 1px solid {p.border};
    border-radius: {RADIUS_MD}px;
}}
QFrame[role="rowcard"]:hover {{
    border-color: {p.border_strong};
}}

/* ========================== 分组行（父节点） ========================== */
/* 比 Mod 行更「实」：实心底 + 描边，让父节点在层级列表里立得住，
   但不用强调色填充——那是选中态的语言。 */
QFrame[role="grouprow"] {{
    background-color: {p.surface};
    border: 1px solid {p.border};
    border-top-color: {p.border_strong};
    border-radius: {RADIUS_MD}px;
}}
QFrame[role="grouprow"]:hover {{
    border-color: {p.accent};
    background-color: {p.surface_alt};
}}

/* ============================== 按钮 ============================== */
QPushButton {{
    background-color: {p.elevated};
    color: {p.text};
    border: 1px solid {p.border_strong};
    border-radius: {RADIUS_SM}px;
    padding: 7px 14px;
    min-height: 20px;
}}
QPushButton:hover {{
    background-color: {p.surface_alt};
    border-color: {p.accent};
}}
QPushButton:pressed {{
    background-color: {p.surface};
}}
QPushButton:focus {{
    border-color: {p.accent};
}}
QPushButton:disabled {{
    color: {p.text_faint};
    background-color: {p.surface};
    border-color: {p.border};
}}
QPushButton[variant="primary"] {{
    background-color: {p.accent};
    color: #07131f;
    border: 1px solid {p.accent};
    font-weight: 600;
}}
QPushButton[variant="primary"]:hover {{
    background-color: {p.accent_hover};
    border-color: {p.accent_hover};
}}
QPushButton[variant="primary"]:pressed {{
    background-color: {p.accent_pressed};
}}
QPushButton[variant="primary"]:disabled {{
    background-color: {p.border};
    border-color: {p.border};
    color: {p.text_faint};
}}
QPushButton[variant="danger"] {{
    background-color: transparent;
    color: {p.danger};
    border: 1px solid {p.danger};
}}
QPushButton[variant="danger"]:hover {{
    background-color: {p.elevated};
}}
QPushButton[variant="ghost"] {{
    background-color: transparent;
    border: 1px solid transparent;
    color: {p.text_muted};
    padding: 6px 10px;
}}
QPushButton[variant="ghost"]:hover {{
    background-color: {p.surface_alt};
    color: {p.text};
}}
QPushButton[variant="ghost"]:checked {{
    background-color: {p.elevated};
    color: {p.accent};
    border-color: {p.accent};
}}
QPushButton[variant="icon"] {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: {RADIUS_SM}px;
    padding: 5px;
}}
QPushButton[variant="icon"]:hover {{
    background-color: {p.surface_alt};
    border-color: {p.border_strong};
}}

/* ========================== 侧边栏导航 ========================== */
QPushButton[nav="true"] {{
    background-color: transparent;
    border: none;
    border-left: 3px solid transparent;
    border-radius: {RADIUS_MD}px;
    padding: 10px 12px;
    text-align: left;
    color: {p.text_muted};
    font-size: 13px;
}}
QPushButton[nav="true"]:hover {{
    background-color: {p.surface};
    color: {p.text};
}}
QPushButton[nav="true"]:checked {{
    background-color: {p.elevated};
    border-left-color: {p.accent};
    color: {p.accent};
    font-weight: 600;
}}

/* ============================== 输入 ============================== */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QComboBox {{
    background-color: {p.elevated};
    border: 1px solid {p.border_strong};
    border-radius: {RADIUS_SM}px;
    padding: 6px 10px;
    selection-background-color: {p.accent};
    selection-color: #07131f;
}}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover {{
    border-color: {p.text_faint};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border-color: {p.accent};
}}
QLineEdit:disabled, QComboBox:disabled {{
    color: {p.text_faint};
    background-color: {p.surface};
}}
QLineEdit[mono="true"] {{
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 12px;
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox QAbstractItemView {{
    background-color: {p.elevated};
    border: 1px solid {p.border_strong};
    border-radius: {RADIUS_SM}px;
    /* 下拉本身已经是 elevated 底，选中项再用中性色就看不出来了，
       所以这里用实心强调色 + 深色字——实心色不算「弱色底」。 */
    selection-background-color: {p.accent};
    selection-color: {p.bg};
    padding: 4px;
}}

/* ============================== 列表 ============================== */
QListWidget, QListView, QTreeWidget, QTableWidget {{
    background-color: transparent;
    border: none;
    outline: none;
}}
QListWidget::item {{
    border-radius: {RADIUS_MD}px;
    padding: 0px;
    margin: 3px 2px;
}}
QListWidget::item:selected {{
    background-color: transparent;
}}
QListWidget::item:hover {{
    background-color: {p.surface_alt};
}}
QTableWidget {{
    gridline-color: {p.border};
    selection-background-color: {p.elevated};
    selection-color: {p.text};
    alternate-background-color: {p.surface_alt};
}}
QTableWidget::item {{
    padding: 6px 8px;
    border: none;
}}
QTableWidget::item:selected {{
    background-color: {p.elevated};
    color: {p.text};
}}
QHeaderView {{
    background-color: transparent;
}}
QHeaderView::section {{
    background-color: {p.surface_alt};
    color: {p.text_muted};
    border: none;
    border-bottom: 1px solid {p.border};
    padding: 7px 8px;
    font-weight: 600;
}}
QHeaderView::section:first {{
    border-top-left-radius: {RADIUS_SM}px;
}}
QHeaderView::section:last {{
    border-top-right-radius: {RADIUS_SM}px;
}}
QTableCornerButton::section {{
    background-color: {p.surface_alt};
    border: none;
}}

/* ============================== 树 ============================== */
QTreeWidget::item {{
    padding: 6px 4px;
    border-radius: {RADIUS_SM}px;
}}
QTreeWidget::item:hover {{
    background-color: {p.surface_alt};
}}
QTreeWidget::item:selected {{
    background-color: {p.elevated};
    color: {p.text};
}}
QTreeWidget::branch {{
    background: transparent;
}}

/* ============================= 滚动条 ============================= */
/* 滚动区域和它的 viewport 默认取系统调色板的底色（白），
   不显式透明的话深色主题下会露出一大块白。这个后代选择器是 Qt 里的固定写法：
   QScrollArea 的直接子 widget 是 viewport，viewport 的子 widget 才是内容。 */
QScrollArea,
QScrollArea > QWidget > QWidget,
QAbstractScrollArea,
QAbstractScrollArea > QWidget > QWidget {{
    background: transparent;
}}
QScrollArea {{
    border: none;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 11px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {p.border_strong};
    border-radius: 5px;
    min-height: 36px;
}}
QScrollBar::handle:vertical:hover {{
    background: {p.text_faint};
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 11px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {p.border_strong};
    border-radius: 5px;
    min-width: 36px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {p.text_faint};
}}
QScrollBar::add-line, QScrollBar::sub-line,
QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
    border: none;
    height: 0px;
    width: 0px;
}}

/* ============================= 进度条 ============================= */
QProgressBar {{
    background-color: {p.surface_alt};
    border: none;
    border-radius: 5px;
    height: 10px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background-color: {p.accent};
    border-radius: 5px;
}}
QProgressBar[state="done"]::chunk {{
    background-color: {p.success};
}}
QProgressBar[state="error"]::chunk {{
    background-color: {p.danger};
}}

/* ============================== 标签页 ============================== */
QTabWidget::pane {{
    background-color: {p.surface};
    border: 1px solid {p.border};
    border-radius: {RADIUS_MD}px;
    top: -1px;
}}
QTabBar {{
    background: transparent;
}}
QTabBar::tab {{
    background: transparent;
    color: {p.text_muted};
    padding: 8px 16px;
    margin-right: 4px;
    border: 1px solid transparent;
    border-radius: {RADIUS_MD}px;
}}
QTabBar::tab:hover {{
    color: {p.text};
    background-color: {p.surface_alt};
}}
QTabBar::tab:selected {{
    background-color: {p.elevated};
    color: {p.accent};
    border-color: {p.accent};
    font-weight: 600;
}}

/* ============================= 复选框 ============================= */
QCheckBox, QRadioButton {{
    spacing: 8px;
    color: {p.text};
    padding: 2px 0;
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 17px;
    height: 17px;
    border: 1px solid {p.border_strong};
    background-color: {p.elevated};
}}
QCheckBox::indicator {{
    border-radius: 5px;
}}
QRadioButton::indicator {{
    border-radius: 9px;
}}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {p.accent};
}}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background-color: {p.accent};
    border-color: {p.accent};
}}
/* 只给复选框加勾：单选框的实心圆点本身就是「已选」的通用表达 */
QCheckBox::indicator:checked {{
    {check_rule}
}}
QCheckBox:disabled, QRadioButton:disabled {{
    color: {p.text_faint};
}}

/* ============================== 其它 ============================== */
QMenu {{
    background-color: {p.elevated};
    border: 1px solid {p.border_strong};
    border-radius: {RADIUS_MD}px;
    padding: 5px;
}}
QMenu::item {{
    padding: 7px 22px 7px 12px;
    border-radius: {RADIUS_SM}px;
}}
QMenu::item:selected {{
    background-color: {p.elevated};
    color: {p.text};
}}
QMenu::separator {{
    height: 1px;
    background: {p.border};
    margin: 5px 8px;
}}
QStatusBar {{
    background-color: {p.bg_alt};
    border-top: 1px solid {p.border};
    color: {p.text_muted};
}}
QStatusBar::item {{
    border: none;
}}
QSplitter::handle {{
    background-color: {p.border};
}}
QToolButton {{
    background: transparent;
    border: none;
    border-radius: {RADIUS_SM}px;
    padding: 4px;
}}
QToolButton:hover {{
    background-color: {p.surface_alt};
}}
"""
