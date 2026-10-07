"""公共测试夹具：离屏 Qt、假游戏目录与隔离的应用数据目录。"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

import pytest

# 必须在导入任何 Qt 模块之前指定离屏后端
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from stellar_mod_manager.core import paths  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    """整个测试会话共用一个 QApplication。

    Qt 不允许同一进程里创建第二个，所以作用域必须是 session。
    """
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    yield app


@pytest.fixture
def isolated_app_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 ``%LOCALAPPDATA%\\StellarModManager`` 重定向到临时目录。

    否则测试会写到开发者真实的配置与「已停用 Mod」目录里。
    """
    appdata = tmp_path / "appdata"
    monkeypatch.setattr(paths, "app_data_dir", lambda: appdata)
    appdata.mkdir(parents=True, exist_ok=True)
    return appdata


@pytest.fixture
def fake_game(tmp_path: Path) -> Path:
    """造一个结构合法的最小游戏目录。"""
    game = tmp_path / "StellarBlade"
    (game / "SB" / "Content" / "Paks").mkdir(parents=True)
    (game / "SB" / "Binaries" / "Win64").mkdir(parents=True)
    return game


@pytest.fixture
def mod_archive(tmp_path: Path) -> Path:
    """一个标准布局的 Mod 压缩包：``SB/Content/Paks/~mods/MyMod/*.pak``。"""
    target = tmp_path / "MyMod.zip"
    with zipfile.ZipFile(target, "w") as zf:
        zf.writestr("SB/Content/Paks/~mods/MyMod/MyMod_P.pak", b"P" * 2048)
        zf.writestr("SB/Content/Paks/~mods/MyMod/MyMod_P.utoc", b"U" * 512)
        zf.writestr("SB/Content/Paks/~mods/MyMod/说明.txt", "hello")
    return target
