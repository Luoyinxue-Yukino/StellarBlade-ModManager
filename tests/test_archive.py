"""压缩包引擎测试：格式判定、路径净化、解压正确性与安全性。"""

from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path

import pytest

from stellar_mod_manager.core import archive

# ---------------------------------------------------------------------------
# 夹具：现场造出各种压缩包
# ---------------------------------------------------------------------------


@pytest.fixture
def payload_dir(tmp_path: Path) -> Path:
    """模拟一个标准 Mod 的目录结构。"""
    root = tmp_path / "payload" / "SB" / "Content" / "Paks" / "~mods" / "MyMod"
    root.mkdir(parents=True)
    (root / "MyMod.pak").write_bytes(b"PAK" * 500)
    (root / "MyMod.utoc").write_bytes(b"UTOC" * 100)
    (root / "MyMod.ucas").write_bytes(b"UCAS" * 200)
    (root / "readme.txt").write_text("hello", encoding="utf-8")
    return tmp_path / "payload"


@pytest.fixture
def mod_content_dir(payload_dir: Path) -> Path:
    """Mod 内容本体（会被打进压缩包的那个目录）。"""
    return payload_dir / "SB" / "Content" / "Paks" / "~mods" / "MyMod"


@pytest.fixture
def zip_archive(tmp_path: Path, payload_dir: Path) -> Path:
    target = tmp_path / "MyMod.zip"
    with zipfile.ZipFile(target, "w") as zf:
        for path in sorted(payload_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(payload_dir).as_posix())
    return target


@pytest.fixture
def sevenzip_archive(tmp_path: Path, mod_content_dir: Path) -> Path:
    py7zr = pytest.importorskip("py7zr")
    target = tmp_path / "MyMod.7z"
    with py7zr.SevenZipFile(target, "w") as archive_file:
        archive_file.writeall(mod_content_dir, "SB/Content/Paks/~mods/MyMod")
    return target


@pytest.fixture
def tar_archive(tmp_path: Path, mod_content_dir: Path) -> Path:
    target = tmp_path / "MyMod.tar.gz"
    with tarfile.open(target, "w:gz") as tf:
        tf.add(mod_content_dir, arcname="SB/Content/Paks/~mods/MyMod")
    return target


# ---------------------------------------------------------------------------
# 路径净化
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a/b/c.pak", "a/b/c.pak"),
        ("a\\b\\c.pak", "a/b/c.pak"),
        ("./a/./b.pak", "a/b.pak"),
        ("a//b.pak", "a/b.pak"),
        ("目录/文件.pak", "目录/文件.pak"),
        ("~mods/MyMod.pak", "~mods/MyMod.pak"),
    ],
)
def test_safe_relative_path_accepts(raw: str, expected: str) -> None:
    assert archive.safe_relative_path(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "../evil.txt",
        "a/../../evil.txt",
        "..\\evil.txt",
        "a/b/../../../evil.txt",
        "",
        "   ",
        "a/CON.txt",
        "NUL",
        "a/b.pak:stream",
        "a/b<c>.pak",
        "a/b|c.pak",
        # 绝对路径一律拒绝，而不是静默改写成相对路径
        "/abs/path.pak",
        "C:\\Games\\mod.pak",
        "C:/Games/mod.pak",
        "\\\\server\\share\\mod.pak",
    ],
)
def test_safe_relative_path_rejects(raw: str) -> None:
    assert archive.safe_relative_path(raw) is None


# ---------------------------------------------------------------------------
# 格式判定
# ---------------------------------------------------------------------------


def test_detect_format_zip(zip_archive: Path) -> None:
    assert archive.detect_format(zip_archive) is archive.ArchiveFormat.ZIP


def test_detect_format_7z(sevenzip_archive: Path) -> None:
    assert archive.detect_format(sevenzip_archive) is archive.ArchiveFormat.SEVEN_ZIP


def test_detect_format_tar(tar_archive: Path) -> None:
    assert archive.detect_format(tar_archive) is archive.ArchiveFormat.TAR


def test_detect_format_ignores_wrong_extension(tmp_path: Path, zip_archive: Path) -> None:
    """扩展名撒谎时应以魔术字节为准。"""
    liar = tmp_path / "actually_a_zip.7z"
    liar.write_bytes(zip_archive.read_bytes())
    assert archive.detect_format(liar) is archive.ArchiveFormat.ZIP


def test_detect_format_unknown(tmp_path: Path) -> None:
    junk = tmp_path / "junk.dat"
    junk.write_bytes(b"not an archive at all")
    assert archive.detect_format(junk) is archive.ArchiveFormat.UNKNOWN
    assert not archive.is_supported_archive(junk)


def test_fake_zip_extension_reports_corruption(tmp_path: Path) -> None:
    """扩展名声称是 zip 但内容不是：应给出「已损坏」而不是「无法识别」。"""
    liar = tmp_path / "fake.zip"
    liar.write_bytes(b"definitely not a zip file, just plain text")

    info = archive.inspect_archive(liar)
    assert not info.ok
    assert "损坏" in (info.error or "")

    with pytest.raises(archive.ArchiveError):
        archive.extract_archive(liar, tmp_path / "out_fake")


# ---------------------------------------------------------------------------
# 探测
# ---------------------------------------------------------------------------


def test_inspect_zip_reports_payloads(zip_archive: Path) -> None:
    info = archive.inspect_archive(zip_archive)

    assert info.ok
    assert info.archive_format is archive.ArchiveFormat.ZIP
    assert info.file_count == 4
    assert info.total_size > 0
    assert len(info.payload_entries) == 3
    assert info.has_payload
    assert not info.encrypted
    assert not info.unsafe_entries


def test_inspect_missing_file(tmp_path: Path) -> None:
    info = archive.inspect_archive(tmp_path / "nope.zip")
    assert not info.ok
    assert info.error


# ---------------------------------------------------------------------------
# 解压
# ---------------------------------------------------------------------------


def test_extract_zip_roundtrip(zip_archive: Path, tmp_path: Path) -> None:
    dest = tmp_path / "out"
    result = archive.extract_archive(zip_archive, dest)

    assert result.files_written == 4
    assert result.bytes_written > 0
    assert not result.skipped
    assert (dest / "SB/Content/Paks/~mods/MyMod/MyMod.pak").read_bytes() == b"PAK" * 500
    assert (dest / "SB/Content/Paks/~mods/MyMod/readme.txt").read_text(encoding="utf-8") == "hello"


def test_extract_7z_roundtrip(sevenzip_archive: Path, tmp_path: Path) -> None:
    dest = tmp_path / "out7z"
    result = archive.extract_archive(sevenzip_archive, dest)

    assert result.files_written == 4
    assert (dest / "SB/Content/Paks/~mods/MyMod/MyMod.utoc").read_bytes() == b"UTOC" * 100


def test_extract_tar_roundtrip(tar_archive: Path, tmp_path: Path) -> None:
    dest = tmp_path / "outtar"
    result = archive.extract_archive(tar_archive, dest)

    assert result.files_written == 4
    assert (dest / "SB/Content/Paks/~mods/MyMod/MyMod.ucas").read_bytes() == b"UCAS" * 200


def test_extract_reports_progress(zip_archive: Path, tmp_path: Path) -> None:
    seen: list[tuple[int, int, str]] = []
    archive.extract_archive(
        zip_archive, tmp_path / "out", progress=lambda d, t, m: seen.append((d, t, m))
    )

    assert seen
    assert seen[-1][0] == seen[-1][1]  # 最后一条进度应到达总数
    assert all(total == 4 for _, total, _ in seen)


# ---------------------------------------------------------------------------
# 安全性
# ---------------------------------------------------------------------------


def test_extract_zip_blocks_path_traversal(tmp_path: Path) -> None:
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../escaped.txt", "pwned")
        zf.writestr("good/ok.txt", "fine")

    dest = tmp_path / "sandbox" / "out"
    result = archive.extract_archive(evil, dest)

    assert not (tmp_path / "sandbox" / "escaped.txt").exists()
    assert not (tmp_path / "escaped.txt").exists()
    assert "../escaped.txt" in result.skipped
    assert (dest / "good/ok.txt").read_text(encoding="utf-8") == "fine"
    assert result.files_written == 1


def test_extract_zip_blocks_absolute_and_streams(tmp_path: Path) -> None:
    evil = tmp_path / "evil2.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("C:/Windows/System32/evil.dll", "x")
        zf.writestr("ok/safe.txt:ads", "x")
        zf.writestr("ok/real.txt", "y")

    dest = tmp_path / "out2"
    result = archive.extract_archive(evil, dest)

    assert result.files_written == 1
    assert (dest / "ok/real.txt").exists()
    assert len(result.skipped) == 2


def test_extract_tar_skips_symlinks(tmp_path: Path) -> None:
    evil = tmp_path / "evil.tar"
    with tarfile.open(evil, "w") as tf:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tf.addfile(info)
        data = b"real content"
        regular = tarfile.TarInfo("real.txt")
        regular.size = len(data)
        import io

        tf.addfile(regular, io.BytesIO(data))

    dest = tmp_path / "out3"
    result = archive.extract_archive(evil, dest)

    assert "link" in result.skipped
    assert not (dest / "link").exists()
    assert (dest / "real.txt").read_bytes() == b"real content"


def test_inspect_flags_unsafe_entries(tmp_path: Path) -> None:
    evil = tmp_path / "evil3.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../../boom.txt", "x")
        zf.writestr("fine.txt", "y")

    info = archive.inspect_archive(evil)
    assert info.ok
    assert "../../boom.txt" in info.unsafe_entries
    assert info.file_count == 1


# ---------------------------------------------------------------------------
# 取消
# ---------------------------------------------------------------------------


def test_extract_cancels_and_cleans_up(tmp_path: Path) -> None:
    big = tmp_path / "big.zip"
    with zipfile.ZipFile(big, "w") as zf:
        for index in range(20):
            zf.writestr(f"file{index:02d}.pak", b"Z" * 4096)

    dest = tmp_path / "out_cancel"
    state = {"calls": 0}

    def cancel() -> bool:
        state["calls"] += 1
        return state["calls"] > 3

    with pytest.raises(archive.ExtractionCancelled):
        archive.extract_archive(big, dest, cancel=cancel)

    # 取消后不应留下半成品
    assert not list(dest.rglob("*")) or not any(p.is_file() for p in dest.rglob("*"))


# ---------------------------------------------------------------------------
# 结果分析
# ---------------------------------------------------------------------------


def test_find_mod_files_and_guess_name(tmp_path: Path, payload_dir: Path) -> None:
    files = archive.find_mod_files(payload_dir)
    # 只认 .pak/.utoc/.ucas/.sig，readme.txt 不算
    assert len(files) == 3
    assert {f.suffix for f in files} == {".pak", ".utoc", ".ucas"}

    assert archive.guess_mod_name("SomeMod_v1.2.zip") == "SomeMod_v1.2"
    assert archive.guess_mod_name("x.zip", root=payload_dir) == "MyMod"
    assert archive.find_mod_files(tmp_path / "does_not_exist") == []
