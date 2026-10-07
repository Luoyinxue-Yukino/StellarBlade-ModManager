"""Mod 库：仓库模型下的导入、部署、启用/停用与卸载。

存储模型
--------
**库是权威副本，游戏目录只是部署目标。**

============  ==========================================  ==========================
位置          内容                                        是否被游戏加载
============  ==========================================  ==========================
库            全部 Mod，每个一个子目录，只放 Mod 文件本身   否（在 ``Paks`` 树之外）
游戏 ``~mods``  只有已启用的 Mod                              是
============  ==========================================  ==========================

所以你可以囤很多 Mod 只留几个启用：不用的留在库里，游戏完全不碰它们；
启用某个 Mod 就是把它从库部署进 ``~mods``，停用就是把游戏目录那份删掉、库里那份
原封不动。**不会丢文件**——这正是仓库模型要解决的问题。

为什么用硬链接部署
------------------
硬链接让同一份数据同时出现在库和游戏目录里，但磁盘上**只占一份空间**；
删掉任意一侧，另一侧依然完好。前提是两者在同一个卷上，这也是库目录默认放在
游戏所在盘根目录的原因（见 :func:`core.paths.default_library_dir`）。

跨卷、或文件系统不支持硬链接（如 exFAT 的 Steam 库）时自动退回复制，
并把实际情况记在 :attr:`~core.models.Mod.deploy_mode` 里如实告诉用户。
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import paths
from .models import MOD_SUFFIXES, Mod, ModFile
from .translate import DekcnsDocument

from ..logging_setup import get_logger
logger = get_logger(__name__)

#: 库根目录下的元数据索引文件名。
_INDEX_NAME = "library.json"
#: 旧版「已停用」目录里的布局侧车文件，迁移时必须丢掉。
_LEGACY_MARKER = "mod.json"
#: 与 Mod 功能无关的装饰性文件：不入库、不部署。
#:
#: 除此之外**一切都算 Mod 内容**——CNS 系列 Mod 会把 ``.dekcns.json`` 注册文件
#: 放在 pak 旁边，少一个 Mod 就废了；只认 ``.pak/.utoc/.ucas`` 会把它删掉。
IGNORED_SUFFIXES = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".url", ".lnk", ".torrent"}
)

DEPLOY_HARDLINK = "hardlink"
DEPLOY_COPY = "copy"
DEPLOY_MIXED = "mixed"


class LibraryError(RuntimeError):
    """Mod 库操作失败（目录缺失、权限不足、目标被占用等）。"""


class FolderImportCancelled(Exception):
    """用户中止了批量导入。"""



@dataclass(slots=True)
class _Group:
    """扫描过程中聚合出来的一组文件，代表一个 Mod。"""

    name: str
    files: list[Path]
    root: Path | None = None
    """该 Mod 独占的目录；``None`` 表示文件散落在 ``~mods`` 顶层。"""


@dataclass(slots=True)
class MigrationResult:
    """旧「已停用」目录的迁移结果。"""

    moved: int = 0
    skipped: int = 0
    legacy_dir: Path | None = None

    @property
    def did_something(self) -> bool:
        return self.moved > 0


@dataclass(slots=True)
class FolderImportResult:
    """批量导入目录的结果。"""

    source_dir: Path
    scanned: int = 0
    imported: list[Mod] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def imported_count(self) -> int:
        return len(self.imported)

    @property
    def total_bytes(self) -> int:
        return sum(m.library_size for m in self.imported)


@dataclass(slots=True)
class TranslationWriteResult:
    """一次译文写回的结果。"""

    path: Path
    backup: Path
    entries: int
    redeployed: bool = False


class ModLibrary:
    """围绕某个游戏目录与某个库目录的 Mod 增删查改。"""

    def __init__(self, game_root: Path | None, library_root: Path | None = None) -> None:
        self.game_root = Path(game_root) if game_root else None
        self.library_root = (
            Path(library_root)
            if library_root
            else paths.default_library_dir(self.game_root)
        )
        self.legacy_disabled_dir = paths.disabled_store_dir()
        self._index_path = self.library_root / _INDEX_NAME

    # ------------------------------------------------------------------
    # 基础
    # ------------------------------------------------------------------

    @property
    def mods_dir(self) -> Path:
        target = paths.mods_dir(self.game_root)
        if target is None:
            raise LibraryError("尚未配置《剑星》游戏目录")
        return target

    @property
    def is_ready(self) -> bool:
        return self.game_root is not None and paths.is_valid_game_root(self.game_root)

    @property
    def same_volume(self) -> bool:
        """库与游戏目录是否在同一个卷上——决定能否硬链接部署。"""
        if not self.is_ready:
            return False
        return _same_volume(self.library_root, self.mods_dir.parent)

    def ensure_dirs(self) -> None:
        """确保库与 ``~mods`` 都存在。"""
        self.library_root.mkdir(parents=True, exist_ok=True)
        if self.is_ready:
            self.mods_dir.mkdir(parents=True, exist_ok=True)

    def open_mods_dir(self) -> Path:
        self.ensure_dirs()
        return self.mods_dir

    # ------------------------------------------------------------------
    # 扫描
    # ------------------------------------------------------------------

    def scan(self) -> list[Mod]:
        """合并扫描库与游戏目录，按名字配对成完整的 Mod 状态。"""
        merged: dict[str, Mod] = {}
        for mod in self._scan_library():
            merged[mod.name.lower()] = mod

        if self.is_ready:
            for game_mod in self._scan_game_dir():
                key = game_mod.name.lower()
                existing = merged.get(key)
                if existing is None:
                    # 只在游戏目录里找到：未纳管
                    merged[key] = game_mod
                    continue
                existing.deployed = True
                existing.deployed_files = game_mod.deployed_files
                existing.deploy_mode = _detect_deploy_mode(
                    existing.library_files, game_mod.deployed_files
                )

        return sorted(merged.values(), key=lambda m: m.name.lower())

    def _scan_library(self) -> list[Mod]:
        root = self.library_root
        if not root.is_dir():
            return []

        index = self._load_index()
        mods: list[Mod] = []

        for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            if not _payload_files(child):
                continue
            files = _collect_content(child)
            if not files:
                continue

            meta = index.get(child.name, {})
            source = meta.get("source_archive")
            mods.append(
                Mod(
                    name=child.name,
                    in_library=True,
                    library_files=[
                        ModFile(
                            path=path,
                            relative=path.relative_to(child).as_posix(),
                            size=_safe_size(path),
                        )
                        for path in files
                    ],
                    source_archive=Path(source) if source else None,
                    installed_at=_parse_time(meta.get("imported_at")),
                )
            )
        return mods

    def _scan_game_dir(self) -> list[Mod]:
        root = self.mods_dir
        if not root.is_dir():
            return []
        return [
            Mod(
                name=group.name,
                in_library=False,
                deployed=True,
                deployed_files=[
                    ModFile(
                        path=path,
                        relative=_relative_to(path, group.root),
                        size=_safe_size(path),
                    )
                    for path in group.files
                ],
                deployed_root=group.root,
                deploy_mode="",
                installed_at=_safe_mtime(group.files[0]) if group.files else None,
            )
            for group in _group_mod_files(root)
        ]

    # ------------------------------------------------------------------
    # 元数据索引
    # ------------------------------------------------------------------

    def _load_index(self) -> dict[str, dict]:
        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(k): v for k, v in data.items() if isinstance(v, dict)}

    def _remember(self, name: str, **fields: object) -> None:
        """记录来源压缩包等**可选**元数据。

        文件系统仍是唯一事实来源：索引丢了功能照常，只是不再显示来源。
        """
        data = self._load_index()
        entry = data.setdefault(name, {})
        entry.update({k: (str(v) if isinstance(v, Path) else v) for k, v in fields.items()})
        try:
            self.library_root.mkdir(parents=True, exist_ok=True)
            self._index_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            logger.warning("写入库索引失败: %s", exc)

    def _forget(self, name: str) -> None:
        data = self._load_index()
        if data.pop(name, None) is not None:
            try:
                self._index_path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except OSError:
                pass

    # ------------------------------------------------------------------
    # 入库
    # ------------------------------------------------------------------

    def import_to_library(
        self,
        source_dir: Path | str,
        mod_name: str,
        *,
        source_archive: Path | None = None,
        move: bool = False,
        link: bool = False,
    ) -> Mod:
        """把解压结果收进库。

        ``move=True`` 直接搬动文件（调用方不再需要源目录）；
        ``link=True`` 在同一个卷上使用硬链接——**不额外占用空间**，
        源目录（例如你自己的 Mod 备份）保持可用。两者互斥，``move`` 优先。
        """
        source = Path(source_dir)
        if not source.is_dir():
            raise LibraryError(f"源目录不存在：{source}")

        payloads = _payload_files(source)
        if not payloads:
            raise LibraryError("没有在解压结果中找到 .pak / .utoc / .ucas 文件")

        # 剥掉压缩包里的外层目录（如 SB/Content/Paks/~mods/），
        # 然后取该目录下的**全部**文件——含 .dekcns.json 之类的伴随文件。
        mod_root = _find_mod_root(source, payloads)
        candidates = _collect_content(mod_root) or payloads
        target = self._reserve_folder(mod_name)
        target.mkdir(parents=True, exist_ok=True)

        written: list[Path] = []
        try:
            for path in candidates:
                relative = path.relative_to(mod_root)
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                if move:
                    shutil.move(str(path), str(destination))
                elif link:
                    _link_or_copy(path, destination)
                else:
                    shutil.copy2(path, destination)
                written.append(destination)
        except OSError as exc:
            _remove_tree(target)
            raise LibraryError(f"存入库失败：{exc}") from exc

        self._remember(
            target.name,
            source_archive=source_archive or Path(source),
            imported_at=datetime.now().isoformat(timespec="seconds"),
        )
        return self._reload(target.name)

    def import_folder_tree(
        self,
        parent: Path | str,
        *,
        link: bool = True,
        progress: Callable[[int, int, str], None] | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> FolderImportResult:
        """把一个目录下的每个 Mod 子目录分别导入库。

        用于收纳「已解压好的一堆 Mod 文件夹」这种收藏。默认在同卷上走硬链接，
        因此不会额外占用空间，原目录也不会被动过。

        不含有效载荷的子目录会被跳过（可能是截图、说明或还没解压的压缩包）。
        """
        root = Path(parent)
        if not root.is_dir():
            raise LibraryError(f"目录不存在：{root}")

        candidates = [
            child
            for child in sorted(root.iterdir(), key=lambda p: p.name.lower())
            if child.is_dir() and not child.name.startswith(".") and _payload_files(child)
        ]

        result = FolderImportResult(source_dir=root, scanned=len(candidates))
        total = len(candidates)

        for index, folder in enumerate(candidates, start=1):
            if cancel is not None and cancel():
                raise FolderImportCancelled
            if progress is not None:
                progress(index - 1, total, folder.name)
            try:
                mod = self.import_to_library(folder, folder.name, link=link)
            except LibraryError as exc:
                logger.warning("导入 %s 失败: %s", folder.name, exc)
                result.failed.append((folder.name, str(exc)))
            else:
                result.imported.append(mod)
            if progress is not None:
                progress(index, total, folder.name)

        return result

    def adopt(self, mod: Mod) -> Mod:
        """把只存在于游戏目录的 Mod 收进库（「纳入库」）。

        同卷时使用硬链接，所以**不额外占用空间**，游戏目录里那份照常工作。
        """
        if mod.in_library:
            return mod
        if not mod.deployed_files:
            raise LibraryError(f"「{mod.name}」没有可纳入的文件")
        if not self.is_ready:
            raise LibraryError("尚未配置《剑星》游戏目录")

        # 相对路径必须以「该 Mod 在游戏目录里占据的文件夹」为基准，
        # 而不是所有文件的公共父目录——否则文件都在一层子目录里时会多剥一层。
        root = mod.deployed_root or _common_root([f.path for f in mod.deployed_files])
        target = self._reserve_folder(mod.name)
        target.mkdir(parents=True, exist_ok=True)

        linked = 0
        try:
            for item in mod.deployed_files:
                relative = _relative_path(item.path, root)
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                if _link_or_copy(item.path, destination) == DEPLOY_HARDLINK:
                    linked += 1
        except OSError as exc:
            _remove_tree(target)
            raise LibraryError(f"纳入库失败：{exc}") from exc

        self._remember(
            target.name,
            source_archive=mod.source_archive,
            imported_at=datetime.now().isoformat(timespec="seconds"),
        )
        logger.info(
            "已纳管 %s（%d/%d 个文件使用硬链接）",
            target.name,
            linked,
            len(mod.deployed_files),
        )
        return self._reload(target.name)

    def remove_from_library(self, mod: Mod) -> None:
        """从库里彻底删除。游戏目录里若还部署着，会先解除部署。"""
        if mod.deployed:
            self.undeploy(mod)
        if mod.in_library:
            _remove_tree(self.library_root / mod.name)
            self._forget(mod.name)

    # ------------------------------------------------------------------
    # 部署 / 解除部署
    # ------------------------------------------------------------------

    def deploy(self, mod: Mod) -> Mod:
        """把库里的 Mod 部署进游戏目录（即「启用」）。"""
        if not mod.in_library:
            raise LibraryError(f"「{mod.name}」不在库中，无法启用")
        if not self.is_ready:
            raise LibraryError("尚未配置《剑星》游戏目录")

        source_root = self.library_root / mod.name
        if not source_root.is_dir():
            raise LibraryError(f"库中找不到「{mod.name}」")

        mods_dir = self.mods_dir
        mods_dir.mkdir(parents=True, exist_ok=True)
        target_root = mods_dir / mod.name

        # 先清干净，保证游戏目录里的内容与库严格一致
        if target_root.exists():
            _remove_tree(target_root)
        target_root.mkdir(parents=True, exist_ok=True)

        linked = 0
        total = 0
        try:
            for source in sorted(source_root.rglob("*")):
                if not source.is_file():
                    continue
                relative = source.relative_to(source_root)
                destination = target_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                if _link_or_copy(source, destination) == DEPLOY_HARDLINK:
                    linked += 1
                total += 1
        except OSError as exc:
            _remove_tree(target_root)
            raise LibraryError(f"启用「{mod.name}」失败：{exc}") from exc

        mode = _mode_from_counts(linked, total)
        if mode == DEPLOY_COPY:
            logger.info("「%s」跨卷或文件系统不支持硬链接，已改用复制部署", mod.name)

        self._remember(mod.name, deployed_at=datetime.now().isoformat(timespec="seconds"))
        return self._reload(mod.name)

    def undeploy(self, mod: Mod) -> Mod:
        """把 Mod 从游戏目录移除（即「停用」）。**库里那份原封不动。**"""
        if not mod.deployed:
            return mod
        if not self.is_ready:
            raise LibraryError("尚未配置《剑星》游戏目录")

        removed = 0
        for item in mod.deployed_files:
            try:
                item.path.unlink(missing_ok=True)
                removed += 1
            except OSError as exc:
                raise LibraryError(f"停用「{mod.name}」失败：{exc}") from exc

        _prune_empty_dirs(self.mods_dir)
        logger.info("已停用 %s（移除 %d 个文件，库中副本保留）", mod.name, removed)
        return self._reload(mod.name)

    def set_enabled(self, mod: Mod, enabled: bool) -> Mod:
        """启用或停用。状态一致时原样返回。"""
        if mod.enabled == enabled:
            return mod
        return self.deploy(mod) if enabled else self.undeploy(mod)

    # ------------------------------------------------------------------
    # 翻译写回
    # ------------------------------------------------------------------

    @property
    def translation_backup_root(self) -> Path:
        return paths.translation_backup_dir()

    def backup_path_for(self, mod: Mod, source: Path) -> Path:
        """某个 Mod 的某个文件在翻译前的备份位置。"""
        return self.translation_backup_root / _sanitize_name(mod.name) / source.name

    def has_translation_backup(self, mod: Mod) -> bool:
        folder = self.translation_backup_root / _sanitize_name(mod.name)
        return folder.is_dir() and any(folder.iterdir())

    def apply_translation(
        self,
        mod: Mod,
        document: DekcnsDocument | str | Path,
        translations: dict[int, str],
    ) -> TranslationWriteResult:
        """把译文写回库中的 ``.dekcns.json``。

        三步保护：

        1. **先备份原件**到 ``%LOCALAPPDATA%\\StellarModManager\\translations\\backup``；
        2. **原子替换**目标文件——这会同时断开它所有的硬链接，因此你的 Mod 备份
           目录（如果当初是硬链接进来的）不会被顺手改掉；
        3. 若该 Mod 已部署，**重新部署**一次，让游戏目录也拿到新内容。
        """
        doc = (
            document
            if isinstance(document, DekcnsDocument)
            else DekcnsDocument.load(document)
        )
        target = doc.path
        if not self._inside_library(target):
            raise LibraryError(f"只能改写库中的文件，拒绝操作：{target}")

        if not translations:
            raise LibraryError("没有可写入的译文")

        # 最后一道防线：被 ControlledBy 引用的名称写进去会让整个控件树失效，
        # 界面已经不会把它们交给这里，但写盘这一层也必须自己挡住。
        locked = [
            doc.spans[index].text
            for index in translations
            if 0 <= index < len(doc.spans) and doc.spans[index].protected
        ]
        if locked:
            raise LibraryError(
                "以下名称被其它控件引用，翻译会导致 Mod 失效，已拒绝写入：\n"
                + "、".join(locked[:5])
            )

        backup = self.backup_path_for(mod, target)
        try:
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not backup.is_file():
                shutil.copy2(target, backup)
        except OSError as exc:
            raise LibraryError(f"备份原件失败，已中止：{exc}") from exc

        new_text = doc.render(translations)
        try:
            # 临时文件 + os.replace：写入是原子的，且新文件不继承任何硬链接
            temp = target.with_name(target.name + ".new")
            temp.write_bytes(doc.encode(new_text))
            os.replace(temp, target)
        except OSError as exc:
            raise LibraryError(f"写入译文失败：{exc}") from exc

        redeployed = False
        if mod.deployed:
            try:
                self.undeploy(mod)
                self.deploy(self._reload(mod.name))
                redeployed = True
            except LibraryError as exc:
                logger.warning("译文已写入，但重新部署失败：%s", exc)

        return TranslationWriteResult(
            path=target, backup=backup, entries=len(translations), redeployed=redeployed
        )

    def revert_translation(self, mod: Mod) -> int:
        """用备份还原该 Mod 全部被改写的文件，返回还原数量。"""
        folder = self.translation_backup_root / _sanitize_name(mod.name)
        if not folder.is_dir():
            raise LibraryError(f"「{mod.name}」没有翻译备份")

        restored = 0
        for backup in sorted(folder.iterdir()):
            if not backup.is_file():
                continue
            target = self._find_library_file(mod, backup.name)
            if target is None:
                continue
            try:
                temp = target.with_name(target.name + ".new")
                shutil.copy2(backup, temp)
                os.replace(temp, target)
            except OSError as exc:
                raise LibraryError(f"还原 {target.name} 失败：{exc}") from exc
            restored += 1

        if mod.deployed and restored:
            try:
                self.undeploy(mod)
                self.deploy(self._reload(mod.name))
            except LibraryError as exc:
                logger.warning("已还原译文，但重新部署失败：%s", exc)
        return restored

    def _inside_library(self, path: Path) -> bool:
        root = self.library_root
        try:
            path.resolve().relative_to(root.resolve())
        except (ValueError, OSError):
            return False
        return True

    def _find_library_file(self, mod: Mod, name: str) -> Path | None:
        folder = self.library_root / mod.name
        if not folder.is_dir():
            return None
        for candidate in folder.rglob(name):
            if candidate.is_file():
                return candidate
        return None

    # ------------------------------------------------------------------
    # 迁移
    # ------------------------------------------------------------------

    def migrate_legacy_disabled(self) -> MigrationResult:
        """把旧版放在 ``%LOCALAPPDATA%`` 的「已停用」Mod 搬进库。

        旧模型把停用的 Mod 移出游戏目录存到应用数据里；仓库模型下它们本就该待在库中，
        所以这里一次性搬过来。只搬 Mod 文件，旧版的 ``mod.json`` 布局侧车会丢掉。
        """
        legacy = self.legacy_disabled_dir
        result = MigrationResult(legacy_dir=legacy)
        if not legacy.is_dir():
            return result

        for entry in sorted(legacy.iterdir(), key=lambda p: p.name.lower()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            if not _payload_files(entry):
                result.skipped += 1
                continue
            files = _collect_content(entry)
            if not files:
                result.skipped += 1
                continue

            source = _read_legacy_source(entry)
            target = self._reserve_folder(entry.name)
            target.mkdir(parents=True, exist_ok=True)
            try:
                for path in files:
                    destination = target / path.relative_to(entry)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(path), str(destination))
            except OSError as exc:
                logger.warning("迁移 %s 失败: %s", entry.name, exc)
                _remove_tree(target)
                result.skipped += 1
                continue

            self._remember(
                target.name,
                source_archive=source,
                imported_at=datetime.now().isoformat(timespec="seconds"),
            )
            _remove_tree(entry)
            result.moved += 1

        if result.did_something:
            logger.info("已从旧停用目录迁移 %d 个 Mod 到库", result.moved)
        _remove_tree(legacy)
        return result

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _reserve_folder(self, mod_name: str) -> Path:
        """在库中占一个不重名的目录。"""
        self.library_root.mkdir(parents=True, exist_ok=True)
        return _unique_dir(self.library_root / _sanitize_name(mod_name))

    def _reload(self, name: str) -> Mod:
        """重新扫描并取出指定名字的 Mod。

        增删改之后一律走这里取返回值，保证调用方拿到的一定与磁盘现状一致，
        而不是手工拼出来的、可能与实际不符的对象。
        """
        for mod in self.scan():
            if mod.name == name:
                return mod
        return Mod(name=name)


# ---------------------------------------------------------------------------
# 扫描辅助
# ---------------------------------------------------------------------------


def _collect_content(root: Path) -> list[Path]:
    """目录下属于该 Mod 的**全部**文件。

    不只认 ``.pak/.utoc/.ucas/.sig``：CNS 系列 Mod 的 ``.dekcns.json`` 就放在
    pak 旁边，部署时漏掉它 Mod 会失效。只有明确的装饰性文件才排除。
    """
    if not root.is_dir():
        return []
    return [
        p
        for p in sorted(root.rglob("*"))
        if p.is_file()
        and p.suffix.lower() not in IGNORED_SUFFIXES
        and p.name != _LEGACY_MARKER
    ]


def _payload_files(root: Path) -> list[Path]:
    """目录下的有效载荷文件（``.pak/.utoc/.ucas/.sig``），用于判断「这是不是一个 Mod」。"""
    if not root.is_dir():
        return []
    return [
        p
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.suffix.lower() in MOD_SUFFIXES
    ]


def _collect_mod_files(root: Path) -> list[Path]:
    """仅收集有效载荷文件。用于判断目录里有没有 Mod，不用于拷贝内容。"""
    return _payload_files(root)


def _group_mod_files(root: Path) -> list[_Group]:
    """把 ``~mods`` 下的文件按 Mod 归组。

    一个子目录 = 一个 Mod，内容是该目录下的全部文件；
    散落在顶层的文件按主名归组（``X.pak`` / ``X.utoc`` / ``X.ucas`` / ``X.sig``）。
    """
    try:
        children = sorted(root.iterdir(), key=lambda p: p.name.lower())
    except OSError as exc:
        logger.warning("无法读取目录 %s: %s", root, exc)
        return []

    groups: list[_Group] = []
    directory_names: set[str] = set()

    for child in children:
        if child.name.startswith(".") or child.name == _LEGACY_MARKER:
            continue
        if not child.is_dir():
            continue
        # 有有效载荷才算 Mod；内容是整个目录（含 .dekcns.json 之类的伴随文件）
        if not _payload_files(child):
            continue
        groups.append(_Group(name=child.name, files=_collect_content(child), root=child))
        directory_names.add(child.name.lower())

    loose: dict[str, _Group] = {}
    for child in children:
        if child.is_dir() or child.suffix.lower() not in MOD_SUFFIXES:
            continue
        if child.name.startswith(".") or child.name == _LEGACY_MARKER:
            continue

        key = child.stem.lower()
        if key in directory_names:
            groups.append(_Group(name=f"{child.stem} (松散文件)", files=[child]))
            continue
        group = loose.get(key)
        if group is None:
            group = _Group(name=child.stem, files=[child])
            loose[key] = group
            groups.append(group)
        else:
            group.files.append(child)

    return groups


def _find_mod_root(root: Path, files: list[Path]) -> Path:
    """求出「Mod 内容」的根目录，用于剥掉压缩包里多余的外层目录。

    直接取全部 Mod 文件的公共父目录：``SB/Content/Paks/~mods/MyMod/MyMod.pak``
    的公共父目录就是 ``…/~mods/MyMod``，剥掉后只剩 ``MyMod.pak``。
    """
    return _common_root(files, fallback=root)


def _common_root(files: list[Path], fallback: Path | None = None) -> Path:
    """一批文件的公共父目录。"""
    if not files:
        if fallback is None:
            raise LibraryError("没有可用于推断根目录的文件")
        return fallback
    if len(files) == 1:
        return files[0].parent
    try:
        return Path(os.path.commonpath([str(f.parent) for f in files]))
    except ValueError:
        return files[0].parent


def _relative_to(path: Path, root: Path | None) -> str:
    if root is None:
        return path.name
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _relative_path(path: Path, root: Path) -> Path:
    try:
        return path.relative_to(root)
    except ValueError:
        return Path(path.name)


def _same_volume(left: Path, right: Path) -> bool:
    """两个路径是否在同一个卷上（硬链接的前提）。"""
    left_drive = Path(left).drive
    right_drive = Path(right).drive
    if left_drive and right_drive:
        return left_drive.lower() == right_drive.lower()
    try:
        return os.stat(left).st_dev == os.stat(right).st_dev
    except OSError:
        return False


def _link_or_copy(source: Path, destination: Path) -> str:
    """优先硬链接（同盘零额外占用），失败退回复制。"""
    try:
        os.link(source, destination)
        return DEPLOY_HARDLINK
    except (OSError, NotImplementedError):
        # 跨卷、exFAT/FAT32、权限不足等都会走到这里
        shutil.copy2(source, destination)
        return DEPLOY_COPY


def _detect_deploy_mode(library_files: list[ModFile], deployed_files: list[ModFile]) -> str:
    """判定游戏目录那份到底是硬链接还是复制。"""
    if not library_files or not deployed_files:
        return ""
    by_relative = {f.relative: f for f in library_files}
    linked = 0
    total = 0
    for deployed in deployed_files:
        source = by_relative.get(deployed.relative)
        if source is None:
            continue
        total += 1
        if _is_hardlinked(source.path, deployed.path):
            linked += 1
    if total == 0:
        return ""
    return _mode_from_counts(linked, total)


def _mode_from_counts(linked: int, total: int) -> str:
    if total == 0:
        return ""
    if linked == total:
        return DEPLOY_HARDLINK
    if linked == 0:
        return DEPLOY_COPY
    return DEPLOY_MIXED


def _is_hardlinked(left: Path, right: Path) -> bool:
    try:
        a = left.stat()
        b = right.stat()
    except OSError:
        return False
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def _read_legacy_source(folder: Path) -> Path | None:
    marker = folder / _LEGACY_MARKER
    if not marker.is_file():
        return None
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    raw = data.get("source_archive") if isinstance(data, dict) else None
    return Path(raw) if raw else None


def _sanitize_name(name: str) -> str:
    """把 Mod 名整理成安全的目录名。"""
    cleaned = "".join(ch for ch in name if ch not in '<>:"/\\|?*').strip(" .")
    return cleaned or "未命名Mod"


def _unique_dir(path: Path) -> Path:
    """路径空闲就原样返回，否则追加 ``(2)``、``(3)``…

    注意不能直接从 ``(2)`` 开始试：那样每个 Mod 都会被存成 ``Xxx (2)``。
    """
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.name} ({index})")
        if not candidate.exists():
            return candidate
    raise LibraryError(f"无法为 {path} 找到可用的目录名")


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _safe_mtime(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).astimezone()
    except OSError:
        return None


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _prune_empty_dirs(root: Path) -> None:
    """删掉 ``root`` 下的空目录（但保留 root 本身）。"""
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass


def _remove_tree(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists():
        try:
            path.unlink()
        except OSError:
            pass
