"""DekPakModAudit 集成：调用第三方冲突检测工具，解析并归类 Mod 冲突。

工具做什么
----------
`DekPakModAudit` 会读取游戏原版 pak 与 Mods 目录下的每个 ``.utoc``，逐条比对虚幻
资源路径，找出三类问题：

============================  ==================================================
类别                          后果
============================  ==================================================
ChunkID 冲突                  两个 Mod 占用同一 IoStore 块 ID —— **游戏可能进不去**
覆盖同一原版资源              多个 Mod 替换同一个游戏自带资源，只有一个生效
重复提供同一资源              多个 Mod 提供同名新增资源，多为同作者共用的基础资源
============================  ==================================================

调用上的两个坑（都是实测踩出来的）
----------------------------------
1. 工具用**相对路径**读 ``input/DekPakModAuditConfig.json``（里面的换算表路径也是
   相对的）并写 ``output/``，所以必须把子进程的工作目录设成工具目录；
2. 程序末尾会 ``Console.ReadKey()`` 等按键。我们没法给它真实控制台（stdin 被重定向），
   于是它抛 ``InvalidOperationException`` 并以非零码退出 —— **但此时报告早已写完**。
   因此判断成败**不能看退出码**，要看输出文件是否被刷新。
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from ..logging_setup import get_logger
logger = get_logger(__name__)

#: 工具可执行文件名。
AUDIT_EXE_NAME = "DekPakModAudit.exe"
#: 工具在游戏目录下的默认子目录名。
AUDIT_DIR_NAME = "DekPakModAudit"
#: 默认检测的 Mods 目录（相对 ``Content/Paks``）。
DEFAULT_MODS_FOLDER = "~mods"
#: 另一个 Mods 目录：UE4SS 蓝图 Mod。
LOGIC_MODS_FOLDER = "LogicMods"
#: 默认超时（秒）。实测约 60 个 Mod 只需 5 秒，给足余量。
DEFAULT_TIMEOUT_S = 300

#: 工具的获取入口，显示在「未安装检测工具」引导对话框里。
#:
#: 为什么默认为空
#: --------------
#: 本管理器**不附带也不打包** DekPakModAudit：它的发布目录里没有许可证文件，
#: 也没有可核实的源码地址，无法确认再分发授权。所以这里不预设任何地址。
#:
#: 分发者如果确认了官方发布页，把地址填在这里（或写进用户配置项
#: ``audit_download_url``），界面上的「打开下载页」按钮就会出现。
DOWNLOAD_URL = ""

#: 工具在无控制台时必然抛出的异常特征，属于预期噪声，不该当成失败。
_READKEY_NOISE = "Cannot read keys"

ProgressFn = Callable[[int, int, str], None]
CancelFn = Callable[[], bool]


class AuditError(RuntimeError):
    """检测无法进行或没有产出结果。"""


class AuditCancelled(Exception):
    """用户中止了检测。"""


# ---------------------------------------------------------------------------
# 工具定位
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolLocation:
    """DekPakModAudit 的安装位置。"""

    directory: Path
    executable: Path

    @property
    def config_path(self) -> Path:
        return self.directory / "input" / "DekPakModAuditConfig.json"

    @property
    def json_path(self) -> Path:
        return self.directory / "output" / "DekPakModAudit.json"

    @property
    def log_path(self) -> Path:
        return self.directory / "output" / "DekPakModAudit.log"

    @property
    def has_config(self) -> bool:
        """换算表配置是否存在。缺了它工具通常跑不起来。"""
        return self.config_path.is_file()

    @property
    def paks_dir(self) -> Path | None:
        """从配置里的 ``MainPaksFolder`` 解析出 ``Content/Paks`` 目录。"""
        if not self.has_config:
            return None
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return None
        raw = data.get("MainPaksFolder") if isinstance(data, dict) else None
        if not raw:
            return None
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = self.directory / candidate
        try:
            return candidate.resolve()
        except OSError:
            return None


def find_audit_tool(
    game_root: Path | str | None = None, override: str | Path | None = None
) -> ToolLocation | None:
    """定位 DekPakModAudit。

    先看用户在设置里指定的位置（可以是目录，也可以是 exe 本身），再依次找游戏目录
    下的常见位置。
    """
    for candidate in _candidate_locations(game_root, override):
        executable = _resolve_executable(candidate)
        if executable is not None:
            return ToolLocation(directory=executable.parent, executable=executable)
    return None


def _candidate_locations(
    game_root: Path | str | None, override: str | Path | None
) -> list[Path]:
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override))
    if game_root:
        root = Path(game_root)
        candidates.extend(
            (
                root / AUDIT_DIR_NAME,
                root / "SB" / "Content" / "Paks" / AUDIT_DIR_NAME,
                root / "Tools" / AUDIT_DIR_NAME,
                root / "ModTools" / AUDIT_DIR_NAME,
            )
        )
    return candidates


def _resolve_executable(candidate: Path) -> Path | None:
    """把「目录或 exe 路径」统一解析成 exe 的真实路径。"""
    if candidate.is_file() and candidate.suffix.lower() == ".exe":
        return candidate
    if not candidate.is_dir():
        return None
    # Windows 文件名大小写不敏感，逐个比对而不是硬编码大小写
    for entry in candidate.iterdir():
        if entry.is_file() and entry.name.lower() == AUDIT_EXE_NAME.lower():
            return entry
    return None


# ---------------------------------------------------------------------------
# 报告模型
# ---------------------------------------------------------------------------


class ConflictKind(StrEnum):
    """冲突类型。"""

    CHUNK_ID = "chunk_id"
    BASE_OVERRIDE = "base_override"
    SHARED_ASSET = "shared_asset"

    @property
    def label(self) -> str:
        return {
            ConflictKind.CHUNK_ID: "ChunkID 冲突",
            ConflictKind.BASE_OVERRIDE: "覆盖同一原版资源",
            ConflictKind.SHARED_ASSET: "重复提供同一资源",
        }[self]

    @property
    def color_role(self) -> str:
        """配色角色名，必须对应 :class:`~stellar_mod_manager.ui.theme.Palette` 的字段。

        核心层本不该知道界面配色，但把「严重程度 → 颜色」这层映射放在这里，
        可以避免界面层再写一遍 ``if kind is ...`` 的分支。
        """
        return {
            ConflictKind.CHUNK_ID: "danger",
            ConflictKind.BASE_OVERRIDE: "warning",
            ConflictKind.SHARED_ASSET: "accent",
        }[self]

    @property
    def is_blocking(self) -> bool:
        """是否可能导致游戏无法启动。"""
        return self is ConflictKind.CHUNK_ID

    @property
    def explanation(self) -> str:
        return {
            ConflictKind.CHUNK_ID: (
                "两个 Mod 占用了同一个 IoStore 块 ID。这会让游戏在加载资源时崩溃，"
                "典型表现就是点开始后进不去。请停用其中一个 Mod 后重新检测。"
            ),
            ConflictKind.BASE_OVERRIDE: (
                "多个 Mod 替换了同一个游戏自带资源，实际只有一个会生效，"
                "可能出现贴图或模型错乱。建议只保留想要的那一个。"
            ),
            ConflictKind.SHARED_ASSET: (
                "多个 Mod 提供了同名的新增资源。通常无害——多见于同一作者的作品"
                "共用一份基础资源。只有出现异常表现时才需要处理。"
            ),
        }[self]

    @property
    def priority(self) -> int:
        return {
            ConflictKind.CHUNK_ID: 0,
            ConflictKind.BASE_OVERRIDE: 1,
            ConflictKind.SHARED_ASSET: 2,
        }[self]


@dataclass(slots=True)
class ModAssetSet:
    """一个 ``.utoc`` 所携带的资源清单。"""

    utoc: Path
    mod_name: str = ""
    mods_folder: str = DEFAULT_MODS_FOLDER
    chunk_id: str = ""
    overrides_default: bool = False
    overridden_assets: list[str] = field(default_factory=list)
    unique_assets: list[str] = field(default_factory=list)

    @property
    def all_assets(self) -> list[str]:
        return [*self.overridden_assets, *self.unique_assets]

    @property
    def asset_count(self) -> int:
        return len(self.overridden_assets) + len(self.unique_assets)


@dataclass(slots=True)
class Conflict:
    """一处冲突：某个键（资源路径或 ChunkID）被多个 ``.utoc`` 同时占用。"""

    kind: ConflictKind
    key: str
    owners: list[Path]

    @property
    def owner_count(self) -> int:
        return len(self.owners)

    @property
    def display_key(self) -> str:
        """资源路径去掉 ``Content/`` 前缀后更易读。"""
        if self.kind is ConflictKind.CHUNK_ID:
            return f"ChunkID {self.key}"
        return self.key


@dataclass(slots=True)
class AuditReport:
    """一次检测的完整结果。"""

    tool: ToolLocation
    folders: list[str]
    started_at: datetime
    duration_s: float
    mods: list[ModAssetSet] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    raw_log: str = ""
    warnings: list[str] = field(default_factory=list)

    # -- 汇总 -----------------------------------------------------------

    @property
    def mod_count(self) -> int:
        return len({m.mod_name for m in self.mods})

    @property
    def utoc_count(self) -> int:
        return len(self.mods)

    @property
    def total_assets(self) -> int:
        return sum(m.asset_count for m in self.mods)

    @property
    def blocking_conflicts(self) -> list[Conflict]:
        return [c for c in self.conflicts if c.kind.is_blocking]

    @property
    def override_mods(self) -> list[ModAssetSet]:
        """替换了游戏自带资源的 Mod。"""
        return [m for m in self.mods if m.overrides_default]

    @property
    def override_asset_count(self) -> int:
        return sum(len(m.overridden_assets) for m in self.mods)

    @property
    def has_conflicts(self) -> bool:
        return bool(self.conflicts)

    @property
    def verdict(self) -> tuple[str, str]:
        """``(结论文案, 颜色角色)``。"""
        if self.blocking_conflicts:
            return (
                f"发现 {len(self.blocking_conflicts)} 处可能导致游戏无法启动的 ChunkID 冲突",
                "danger",
            )
        if self.has_conflicts:
            return (
                f"发现 {len(self.conflicts)} 处资源冲突，游戏可以启动但表现可能异常",
                "warning",
            )
        return ("未检测到 Mod 冲突", "success")

    def conflicts_of(self, kind: ConflictKind) -> list[Conflict]:
        return [c for c in self.conflicts if c.kind is kind]

    def owners_of(self, conflict: Conflict) -> list[str]:
        """把冲突里的 ``.utoc`` 路径映射成 Mod 名，去重后保持顺序。"""
        by_utoc = {m.utoc: m.mod_name for m in self.mods}
        names: list[str] = []
        for utoc in conflict.owners:
            name = by_utoc.get(utoc) or utoc.parent.name
            if name not in names:
                names.append(name)
        return names

    def to_text(self) -> str:
        """纯文本摘要，便于复制粘贴到求助帖。"""
        lines = [
            "《剑星》Mod 冲突检测报告",
            f"检测时间：{self.started_at:%Y-%m-%d %H:%M:%S}",
            f"检测目录：{'、'.join(self.folders)}",
            f"耗时：{self.duration_s:.1f} 秒",
            "",
            "== 统计 ==",
            f"检测到 Mod 数：{self.mod_count}",
            f"资源条目总数：{self.total_assets}",
            f"替换原版资源的 Mod 数：{len(self.override_mods)}",
            f"冲突总数：{len(self.conflicts)}",
        ]
        for kind in ConflictKind:
            lines.append(f"  - {kind.label}：{len(self.conflicts_of(kind))}")

        lines.append("")
        lines.append(f"== 结论 ==\n{self.verdict[0]}")

        if self.conflicts:
            lines.append("")
            lines.append("== 冲突明细 ==")
            for kind in ConflictKind:
                items = self.conflicts_of(kind)
                if not items:
                    continue
                lines.append("")
                lines.append(f"[{kind.label}] {kind.explanation}")
                for conflict in items:
                    lines.append(f"  * {conflict.display_key}")
                    for name in self.owners_of(conflict):
                        lines.append(f"      - {name}")

        if self.warnings:
            lines.append("")
            lines.append("== 提示 ==")
            lines.extend(f"  ! {w}" for w in self.warnings)

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 执行检测
# ---------------------------------------------------------------------------


def run_audit(
    tool: ToolLocation,
    *,
    folders: Sequence[str] = (DEFAULT_MODS_FOLDER,),
    timeout_s: int = DEFAULT_TIMEOUT_S,
    cancel: CancelFn | None = None,
    progress: ProgressFn | None = None,
) -> AuditReport:
    """运行检测并解析结果。

    ``folders`` 里每一项都是相对 ``Content/Paks`` 的目录名（如 ``~mods``、
    ``LogicMods``）。多个目录会各跑一次再合并——工具一次只认一个目录，
    但合并后才能发现跨目录的冲突。
    """
    if not tool.executable.is_file():
        raise AuditError(f"找不到检测程序：{tool.executable}")
    if not tool.has_config:
        raise AuditError(
            f"缺少配置文件 {tool.config_path.name}。\n"
            "工具依赖 input/ 目录下的换算表，请确认 DekPakModAudit 安装完整。"
        )

    started = datetime.now()
    clock = time.monotonic()

    collected: list[ModAssetSet] = []
    warnings: list[str] = []
    raw_log = ""

    targets = list(folders) or [DEFAULT_MODS_FOLDER]
    for index, folder in enumerate(targets, start=1):
        if cancel is not None and cancel():
            raise AuditCancelled
        if progress is not None:
            progress(index - 1, len(targets), f"正在检测 {folder} …")

        mods, log_text, folder_warnings = _run_once(
            tool, folder, timeout_s=timeout_s, cancel=cancel
        )
        collected.extend(mods)
        warnings.extend(folder_warnings)
        if log_text:
            raw_log = log_text if not raw_log else f"{raw_log}\n\n{log_text}"

        if progress is not None:
            progress(index, len(targets), f"{folder} 检测完成")

    report = AuditReport(
        tool=tool,
        folders=targets,
        started_at=started,
        duration_s=time.monotonic() - clock,
        mods=collected,
        conflicts=_build_conflicts(collected),
        raw_log=raw_log,
        warnings=warnings,
    )
    if not collected:
        report.warnings.append(
            "没有在检测目录下找到任何 .utoc 文件，请确认 Mod 已正确安装。"
        )
    return report


def _run_once(
    tool: ToolLocation,
    folder: str,
    *,
    timeout_s: int,
    cancel: CancelFn | None,
) -> tuple[list[ModAssetSet], str, list[str]]:
    """跑一次工具，返回 ``(资源清单, 原始日志, 警告)``。"""
    report_json = tool.json_path
    report_json.parent.mkdir(parents=True, exist_ok=True)

    before_mtime = report_json.stat().st_mtime if report_json.is_file() else 0.0

    # 输出重定向到临时文件而不是管道：管道缓冲区满了会让子进程卡死，
    # 而这里根本不需要实时读取。
    with tempfile.TemporaryFile() as out_handle, tempfile.TemporaryFile() as err_handle:
        creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            process = subprocess.Popen(  # noqa: S603 - 路径来自本地安装的工具
                [str(tool.executable), f"--modsfolder={folder}"],
                cwd=str(tool.directory),
                stdin=subprocess.DEVNULL,
                stdout=out_handle,
                stderr=err_handle,
                creationflags=creation_flags,
            )
        except OSError as exc:
            raise AuditError(f"无法启动检测程序：{exc}") from exc

        _wait_for(process, timeout_s=timeout_s, cancel=cancel)

        out_handle.seek(0)
        stdout_text = out_handle.read().decode("utf-8", errors="replace")
        err_handle.seek(0)
        stderr_text = err_handle.read().decode("utf-8", errors="replace")

    warnings: list[str] = []
    if stderr_text and _READKEY_NOISE not in stderr_text:
        # 除了末尾那个「等按键」异常之外的 stderr 才值得报告
        first_line = next(
            (line for line in stderr_text.splitlines() if line.strip()), ""
        )
        if first_line:
            warnings.append(f"检测程序输出：{first_line.strip()[:200]}")

    # 退出码不可信：工具末尾等按键失败会导致非零退出，但报告其实已经写完。
    if not report_json.is_file():
        raise AuditError(
            "检测程序没有生成报告。\n"
            "请确认 DekPakModAudit 能独立运行（工作目录需为它自己的目录）。"
        )
    if report_json.stat().st_mtime <= before_mtime:
        raise AuditError("检测没有刷新报告文件，程序可能提前退出或根本没有执行。")

    try:
        payload = json.loads(report_json.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditError(f"报告文件无法解析：{exc}") from exc

    if not isinstance(payload, dict):
        raise AuditError("报告文件结构异常：根节点不是对象")

    mods = _parse_mods(payload, tool, folder)

    # 原始日志优先取工具自己写的那份；取不到就用捕获的 stdout
    log_text = ""
    if tool.log_path.is_file():
        try:
            log_text = tool.log_path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            log_text = ""
    if not log_text:
        log_text = stdout_text

    return mods, log_text, warnings


def _wait_for(process: subprocess.Popen, *, timeout_s: int, cancel: CancelFn | None) -> None:
    """等待子进程结束，期间响应取消与超时。"""
    deadline = time.monotonic() + timeout_s
    while process.poll() is None:
        if cancel is not None and cancel():
            _terminate(process)
            raise AuditCancelled
        if time.monotonic() > deadline:
            _terminate(process)
            raise AuditError(f"检测超时（超过 {timeout_s} 秒），已强制结束。")
        time.sleep(0.15)


def _terminate(process: subprocess.Popen) -> None:
    try:
        process.kill()
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        logger.warning("无法结束检测进程 %s", process.pid)


def _parse_mods(payload: dict, tool: ToolLocation, folder: str) -> list[ModAssetSet]:
    """把报告 JSON 转成 :class:`ModAssetSet` 列表。

    只保留**属于本次被检测目录**的条目：工具每次运行都会覆盖同一份报告文件，
    万一读到的是上一次（或别的目录）的残留，按目录过滤能保证不会张冠李戴。
    """
    mods_root = _mods_root(tool, folder)
    result: list[ModAssetSet] = []

    for raw_path, info in payload.items():
        if not isinstance(info, dict):
            continue
        utoc = Path(raw_path)
        if mods_root is not None and not _is_within(utoc, mods_root):
            continue
        result.append(
            ModAssetSet(
                utoc=utoc,
                mod_name=_mod_name_for(utoc, mods_root, folder),
                mods_folder=folder,
                chunk_id=str(info.get("ChunkID") or ""),
                overrides_default=bool(info.get("OverridesDefaultAssets")),
                overridden_assets=_as_str_list(info.get("OverriddenAssets")),
                unique_assets=_as_str_list(info.get("UniqueAssets")),
            )
        )

    result.sort(key=lambda m: (m.mod_name.lower(), m.utoc.name.lower()))
    return result


def _is_within(path: Path, root: Path) -> bool:
    """路径是否位于 root 之下（大小写不敏感，Windows 下两者都可能带不同大小写）。"""
    try:
        path.relative_to(root)
    except ValueError:
        # 退一步做大小写不敏感的比较
        left = [p.lower() for p in path.parts]
        right = [p.lower() for p in root.parts]
        return left[: len(right)] == right
    return True


def _mods_root(tool: ToolLocation, folder: str) -> Path | None:
    paks = tool.paks_dir
    if paks is None:
        return None
    return paks / Path(folder)


def _mod_name_for(utoc: Path, mods_root: Path | None, folder: str) -> str:
    """推断这个 ``.utoc`` 属于哪个 Mod（与 Mod 库的归组口径保持一致）。"""
    if mods_root is not None:
        try:
            parts = utoc.relative_to(mods_root).parts
        except ValueError:
            parts = ()
        if len(parts) >= 2:
            return parts[0]
        if len(parts) == 1:
            return Path(parts[0]).stem

    # 兜底：在路径里找 Mods 目录名之后的第一段
    marker = Path(folder).parts[-1].lower()
    all_parts = utoc.parts
    for index, part in enumerate(all_parts):
        if part.lower() == marker:
            rest = all_parts[index + 1 :]
            if len(rest) >= 2:
                return rest[0]
            if rest:
                return Path(rest[0]).stem
            break
    return utoc.parent.name


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


# ---------------------------------------------------------------------------
# 冲突判定
# ---------------------------------------------------------------------------


def _build_conflicts(mods: list[ModAssetSet]) -> list[Conflict]:
    """从资源清单里推导冲突。

    判定口径与工具自身一致：某个资源路径被 **多于一个** ``.utoc`` 提供即算冲突；
    若该资源同时属于「原版资源」（出现在某个 Mod 的 ``OverriddenAssets`` 里），
    则升级为「覆盖同一原版资源」。ChunkID 重复单独成类，且是最严重的一类。
    """
    asset_owners: dict[str, list[Path]] = {}
    override_assets: set[str] = set()

    for mod in mods:
        for asset in mod.overridden_assets:
            override_assets.add(asset)
            _append_owner(asset_owners, asset, mod.utoc)
        for asset in mod.unique_assets:
            _append_owner(asset_owners, asset, mod.utoc)

    chunk_owners: dict[str, list[Path]] = {}
    for mod in mods:
        if mod.chunk_id:
            _append_owner(chunk_owners, mod.chunk_id, mod.utoc)

    conflicts: list[Conflict] = []
    for chunk_id, owners in chunk_owners.items():
        if len(owners) > 1:
            conflicts.append(Conflict(ConflictKind.CHUNK_ID, chunk_id, owners))

    for asset, owners in asset_owners.items():
        if len(owners) <= 1:
            continue
        kind = (
            ConflictKind.BASE_OVERRIDE
            if asset in override_assets
            else ConflictKind.SHARED_ASSET
        )
        conflicts.append(Conflict(kind, asset, owners))

    conflicts.sort(key=lambda c: (c.kind.priority, -c.owner_count, c.key.lower()))
    return conflicts


def _append_owner(mapping: dict[str, list[Path]], key: str, owner: Path) -> None:
    bucket = mapping.setdefault(key, [])
    if owner not in bucket:
        bucket.append(owner)
