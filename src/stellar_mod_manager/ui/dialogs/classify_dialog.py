"""「按内容自动分类」的预览对话框。

沿用项目里「先预览、再写回」的习惯：批量改动几十上百个 Mod 之前，先把识别结果
和影响范围摆出来，用户点确认才落盘。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...core.classify import sorting_key
from ..theme import SPACE_LG, SPACE_MD, SPACE_SM, Palette
from ..widgets.common import Badge, Divider, button

#: 每类给一句「凭什么这么判」的说明，让用户能判断靠不靠谱
_REASON = {
    "服装": "NanoSuit / CH_P_EVE / Skin 等外观资源，或 CNS 元数据里标了部位",
    "武器": "资源名含 _WP_ / Weapon / Sword",
    "玩法": "带脚本（.lua / .dll）或 LogicMods，属于功能性 Mod",
    "其他": "没有足够的特征可以判断，先放这里",
}


class ClassifyDialog(QDialog):
    """展示自动分类结果，确认后应用。"""

    def __init__(
        self,
        colors: Palette,
        *,
        suggestions: dict[str, list[str]],
        missing_groups: set[str],
        total: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.colors = colors
        self._suggestions = suggestions

        self.setWindowTitle("按内容自动分类")
        self.setMinimumSize(560, 420)

        root = QVBoxLayout(self)
        root.setContentsMargins(SPACE_LG, SPACE_LG, SPACE_LG, SPACE_MD)
        root.setSpacing(SPACE_MD)

        headline = QLabel(f"识别了 {total} 个尚未分组的 Mod", self)
        headline.setStyleSheet("font-size: 15px; font-weight: 600;")
        root.addWidget(headline)

        note = QLabel(
            "判断依据是每个 Mod 内部的资源清单（<code>.utoc</code> 里的资源路径）"
            "与 CNS 元数据。<b>已经归类的 Mod 不会被动</b>，分类只是猜测，"
            "随时可以自己改。",
            self,
        )
        note.setWordWrap(True)
        note.setProperty("role", "muted")
        note.setStyleSheet("font-size: 12px;")
        root.addWidget(note)
        root.addWidget(Divider(self))

        root.addWidget(self._build_list(), 1)

        if missing_groups:
            self.create_missing = QCheckBox(
                f"自动创建缺少的分类：{'、'.join(sorted(missing_groups))}", self
            )
            self.create_missing.setChecked(True)
            root.addWidget(self.create_missing)
        else:
            self.create_missing = None

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = button("取消", palette=colors, parent=self)
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        apply_btn = button("应用", variant="primary", parent=self)
        apply_btn.clicked.connect(self.accept)
        row.addWidget(apply_btn)
        root.addLayout(row)

    # ------------------------------------------------------------------

    def _build_list(self) -> QWidget:
        area = QScrollArea(self)
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 10, 0)
        layout.setSpacing(SPACE_SM)

        ordered = sorted(self._suggestions.items(), key=lambda kv: sorting_key(kv[0]))
        for category, mods in ordered:
            if not mods:
                continue
            card = QWidget(host)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(0, 0, 0, 0)
            card_layout.setSpacing(3)

            head = QHBoxLayout()
            head.setSpacing(SPACE_SM)
            name = QLabel(category, card)
            name.setStyleSheet("font-size: 13px; font-weight: 600;")
            head.addWidget(name)
            head.addWidget(Badge(f"{len(mods)} 个", self.colors, color_role="accent"))
            head.addStretch(1)
            card_layout.addLayout(head)

            reason = QLabel(_REASON.get(category, ""), card)
            reason.setProperty("role", "faint")
            reason.setStyleSheet("font-size: 11px;")
            card_layout.addWidget(reason)

            preview = QLabel("、".join(m[:34] for m in mods[:4]), card)
            preview.setProperty("role", "muted")
            preview.setStyleSheet("font-size: 11px;")
            preview.setWordWrap(True)
            if len(mods) > 4:
                preview.setText(preview.text() + f" … 等 {len(mods)} 个")
            card_layout.addWidget(preview)

            layout.addWidget(card)

        layout.addStretch(1)
        area.setWidget(host)
        return area

    # ------------------------------------------------------------------

    @property
    def should_create_missing(self) -> bool:
        return self.create_missing is None or self.create_missing.isChecked()


__all__ = ["ClassifyDialog"]
