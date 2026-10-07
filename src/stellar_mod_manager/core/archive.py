"""压缩包读取与解压。

支持格式
--------
================  ==================  ==============================
格式              依赖                说明
================  ==================  ==============================
``.zip``          标准库 ``zipfile``  逐文件解压，进度精确
``.tar`` 系       标准库 ``tarfile``  含 ``.tar.gz/.tgz/.bz2/.xz``
``.7z``           ``py7zr``           整体解压，尽力回报进度
``.rar``          ``rarfile``         需要系统内有 unrar/7z/bsdtar
================  ==================  ==============================

安全策略
--------
1. **拒绝路径穿越**：含 ``..``、绝对路径、Windows 盘符、NTFS 数据流（``:``）或
   保留设备名（``CON``、``NUL``…）的条目一律跳过并记入 ``skipped``；
2. **拒绝链接**：tar 中的符号链接/硬链接/设备文件不解压；
3. **不信任扩展名**：格式以魔术字节判定，扩展名只作兜底。
"""

from __future__ import annotations

import re
import shutil
import tarfile
import time
import zipfile
from collections.abc import Callable
from pathlib import Path

from .models import (
    MOD_SUFFIXES,
    ArchiveEntry,
    ArchiveFormat,
    ArchiveInspection,
    ExtractionResult,
)

from ..logging_setup import get_logger
logger = get_logger(__name__)

#: 界面上允许选择的后缀。
SUPPORTED_SUFFIXES: tuple[str, ...] = (
    ".zip",
    ".7z",
    ".rar",
    ".tar",
    ".gz",
    ".tgz",
    ".bz2",
    ".tbz2",
    ".xz",
    ".txz",
)

#: 进度回调：``(已完成, 总数, 当前项)``。
ProgressFn = Callable[[int, int, str], None]
#: 取消回调：返回 ``True`` 表示应尽快中止。
CancelFn = Callable[[], bool]

_COPY_CHUNK = 256 * 1024

_EXTENSION_FORMATS: dict[str, ArchiveFormat] = {
    ".zip": ArchiveFormat.ZIP,
    ".7z": ArchiveFormat.SEVEN_ZIP,
    ".rar": ArchiveFormat.RAR,
    ".tar": ArchiveFormat.TAR,
    ".gz": ArchiveFormat.TAR,
    ".tgz": ArchiveFormat.TAR,
    ".bz2": ArchiveFormat.TAR,
    ".tbz2": ArchiveFormat.TAR,
    ".xz": ArchiveFormat.TAR,
    ".txz": ArchiveFormat.TAR,
}

_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")
_ILLEGAL_CHARS = frozenset('<>"|?*')
_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


class ArchiveError(RuntimeError):
    """压缩包无法读取或解压。"""


class ExtractionCancelled(Exception):
    """调用方请求中止解压。"""


# ---------------------------------------------------------------------------
# 格式判定
# ---------------------------------------------------------------------------


def detect_format(path: Path | str) -> ArchiveFormat:
    """判定压缩包格式：优先看魔术字节，失败再退回扩展名。"""
    target = Path(path)
    sniffed = _sniff_format(target)
    if sniffed is not ArchiveFormat.UNKNOWN:
        return sniffed
    return _EXTENSION_FORMATS.get(target.suffix.lower(), ArchiveFormat.UNKNOWN)


def _sniff_format(path: Path) -> ArchiveFormat:
    try:
        with open(path, "rb") as handle:
            head = handle.read(512)
    except OSError:
        return ArchiveFormat.UNKNOWN

    if not head:
        return ArchiveFormat.UNKNOWN
    if head[:8] == b"Rar!\x1a\x07\x01\x00" or head[:7] == b"Rar!\x1a\x07\x00":
        return ArchiveFormat.RAR
    if head[:6] == b"7z\xbc\xaf\x27\x1c":
        return ArchiveFormat.SEVEN_ZIP
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return ArchiveFormat.ZIP
    if len(head) >= 262 and head[257:262] == b"ustar":
        return ArchiveFormat.TAR
    # gzip / bzip2 / xz 都交给 tarfile 处理
    if head[:2] == b"\x1f\x8b" or head[:3] == b"BZh" or head[:6] == b"\xfd7zXZ\x00":
        return ArchiveFormat.TAR
    return ArchiveFormat.UNKNOWN


def is_supported_archive(path: Path | str) -> bool:
    return detect_format(path) is not ArchiveFormat.UNKNOWN


# ---------------------------------------------------------------------------
# 条目路径净化
# ---------------------------------------------------------------------------


def safe_relative_path(name: str) -> str | None:
    """把压缩包内的条目名规范化为安全的相对 POSIX 路径。

    不安全（可能逃出目标目录）时返回 ``None``。

    绝对路径（``/x``、``C:\\x``、``\\\\server\\share``）一律**拒绝**而不是静默
    改写——Mod 压缩包不应该出现绝对路径，出现即说明归档本身可疑。
    """
    if not name:
        return None

    text = name.replace("\\", "/").strip()
    if not text:
        return None

    # 绝对路径 / 盘符 / UNC
    if text.startswith("/") or _DRIVE_PREFIX_RE.match(text):
        return None

    parts: list[str] = []
    for raw in text.split("/"):
        part = raw.strip()
        if part in ("", "."):
            continue
        if part == "..":
            return None
        if _ILLEGAL_CHARS & set(part) or ":" in part:
            return None
        if part.split(".")[0].upper() in _RESERVED_NAMES:
            return None
        # 去掉结尾的点与空格（Windows 会静默裁剪，可能造成覆盖）
        part = part.rstrip(" .")
        if not part:
            continue
        parts.append(part)

    return "/".join(parts) if parts else None


# ---------------------------------------------------------------------------
# 只读探测
# ---------------------------------------------------------------------------


def inspect_archive(path: Path | str, *, password: str | None = None) -> ArchiveInspection:
    """读取条目清单，不落盘。任何异常都收敛到 ``result.error``。"""
    target = Path(path)
    fmt = detect_format(target)
    inspection = ArchiveInspection(path=target, archive_format=fmt)

    if fmt is ArchiveFormat.UNKNOWN:
        inspection.error = "无法识别的压缩包格式"
        return inspection
    if not target.is_file():
        inspection.error = "文件不存在"
        return inspection

    try:
        raw_entries, encrypted = _raw_entries(target, fmt, password)
    except ArchiveError as exc:
        inspection.error = str(exc)
        return inspection
    except Exception as exc:  # noqa: BLE001 - 第三方库异常类型五花八门
        logger.warning("读取 %s 失败: %s", target, exc)
        inspection.error = f"读取压缩包失败：{exc}"
        return inspection

    inspection.encrypted = encrypted
    for name, size, is_dir, is_link in raw_entries:
        safe = safe_relative_path(name)
        # 链接条目同样视为不安全：它可以指向目标目录之外
        if safe is None or is_link:
            inspection.unsafe_entries.append(name)
            continue
        inspection.entries.append(ArchiveEntry(path=safe, size=size, is_dir=is_dir))
    return inspection


def _raw_entries(
    path: Path, fmt: ArchiveFormat, password: str | None
) -> tuple[list[tuple[str, int, bool, bool]], bool]:
    """返回 ``([(名字, 大小, 是否目录, 是否链接)], 是否加密)``。"""
    if fmt is ArchiveFormat.ZIP:
        return _raw_entries_zip(path)
    if fmt is ArchiveFormat.SEVEN_ZIP:
        return _raw_entries_7z(path, password)
    if fmt is ArchiveFormat.RAR:
        return _raw_entries_rar(path, password)
    if fmt is ArchiveFormat.TAR:
        return _raw_entries_tar(path)
    raise ArchiveError(f"不支持的格式：{fmt}")


def _zip_is_link(info: zipfile.ZipInfo) -> bool:
    """判断 ZIP 条目是否为 Unix 符号链接（存放于高 16 位的外部属性中）。"""
    return (info.external_attr >> 16) & 0xF000 == 0xA000


def _raw_entries_zip(path: Path) -> tuple[list[tuple[str, int, bool, bool]], bool]:
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            encrypted = any(info.flag_bits & 0x1 for info in infos)
            entries = [
                (i.filename, i.file_size, i.is_dir(), _zip_is_link(i)) for i in infos
            ]
            return entries, encrypted
    except zipfile.BadZipFile as exc:
        raise ArchiveError("ZIP 文件已损坏或不是有效的压缩包") from exc


def _raw_entries_7z(
    path: Path, password: str | None
) -> tuple[list[tuple[str, int, bool, bool]], bool]:
    py7zr = _require("py7zr", "需要安装 py7zr 才能读取 7z 压缩包（uv sync）")
    try:
        with py7zr.SevenZipFile(path, mode="r", password=password) as archive:
            infos = archive.list()
            encrypted = bool(getattr(archive, "needs_password", lambda: False)())
            entries = [
                (
                    i.filename,
                    int(getattr(i, "uncompressed", 0) or 0),
                    bool(i.is_directory),
                    bool(getattr(i, "is_symlink", False)),
                )
                for i in infos
            ]
        return entries, encrypted
    except py7zr.exceptions.PasswordRequired as exc:
        raise ArchiveError("7z 压缩包已加密，需要密码") from exc
    except py7zr.exceptions.Bad7zFile as exc:
        raise ArchiveError("7z 文件已损坏") from exc


def _raw_entries_rar(
    path: Path, password: str | None
) -> tuple[list[tuple[str, int, bool, bool]], bool]:
    rarfile = _require("rarfile", "需要安装 rarfile 才能读取 RAR 压缩包（uv sync）")
    _ensure_rar_tool(rarfile)
    try:
        with rarfile.RarFile(path) as archive:
            encrypted = bool(archive.needs_password())
            entries = [
                (
                    i.filename,
                    int(i.file_size),
                    bool(i.isdir()),
                    bool(i.is_symlink()),
                )
                for i in archive.infolist()
            ]
        return entries, encrypted
    except rarfile.PasswordRequired as exc:
        raise ArchiveError("RAR 压缩包已加密，需要密码") from exc
    except rarfile.BadRarFile as exc:
        raise ArchiveError("RAR 文件已损坏") from exc


def _raw_entries_tar(path: Path) -> tuple[list[tuple[str, int, bool, bool]], bool]:
    try:
        with tarfile.open(path, "r:*") as archive:
            members = archive.getmembers()
            entries = [
                (m.name, int(m.size), m.isdir(), m.issym() or m.islnk() or m.isdev())
                for m in members
            ]
        return entries, False
    except tarfile.TarError as exc:
        raise ArchiveError("TAR 归档已损坏") from exc


def _require(module_name: str, hint: str):
    try:
        return __import__(module_name)
    except ImportError as exc:
        raise ArchiveError(hint) from exc


def _ensure_rar_tool(rarfile) -> None:
    """RAR 是专有格式，rarfile 必须借助外部程序。"""
    try:
        rarfile.tool_setup()
    except Exception as exc:  # noqa: BLE001
        raise ArchiveError(
            "系统里找不到可解 RAR 的程序。请安装 WinRAR / 7-Zip，"
            "或把压缩包改存为 .zip / .7z 后再导入。"
        ) from exc


# ---------------------------------------------------------------------------
# 解压
# ---------------------------------------------------------------------------


def extract_archive(
    path: Path | str,
    destination: Path | str,
    *,
    progress: ProgressFn | None = None,
    cancel: CancelFn | None = None,
    password: str | None = None,
    overwrite: bool = True,
) -> ExtractionResult:
    """把压缩包解压到 ``destination``。

    ``destination`` 应当是（但不要求是）空目录：只有当它原本为空时，
    ``files_written`` 才等于解压出的文件总数。
    """
    source = Path(path)
    dest = Path(destination)
    started = time.monotonic()

    fmt = detect_format(source)
    if fmt is ArchiveFormat.UNKNOWN:
        raise ArchiveError(f"无法识别的压缩包格式：{source.name}")
    if not source.is_file():
        raise ArchiveError(f"文件不存在：{source}")

    dest.mkdir(parents=True, exist_ok=True)
    before = _snapshot(dest)

    result = ExtractionResult(source=source, destination=dest)

    try:
        if fmt is ArchiveFormat.ZIP:
            _extract_zip(source, dest, result, progress, cancel, password, overwrite)
        elif fmt is ArchiveFormat.TAR:
            _extract_tar(source, dest, result, progress, cancel, overwrite)
        elif fmt is ArchiveFormat.SEVEN_ZIP:
            _extract_7z(source, dest, result, progress, cancel, password)
        elif fmt is ArchiveFormat.RAR:
            _extract_rar(source, dest, result, progress, cancel, password)
    except ExtractionCancelled:
        _cleanup_partial(dest, before)
        raise

    created = _snapshot(dest) - before
    result.files_written = len(created)
    result.bytes_written = sum(_safe_size(p) for p in created)
    result.duration_s = time.monotonic() - started
    return result


def _extract_zip(
    source: Path,
    dest: Path,
    result: ExtractionResult,
    progress: ProgressFn | None,
    cancel: CancelFn | None,
    password: str | None,
    overwrite: bool,
) -> None:
    pwd = password.encode("utf-8") if password else None
    try:
        archive = zipfile.ZipFile(source)
    except zipfile.BadZipFile as exc:
        raise ArchiveError("ZIP 文件已损坏或不是有效的压缩包") from exc

    with archive:
        infos = archive.infolist()
        total = len(infos)
        _notify(progress, 0, total, "准备解压…")

        for index, info in enumerate(infos, start=1):
            _check_cancel(cancel)
            safe = safe_relative_path(info.filename)
            if safe is None or _zip_is_link(info):
                result.skipped.append(info.filename)
                _notify(progress, index, total, info.filename)
                continue

            target = dest / safe
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                _notify(progress, index, total, safe)
                continue

            if target.exists() and not overwrite:
                result.skipped.append(safe)
                _notify(progress, index, total, safe)
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with archive.open(info, pwd=pwd) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst, _COPY_CHUNK)
            except RuntimeError as exc:
                # 通常是「密码错误」或「需要密码」
                raise ArchiveError(f"{safe} 解压失败：{exc}") from exc
            _notify(progress, index, total, safe)


def _extract_tar(
    source: Path,
    dest: Path,
    result: ExtractionResult,
    progress: ProgressFn | None,
    cancel: CancelFn | None,
    overwrite: bool,
) -> None:
    try:
        archive = tarfile.open(source, "r:*")
    except tarfile.TarError as exc:
        raise ArchiveError("TAR 归档已损坏") from exc

    with archive:
        members = archive.getmembers()
        total = len(members)
        _notify(progress, 0, total, "准备解压…")

        for index, member in enumerate(members, start=1):
            _check_cancel(cancel)
            safe = safe_relative_path(member.name)

            # 链接与设备文件一律跳过：它们能间接写到目标目录之外
            if safe is None or member.issym() or member.islnk() or member.isdev():
                result.skipped.append(member.name)
                _notify(progress, index, total, member.name)
                continue

            target = dest / safe
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                _notify(progress, index, total, safe)
                continue
            if not member.isfile():
                result.skipped.append(member.name)
                _notify(progress, index, total, member.name)
                continue
            if target.exists() and not overwrite:
                result.skipped.append(safe)
                _notify(progress, index, total, safe)
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            extracted = archive.extractfile(member)
            if extracted is None:
                result.skipped.append(member.name)
                _notify(progress, index, total, member.name)
                continue
            with extracted, open(target, "wb") as dst:
                shutil.copyfileobj(extracted, dst, _COPY_CHUNK)
            _notify(progress, index, total, safe)


def _extract_7z(
    source: Path,
    dest: Path,
    result: ExtractionResult,
    progress: ProgressFn | None,
    cancel: CancelFn | None,
    password: str | None,
) -> None:
    py7zr = _require("py7zr", "需要安装 py7zr 才能解压 7z 压缩包（uv sync）")
    _check_cancel(cancel)
    total = 1

    try:
        with py7zr.SevenZipFile(source, mode="r", password=password) as archive:
            infos = archive.list()
            safe_names: list[str] = []
            for info in infos:
                # 链接条目会指向目标目录之外，一律不还原
                if safe_relative_path(info.filename) is None or getattr(
                    info, "is_symlink", False
                ):
                    result.skipped.append(info.filename)
                else:
                    safe_names.append(info.filename)

            total = max(1, len(safe_names))
            _notify(progress, 0, total, "准备解压…")
            callback = _make_7z_callback(progress, total) if progress else None
            everything_safe = len(safe_names) == len(infos)

            if everything_safe:
                # py7zr 1.x: extractall(path, *, callback=..., factory=...)
                archive.extractall(path=str(dest), callback=callback)
            else:
                # 需要挑条目时只能走 extract()，它同时支持 targets
                archive.extract(path=str(dest), targets=safe_names)
    except py7zr.exceptions.PasswordRequired as exc:
        raise ArchiveError("7z 压缩包已加密，需要密码") from exc
    except py7zr.exceptions.Bad7zFile as exc:
        raise ArchiveError("7z 文件已损坏") from exc

    _notify(progress, total, total, "完成")


def _make_7z_callback(progress: ProgressFn, total: int):
    """构造 py7zr 的进度回调；接口在不同版本间有差异，这里做防御性适配。"""
    try:
        from py7zr.callbacks import ExtractCallback
    except Exception:  # noqa: BLE001 - 版本过旧或结构变化
        return None

    class _Callback(ExtractCallback):
        def __init__(self) -> None:
            self._done = 0

        def report_start_preparation(self) -> None:  # noqa: D102
            pass

        def report_start(self, *args, **kwargs) -> None:  # noqa: D102
            pass

        def report_update(self, *args, **kwargs) -> None:  # noqa: D102
            pass

        def report_end(self, processing_file_path: str = "", *args, **kwargs) -> None:  # noqa: D102
            self._done += 1
            progress(self._done, total, str(processing_file_path))

        def report_warning(self, message: str = "", *args, **kwargs) -> None:  # noqa: D102
            logger.warning("7z: %s", message)

        def report_postprocess(self) -> None:  # noqa: D102
            pass

    try:
        return _Callback()
    except TypeError:  # pragma: no cover - 抽象基类签名变化
        return None


def _extract_rar(
    source: Path,
    dest: Path,
    result: ExtractionResult,
    progress: ProgressFn | None,
    cancel: CancelFn | None,
    password: str | None,
) -> None:
    rarfile = _require("rarfile", "需要安装 rarfile 才能解压 RAR 压缩包（uv sync）")
    _ensure_rar_tool(rarfile)
    _check_cancel(cancel)

    kwargs: dict[str, object] = {}
    if password:
        kwargs["pwd"] = password

    total = 1
    try:
        with rarfile.RarFile(source) as archive:
            members = []
            for info in archive.infolist():
                if safe_relative_path(info.filename) is None:
                    result.skipped.append(info.filename)
                else:
                    members.append(info)

            total = max(1, len(members))
            _notify(progress, 0, total, "准备解压…")
            archive.extractall(path=str(dest), members=members, **kwargs)
    except rarfile.PasswordRequired as exc:
        raise ArchiveError("RAR 压缩包已加密，需要密码") from exc
    except rarfile.BadRarFile as exc:
        raise ArchiveError("RAR 文件已损坏") from exc

    _notify(progress, total, total, "完成")


# ---------------------------------------------------------------------------
# 解压结果分析
# ---------------------------------------------------------------------------


def find_mod_files(root: Path | str) -> list[Path]:
    """递归收集目录下所有 Mod 相关文件（``.pak/.utoc/.ucas/.sig``）。"""
    base = Path(root)
    if not base.is_dir():
        return []
    return [
        p
        for p in sorted(base.rglob("*"))
        if p.is_file() and p.suffix.lower() in MOD_SUFFIXES
    ]


def guess_mod_name(archive_path: Path | str, root: Path | str | None = None) -> str:
    """猜一个像样的 Mod 名：优先压缩包内 ``~mods`` 下的子目录名，其次压缩包文件名。"""
    if root is not None:
        base = Path(root)
        tilde_dirs = [
            d for d in base.rglob("*") if d.is_dir() and d.name.lower() == "~mods"
        ]
        for tilde in tilde_dirs:
            children = [c for c in sorted(tilde.iterdir()) if c.is_dir()]
            if len(children) == 1:
                return children[0].name

    stem = Path(archive_path).stem
    # 去掉常见的下载站后缀，如 MyMod_v1.2_fix
    return stem or "未命名Mod"


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _snapshot(root: Path) -> set[Path]:
    if not root.is_dir():
        return set()
    return {p for p in root.rglob("*") if p.is_file()}


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _notify(progress: ProgressFn | None, done: int, total: int, message: str) -> None:
    if progress is not None:
        try:
            progress(done, total, message)
        except Exception:  # noqa: BLE001 - 进度回调不该拖垮解压
            logger.debug("进度回调抛出异常", exc_info=True)


def _check_cancel(cancel: CancelFn | None) -> None:
    if cancel is not None and cancel():
        raise ExtractionCancelled


def _cleanup_partial(dest: Path, before: set[Path]) -> None:
    """取消时删掉本次已写出的文件，避免留下半个 Mod。"""
    for path in _snapshot(dest) - before:
        try:
            path.unlink()
        except OSError:
            pass
