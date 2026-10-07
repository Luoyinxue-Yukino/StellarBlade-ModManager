"""应用配置的持久化。

用 JSON 存于 ``%LOCALAPPDATA%\\StellarModManager\\config.json``。读取时对缺失字段、
多余字段、类型不符都保持容错——配置文件损坏不应该让程序打不开。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from . import paths

from ..logging_setup import get_logger
logger = get_logger(__name__)

CONFIG_VERSION = 1


@dataclass(slots=True)
class AppConfig:
    """全部用户偏好。字段全部可缺省，保证首次启动即可用。"""

    version: int = CONFIG_VERSION

    # 游戏
    game_root: str = ""
    """《剑星》游戏根目录；空表示尚未配置。"""

    # 目录
    staging_dir: str = ""
    """解压暂存目录；空表示使用 ``%LOCALAPPDATA%\\StellarModManager\\cache``。"""
    library_dir: str = ""
    """Mod 库（全部 Mod 的权威副本）；空表示按游戏位置自动推荐。"""
    last_browse_dir: str = ""
    """上次打开文件对话框的位置，下次从这里开始。"""

    # 行为
    delete_staging_after_install: bool = True
    confirm_before_install: bool = True
    open_mods_dir_after_install: bool = False
    scan_downloads_on_start: bool = True

    # 冲突检测（DekPakModAudit）
    audit_tool_dir: str = ""
    """工具所在目录或 exe 路径；空表示只在游戏目录下自动查找。"""
    audit_download_url: str = ""
    """工具的下载页地址；空则用 :data:`~core.audit.DOWNLOAD_URL`。"""
    audit_include_logicmods: bool = False
    """是否把 UE4SS 的 ``LogicMods`` 目录一并纳入检测。"""
    audit_prompt_after_install: bool = True
    """安装完成后是否询问「立即检测冲突」。"""

    # 翻译（任何 OpenAI 兼容接口）
    translate_base_url: str = ""
    """接口根地址，形如 ``https://api.deepseek.com/v1``；留空表示未启用翻译。"""
    translate_api_key: str = ""
    """API Key。以明文存在本机配置文件里，不会外传。"""
    translate_model: str = ""
    """模型名，如 ``deepseek-chat``。"""
    translate_auto_after_import: bool = False
    """导入完成后是否自动翻译（仍需人工确认才写回）。"""

    # 外观
    theme: str = "dark"
    show_disabled_mods: bool = True

    # 窗口
    window_width: int = 1180
    window_height: int = 760

    # 忽略项
    ignored_archives: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # 派生属性
    # ------------------------------------------------------------------

    @property
    def game_root_path(self) -> Path | None:
        return Path(self.game_root) if self.game_root else None

    @property
    def mods_dir(self) -> Path | None:
        """当前生效的 ``~mods`` 目录。"""
        return paths.mods_dir(self.game_root_path)

    @property
    def staging_path(self) -> Path:
        return Path(self.staging_dir) if self.staging_dir else paths.cache_dir()

    @property
    def library_path(self) -> Path:
        """Mod 库位置：显式配置优先，否则按游戏位置推荐。"""
        if self.library_dir:
            return Path(self.library_dir)
        return paths.default_library_dir(self.game_root_path)

    @property
    def translation_ready(self) -> bool:
        """翻译功能是否可用（三项都填了才算配置好）。"""
        return bool(
            self.translate_base_url.strip()
            and self.translate_api_key.strip()
            and self.translate_model.strip()
        )

    @property
    def is_game_configured(self) -> bool:
        return bool(self.game_root) and paths.is_valid_game_root(self.game_root)

    # ------------------------------------------------------------------
    # 读写
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        """从字典构造，忽略未知键并对错误类型回退到默认值。"""
        known = {f.name: f for f in fields(cls)}
        kwargs: dict[str, Any] = {}
        defaults = cls()

        for name, fdef in known.items():
            if name not in data:
                continue
            value = data[name]
            expected = fdef.type
            try:
                kwargs[name] = _coerce(value, expected, getattr(defaults, name))
            except (TypeError, ValueError):
                logger.warning("配置项 %s 值非法(%r)，使用默认值", name, value)
        return cls(**kwargs)

    @classmethod
    def load(cls, path: Path | None = None) -> "AppConfig":
        """读取配置；文件缺失或损坏时返回默认配置。"""
        target = path or paths.config_path()
        if not target.is_file():
            return cls()
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("配置文件无法解析(%s)，改用默认配置", exc)
            return cls()
        if not isinstance(raw, dict):
            logger.warning("配置文件根节点不是对象，改用默认配置")
            return cls()
        return cls.from_dict(raw)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self, path: Path | None = None) -> Path:
        """原子写入配置，避免中途崩溃留下半个文件。"""
        target = path or paths.config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(target.suffix + ".tmp")
        payload = json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
        temp.write_text(payload, encoding="utf-8")
        temp.replace(target)
        return target


def _coerce(value: Any, expected: Any, default: Any) -> Any:
    """按 dataclass 字段类型做一次轻量转换。"""
    if isinstance(value, bool) or not isinstance(expected, str):
        return value
    try:
        if expected in ("int", "int | None") and not isinstance(value, bool):
            return int(value)
        if expected in ("float", "float | None"):
            return float(value)
        if expected.startswith("str"):
            return str(value)
        if expected.startswith("bool"):
            return bool(value)
        if expected.startswith("list"):
            return list(value) if isinstance(value, (list, tuple)) else default
    except (TypeError, ValueError):
        return default
    return value


def load_config() -> AppConfig:
    """便捷入口：加载配置并在需要时补齐目录。"""
    config = AppConfig.load()
    paths.ensure_app_dirs()
    return config
