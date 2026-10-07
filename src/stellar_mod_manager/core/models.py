"""领域数据模型。

这里只放纯数据结构，不含任何 Qt 或文件系统副作用，方便单元测试与复用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path

# ---------------------------------------------------------------------------
# 有效载荷（Payload）
# ---------------------------------------------------------------------------


class PayloadKind(StrEnum):
    """《剑星》基于虚幻引擎，Mod 的有效载荷是资源包文件。"""

    PAK = "pak"
    UTOC = "utoc"
    UCAS = "ucas"
    SIG = "sig"
    OTHER = "other"

    @classmethod
    def from_suffix(cls, suffix: str) -> "PayloadKind":
        key = suffix.lower().lstrip(".")
        try:
            return cls(key)
        except ValueError:
            return cls.OTHER

    @property
    def is_payload(self) -> bool:
        """``.pak`` / ``.utoc`` / ``.ucas`` 才是真正会被游戏加载的文件。"""
        return self in (PayloadKind.PAK, PayloadKind.UTOC, PayloadKind.UCAS)


#: 会被游戏加载的后缀（小写）。
PAYLOAD_SUFFIXES: tuple[str, ...] = (".pak", ".utoc", ".ucas")
#: 会被一并复制、但不参与“是否有内容”判断的伴随后缀。
COMPANION_SUFFIXES: tuple[str, ...] = (".sig",)
#: 识别 Mod 时关心的全部后缀。
MOD_SUFFIXES: tuple[str, ...] = PAYLOAD_SUFFIXES + COMPANION_SUFFIXES


# ---------------------------------------------------------------------------
# 压缩包
# ---------------------------------------------------------------------------


class ArchiveFormat(StrEnum):
    """支持读取的压缩包格式。"""

    ZIP = "zip"
    SEVEN_ZIP = "7z"
    RAR = "rar"
    TAR = "tar"
    UNKNOWN = "unknown"

    @property
    def display_name(self) -> str:
        return {
            ArchiveFormat.ZIP: "ZIP 压缩包",
            ArchiveFormat.SEVEN_ZIP: "7-Zip 压缩包",
            ArchiveFormat.RAR: "RAR 压缩包",
            ArchiveFormat.TAR: "TAR 归档",
            ArchiveFormat.UNKNOWN: "未知格式",
        }[self]


@dataclass(frozen=True, slots=True)
class ArchiveEntry:
    """压缩包内的一个条目（目录或文件）。"""

    path: str
    """规范化的相对路径，统一使用 ``/`` 分隔，且不含 ``..`` 等越界片段。"""

    size: int
    is_dir: bool

    @property
    def suffix(self) -> str:
        return Path(self.path).suffix.lower()

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]


@dataclass(slots=True)
class ArchiveInspection:
    """``inspect_archive()`` 的结果：只读探测，不落盘。"""

    path: Path
    archive_format: ArchiveFormat
    entries: list[ArchiveEntry] = field(default_factory=list)
    unsafe_entries: list[str] = field(default_factory=list)
    encrypted: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def file_count(self) -> int:
        return sum(1 for e in self.entries if not e.is_dir)

    @property
    def total_size(self) -> int:
        return sum(e.size for e in self.entries if not e.is_dir)

    @property
    def payload_entries(self) -> list[ArchiveEntry]:
        return [e for e in self.entries if e.suffix in PAYLOAD_SUFFIXES]

    @property
    def has_payload(self) -> bool:
        return bool(self.payload_entries)


@dataclass(slots=True)
class ExtractionResult:
    """``extract_archive()`` 的结果。"""

    source: Path
    destination: Path
    files_written: int = 0
    bytes_written: int = 0
    skipped: list[str] = field(default_factory=list)
    duration_s: float = 0.0

    @property
    def ok(self) -> bool:
        return self.files_written > 0


# ---------------------------------------------------------------------------
# 已安装的 Mod
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ModFile:
    """``~mods`` 目录下的一个实际文件。"""

    path: Path
    relative: str
    size: int

    @property
    def kind(self) -> PayloadKind:
        return PayloadKind.from_suffix(self.path.suffix)

    @property
    def name(self) -> str:
        return self.path.name


@dataclass(slots=True)
class Mod:
    """一个 Mod 在「库」与「游戏目录」两处的完整状态。

    仓库模型下这两处是分开的：

    * **库**（``in_library``）是权威副本，存放全部 Mod，永远不会被游戏加载；
    * **游戏目录**（``deployed``）只放已启用的 Mod，由库部署而来。

    磁盘是唯一事实来源：本对象由扫描这两处得到，不依赖数据库，因此用户手动增删
    文件后界面依然正确。
    """

    name: str
    in_library: bool = False
    """库里是否有一份（权威副本）。"""
    deployed: bool = False
    """是否已部署进游戏目录（即「已启用」）。"""

    library_files: list[ModFile] = field(default_factory=list)
    deployed_files: list[ModFile] = field(default_factory=list)

    deployed_root: Path | None = None
    """该 Mod 在游戏目录里占据的顶层文件夹；``None`` 表示松散文件直接放在 ``~mods``。

    纳管时必须用它作为相对路径的基准，而不是「所有文件的公共父目录」——
    当 Mod 的文件都放在一层子目录里时（``~mods/X/Sub/a.pak``），后者会多剥一层，
    导致库里的目录结构与游戏目录对不上。
    """

    deploy_mode: str = ""
    """部署方式：``hardlink``（同盘，不额外占空间）/ ``copy`` / ``mixed``。"""

    source_archive: Path | None = None
    installed_at: datetime | None = None

    # -- 语义别名 --------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """界面与旧代码用「启用」表示「已部署进游戏目录」。"""
        return self.deployed

    @property
    def is_managed(self) -> bool:
        """是否已纳入库。只在游戏目录里找到的 Mod 是「未纳管」。"""
        return self.in_library

    @property
    def files(self) -> list[ModFile]:
        """代表该 Mod 内容的文件：优先库，其次游戏目录。"""
        return self.library_files or self.deployed_files

    @property
    def library_size(self) -> int:
        return sum(f.size for f in self.library_files)

    @property
    def deployed_size(self) -> int:
        return sum(f.size for f in self.deployed_files)

    @property
    def total_size(self) -> int:
        return self.library_size or self.deployed_size

    @property
    def disk_usage(self) -> int:
        """实际磁盘占用。

        硬链接部署时游戏目录那份与库共用同一份数据，**不重复计算**；
        复制部署（跨卷或文件系统不支持）才会真的多占一份。
        """
        if not self.deployed:
            return self.library_size or self.deployed_size
        if not self.in_library or self.deploy_mode != "hardlink":
            return self.library_size + self.deployed_size
        return self.library_size

    @property
    def deploy_label(self) -> str:
        return {
            "hardlink": "硬链接（不额外占空间）",
            "copy": "复制（额外占用一倍空间）",
            "mixed": "部分硬链接、部分复制",
        }.get(self.deploy_mode, "")

    # -- 内容特征 --------------------------------------------------------

    @property
    def payload_files(self) -> list[ModFile]:
        return [f for f in self.files if f.kind.is_payload]

    @property
    def primary_file(self) -> ModFile | None:
        """用于展示的代表性文件：优先 ``.pak``，其次 ``.utoc``。"""
        for kind in (PayloadKind.PAK, PayloadKind.UTOC):
            for f in self.files:
                if f.kind is kind:
                    return f
        return self.files[0] if self.files else None

    @property
    def kinds(self) -> list[PayloadKind]:
        seen: list[PayloadKind] = []
        for f in self.files:
            if f.kind not in seen:
                seen.append(f.kind)
        return seen

    @property
    def is_iostore(self) -> bool:
        """是否使用虚幻 IoStore（``.utoc`` + ``.ucas``）而非传统 ``.pak``。"""
        kinds = set(self.kinds)
        return PayloadKind.UTOC in kinds or PayloadKind.UCAS in kinds

    @property
    def format_label(self) -> str:
        if self.is_iostore:
            return "IoStore"
        if PayloadKind.PAK in self.kinds:
            return "Pak"
        return "未知"


@dataclass(slots=True)
class InstallPlan:
    """「把解压出来的东西装进游戏」的计划，安装前可先行预览。"""

    mod_name: str
    source_dir: Path
    files: list[Path] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.files)

    @property
    def total_size(self) -> int:
        return sum(f.stat().st_size for f in self.files if f.is_file())
