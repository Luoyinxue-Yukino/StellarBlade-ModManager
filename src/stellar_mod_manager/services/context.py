"""应用上下文：把配置、Mod 库与后台任务串起来，供各页面共享。

页面不直接持有 ``AppConfig`` / ``ModLibrary``，而是通过本对象访问——这样切换游戏
目录、刷新列表这类全局状态变化只需要在一处广播信号。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..core import paths
from ..core.audit import (
    DEFAULT_MODS_FOLDER,
    LOGIC_MODS_FOLDER,
    ToolLocation,
    find_audit_tool,
)
from ..core.config import AppConfig
from ..core.groups import GroupTree
from ..core.library import ModLibrary
from ..core.models import Mod
from ..core.translate import (
    DekcnsDocument,
    OpenAITranslator,
    TranslationCache,
    find_documents,
)
from .worker import TaskManager

from ..logging_setup import get_logger
logger = get_logger(__name__)


class AppContext(QObject):
    """全局状态与服务的集散地。"""

    game_root_changed = Signal()
    mods_changed = Signal()
    busy_changed = Signal(bool)
    conflict_check_requested = Signal()
    """任何页面都可以发这个信号请求检测冲突，由主窗口统一执行并展示结果。"""

    groups_changed = Signal()
    """分组结构或 Mod 归属发生变化。分组是纯组织信息，与 ``mods_changed`` 分开，
    这样移动一个 Mod 不必触发整库重扫描。"""

    status = Signal(str, str)
    """``(文本, 级别)``，级别取 ``info`` / ``success`` / ``warning`` / ``error``。"""

    def __init__(self, config: AppConfig, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.tasks = TaskManager(self)
        self.tasks.busy_changed.connect(self.busy_changed)
        self._library = ModLibrary(config.game_root_path, config.library_path)
        self._translation_cache: TranslationCache | None = None
        self.groups = GroupTree()

    # ------------------------------------------------------------------

    @property
    def library(self) -> ModLibrary:
        return self._library

    @property
    def library_dir(self) -> Path:
        """Mod 库位置（全部 Mod 的权威副本）。"""
        return self._library.library_root

    @property
    def game_root(self) -> Path | None:
        return self.config.game_root_path

    @property
    def mods_dir(self) -> Path | None:
        return paths.mods_dir(self.game_root)

    @property
    def is_ready(self) -> bool:
        return self.config.is_game_configured

    # ------------------------------------------------------------------

    def set_game_root(self, root: Path | str | None) -> bool:
        """设置游戏目录。返回是否被接受。"""
        if root:
            resolved = paths.find_game_root(root)
            if resolved is None:
                self.notify("这个目录不像是《剑星》的安装位置", "error")
                return False
            target = resolved
        else:
            target = None

        self.config.game_root = str(target) if target else ""
        self.config.save()
        self._library = ModLibrary(target, self.config.library_path)
        self.game_root_changed.emit()
        return True

    def set_library_dir(self, root: Path | str | None) -> None:
        """更换 Mod 库位置并重建库对象。"""
        self.config.library_dir = str(root) if root else ""
        self.config.save()
        self._library = ModLibrary(self.game_root, self.config.library_path)
        self.game_root_changed.emit()

    def migrate_legacy_store(self) -> int:
        """把旧版放在应用数据里的「已停用」Mod 迁进库，返回迁移数量。

        只在启动时调用一次；迁移失败不影响其它功能。
        """
        try:
            result = self._library.migrate_legacy_disabled()
        except Exception as exc:  # noqa: BLE001 - 迁移失败不该拦住启动
            logger.warning("迁移旧停用目录失败: %s", exc)
            return 0
        if result.did_something:
            self.notify(f"已把 {result.moved} 个旧停用 Mod 迁入库", "info")
        return result.moved

    def autodetect_game_root(self) -> Path | None:
        """自动探测并写入第一个命中的游戏目录。"""
        found = paths.detect_game_roots()
        if not found:
            return None
        self.set_game_root(found[0])
        return found[0]

    def scan_mods(self) -> list[Mod]:
        if not self.is_ready:
            return []
        try:
            return self._library.scan()
        except Exception as exc:  # noqa: BLE001 - 扫描失败不该让界面崩掉
            logger.exception("扫描 Mod 库失败")
            self.notify(f"扫描 Mod 库失败：{exc}", "error")
            return []

    def notify(self, text: str, level: str = "info") -> None:
        self.status.emit(text, level)

    def mods_updated(self) -> None:
        self.mods_changed.emit()

    # ------------------------------------------------------------------
    # 冲突检测（DekPakModAudit）
    # ------------------------------------------------------------------

    @property
    def audit_tool(self) -> ToolLocation | None:
        """定位冲突检测工具；没装或没配置时返回 ``None``。"""
        return find_audit_tool(self.game_root, self.config.audit_tool_dir or None)

    def audit_folders(self) -> list[str]:
        """本次要检测的 Mods 目录。"""
        folders = [DEFAULT_MODS_FOLDER]
        if self.config.audit_include_logicmods:
            folders.append(LOGIC_MODS_FOLDER)
        return folders

    def request_conflict_check(self) -> None:
        """请求一次冲突检测（由主窗口接管执行）。"""
        self.conflict_check_requested.emit()

    # ------------------------------------------------------------------
    # 翻译
    # ------------------------------------------------------------------

    @property
    def translator(self) -> OpenAITranslator | None:
        """按配置构造翻译引擎；没配全就返回 ``None``（界面据此禁用翻译入口）。"""
        config = self.config
        if not config.translation_ready:
            return None
        return OpenAITranslator(
            config.translate_base_url.strip(),
            config.translate_api_key.strip(),
            config.translate_model.strip(),
        )

    @property
    def translation_cache(self) -> TranslationCache:
        """全局翻译缓存（延迟创建，进程内复用）。"""
        if self._translation_cache is None:
            self._translation_cache = TranslationCache(
                paths.translation_dir() / "cache.json"
            ).load()
        return self._translation_cache

    def save_translation_cache(self) -> None:
        if self._translation_cache is not None:
            self._translation_cache.save()

    def mod_documents(self, mod: Mod) -> list[DekcnsDocument]:
        """读取某个 Mod 在库中的全部 ``.dekcns.json``。"""
        if not mod.in_library:
            return []
        folder = self._library.library_root / mod.name
        documents: list[DekcnsDocument] = []
        for path in find_documents(folder):
            try:
                documents.append(DekcnsDocument.load(path))
            except OSError as exc:
                logger.warning("读取 %s 失败: %s", path, exc)
        return documents

    def shutdown(self) -> None:
        """退出前调用：让后台任务收尾。"""
        self.save_translation_cache()
        self.config.save()
        self.tasks.wait_all()
