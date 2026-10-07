"""游戏目录、应用数据目录的探测与校验。

设计原则
--------
* **游戏目录**：优先读注册表定位 Steam，再解析 ``libraryfolders.vdf`` 得到全部库，
  最后兜底扫描常见盘符路径；所有候选都会用 :func:`is_valid_game_root` 校验。
* **应用数据目录**：统一放在 ``%LOCALAPPDATA%\\StellarModManager``，与被停用的
  Mod 一起存放——绝不能放进游戏 ``Paks`` 目录树内，否则虚幻引擎会连停用的 pak
  一并挂载。
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 常量：游戏的目录约定
# ---------------------------------------------------------------------------

#: 正式版与试玩版的游戏根目录名。
GAME_FOLDER_NAMES: tuple[str, ...] = ("StellarBlade", "StellarBladeDemo")

#: 相对游戏根目录的 Paks 路径。
PAKS_RELATIVE = Path("SB") / "Content" / "Paks"

#: 相对 Paks 的 Mod 目录名（波浪号不能省，虚幻按此约定加载）。
MODS_FOLDER_NAME = "~mods"

#: Mod 库的默认文件夹名。
LIBRARY_FOLDER_NAME = "StellarBladeModLibrary"

#: 用于校验游戏根目录的关键子路径。
_GAME_ROOT_MARKERS: tuple[Path, ...] = (
    PAKS_RELATIVE,
    Path("SB") / "Binaries" / "Win64",
    Path("SB.exe"),
    Path("StellarBlade.exe"),
)

_APP_DIR_NAME = "StellarModManager"
_LIBRARY_PATH_RE = re.compile(r'"path"\s*"([^"]+)"', re.IGNORECASE)


# ---------------------------------------------------------------------------
# 应用数据目录
# ---------------------------------------------------------------------------


def app_data_dir() -> Path:
    """应用数据根目录（``%LOCALAPPDATA%\\StellarModManager``）。"""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / _APP_DIR_NAME


def asset_path(name: str) -> Path:
    """随包分发的静态资源（图标等）。

    路径相对本文件解析，源码运行与安装后运行都能找到。
    """
    return Path(__file__).resolve().parent.parent / "assets" / name


def groups_path() -> Path:
    """Mod 分组定义文件。

    单独放一个文件而不是塞进 ``config.json``：分组是用户组织数据、会频繁增删，
    和配置的读写节奏不同；分开存也便于备份和手工修复。
    """
    return app_data_dir() / "groups.json"


def config_path() -> Path:
    """配置文件路径。"""
    return app_data_dir() / "config.json"


def cache_dir() -> Path:
    """压缩包解压暂存目录。"""
    return app_data_dir() / "cache"


def disabled_store_dir() -> Path:
    """**旧版**「已停用 Mod」的存放目录。

    仓库模型（见 :mod:`core.library`）引入后，停用的 Mod 留在库里，不再搬到这里。
    保留该函数只为了读取并迁移历史数据。
    """
    return app_data_dir() / "disabled"


def library_dir() -> Path:
    """未配置游戏目录时的兜底库位置。"""
    return app_data_dir() / "library"


def translation_dir() -> Path:
    """翻译缓存与原件备份的根目录。"""
    return app_data_dir() / "translations"


def translation_backup_dir() -> Path:
    """翻译前的原件备份。

    刻意放在应用数据目录而不是 Mod 库里：库里的任何文件都会被当作 Mod 内容
    部署到游戏目录，多一个 ``.orig`` 就会被一起搬过去。
    """
    return translation_dir() / "backup"


def default_library_dir(game_root: Path | str | None) -> Path:
    """按游戏位置推荐一个库目录。

    库是全部 Mod 的权威副本，部署时优先走**硬链接**——而硬链接要求库与游戏目录
    在同一个卷上。所以默认取游戏目录的**同级目录**：同盘，且不会被虚幻引擎加载
    （它在 ``Paks`` 目录树之外）。

    放在同级而不是盘根，是为了不往用户的盘根乱写目录；也正因为如此，测试与
    便携部署都只会影响游戏所在的目录。
    """
    if game_root:
        root = Path(game_root)
        return root.parent / LIBRARY_FOLDER_NAME
    return library_dir()


def ensure_app_dirs() -> None:
    """确保应用数据目录存在。"""
    for path in (app_data_dir(), cache_dir(), disabled_store_dir()):
        path.mkdir(parents=True, exist_ok=True)


def default_downloads_dir() -> Path:
    """系统下载目录，作为「导入压缩包」的默认起始位置。"""
    downloads = Path.home() / "Downloads"
    return downloads if downloads.is_dir() else Path.home()


# ---------------------------------------------------------------------------
# 游戏目录
# ---------------------------------------------------------------------------


def mods_dir(game_root: Path | str | None) -> Path | None:
    """``<游戏根>\\SB\\Content\\Paks\\~mods``；未配置游戏目录时返回 ``None``。"""
    if not game_root:
        return None
    return Path(game_root) / PAKS_RELATIVE / MODS_FOLDER_NAME


def paks_dir(game_root: Path | str | None) -> Path | None:
    """``<游戏根>\\SB\\Content\\Paks``。"""
    if not game_root:
        return None
    return Path(game_root) / PAKS_RELATIVE


def is_valid_game_root(path: Path | str | None) -> bool:
    """判断给定路径是否像一个《剑星》游戏根目录。"""
    if not path:
        return False
    root = Path(path)
    if not root.is_dir():
        return False
    return any((root / marker).exists() for marker in _GAME_ROOT_MARKERS)


def find_game_root(candidate: Path | str) -> Path | None:
    """把用户随手选中的目录「翻译」成游戏根目录。

    支持选中的是：游戏根目录本身、其父级（``common``）、``SB`` 子目录、
    ``Paks`` 目录，或游戏主程序。找不到返回 ``None``。
    """
    path = Path(candidate)
    try:
        path = path.resolve()
    except OSError:
        return None

    if not path.is_dir():
        path = path.parent

    # 本身就是游戏根目录
    if is_valid_game_root(path):
        return path

    # 向下：选中的是安装父目录
    for name in GAME_FOLDER_NAMES:
        nested = path / name
        if is_valid_game_root(nested):
            return nested

    # 向上：逐级回溯
    for parent in path.parents:
        if is_valid_game_root(parent):
            return parent

    return None


def _steam_root_from_registry() -> Path | None:
    """从注册表读取 Steam 安装位置。"""
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:  # pragma: no cover - 非 Windows
        return None

    locations = (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
    )
    for hive, subkey in locations:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                for value_name in ("SteamPath", "InstallPath"):
                    try:
                        raw, _ = winreg.QueryValueEx(key, value_name)
                    except FileNotFoundError:
                        continue
                    if raw and Path(raw).is_dir():
                        return Path(raw)
        except OSError:
            continue
    return None


def steam_library_roots() -> list[Path]:
    """返回全部 Steam 库根目录（``...\\steamapps`` 的父目录）。"""
    roots: list[Path] = []

    def _add(path: Path | None) -> None:
        if path and path.is_dir() and path not in roots:
            roots.append(path)

    steam = _steam_root_from_registry()

    # 兜底：常见安装位置
    if steam is None:
        for drive in _available_drives():
            for relative in (
                Path("Steam"),
                Path("SteamLibrary"),
                Path("Program Files (x86)") / "Steam",
                Path("Program Files") / "Steam",
                Path("Games") / "Steam",
            ):
                candidate = drive / relative
                if (candidate / "steamapps").is_dir():
                    _add(candidate)
                    break

    _add(steam)
    if steam is None:
        return roots

    # 解析 libraryfolders.vdf 拿到其它盘符的库
    vdf = steam / "steamapps" / "libraryfolders.vdf"
    if vdf.is_file():
        try:
            text = vdf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            text = ""
        for match in _LIBRARY_PATH_RE.finditer(text):
            raw = match.group(1).replace("\\\\", "\\")
            candidate = Path(raw)
            if candidate.is_dir():
                _add(candidate)

    return roots


def _available_drives() -> list[Path]:
    """枚举当前存在的盘符根目录。"""
    if sys.platform != "win32":
        return [Path("/")]
    drives: list[Path] = []
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        root = Path(f"{letter}:\\")
        if root.exists():
            drives.append(root)
    return drives


def detect_game_roots() -> list[Path]:
    """自动探测本机所有《剑星》安装（正式版优先，其次试玩版）。"""
    found: list[Path] = []

    def _add(path: Path) -> None:
        if is_valid_game_root(path) and path not in found:
            found.append(path)

    for library in steam_library_roots():
        common = library / "steamapps" / "common"
        for name in GAME_FOLDER_NAMES:
            _add(common / name)

    # 非 Steam / 移动过的安装：扫描盘符下的常见位置
    if not found:
        for drive in _available_drives():
            for relative in (
                Path("SteamLibrary") / "steamapps" / "common",
                Path("Steam") / "steamapps" / "common",
                Path("Games") / "SteamLibrary" / "steamapps" / "common",
                Path("Program Files (x86)") / "Steam" / "steamapps" / "common",
            ):
                for name in GAME_FOLDER_NAMES:
                    _add(drive / relative / name)

    # 正式版排在试玩版前面
    order = {name: index for index, name in enumerate(GAME_FOLDER_NAMES)}
    found.sort(key=lambda p: (order.get(p.name, len(order)), str(p).lower()))
    return found
