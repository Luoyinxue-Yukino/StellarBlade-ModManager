"""Mod 库测试：仓库模型下的入库、部署、启停、纳管与迁移。

仓库模型的核心承诺是「**不丢文件**」：库是权威副本，停用只是把游戏目录那份删掉。
所以这里的重点断言是——任何操作之后，库里的内容都还在且完好。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from stellar_mod_manager.core import paths
from stellar_mod_manager.core.archive import extract_archive
from stellar_mod_manager.core.library import (
    DEPLOY_COPY,
    DEPLOY_HARDLINK,
    LibraryError,
    ModLibrary,
)


@pytest.fixture
def library(fake_game: Path, isolated_app_data: Path, tmp_path: Path) -> ModLibrary:
    """库与游戏目录都在 tmp_path 下，因此同一个卷，可走硬链接。"""
    paths.ensure_app_dirs()
    lib = ModLibrary(fake_game, tmp_path / "library")
    lib.ensure_dirs()
    return lib


def _write(path: Path, name: str, payload: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    target = path / name
    target.write_bytes(payload.encode())
    return target


def _make_mod_dir(base: Path, name: str, files: dict[str, str]) -> Path:
    target = base / name
    target.mkdir(parents=True, exist_ok=True)
    for filename, payload in files.items():
        (target / filename).write_bytes(payload.encode())
    return target


# ---------------------------------------------------------------------------
# 扫描
# ---------------------------------------------------------------------------


def test_scan_empty(library: ModLibrary) -> None:
    assert library.scan() == []


def test_scan_library_mod_is_not_deployed(library: ModLibrary) -> None:
    _make_mod_dir(library.library_root, "Alpha", {"a.pak": "x", "a.utoc": "y"})

    mods = library.scan()
    assert len(mods) == 1
    mod = mods[0]
    assert mod.in_library and mod.is_managed
    assert not mod.deployed and not mod.enabled
    assert len(mod.files) == 2
    assert mod.format_label == "IoStore"


def test_scan_game_only_mod_is_unmanaged(library: ModLibrary) -> None:
    """只存在于游戏目录的 Mod 属于「未纳管」，界面上要能区分出来。"""
    _make_mod_dir(library.mods_dir, "Legacy", {"l.pak": "x"})

    mod = library.scan()[0]
    assert mod.deployed
    assert not mod.in_library
    assert not mod.is_managed
    assert mod.total_size > 0


def test_scan_pairs_library_and_game_dir(library: ModLibrary) -> None:
    source = _make_mod_dir(library.mods_dir, "Both", {"b.pak": "payload"})
    library.adopt(library.scan()[0])

    mod = library.scan()[0]
    assert mod.in_library and mod.deployed
    assert mod.deploy_mode == DEPLOY_HARDLINK
    assert mod.library_size == mod.deployed_size
    assert source.exists(), "纳管不应动到游戏目录里的文件"


def test_scan_groups_loose_game_files_by_stem(library: ModLibrary) -> None:
    mods_dir = library.mods_dir
    mods_dir.mkdir(parents=True, exist_ok=True)
    for suffix in (".pak", ".utoc", ".ucas", ".sig"):
        (mods_dir / f"Loose{suffix}").write_bytes(b"x")

    mods = library.scan()
    assert len(mods) == 1
    assert mods[0].name == "Loose"
    assert len(mods[0].deployed_files) == 4


def test_scan_ignores_non_mod_files(library: ModLibrary) -> None:
    (library.library_root / "notes.txt").write_text("hi", encoding="utf-8")
    library.mods_dir.mkdir(parents=True, exist_ok=True)
    (library.mods_dir / "readme.md").write_text("hi", encoding="utf-8")
    assert library.scan() == []


# ---------------------------------------------------------------------------
# 入库
# ---------------------------------------------------------------------------


def test_import_strips_redundant_prefix(library: ModLibrary, tmp_path: Path) -> None:
    stage = tmp_path / "stage" / "SB" / "Content" / "Paks" / "~mods" / "MyMod"
    stage.mkdir(parents=True)
    (stage / "MyMod.pak").write_bytes(b"payload")
    (stage / "MyMod.sig").write_bytes(b"sig")

    mod = library.import_to_library(tmp_path / "stage", "MyMod")

    assert mod.in_library and not mod.deployed
    assert (library.library_root / "MyMod" / "MyMod.pak").read_bytes() == b"payload"
    # 库里不能出现嵌套的 SB/Content 目录
    assert not (library.library_root / "MyMod" / "SB").exists()


def test_import_preserves_subdirectories(library: ModLibrary, tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    (stage / "Textures").mkdir(parents=True)
    (stage / "Textures" / "skin.pak").write_bytes(b"a")
    (stage / "root.pak").write_bytes(b"b")

    library.import_to_library(stage, "Nested")

    assert (library.library_root / "Nested" / "Textures" / "skin.pak").exists()
    assert (library.library_root / "Nested" / "root.pak").exists()


def test_import_rejects_payload_free_directory(library: ModLibrary, tmp_path: Path) -> None:
    stage = tmp_path / "empty"
    stage.mkdir()
    (stage / "readme.txt").write_text("no mod here", encoding="utf-8")

    with pytest.raises(LibraryError, match="没有在解压结果中找到"):
        library.import_to_library(stage, "Empty")


def test_import_rejects_missing_directory(library: ModLibrary, tmp_path: Path) -> None:
    with pytest.raises(LibraryError, match="源目录不存在"):
        library.import_to_library(tmp_path / "nope", "Ghost")


def test_import_avoids_name_collision(library: ModLibrary, tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "a.pak").write_bytes(b"first")
    library.import_to_library(stage, "Dupe")

    (stage / "a.pak").write_bytes(b"second")
    second = library.import_to_library(stage, "Dupe")

    assert second.name == "Dupe (2)"
    assert (library.library_root / "Dupe" / "a.pak").read_bytes() == b"first"
    assert (library.library_root / "Dupe (2)" / "a.pak").read_bytes() == b"second"


def test_import_remembers_source_archive(library: ModLibrary, tmp_path: Path) -> None:
    archive = tmp_path / "Origin.zip"
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "o.pak").write_bytes(b"x")

    library.import_to_library(stage, "Origin", source_archive=archive)
    assert library.scan()[0].source_archive == archive


def test_library_folder_contains_only_mod_files(library: ModLibrary, tmp_path: Path) -> None:
    """库目录里只放 Mod 文件本身——元数据走索引，不能混进去被部署到游戏。"""
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "clean.pak").write_bytes(b"x")
    library.import_to_library(stage, "Clean", source_archive=tmp_path / "Clean.zip")

    names = {p.name for p in (library.library_root / "Clean").rglob("*") if p.is_file()}
    assert names == {"clean.pak"}


def test_library_without_game_root_raises(tmp_path: Path, isolated_app_data: Path) -> None:
    empty = ModLibrary(None, tmp_path / "library")
    assert not empty.is_ready
    with pytest.raises(LibraryError, match="尚未配置"):
        _ = empty.mods_dir


# ---------------------------------------------------------------------------
# 部署（启用）
# ---------------------------------------------------------------------------


def test_deploy_uses_hardlink_on_same_volume(library: ModLibrary, tmp_path: Path) -> None:
    """同卷部署必须是硬链接：文件存在于两处，但磁盘上只占一份。"""
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "h.pak").write_bytes(b"payload")

    mod = library.import_to_library(stage, "Hard")
    deployed = library.deploy(mod)

    in_library = library.library_root / "Hard" / "h.pak"
    in_game = library.mods_dir / "Hard" / "h.pak"

    assert in_library.is_file() and in_game.is_file()
    assert deployed.deployed and deployed.deploy_mode == DEPLOY_HARDLINK
    # 两个路径指向同一份数据：链接数为 2，inode 相同
    assert in_library.stat().st_nlink == 2
    assert in_library.stat().st_ino == in_game.stat().st_ino


def test_deploy_does_not_double_count_disk_usage(library: ModLibrary, tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "size.pak").write_bytes(b"x" * 1000)

    mod = library.import_to_library(stage, "Size")
    assert mod.disk_usage == 1000

    deployed = library.deploy(mod)
    assert deployed.disk_usage == 1000, "硬链接不该被算成两份"


def test_deploy_falls_back_to_copy_when_link_unsupported(
    library: ModLibrary, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """exFAT、跨卷或权限不足时 os.link 会失败，必须退回复制而不是报错。"""
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "c.pak").write_bytes(b"payload")

    def fail_link(*args, **kwargs):  # noqa: ANN002, ANN003
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(os, "link", fail_link)

    mod = library.import_to_library(stage, "Copy")
    deployed = library.deploy(mod)

    assert deployed.deployed
    assert deployed.deploy_mode == DEPLOY_COPY
    assert (library.mods_dir / "Copy" / "c.pak").read_bytes() == b"payload"
    # 复制模式下库与游戏各占一份，共 14 字节
    assert deployed.disk_usage == 14


def test_deploy_twice_is_idempotent(library: ModLibrary, tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "i.pak").write_bytes(b"x")
    mod = library.import_to_library(stage, "Idem")

    library.deploy(mod)
    again = library.deploy(library.scan()[0])

    assert again.deployed
    files = [p for p in (library.mods_dir / "Idem").rglob("*") if p.is_file()]
    assert len(files) == 1


def test_deploy_requires_library_membership(library: ModLibrary) -> None:
    _make_mod_dir(library.mods_dir, "Unmanaged", {"u.pak": "x"})
    mod = library.scan()[0]

    with pytest.raises(LibraryError, match="不在库中"):
        library.deploy(mod)


# ---------------------------------------------------------------------------
# 停用 / 卸载
# ---------------------------------------------------------------------------


def test_undeploy_keeps_library_copy(library: ModLibrary, tmp_path: Path) -> None:
    """这是仓库模型的核心承诺：停用之后库里的文件必须原封不动。"""
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "keep.pak").write_bytes(b"precious")

    mod = library.import_to_library(stage, "Keep")
    library.deploy(mod)
    stopped = library.undeploy(library.scan()[0])

    assert not stopped.deployed
    assert not (library.mods_dir / "Keep").exists()
    assert (library.library_root / "Keep" / "keep.pak").read_bytes() == b"precious"


def test_enable_disable_roundtrip(library: ModLibrary, tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "r.pak").write_bytes(b"round")

    mod = library.import_to_library(stage, "Round")

    for _ in range(3):
        mod = library.set_enabled(mod, True)
        assert (library.mods_dir / "Round" / "r.pak").read_bytes() == b"round"
        mod = library.set_enabled(mod, False)
        assert not (library.mods_dir / "Round").exists()
        assert (library.library_root / "Round" / "r.pak").read_bytes() == b"round"


def test_set_enabled_is_noop_when_state_matches(library: ModLibrary, tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "n.pak").write_bytes(b"x")
    mod = library.import_to_library(stage, "Noop")

    assert library.set_enabled(mod, False) is mod


def test_undeploy_prunes_empty_directories(library: ModLibrary, tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "p.pak").write_bytes(b"x")
    library.deploy(library.import_to_library(stage, "Prune"))

    library.undeploy(library.scan()[0])
    assert list(library.mods_dir.iterdir()) == []


def test_remove_from_library_deletes_both(
    library: ModLibrary, tmp_path: Path, isolated_app_data: Path
) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "d.pak").write_bytes(b"x")
    library.deploy(library.import_to_library(stage, "Delete"))

    library.remove_from_library(library.scan()[0])

    assert not (library.library_root / "Delete").exists()
    assert not (library.mods_dir / "Delete").exists()
    assert library.scan() == []


# ---------------------------------------------------------------------------
# 纳管（把已在游戏目录的 Mod 收进库）
# ---------------------------------------------------------------------------


def test_adopt_does_not_cost_extra_space(library: ModLibrary) -> None:
    _make_mod_dir(library.mods_dir, "Adopt", {"a.pak": "x" * 500, "a.utoc": "y" * 500})

    before = library.scan()[0]
    assert not before.is_managed

    adopted = library.adopt(before)

    assert adopted.in_library and adopted.deployed
    assert adopted.deploy_mode == DEPLOY_HARDLINK
    assert adopted.disk_usage == adopted.library_size  # 不额外占空间


def test_adopt_leaves_game_files_working(library: ModLibrary) -> None:
    _make_mod_dir(library.mods_dir, "Live", {"l.pak": "content"})
    library.adopt(library.scan()[0])

    assert (library.mods_dir / "Live" / "l.pak").read_text(encoding="utf-8") == "content"
    assert (library.library_root / "Live" / "l.pak").read_text(encoding="utf-8") == "content"


def test_adopt_is_idempotent(library: ModLibrary) -> None:
    _make_mod_dir(library.mods_dir, "Once", {"o.pak": "x"})
    adopted = library.adopt(library.scan()[0])
    assert library.adopt(adopted) is adopted


def test_adopt_loose_files(library: ModLibrary) -> None:
    mods_dir = library.mods_dir
    mods_dir.mkdir(parents=True, exist_ok=True)
    (mods_dir / "Flat.pak").write_bytes(b"x")

    adopted = library.adopt(library.scan()[0])

    assert adopted.in_library
    assert (library.library_root / "Flat" / "Flat.pak").exists()


def test_adopt_keeps_nested_layout(library: ModLibrary) -> None:
    """文件都在一层子目录里时，库中必须保留同样的层级。

    这是实际 Mod 的常见形态（``~mods/X/装备名/X.utoc``）。若纳管时以
    「所有文件的公共父目录」为基准，就会多剥一层，库里的结构与游戏目录对不上，
    进而算不出部署方式、把占用重复计算。
    """
    folder = library.mods_dir / "Nested"
    (folder / "Skin").mkdir(parents=True)
    (folder / "Skin" / "N.pak").write_bytes(b"payload")

    adopted = library.adopt(library.scan()[0])

    assert (library.library_root / "Nested" / "Skin" / "N.pak").is_file()
    assert {f.relative for f in adopted.library_files} == {"Skin/N.pak"}
    # 结构与游戏目录一致，因此能正确判定为硬链接、且不重复计占用
    assert adopted.deploy_mode == DEPLOY_HARDLINK
    assert adopted.disk_usage == adopted.library_size


def test_nested_mod_survives_disable_enable(library: ModLibrary) -> None:
    folder = library.mods_dir / "Round"
    (folder / "Inner").mkdir(parents=True)
    (folder / "Inner" / "R.pak").write_bytes(b"payload")

    library.adopt(library.scan()[0])
    library.set_enabled(library.scan()[0], False)
    assert not (library.mods_dir / "Round").exists()

    library.set_enabled(library.scan()[0], True)
    assert (library.mods_dir / "Round" / "Inner" / "R.pak").read_bytes() == b"payload"


# ---------------------------------------------------------------------------
# 伴随文件（.dekcns.json 等）
# ---------------------------------------------------------------------------


def test_import_keeps_companion_files(library: ModLibrary, tmp_path: Path) -> None:
    """CNS 系列 Mod 的注册文件就放在 pak 旁边，漏掉它整个 Mod 会失效。"""
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "Suit_P.pak").write_bytes(b"pak")
    (stage / "Suit_P.utoc").write_bytes(b"toc")
    (stage / "Suit_P.ucas").write_bytes(b"cas")
    (stage / "Suit.dekcns.json").write_text('{"UniqueFitID": "x"}', encoding="utf-8")

    mod = library.import_to_library(stage, "CNS")

    names = {f.relative for f in mod.library_files}
    assert names == {"Suit_P.pak", "Suit_P.utoc", "Suit_P.ucas", "Suit.dekcns.json"}


def test_deploy_and_undeploy_preserve_companion_files(
    library: ModLibrary, tmp_path: Path
) -> None:
    """部署时不能只搬 pak；停用后再启用，伴随文件必须原样回来。"""
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "Suit_P.pak").write_bytes(b"pak")
    (stage / "Suit.dekcns.json").write_text("metadata", encoding="utf-8")

    mod = library.import_to_library(stage, "CNS")
    library.set_enabled(mod, True)

    in_game = library.mods_dir / "CNS" / "Suit.dekcns.json"
    assert in_game.read_text(encoding="utf-8") == "metadata"

    library.set_enabled(library.scan()[0], False)
    assert not (library.mods_dir / "CNS").exists()
    assert (library.library_root / "CNS" / "Suit.dekcns.json").is_file()

    library.set_enabled(library.scan()[0], True)
    assert in_game.read_text(encoding="utf-8") == "metadata"


def test_deploy_does_not_destroy_companion_files_on_redeploy(
    library: ModLibrary, tmp_path: Path
) -> None:
    """重复部署会先清空目标目录，清理不能把伴随文件一起毁掉。"""
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "M_P.pak").write_bytes(b"pak")
    (stage / "M.dekcns.json").write_text("keep me", encoding="utf-8")

    mod = library.import_to_library(stage, "Repeat")
    for _ in range(3):
        library.deploy(library.scan()[0])

    assert (library.mods_dir / "Repeat" / "M.dekcns.json").read_text(
        encoding="utf-8"
    ) == "keep me"


def test_adopt_brings_companion_files_into_library(library: ModLibrary) -> None:
    """未纳管的 Mod 里已经有 dekcns.json，纳管时必须一起收进库。"""
    folder = library.mods_dir / "Live"
    folder.mkdir(parents=True)
    (folder / "Live_P.pak").write_bytes(b"pak")
    (folder / "Live.dekcns.json").write_text("data", encoding="utf-8")

    adopted = library.adopt(library.scan()[0])

    assert {f.relative for f in adopted.library_files} == {
        "Live_P.pak",
        "Live.dekcns.json",
    }
    # 纳管后停用再启用，注册文件依然在
    library.set_enabled(adopted, False)
    library.set_enabled(library.scan()[0], True)
    assert (folder / "Live.dekcns.json").read_text(encoding="utf-8") == "data"


def test_decorative_files_are_excluded(library: ModLibrary, tmp_path: Path) -> None:
    """截图之类的东西不该混进库，更不该被部署到游戏目录。"""
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "M_P.pak").write_bytes(b"pak")
    (stage / "preview.jpg").write_bytes(b"\xff\xd8\xff")
    (stage / "screenshot.png").write_bytes(b"\x89PNG")
    (stage / "M.dekcns.json").write_text("{}", encoding="utf-8")

    mod = library.import_to_library(stage, "NoJunk")
    library.deploy(mod)

    in_library = {p.name for p in (library.library_root / "NoJunk").rglob("*") if p.is_file()}
    in_game = {p.name for p in (library.mods_dir / "NoJunk").rglob("*") if p.is_file()}
    assert in_library == {"M_P.pak", "M.dekcns.json"}
    assert in_game == {"M_P.pak", "M.dekcns.json"}


def test_payload_detection_still_requires_pak(library: ModLibrary, tmp_path: Path) -> None:
    """只有伴随文件、没有 pak 的目录不算 Mod。"""
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "Just.dekcns.json").write_text("{}", encoding="utf-8")

    with pytest.raises(LibraryError, match="没有在解压结果中找到"):
        library.import_to_library(stage, "NotAMod")


# ---------------------------------------------------------------------------
# 旧数据迁移
# ---------------------------------------------------------------------------


def test_migrate_legacy_disabled_store(library: ModLibrary) -> None:
    """旧模型把停用的 Mod 放在应用数据里，迁移后它们应该进库且保持停用。"""
    legacy = paths.disabled_store_dir()
    _make_mod_dir(legacy, "OldDisabled", {"o.pak": "old", "o.utoc": "toc"})
    (legacy / "OldDisabled" / "mod.json").write_text(
        json.dumps({"flat": False, "source_archive": "D:/dl/old.zip"}), encoding="utf-8"
    )

    result = library.migrate_legacy_disabled()

    assert result.moved == 1
    mod = library.scan()[0]
    assert mod.name == "OldDisabled"
    assert mod.in_library
    assert not mod.deployed, "迁移过来的应当仍是停用状态"
    assert (library.library_root / "OldDisabled" / "o.pak").read_text(encoding="utf-8") == "old"
    # 旧侧的布局侧车不能跟进库，否则会被部署到游戏里
    assert not (library.library_root / "OldDisabled" / "mod.json").exists()
    assert not legacy.exists()


def test_migrate_ignores_empty_legacy_dirs(library: ModLibrary) -> None:
    legacy = paths.disabled_store_dir()
    (legacy / "EmptyOne").mkdir(parents=True)

    result = library.migrate_legacy_disabled()
    assert result.moved == 0
    assert result.skipped == 1


def test_migrate_is_safe_when_legacy_dir_absent(library: ModLibrary) -> None:
    result = library.migrate_legacy_disabled()
    assert result.moved == 0


def test_migrated_mod_can_be_deployed(library: ModLibrary) -> None:
    legacy = paths.disabled_store_dir()
    _make_mod_dir(legacy, "Revive", {"r.pak": "revived"})
    library.migrate_legacy_disabled()

    deployed = library.deploy(library.scan()[0])

    assert deployed.deployed
    assert (library.mods_dir / "Revive" / "r.pak").read_text(encoding="utf-8") == "revived"


# ---------------------------------------------------------------------------
# 从文件夹批量导入
# ---------------------------------------------------------------------------


def test_import_folder_tree_brings_in_every_mod(library: ModLibrary, tmp_path: Path) -> None:
    """收藏了一堆已解压的 Mod 文件夹时，一次性全部收进库。"""
    source = tmp_path / "collection"
    for name in ("Alpha", "Beta", "Gamma"):
        folder = source / name
        folder.mkdir(parents=True)
        (folder / f"{name}_P.pak").write_bytes(b"payload")
        (folder / f"{name}.dekcns.json").write_text("{}", encoding="utf-8")

    result = library.import_folder_tree(source)

    assert result.scanned == 3
    assert result.imported_count == 3
    assert not result.failed
    assert {m.name for m in result.imported} == {"Alpha", "Beta", "Gamma"}
    # 伴随文件一并带进来
    assert (library.library_root / "Alpha" / "Alpha.dekcns.json").is_file()
    # 默认不部署，保持停用
    assert all(not m.deployed for m in result.imported)


def test_import_folder_tree_uses_hardlinks(library: ModLibrary, tmp_path: Path) -> None:
    """同盘导入必须走硬链接：源目录与库共用同一份数据，不额外占空间。"""
    source = tmp_path / "collection" / "Linked"
    source.mkdir(parents=True)
    original = source / "L.pak"
    original.write_bytes(b"payload")

    library.import_folder_tree(source.parent)

    copied = library.library_root / "Linked" / "L.pak"
    assert copied.read_bytes() == b"payload"
    assert copied.stat().st_nlink == 2
    assert copied.stat().st_ino == original.stat().st_ino


def test_import_folder_tree_skips_non_mod_folders(library: ModLibrary, tmp_path: Path) -> None:
    source = tmp_path / "collection"
    (source / "RealMod").mkdir(parents=True)
    (source / "RealMod" / "r.pak").write_bytes(b"x")
    (source / "JustScreenshots").mkdir(parents=True)
    (source / "JustScreenshots" / "1.jpg").write_bytes(b"\xff\xd8")
    (source / "NotYetExtracted").mkdir(parents=True)
    (source / "NotYetExtracted" / "mod.7z").write_bytes(b"7z")

    result = library.import_folder_tree(source)

    assert result.scanned == 1
    assert [m.name for m in result.imported] == ["RealMod"]


def test_import_folder_tree_reports_progress(library: ModLibrary, tmp_path: Path) -> None:
    source = tmp_path / "collection"
    for name in ("A", "B"):
        folder = source / name
        folder.mkdir(parents=True)
        (folder / f"{name}.pak").write_bytes(b"x")

    seen: list[tuple[int, int, str]] = []
    library.import_folder_tree(source, progress=lambda d, t, m: seen.append((d, t, m)))

    assert seen[0][:2] == (0, 2)
    assert seen[-1][:2] == (2, 2)


def test_import_folder_tree_rejects_missing_dir(library: ModLibrary, tmp_path: Path) -> None:
    with pytest.raises(LibraryError, match="目录不存在"):
        library.import_folder_tree(tmp_path / "nope")


def test_import_folder_tree_can_be_cancelled(library: ModLibrary, tmp_path: Path) -> None:
    from stellar_mod_manager.core.library import FolderImportCancelled

    source = tmp_path / "collection"
    for name in ("A", "B", "C"):
        folder = source / name
        folder.mkdir(parents=True)
        (folder / f"{name}.pak").write_bytes(b"x")

    with pytest.raises(FolderImportCancelled):
        library.import_folder_tree(source, cancel=lambda: True)


# ---------------------------------------------------------------------------
# 体积统计
# ---------------------------------------------------------------------------


def test_scan_totals_across_library_and_game(library: ModLibrary, tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "a.pak").write_bytes(b"x" * 100)
    library.deploy(library.import_to_library(stage, "Enabled"))

    stage2 = tmp_path / "stage2"
    stage2.mkdir()
    (stage2 / "b.pak").write_bytes(b"y" * 300)
    library.import_to_library(stage2, "Stored")

    _make_mod_dir(library.mods_dir, "Foreign", {"f.pak": "z" * 50})

    mods = {m.name: m for m in library.scan()}
    assert mods["Enabled"].deployed and mods["Stored"].in_library
    assert not mods["Stored"].deployed
    assert not mods["Foreign"].in_library

    library_size = sum(m.library_size for m in mods.values())
    disk = sum(m.disk_usage for m in mods.values())
    # 硬链接的 Enabled 不重复计，Foreign 只在游戏目录里
    assert library_size == 400
    assert disk == 400 + 50


# ---------------------------------------------------------------------------
# 端到端：解压 → 入库 → 启用 → 停用 → 删除
# ---------------------------------------------------------------------------


def test_full_lifecycle_from_archive(library: ModLibrary, mod_archive: Path, tmp_path: Path) -> None:
    """走一遍用户实际会经历的完整流程。"""
    # 1. 解压
    stage = tmp_path / "stage"
    result = extract_archive(mod_archive, stage)
    assert result.files_written == 3

    # 2. 入库（此时游戏目录里什么都没有）
    mod = library.import_to_library(stage, "MyMod", source_archive=mod_archive)
    assert mod.in_library and not mod.deployed
    assert not (library.mods_dir / "MyMod").exists()

    # 3. 启用 → 部署进游戏目录，且是硬链接
    enabled = library.set_enabled(library.scan()[0], True)
    assert enabled.deployed
    assert enabled.deploy_mode == DEPLOY_HARDLINK
    assert (library.mods_dir / "MyMod" / "MyMod_P.pak").exists()
    assert enabled.source_archive == mod_archive

    # 4. 停用 → 游戏目录清空，库完好
    disabled = library.set_enabled(library.scan()[0], False)
    assert not disabled.deployed
    assert not (library.mods_dir / "MyMod").exists()
    assert (library.library_root / "MyMod" / "MyMod_P.pak").exists()

    # 5. 再启用，内容仍然正确
    again = library.set_enabled(library.scan()[0], True)
    assert (library.mods_dir / "MyMod" / "MyMod_P.utoc").exists()

    # 6. 从库删除 → 两处都没了
    library.remove_from_library(library.scan()[0])
    assert library.scan() == []
