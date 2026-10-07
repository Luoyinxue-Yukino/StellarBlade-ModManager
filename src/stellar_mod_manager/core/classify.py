"""按 Mod 的内容推荐分类。

为什么能猜得准
--------------
Stellar Blade 的 Mod 是 IoStore 格式，``.pak`` 只是空壳，真正的资源清单在
``.utoc`` 的目录索引里——**未加密也未压缩**，资源路径是明文。读出这些资源名
就能判断这个 Mod 动了什么：

* ``CH_P_EVE_58_UV1_N.uasset`` / ``NanoSuit`` / ``Skin`` → 服装；
* ``*_WP_*`` / ``Weapon`` / ``Sword`` → 武器；
* ``.lua`` 脚本、``LogicMods``、``UE4SS`` → 玩法（蓝图/脚本类 Mod）。

再叠加 CNS 的 ``.dekcns.json``：它的 ``FitMeshType``（Body / Eyes…）与
``OutfitTypes``（Dress / NSFW…）本来就是作者填好的分类，可信度最高。

猜错没有代价
------------
只在**未分组**的 Mod 上做自动归类，且以分组名匹配（用户把「服装」改名或删掉就
自然失效），任何时候都可以手动改。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path

from ..logging_setup import get_logger
from .groups import UNGROUPED_LABEL
from .models import Mod

logger = get_logger(__name__)

#: IoStore 目录索引里的可读字符串最短长度。太短的都是二进制噪声。
_MIN_STRING = 5
_ASCII_RUN = re.compile(rb"[\x20-\x7e]{%d,}" % _MIN_STRING)


def read_ascii_strings(
    path: Path, *, min_length: int = _MIN_STRING, limit: int = 4000
) -> list[str]:
    """从二进制文件里抠出可读 ASCII 串。

    ``.utoc`` 的目录索引没有加密也没有压缩，资源路径就是明文，所以这一步
    不需要理解 IoStore 的二进制结构——扫描就够了，而且对版本差异免疫。
    """
    if min_length != _MIN_STRING:
        pattern = re.compile(rb"[\x20-\x7e]{%d,}" % min_length)
    else:
        pattern = _ASCII_RUN
    data = path.read_bytes()
    return [
        match.decode("ascii", "replace")
        for match in pattern.findall(data)[:limit]
    ]

#: 自动归类的目标分类名。与 ``groups.DEFAULT_GROUPS`` 对应；用户改了分组名
#: 就匹配不上，该 Mod 会保持未分组，而不是被硬塞进一个不存在的分类。
CLOTHING = "服装"
WEAPON = "武器"
GAMEPLAY = "玩法"
OTHER = "其他"

#: 武器类资源的特征
_WEAPON_ASSET = re.compile(
    r"(_wp_|_wp\d|weapon|sword|gun|rifle|katana|blade_|_blade)", re.IGNORECASE
)
#: 角色外观类资源的特征。
#:
#: 注意 ``ch_p_eve`` 不能写成 ``_ch_p_eve``——资源名常以它**开头**
#: （``CH_P_EVE_OneMillion_01.uasset``），带前导下划线就永远匹配不上。
_OUTFIT_ASSET = re.compile(
    r"(nanosuit|outfit|dress|skin|ch_p_eve|ch_eve|eve_|bodysuit|costume|hair|"
    r"face|eye|makeup|glasses|accessor|tattoo|suit|panty|bikini|bra_|body)",
    re.IGNORECASE,
)
#: 脚本 / 玩法类的**强**信号：只有真正的功能 Mod 才会有
_GAMEPLAY_ASSET = re.compile(
    r"(logicmods?|ue4ss|\.dll$|\.ini$|/scripts?/)", re.IGNORECASE
)
#: 贴图后缀。一个 Mod 大部分资源都是这些，说明它主要是换皮
_TEXTURE_ASSET = re.compile(
    r"(_n|_orm|_a|_e|_mask|_d|_r|_albedo|_normal)\.u(bulk|asset)$", re.IGNORECASE
)

#: 名字里的关键词（兜底：没有 utoc 或读不出来时用）
_NAME_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(weapon|sword|gun|katana|blade|武器|刀|枪)", re.I), WEAPON),
    (re.compile(r"(gameplay|cheat|trainer|script|logic|mod loader|玩法|脚本|修改器)", re.I), GAMEPLAY),
    (re.compile(r"(outfit|dress|suit|body|skin|hair|cosplay|服装|衣服|套装|发型)", re.I), CLOTHING),
)

#: CNS 的 FitMeshType → 分类
_FIT_MESH_TYPE = {
    "body": CLOTHING,
    "eyes": CLOTHING,
    "hair": CLOTHING,
    "head": CLOTHING,
}


def read_asset_names(mod: Mod, *, limit: int = 4000) -> list[str]:
    """读出 Mod 的资源清单。

    优先读 ``.utoc``（IoStore 目录索引未加密），没有就退回文件名列表。
    读不出来不抛异常——自动分类只是锦上添花，不该拦住刷新。
    """
    names: list[str] = []
    utocs = [
        f.path
        for f in mod.files
        if f.path.suffix.lower() == ".utoc" and f.path.is_file()
    ]
    for path in utocs[:4]:
        try:
            names.extend(read_ascii_strings(path, min_length=4, limit=limit))
        except OSError as exc:
            logger.debug("读取 %s 失败：%s", path.name, exc)
    return names


def read_cns_metadata(mod: Mod) -> dict:
    """读 CNS 的 ``.dekcns.json``，取第一个条目。

    顶层是**数组**（一个文件可以声明多套服装），这里只用第一条做判断。
    """
    for f in mod.files:
        if not f.path.name.endswith(".dekcns.json") or not f.path.is_file():
            continue
        try:
            raw = json.loads(f.path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        items = raw if isinstance(raw, list) else [raw]
        for item in items:
            if isinstance(item, dict):
                return item
    return {}


def classify(mod: Mod, asset_names: Iterable[str] | None = None) -> str:
    """给一个 Mod 猜分类。返回 ``CLOTHING`` / ``WEAPON`` / ``GAMEPLAY`` / ``OTHER``。

    判定顺序是按**信号强度**排的，不是按代码顺序随手写的：

    1. ``.dll`` / LogicMods / UE4SS——只有真正的脚本 Mod 才有，最强的玩法信号；
    2. CNS 的 ``FitMeshType`` / ``OutfitTypes``——作者自己填的分类；
    3. 资源名里的武器 / 外观特征；
    4. ``.lua``——**最弱**的信号，所以放在内容判断之后。
       大量 CNS 换装 Mod 都会附带一个 CNS 系统的辅助 lua，
       把它当玩法信号会把一堆衣服错判成脚本 Mod。
    """
    names = list(asset_names if asset_names is not None else read_asset_names(mod))
    joined = "\n".join(names)
    suffixes = {f.path.suffix.lower() for f in mod.files}

    # 1) 强玩法信号
    if ".dll" in suffixes or _GAMEPLAY_ASSET.search(joined):
        return GAMEPLAY

    # 2) 作者填的 CNS 分类
    meta = read_cns_metadata(mod)
    fit = str(meta.get("FitMeshType") or "").strip().lower()
    if fit in _FIT_MESH_TYPE:
        return _FIT_MESH_TYPE[fit]
    outfit_types = meta.get("OutfitTypes")
    if isinstance(outfit_types, list) and outfit_types:
        return CLOTHING

    # 3) 资源名特征
    weapon_hits = len(_WEAPON_ASSET.findall(joined))
    outfit_hits = len(_OUTFIT_ASSET.findall(joined))
    if weapon_hits > outfit_hits:
        return WEAPON
    if outfit_hits:
        return CLOTHING

    # 4) 弱玩法信号：有 lua 又没有内容特征，才当成功能 Mod
    if ".lua" in suffixes:
        return GAMEPLAY

    # 5) 名字兜底
    for pattern, category in _NAME_HINTS:
        if pattern.search(mod.name):
            return category

    return OTHER


def classify_many(mods: Iterable[Mod]) -> dict[str, str]:
    """批量分类，返回 ``{mod 名: 分类名}``。"""
    result: dict[str, str] = {}
    for mod in mods:
        try:
            result[mod.name] = classify(mod)
        except Exception as exc:  # noqa: BLE001 - 单个失败不该影响整批
            logger.debug("分类「%s」失败：%s", mod.name, exc)
            result[mod.name] = OTHER
    return result


def sorting_key(category: str) -> int:
    """让建议结果的展示顺序稳定：服装 → 武器 → 玩法 → 其他。"""
    order = {CLOTHING: 0, WEAPON: 1, GAMEPLAY: 2, OTHER: 3}
    return order.get(category, 9)


__all__ = [
    "CLOTHING",
    "GAMEPLAY",
    "OTHER",
    "UNGROUPED_LABEL",
    "WEAPON",
    "classify",
    "classify_many",
    "read_ascii_strings",
    "read_asset_names",
    "read_cns_metadata",
    "sorting_key",
]
