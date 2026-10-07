"""游戏目录探测与配置持久化的测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stellar_mod_manager.core import paths
from stellar_mod_manager.core.config import AppConfig


# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------


def test_is_valid_game_root_accepts_real_layout(fake_game: Path) -> None:
    assert paths.is_valid_game_root(fake_game)


def test_is_valid_game_root_rejects_others(tmp_path: Path) -> None:
    assert not paths.is_valid_game_root(tmp_path)
    assert not paths.is_valid_game_root(tmp_path / "does_not_exist")
    assert not paths.is_valid_game_root("")
    assert not paths.is_valid_game_root(None)


def test_mods_dir_layout(fake_game: Path) -> None:
    expected = fake_game / "SB" / "Content" / "Paks" / "~mods"
    assert paths.mods_dir(fake_game) == expected
    assert paths.paks_dir(fake_game) == expected.parent
    assert paths.mods_dir(None) is None


@pytest.mark.parametrize(
    "build",
    [
        lambda g: g,
        lambda g: g / "SB",
        lambda g: g / "SB" / "Content" / "Paks",
        lambda g: g / "SB" / "Binaries" / "Win64",
    ],
)
def test_find_game_root_walks_up_and_down(fake_game: Path, build) -> None:
    """用户可能选中游戏根、SB 子目录或 Paks 目录，都应能还原成游戏根。"""
    assert paths.find_game_root(build(fake_game)) == fake_game


def test_find_game_root_accepts_parent_common_dir(fake_game: Path) -> None:
    """用户选中 steamapps\\common 时，应能向下找到 StellarBlade。"""
    common = fake_game.parent
    assert paths.find_game_root(common) == fake_game


def test_find_game_root_returns_none_for_unrelated(tmp_path: Path) -> None:
    other = tmp_path / "SomeRandomFolder"
    other.mkdir()
    assert paths.find_game_root(other) is None


def test_find_game_root_accepts_executable_path(fake_game: Path) -> None:
    exe = fake_game / "SB" / "Binaries" / "Win64" / "SB-Win64-Shipping.exe"
    exe.write_bytes(b"MZ")
    assert paths.find_game_root(exe) == fake_game


def test_disabled_store_is_outside_game_dir(fake_game: Path, isolated_app_data: Path) -> None:
    """停用的 Mod 必须放在游戏目录之外，否则引擎仍会挂载它们。"""
    store = paths.disabled_store_dir()
    assert store == isolated_app_data / "disabled"
    assert not store.is_relative_to(fake_game / "SB" / "Content" / "Paks")


def test_detect_game_roots_returns_list() -> None:
    found = paths.detect_game_roots()
    assert isinstance(found, list)
    assert all(isinstance(p, Path) for p in found)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


def test_config_defaults_are_usable() -> None:
    config = AppConfig()
    assert config.theme == "dark"
    assert config.game_root == ""
    assert not config.is_game_configured
    assert config.staging_path == paths.cache_dir()


def test_config_roundtrip(tmp_path: Path, fake_game: Path) -> None:
    target = tmp_path / "config.json"
    config = AppConfig(game_root=str(fake_game), theme="light", window_width=1000)
    config.save(target)

    loaded = AppConfig.load(target)
    assert loaded.game_root == str(fake_game)
    assert loaded.theme == "light"
    assert loaded.window_width == 1000
    assert loaded.is_game_configured


def test_config_missing_file_returns_defaults(tmp_path: Path) -> None:
    assert AppConfig.load(tmp_path / "nope.json").theme == "dark"


def test_config_corrupt_json_returns_defaults(tmp_path: Path) -> None:
    target = tmp_path / "broken.json"
    target.write_text("{ this is not json", encoding="utf-8")
    assert AppConfig.load(target).theme == "dark"


def test_config_non_object_root_returns_defaults(tmp_path: Path) -> None:
    target = tmp_path / "list.json"
    target.write_text("[1, 2, 3]", encoding="utf-8")
    assert AppConfig.load(target).theme == "dark"


def test_config_ignores_unknown_and_bad_types(tmp_path: Path) -> None:
    target = tmp_path / "mixed.json"
    target.write_text(
        json.dumps(
            {
                "theme": "light",
                "window_width": "1280",  # 字符串应被转成 int
                "unknown_future_key": True,  # 未知键应被忽略
                "ignored_archives": "not-a-list",  # 类型不符应回退默认
            }
        ),
        encoding="utf-8",
    )

    config = AppConfig.load(target)
    assert config.theme == "light"
    assert config.window_width == 1280
    assert config.ignored_archives == []
    assert not hasattr(config, "unknown_future_key")


def test_config_save_is_atomic(tmp_path: Path) -> None:
    """保存后不应留下 .tmp 残留文件。"""
    target = tmp_path / "config.json"
    AppConfig().save(target)
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []
    assert target.is_file()


def test_config_invalid_game_root_is_flagged(tmp_path: Path) -> None:
    config = AppConfig(game_root=str(tmp_path / "not-a-game"))
    assert config.game_root_path is not None
    assert not config.is_game_configured
