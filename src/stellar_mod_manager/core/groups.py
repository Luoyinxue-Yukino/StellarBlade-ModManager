"""Mod 分组：用户自定义的层级分类。

为什么单独一个模块
------------------
分组是**纯组织信息**，和 Mod 文件本身没有关系：删掉一个分组不该碰到任何文件，
把 Mod 移进另一个分组也不该移动磁盘上的数据。所以分组定义与 Mod 归属单独存在
``groups.json`` 里，而不是写进每个 Mod 的目录，也不塞进 ``config.json``
（配置和用户数据的读写节奏、备份方式都不一样）。

层级怎么算
----------
分组可以嵌套（``服装 / 上装``），每个 Mod **至多归属一个分组**。
「未分组」是一个虚拟分组，不对应 ``ModGroup`` 记录，因此不会出现在
``groups.json`` 里——它只是「归属为空」这个状态的显示名。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from ..logging_setup import get_logger
from . import paths

logger = get_logger(__name__)

#: 存储格式版本；将来结构变化时用于迁移。
STORE_VERSION = 1

#: 首次使用时建好的分类。用户可以随意改名或删除。
DEFAULT_GROUPS = ("服装", "武器", "玩法", "其他")

#: 「未分组」的伪分组 id，只在界面层使用，不会被写进文件。
UNGROUPED = "__ungrouped__"

#: 「未分组」的显示名。
UNGROUPED_LABEL = "未分组"


class GroupError(Exception):
    """分组操作失败（名称重复、成环、找不到目标等）。"""


@dataclass(slots=True)
class ModGroup:
    """一个分组节点。"""

    id: str
    name: str
    parent_id: str | None = None
    order: int = 0
    collapsed: bool = False
    """界面上是否折叠。属于使用习惯，跟着分组一起存，下次打开还是原样。"""

    @property
    def is_root(self) -> bool:
        return self.parent_id is None


class GroupTree:
    """分组树 + Mod 归属表。

    典型的用法是先用 :meth:`ensure_defaults` 保证首次打开有分类可选，
    之后由界面调用增删改查。所有写操作都会立刻落盘（原子替换），
    所以进程被强杀也不会留下半个文件。
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.groups_path()
        self._groups: dict[str, ModGroup] = {}
        #: Mod 名 → 分组 id。用 Mod 名做键：库里的目录名与游戏目录里的分组名
        #: 都是它，且扫描时就是这个粒度。
        self._assignments: dict[str, str] = {}
        self._load()

    # ------------------------------------------------------------------
    # 读写
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("分组文件无法解析(%s)，从空分组开始", exc)
            return
        if not isinstance(raw, dict):
            logger.warning("分组文件根节点不是对象，从空分组开始")
            return

        for item in raw.get("groups", []) or []:
            if not isinstance(item, dict):
                continue
            group_id = str(item.get("id") or "").strip()
            name = str(item.get("name") or "").strip()
            if not group_id or not name:
                continue
            parent = item.get("parentId") or item.get("parent_id") or None
            self._groups[group_id] = ModGroup(
                id=group_id,
                name=name,
                parent_id=str(parent) if parent else None,
                order=int(item.get("order", 0) or 0),
                collapsed=bool(item.get("collapsed", False)),
            )

        assignments = raw.get("assignments")
        if isinstance(assignments, dict):
            for mod_name, group_id in assignments.items():
                if isinstance(mod_name, str) and isinstance(group_id, str):
                    self._assignments[mod_name] = group_id

        # 指向不存在分组的归属与父子关系都清掉，避免界面拿到悬空 id
        known = set(self._groups)
        self._assignments = {
            mod: gid for mod, gid in self._assignments.items() if gid in known
        }
        for group in self._groups.values():
            if group.parent_id is not None and group.parent_id not in known:
                logger.warning("分组「%s」的父分组不存在，已提升为顶层", group.name)
                group.parent_id = None
        self._break_cycles()

    def save(self) -> Path:
        """原子写入分组文件，并保留上一版作为 ``.bak``。

        为什么要留备份：这里存的是**用户手工整理的分类**，丢了得一条条重来。
        它的价值远高于几 KB 的磁盘占用，所以每次写盘前先把旧版本留一份。
        真要出事时，把 ``groups.json.bak`` 改名回 ``groups.json`` 即可。
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": STORE_VERSION,
            "groups": [asdict(g) for g in self.sorted_groups()],
            "assignments": dict(sorted(self._assignments.items())),
        }
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # 只在「旧文件有实际内容」时留备份，避免把空文件覆盖上去反而丢了好的那一版
        if self.path.is_file() and self.path.stat().st_size > 0:
            try:
                backup = self.path.with_suffix(self.path.suffix + ".bak")
                backup.write_bytes(self.path.read_bytes())
            except OSError as exc:  # 备份失败不该拦住正常写入
                logger.warning("分组备份失败：%s", exc)

        temp.replace(self.path)
        return self.path

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._groups)

    def all(self) -> list[ModGroup]:
        return list(self._groups.values())

    def get(self, group_id: str | None) -> ModGroup | None:
        if not group_id:
            return None
        return self._groups.get(group_id)

    def sorted_groups(self) -> list[ModGroup]:
        """全部节点，按「先父后子、同级按 order」排列。"""
        out: list[ModGroup] = []

        def walk(parent_id: str | None) -> None:
            for group in self.children(parent_id):
                out.append(group)
                walk(group.id)

        walk(None)
        return out

    def children(self, parent_id: str | None) -> list[ModGroup]:
        """某分组的直接子分组，按 order、再按名称排序。"""
        items = [g for g in self._groups.values() if g.parent_id == parent_id]
        return sorted(items, key=lambda g: (g.order, g.name))

    def roots(self) -> list[ModGroup]:
        return self.children(None)

    def siblings(self, group_id: str) -> list[ModGroup]:
        group = self._groups.get(group_id)
        return self.children(group.parent_id if group else None)

    def descendants(self, group_id: str) -> set[str]:
        """全部后代分组 id（不含自身）。"""
        found: set[str] = set()
        stack = [group_id]
        while stack:
            current = stack.pop()
            for child in self.children(current):
                if child.id not in found:
                    found.add(child.id)
                    stack.append(child.id)
        return found

    def depth(self, group_id: str) -> int:
        """层级深度，顶层为 0。"""
        depth = 0
        current = self._groups.get(group_id)
        seen: set[str] = set()
        while current and current.parent_id and current.parent_id not in seen:
            seen.add(current.parent_id)
            depth += 1
            current = self._groups.get(current.parent_id)
        return depth

    def path_of(self, group_id: str | None) -> list[ModGroup]:
        """从顶层到该分组的路径（面包屑）。"""
        chain: list[ModGroup] = []
        seen: set[str] = set()
        current = self.get(group_id)
        while current and current.id not in seen:
            seen.add(current.id)
            chain.append(current)
            current = self.get(current.parent_id)
        return list(reversed(chain))

    def breadcrumb(self, group_id: str | None, separator: str = " / ") -> str:
        return separator.join(g.name for g in self.path_of(group_id))

    def find_by_name(self, name: str, parent_id: str | None = None) -> ModGroup | None:
        """在指定父级下按名字找分组（同级重名检查用）。"""
        target = name.strip().casefold()
        for group in self.children(parent_id):
            if group.name.casefold() == target:
                return group
        return None

    def is_name_taken(
        self, name: str, parent_id: str | None, *, exclude: str | None = None
    ) -> bool:
        found = self.find_by_name(name, parent_id)
        return found is not None and found.id != exclude

    # ------------------------------------------------------------------
    # 增删改
    # ------------------------------------------------------------------

    def create(self, name: str, parent_id: str | None = None) -> ModGroup:
        """新建分组。同级同名会被拒绝，避免界面上出现两个一模一样的条目。"""
        clean = name.strip()
        if not clean:
            raise GroupError("分组名不能为空")
        if parent_id is not None and parent_id not in self._groups:
            raise GroupError("父分组不存在")
        if self.is_name_taken(clean, parent_id):
            where = f"「{self._groups[parent_id].name}」下" if parent_id else "顶层"
            raise GroupError(f"{where}已经有叫「{clean}」的分组了")

        order = max((g.order for g in self.children(parent_id)), default=-1) + 1
        group = ModGroup(
            id=uuid.uuid4().hex[:12], name=clean, parent_id=parent_id, order=order
        )
        self._groups[group.id] = group
        self.save()
        return group

    def rename(self, group_id: str, name: str) -> ModGroup:
        group = self._require(group_id)
        clean = name.strip()
        if not clean:
            raise GroupError("分组名不能为空")
        if self.is_name_taken(clean, group.parent_id, exclude=group_id):
            raise GroupError(f"同级已经有叫「{clean}」的分组了")
        group.name = clean
        self.save()
        return group

    def set_collapsed(self, group_id: str, collapsed: bool) -> None:
        """记住折叠状态。纯界面偏好，失败也不该打扰用户。"""
        group = self._groups.get(group_id)
        if group is None or group.collapsed == collapsed:
            return
        group.collapsed = collapsed
        self.save()

    def move(self, group_id: str, new_parent_id: str | None) -> ModGroup:
        """把分组挪到另一个父级下。会拒绝把自己挪进自己的子树。"""
        group = self._require(group_id)
        if new_parent_id == group_id:
            raise GroupError("不能把分组移动到它自己里面")
        if new_parent_id is not None:
            if new_parent_id not in self._groups:
                raise GroupError("目标分组不存在")
            if new_parent_id in self.descendants(group_id):
                raise GroupError("不能把分组移动到它自己的子分组里")
            if self.is_name_taken(group.name, new_parent_id, exclude=group_id):
                raise GroupError(
                    f"「{self._groups[new_parent_id].name}」下已经有同名分组了"
                )

        group.parent_id = new_parent_id
        group.order = max((g.order for g in self.children(new_parent_id)), default=-1) + 1
        self.save()
        return group

    def delete(self, group_id: str) -> None:
        """删除分组。

        子分组**上提一级**（保留用户搭好的结构），组内 Mod 变为未分组——
        两者都不会碰磁盘上的任何 Mod 文件。
        """
        group = self._require(group_id)
        parent = group.parent_id
        for child in self.children(group_id):
            child.parent_id = parent
        for mod_name, assigned in list(self._assignments.items()):
            if assigned == group_id:
                del self._assignments[mod_name]
        del self._groups[group_id]
        self.save()

    def clear(self) -> None:
        """清空全部分组与归属（界面上的「重置分组」）。"""
        self._groups.clear()
        self._assignments.clear()
        self.save()

    def ensure_defaults(self, names: tuple[str, ...] = DEFAULT_GROUPS) -> bool:
        """首次使用（一个分组都没有）时建好默认分类。

        返回是否真的创建了，方便界面决定要不要提示。
        """
        if self._groups:
            return False
        for name in names:
            self.create(name)
        logger.info("已创建默认分组：%s", "、".join(names))
        return True

    # ------------------------------------------------------------------
    # Mod 归属
    # ------------------------------------------------------------------

    def group_of(self, mod_name: str) -> str | None:
        """Mod 所属分组 id；``None`` 表示未分组。"""
        group_id = self._assignments.get(mod_name)
        return group_id if group_id in self._groups else None

    def assign(self, mod_name: str, group_id: str | None) -> None:
        """把 Mod 放进某个分组；``None`` 表示移出分组。"""
        if group_id is None:
            if self._assignments.pop(mod_name, None) is not None:
                self.save()
            return
        if group_id not in self._groups:
            raise GroupError("目标分组不存在")
        if self._assignments.get(mod_name) == group_id:
            return
        self._assignments[mod_name] = group_id
        self.save()

    def assign_many(self, mod_names: list[str], group_id: str | None) -> int:
        """批量归属，只落盘一次。返回实际改动的数量。"""
        if group_id is not None and group_id not in self._groups:
            raise GroupError("目标分组不存在")
        changed = 0
        for mod_name in mod_names:
            if group_id is None:
                if self._assignments.pop(mod_name, None) is not None:
                    changed += 1
            elif self._assignments.get(mod_name) != group_id:
                self._assignments[mod_name] = group_id
                changed += 1
        if changed:
            self.save()
        return changed

    def mods_in(self, group_id: str, *, recursive: bool = False) -> set[str]:
        """该分组下的 Mod 名。``recursive`` 时包含所有子分组。"""
        targets = {group_id}
        if recursive:
            targets |= self.descendants(group_id)
        return {
            mod for mod, assigned in self._assignments.items() if assigned in targets
        }

    def ungrouped(self, known_mods: list[str] | None = None) -> list[str]:
        """未分组的 Mod。

        传入 ``known_mods`` 时会以它为准，这样「已删除的 Mod 仍留着归属记录」
        不会在界面上冒出来。
        """
        if known_mods is None:
            return sorted(
                m for m, gid in self._assignments.items() if gid not in self._groups
            )
        return [m for m in known_mods if self.group_of(m) is None]

    def forget_mods(self, keep: set[str]) -> int:
        """丢弃不在 ``keep`` 里的归属记录，返回清理条数。

        用于库扫描之后对账：Mod 被删掉了，它的归属记录也该跟着走，
        否则 ``groups.json`` 会越积越多。

        **``keep`` 为空时直接拒绝。** 那通常意味着扫描失败（游戏目录暂时不可用、
        盘符掉线、配置被改坏），而不是「用户的 Mod 真的一个都不剩了」。
        此时清空归属等于把用户手工整理的分类全删掉，代价远大于留几条失效记录。
        """
        if not keep:
            logger.warning("扫描结果为空，拒绝清理分组归属（可能是游戏目录暂时不可用）")
            return 0
        stale = [m for m in self._assignments if m not in keep]
        for mod_name in stale:
            del self._assignments[mod_name]
        if stale:
            self.save()
            logger.debug("清理了 %d 条失效的 Mod 归属记录", len(stale))
        return len(stale)

    def counts(self, known_mods: list[str] | None = None) -> dict[str, int]:
        """每个分组的 Mod 数（含子分组），用于列表上的计数。"""
        base = known_mods if known_mods is not None else list(self._assignments)
        result: dict[str, int] = {}
        for group in self._groups.values():
            targets = {group.id} | self.descendants(group.id)
            result[group.id] = sum(
                1 for m in base if self._assignments.get(m) in targets
            )
        return result

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _require(self, group_id: str) -> ModGroup:
        group = self._groups.get(group_id)
        if group is None:
            raise GroupError("分组不存在，可能已经被删除")
        return group

    def _break_cycles(self) -> None:
        """兜底：手工改坏了文件也不能让遍历陷入死循环。"""
        for group in self._groups.values():
            seen = {group.id}
            current = group
            while current.parent_id:
                if current.parent_id in seen:
                    logger.warning("分组「%s」存在循环引用，已断开", group.name)
                    current.parent_id = None
                    break
                seen.add(current.parent_id)
                nxt = self._groups.get(current.parent_id)
                if nxt is None:
                    current.parent_id = None
                    break
                current = nxt


__all__ = [
    "DEFAULT_GROUPS",
    "STORE_VERSION",
    "UNGROUPED",
    "UNGROUPED_LABEL",
    "GroupError",
    "GroupTree",
    "ModGroup",
]
