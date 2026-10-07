"""按内容分类的单元测试。

不用真实 Mod 文件：``read_asset_names`` 读的是 ``.utoc`` 里的可读字符串，
这里直接把资源名列表喂给 :func:`classify`，把「读文件」和「判断规则」分开测。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stellar_mod_manager.core import classify
from stellar_mod_manager.core.models import Mod, ModFile


def make_mod(tmp_path: Path, name: str, *, suffixes=(".pak", ".utoc", ".ucas")) -> Mod:
    """造一个带真实（空）文件的 Mod，方便测需要读文件的分支。"""
    folder = tmp_path / name
    folder.mkdir(parents=True, exist_ok=True)
    files = []
    for suffix in suffixes:
        path = folder / f"{name}{suffix}"
        path.write_bytes(b"\x00" * 16)
        files.append(ModFile(path=path, relative=path.name, size=16))
    return Mod(name=name, in_library=True, library_files=files)


def with_cns(tmp_path: Path, name: str, payload: dict, **kwargs) -> Mod:
    folder = tmp_path / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.dekcns.json").write_text(
        json.dumps([payload], ensure_ascii=False), encoding="utf-8"
    )
    mod = make_mod(tmp_path, name, **kwargs)
    doc = folder / f"{name}.dekcns.json"
    mod.library_files.append(ModFile(path=doc, relative=doc.name, size=1))
    return mod


# ---------------------------------------------------------------------------
# 玩法 / 武器
# ---------------------------------------------------------------------------


def test_dll_means_gameplay(tmp_path: Path) -> None:
    mod = make_mod(tmp_path, "ScriptMod", suffixes=(".pak", ".utoc", ".dll"))
    assert classify.classify(mod, []) == classify.GAMEPLAY


def test_logicmods_asset_means_gameplay(tmp_path: Path) -> None:
    mod = make_mod(tmp_path, "LogicMod")
    names = ["../../../SB/Content/Paks/LogicMods/DekCNS_P.pak"]
    assert classify.classify(mod, names) == classify.GAMEPLAY


def test_weapon_assets(tmp_path: Path) -> None:
    mod = make_mod(tmp_path, "InfinityBlade")
    names = ["CH_M_NA_21_WP_A1.uasset", "CH_M_NA_21_WP_A1.ubulk"]
    assert classify.classify(mod, names) == classify.WEAPON


def test_outfit_assets_win_over_stray_weapon_word(tmp_path: Path) -> None:
    # 一件带「枪套」装饰的衣服：外观特征远多于武器特征，仍应判为服装
    mod = make_mod(tmp_path, "HolsterDress")
    names = ["CH_P_EVE_58_UV1_N.uasset", "NanoSuit_A.uasset", "gun_holster.uasset"]
    assert classify.classify(mod, names) == classify.CLOTHING


# ---------------------------------------------------------------------------
# 服装
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "asset",
    [
        "CH_P_EVE_OneMillion_01.uasset",
        "NanoSuit_Icon_BS_20.uasset",
        "MI_Skin_Inst.uasset",
        "EVE_EyeIrisBaseColor.uasset",
        "Tattoo_EVE_63_Suit_Inst.uasset",
    ],
)
def test_outfit_asset_patterns(tmp_path: Path, asset: str) -> None:
    mod = make_mod(tmp_path, "Outfit")
    assert classify.classify(mod, [asset]) == classify.CLOTHING


def test_asset_name_starting_with_ch_p_eve_is_detected(tmp_path: Path) -> None:
    """回归：正则曾写成 ``_ch_p_eve``，匹配不到以它开头的资源名。"""
    mod = make_mod(tmp_path, "Regression")
    assert classify.classify(mod, ["CH_P_EVE_63.uasset"]) == classify.CLOTHING


def test_cns_fit_mesh_type_wins(tmp_path: Path) -> None:
    mod = with_cns(tmp_path, "BodyMod", {"FitMeshType": "Body", "DisplayName": "X"})
    # 即便资源名全无特征，作者填的 FitMeshType 也够定性
    assert classify.classify(mod, ["aab.uasset"]) == classify.CLOTHING


def test_cns_outfit_types_wins_when_no_fit_mesh(tmp_path: Path) -> None:
    mod = with_cns(tmp_path, "DressMod", {"OutfitTypes": ["Dress"]})
    assert classify.classify(mod, ["nothing_here.uasset"]) == classify.CLOTHING


def test_lua_alone_does_not_beat_outfit_assets(tmp_path: Path) -> None:
    """回归：CNS 换装 Mod 普遍附带一个辅助 lua，不能因此被判成玩法 Mod。"""
    mod = make_mod(tmp_path, "CowBikini", suffixes=(".pak", ".utoc", ".lua"))
    names = ["CH_P_EVE_Cow_Bikini_A.uasset"]
    assert classify.classify(mod, names) == classify.CLOTHING


def test_lua_without_content_signal_is_gameplay(tmp_path: Path) -> None:
    mod = make_mod(tmp_path, "Helper", suffixes=(".pak", ".utoc", ".lua"))
    assert classify.classify(mod, ["unknown_blob.uasset"]) == classify.GAMEPLAY


# ---------------------------------------------------------------------------
# 兜底
# ---------------------------------------------------------------------------


def test_name_hints_used_when_assets_are_silent(tmp_path: Path) -> None:
    mod = make_mod(tmp_path, "Cool Weapon Pack")
    assert classify.classify(mod, []) == classify.WEAPON


def test_opaque_mod_falls_back_to_other(tmp_path: Path) -> None:
    mod = make_mod(tmp_path, "WRYH 3410")
    assert classify.classify(mod, ["AGENCYB_1_1016.uasset"]) == classify.OTHER


def test_classify_many_survives_bad_file(tmp_path: Path) -> None:
    """单个 Mod 读不下去也不能让整批失败。"""
    mod = make_mod(tmp_path, "Broken")
    # 把 utoc 换成目录，制造 OSError
    for f in mod.library_files:
        if f.path.suffix == ".utoc":
            f.path.unlink()
            f.path.mkdir()
    result = classify.classify_many([mod])
    assert result == {"Broken": classify.OTHER}


def test_sorting_key_is_stable_order() -> None:
    keys = [
        classify.sorting_key(c)
        for c in (classify.CLOTHING, classify.WEAPON, classify.GAMEPLAY, classify.OTHER)
    ]
    assert keys == sorted(keys)


def test_unknown_category_sorts_last() -> None:
    assert classify.sorting_key("莫名其妙") > classify.sorting_key(classify.OTHER)


# ---------------------------------------------------------------------------
# 读取工具
# ---------------------------------------------------------------------------


def test_read_ascii_strings_picks_readable_runs(tmp_path: Path) -> None:
    path = tmp_path / "blob.bin"
    path.write_bytes(b"\x00\x01\x02SB/Content/Paks/~mods\x00\xff\xfe" + b"A" * 8)
    found = classify.read_ascii_strings(path)
    assert "SB/Content/Paks/~mods" in found
    assert "AAAAAAAA" in found
    # 二进制噪声不该被当成字符串
    assert all(len(s) >= 5 for s in found)


def test_read_cns_metadata_reads_first_entry(tmp_path: Path) -> None:
    folder = tmp_path / "Multi"
    folder.mkdir()
    doc = folder / "Multi.dekcns.json"
    doc.write_text(
        json.dumps(
            [
                {"DisplayName": "第一套", "FitMeshType": "Body"},
                {"DisplayName": "第二套", "FitMeshType": "Eyes"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    mod = Mod(
        name="Multi",
        in_library=True,
        library_files=[ModFile(path=doc, relative=doc.name, size=1)],
    )
    meta = classify.read_cns_metadata(mod)
    assert meta["DisplayName"] == "第一套"


def test_read_cns_metadata_handles_broken_json(tmp_path: Path) -> None:
    folder = tmp_path / "Broken"
    folder.mkdir()
    doc = folder / "Broken.dekcns.json"
    doc.write_text("{ 不是 json", encoding="utf-8")
    mod = Mod(
        name="Broken",
        in_library=True,
        library_files=[ModFile(path=doc, relative=doc.name, size=1)],
    )
    assert classify.read_cns_metadata(mod) == {}


def test_read_cns_metadata_skips_utf8_bom(tmp_path: Path) -> None:
    """CNS 的文件普遍带 BOM，用 utf-8 直接读会炸。"""
    folder = tmp_path / "Bom"
    folder.mkdir()
    doc = folder / "Bom.dekcns.json"
    doc.write_bytes(
        b"\xef\xbb\xbf" + json.dumps([{"FitMeshType": "Body"}]).encode("utf-8")
    )
    mod = Mod(
        name="Bom",
        in_library=True,
        library_files=[ModFile(path=doc, relative=doc.name, size=1)],
    )
    assert classify.read_cns_metadata(mod)["FitMeshType"] == "Body"
