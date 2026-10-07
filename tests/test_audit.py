"""DekPakModAudit 集成测试：工具定位、报告解析、冲突归类、异常路径。

不依赖真实工具——用合成的报告 JSON 与假进程覆盖全部逻辑分支。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from stellar_mod_manager.core import audit


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


@pytest.fixture
def tool_tree(tmp_path: Path) -> Path:
    """造出一个结构正确的工具目录。"""
    directory = tmp_path / "StellarBlade" / "DekPakModAudit"
    (directory / "input").mkdir(parents=True)
    (directory / "output").mkdir(parents=True)
    (directory / audit.AUDIT_EXE_NAME).write_bytes(b"MZ")
    (directory / "input" / "DekPakModAuditConfig.json").write_text(
        json.dumps(
            {
                "MainPaksFolder": "..\\SB\\Content\\Paks",
                "MainModsFolder": "",
                "MappingsPath": "input\\StellarBlade_1.1.0.usmap",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "StellarBlade" / "SB" / "Content" / "Paks").mkdir(parents=True)
    return directory


@pytest.fixture
def tool(tool_tree: Path) -> audit.ToolLocation:
    located = audit.find_audit_tool(tool_tree.parent)
    assert located is not None
    return located


def _utoc(tool: audit.ToolLocation, folder: str, *parts: str) -> str:
    """构造一个符合工具输出格式的绝对 utoc 路径。"""
    return str(tool.paks_dir / folder / Path(*parts))


class _FakeProcess:
    """假子进程：poll 立刻返回 0，模拟「已经跑完」。"""

    pid = 4242

    def __init__(self, exit_code: int = 0) -> None:
        self._exit_code = exit_code
        self.killed = False

    def poll(self) -> int:
        return self._exit_code

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        return self._exit_code


def _install_fake_tool(
    monkeypatch: pytest.MonkeyPatch,
    tool: audit.ToolLocation,
    payload: dict,
    *,
    log_text: str = "fake log",
    write_report: bool = True,
    write_log: bool = True,
    stderr: str = "",
) -> dict:
    """把 subprocess.Popen 换成会写出报告文件的假实现。"""
    captured: dict = {}

    def fake_popen(args, **kwargs):  # noqa: ANN001, ANN003
        captured["args"] = args
        captured["kwargs"] = kwargs

        if write_report:
            tool.json_path.parent.mkdir(parents=True, exist_ok=True)
            tool.json_path.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        if write_log and log_text:
            tool.log_path.write_text(log_text, encoding="utf-8")

        # 模拟工具把输出写进我们给的句柄（TemporaryFile 是二进制模式）
        if kwargs.get("stdout") is not None:
            kwargs["stdout"].write(log_text.encode("utf-8"))
        if kwargs.get("stderr") is not None and stderr:
            kwargs["stderr"].write(stderr.encode("utf-8"))

        return _FakeProcess()

    monkeypatch.setattr(audit.subprocess, "Popen", fake_popen)
    return captured


# ---------------------------------------------------------------------------
# 工具定位
# ---------------------------------------------------------------------------


def test_find_tool_in_game_root(tool_tree: Path) -> None:
    located = audit.find_audit_tool(tool_tree.parent)
    assert located is not None
    assert located.directory == tool_tree
    assert located.executable.name == audit.AUDIT_EXE_NAME


def test_find_tool_accepts_exe_path_directly(tool_tree: Path) -> None:
    located = audit.find_audit_tool(None, override=tool_tree / audit.AUDIT_EXE_NAME)
    assert located is not None
    assert located.directory == tool_tree


def test_find_tool_accepts_directory_override(tool_tree: Path) -> None:
    located = audit.find_audit_tool(None, override=tool_tree)
    assert located is not None
    assert located.directory == tool_tree


def test_find_tool_is_case_insensitive(tmp_path: Path) -> None:
    directory = tmp_path / "tool"
    directory.mkdir()
    (directory / "dekpakmodaudit.EXE").write_bytes(b"MZ")
    located = audit.find_audit_tool(None, override=directory)
    assert located is not None


def test_find_tool_returns_none_when_absent(tmp_path: Path) -> None:
    assert audit.find_audit_tool(tmp_path) is None
    assert audit.find_audit_tool(None) is None


def test_tool_resolves_paks_dir_from_config(tool: audit.ToolLocation) -> None:
    """配置里的 MainPaksFolder 是相对工具的，必须解析成绝对路径。"""
    assert tool.paks_dir is not None
    assert tool.paks_dir.is_absolute()
    assert tool.paks_dir.name == "Paks"
    assert tool.paks_dir.parent.name == "Content"


def test_tool_without_config_reports_it(tmp_path: Path) -> None:
    directory = tmp_path / "bare"
    directory.mkdir()
    (directory / audit.AUDIT_EXE_NAME).write_bytes(b"MZ")
    located = audit.find_audit_tool(None, override=directory)
    assert located is not None
    assert not located.has_config
    assert located.paks_dir is None


# ---------------------------------------------------------------------------
# 报告解析与冲突归类
# ---------------------------------------------------------------------------


def test_parse_and_classify_conflicts(tool: audit.ToolLocation) -> None:
    payload = {
        _utoc(tool, "~mods", "ModA", "A.utoc"): {
            "OverridesDefaultAssets": True,
            "OverriddenAssets": ["Content/Shared/Base.uasset"],
            "UniqueAssets": ["Content/A/OnlyA.uasset"],
            "ChunkID": "111",
        },
        _utoc(tool, "~mods", "ModB", "B.utoc"): {
            "OverridesDefaultAssets": False,
            "OverriddenAssets": [],
            "UniqueAssets": [
                "Content/Shared/Base.uasset",  # 与 ModA 的原版资源重名 → 高危
                "Content/Shared/New.uasset",  # 下面 ModC 也有 → 低危
            ],
            "ChunkID": "222",
        },
        _utoc(tool, "~mods", "ModC", "C.utoc"): {
            "OverridesDefaultAssets": False,
            "OverriddenAssets": [],
            "UniqueAssets": ["Content/Shared/New.uasset"],
            "ChunkID": "333",
        },
    }
    report = _run(tool, payload)

    assert report.mod_count == 3
    assert report.utoc_count == 3
    # ModA: 覆盖 1 + 新增 1；ModB: 新增 2；ModC: 新增 1
    assert report.total_assets == 5

    blocked = report.conflicts_of(audit.ConflictKind.BASE_OVERRIDE)
    assert len(blocked) == 1
    assert blocked[0].key == "Content/Shared/Base.uasset"
    assert {p.name for p in blocked[0].owners} == {"A.utoc", "B.utoc"}

    shared = report.conflicts_of(audit.ConflictKind.SHARED_ASSET)
    assert len(shared) == 1
    assert shared[0].key == "Content/Shared/New.uasset"

    assert report.conflicts_of(audit.ConflictKind.CHUNK_ID) == []
    assert not report.blocking_conflicts


def test_chunk_id_conflict_is_blocking(tool: audit.ToolLocation) -> None:
    """ChunkID 重复是最严重的一类——它才是「游戏进不去」的典型原因。"""
    payload = {
        _utoc(tool, "~mods", "ModA", "A.utoc"): {
            "OverridesDefaultAssets": False,
            "OverriddenAssets": [],
            "UniqueAssets": ["Content/A.uasset"],
            "ChunkID": "999",
        },
        _utoc(tool, "~mods", "ModB", "B.utoc"): {
            "OverridesDefaultAssets": False,
            "OverriddenAssets": [],
            "UniqueAssets": ["Content/B.uasset"],
            "ChunkID": "999",
        },
    }
    report = _run(tool, payload)

    blocking = report.blocking_conflicts
    assert len(blocking) == 1
    assert blocking[0].kind is audit.ConflictKind.CHUNK_ID
    assert blocking[0].kind.is_blocking
    assert "无法启动" in report.verdict[0]
    assert report.verdict[1] == "danger"


def test_no_conflicts_gives_success_verdict(tool: audit.ToolLocation) -> None:
    payload = {
        _utoc(tool, "~mods", "ModA", "A.utoc"): {
            "OverridesDefaultAssets": False,
            "OverriddenAssets": [],
            "UniqueAssets": ["Content/A.uasset"],
            "ChunkID": "1",
        }
    }
    report = _run(tool, payload)
    assert not report.has_conflicts
    assert report.verdict[1] == "success"


def test_same_mod_sharing_asset_is_not_a_conflict(tool: audit.ToolLocation) -> None:
    """同一个 Mod 的多个 utoc 共用资源不算冲突。"""
    payload = {
        _utoc(tool, "~mods", "ModA", "One.utoc"): {
            "OverridesDefaultAssets": False,
            "OverriddenAssets": [],
            "UniqueAssets": ["Content/Shared.uasset"],
            "ChunkID": "1",
        },
        _utoc(tool, "~mods", "ModA", "Two.utoc"): {
            "OverridesDefaultAssets": False,
            "OverriddenAssets": [],
            "UniqueAssets": ["Content/Shared.uasset"],
            "ChunkID": "2",
        },
    }
    report = _run(tool, payload)

    # 资源确实被两个 utoc 提供，所以算冲突；但归属的 Mod 只有一个
    assert len(report.conflicts) == 1
    assert report.owners_of(report.conflicts[0]) == ["ModA"]


def test_mod_names_match_library_grouping(tool: audit.ToolLocation) -> None:
    """嵌套目录取 ~mods 下的第一段；~mods 顶层的松散文件取主名。"""
    payload = {
        _utoc(tool, "~mods", "Weapon Rael-2798", "1.2", "Rael.utoc"): {
            "OverridesDefaultAssets": False,
            "OverriddenAssets": [],
            "UniqueAssets": [],
            "ChunkID": "1",
        },
        _utoc(tool, "~mods", "LooseMod.utoc"): {
            "OverridesDefaultAssets": False,
            "OverriddenAssets": [],
            "UniqueAssets": [],
            "ChunkID": "2",
        },
    }
    report = _run(tool, payload)
    names = {m.mod_name for m in report.mods}
    assert names == {"Weapon Rael-2798", "LooseMod"}


def test_override_mods_are_listed(tool: audit.ToolLocation) -> None:
    payload = {
        _utoc(tool, "~mods", "ModA", "A.utoc"): {
            "OverridesDefaultAssets": True,
            "OverriddenAssets": ["Content/X.uasset", "Content/Y.uasset"],
            "UniqueAssets": ["Content/Z.uasset"],
            "ChunkID": "1",
        }
    }
    report = _run(tool, payload)
    assert len(report.override_mods) == 1
    assert report.override_asset_count == 2


def test_owners_are_mapped_to_mod_names(tool: audit.ToolLocation) -> None:
    payload = {
        _utoc(tool, "~mods", "Alpha", "A.utoc"): {
            "OverridesDefaultAssets": False,
            "OverriddenAssets": [],
            "UniqueAssets": ["Content/S.uasset"],
            "ChunkID": "1",
        },
        _utoc(tool, "~mods", "Beta", "B.utoc"): {
            "OverridesDefaultAssets": False,
            "OverriddenAssets": [],
            "UniqueAssets": ["Content/S.uasset"],
            "ChunkID": "2",
        },
    }
    report = _run(tool, payload)
    assert set(report.owners_of(report.conflicts[0])) == {"Alpha", "Beta"}


# ---------------------------------------------------------------------------
# 多目录合并
# ---------------------------------------------------------------------------


def test_multiple_folders_are_merged(
    monkeypatch: pytest.MonkeyPatch, tool: audit.ToolLocation
) -> None:
    """~mods 与 LogicMods 之间的跨目录冲突也必须被发现。"""
    payload = {
        _utoc(tool, "~mods", "ModA", "A.utoc"): {
            "OverridesDefaultAssets": False,
            "OverriddenAssets": [],
            "UniqueAssets": ["Content/Cross.uasset"],
            "ChunkID": "1",
        },
        _utoc(tool, "LogicMods", "BP.utoc"): {
            "OverridesDefaultAssets": False,
            "OverriddenAssets": [],
            "UniqueAssets": ["Content/Cross.uasset"],
            "ChunkID": "1",
        },
    }
    _install_fake_tool(monkeypatch, tool, payload)
    report = audit.run_audit(tool, folders=["~mods", "LogicMods"])

    assert report.folders == ["~mods", "LogicMods"]
    assert report.mod_count == 2
    # 跨目录的同名资源 + 同 ChunkID，两类冲突都该出现
    assert len(report.conflicts_of(audit.ConflictKind.CHUNK_ID)) == 1
    assert len(report.conflicts_of(audit.ConflictKind.SHARED_ASSET)) == 1


# ---------------------------------------------------------------------------
# 执行路径与异常
# ---------------------------------------------------------------------------


def test_run_audit_passes_expected_arguments(
    monkeypatch: pytest.MonkeyPatch, tool: audit.ToolLocation
) -> None:
    captured = _install_fake_tool(
        monkeypatch,
        tool,
        {
            _utoc(tool, "~mods", "M", "M.utoc"): {
                "OverridesDefaultAssets": False,
                "OverriddenAssets": [],
                "UniqueAssets": [],
                "ChunkID": "1",
            }
        },
    )
    audit.run_audit(tool, folders=["~mods"])

    assert captured["args"][0] == str(tool.executable)
    assert captured["args"][1] == "--modsfolder=~mods"
    # 工作目录必须是工具目录，否则它读不到 input/ 里的换算表
    assert captured["kwargs"]["cwd"] == str(tool.directory)
    # stdin 必须断开：给了它真实控制台反而会停在「按任意键」上
    assert captured["kwargs"]["stdin"] == audit.subprocess.DEVNULL


def test_run_audit_reports_progress(
    monkeypatch: pytest.MonkeyPatch, tool: audit.ToolLocation
) -> None:
    _install_fake_tool(monkeypatch, tool, {})
    seen: list[tuple[int, int, str]] = []
    audit.run_audit(
        tool, folders=["~mods", "LogicMods"], progress=lambda d, t, m: seen.append((d, t, m))
    )
    assert seen[0][0] == 0
    assert seen[-1][0] == seen[-1][1] == 2


def test_run_audit_raises_when_report_missing(
    monkeypatch: pytest.MonkeyPatch, tool: audit.ToolLocation
) -> None:
    _install_fake_tool(monkeypatch, tool, {}, write_report=False)
    with pytest.raises(audit.AuditError, match="没有生成报告"):
        audit.run_audit(tool, folders=["~mods"])


def test_run_audit_detects_stale_report(
    monkeypatch: pytest.MonkeyPatch, tool: audit.ToolLocation
) -> None:
    """报告没有被刷新，说明程序其实没跑成——不能拿旧结果糊弄用户。"""
    tool.json_path.parent.mkdir(parents=True, exist_ok=True)
    tool.json_path.write_text("{}", encoding="utf-8")
    _install_fake_tool(monkeypatch, tool, {}, write_report=False)

    with pytest.raises(audit.AuditError, match="没有刷新报告"):
        audit.run_audit(tool, folders=["~mods"])


def test_run_audit_rejects_broken_json(
    monkeypatch: pytest.MonkeyPatch, tool: audit.ToolLocation
) -> None:
    def fake_popen(args, **kwargs):  # noqa: ANN001, ANN003
        tool.json_path.write_text("{ not json", encoding="utf-8")
        return _FakeProcess()

    monkeypatch.setattr(audit.subprocess, "Popen", fake_popen)
    with pytest.raises(audit.AuditError, match="无法解析"):
        audit.run_audit(tool, folders=["~mods"])


def test_run_audit_requires_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    directory = tmp_path / "bare"
    directory.mkdir()
    (directory / audit.AUDIT_EXE_NAME).write_bytes(b"MZ")
    located = audit.find_audit_tool(None, override=directory)
    assert located is not None

    with pytest.raises(audit.AuditError, match="缺少配置文件"):
        audit.run_audit(located, folders=["~mods"])


def test_readkey_noise_is_not_a_warning(
    monkeypatch: pytest.MonkeyPatch, tool: audit.ToolLocation
) -> None:
    """工具末尾等按键必然抛异常，这是预期噪声，不该吓到用户。"""
    _install_fake_tool(
        monkeypatch,
        tool,
        {},
        stderr=(
            "Unhandled exception. System.InvalidOperationException: "
            "Cannot read keys when either application does not have a console"
        ),
    )
    report = audit.run_audit(tool, folders=["~mods"])
    assert not any("Cannot read keys" in w for w in report.warnings)


def test_other_stderr_becomes_a_warning(
    monkeypatch: pytest.MonkeyPatch, tool: audit.ToolLocation
) -> None:
    _install_fake_tool(monkeypatch, tool, {}, stderr="Something else went wrong")
    report = audit.run_audit(tool, folders=["~mods"])
    assert report.warnings
    assert "Something else went wrong" in report.warnings[0]


def test_cancel_before_start(monkeypatch: pytest.MonkeyPatch, tool: audit.ToolLocation) -> None:
    _install_fake_tool(monkeypatch, tool, {})
    with pytest.raises(audit.AuditCancelled):
        audit.run_audit(tool, folders=["~mods"], cancel=lambda: True)


def test_empty_mods_folder_produces_warning(
    monkeypatch: pytest.MonkeyPatch, tool: audit.ToolLocation
) -> None:
    _install_fake_tool(monkeypatch, tool, {})
    report = audit.run_audit(tool, folders=["~mods"])
    assert report.mods == []
    assert any("没有在检测目录下找到" in w for w in report.warnings)


# ---------------------------------------------------------------------------
# 文本摘要
# ---------------------------------------------------------------------------


def test_to_text_contains_the_essentials(tool: audit.ToolLocation) -> None:
    payload = {
        _utoc(tool, "~mods", "Alpha", "A.utoc"): {
            "OverridesDefaultAssets": True,
            "OverriddenAssets": ["Content/Base.uasset"],
            "UniqueAssets": ["Content/S.uasset"],
            "ChunkID": "7",
        },
        _utoc(tool, "~mods", "Beta", "B.utoc"): {
            "OverridesDefaultAssets": False,
            "OverriddenAssets": [],
            "UniqueAssets": ["Content/S.uasset"],
            "ChunkID": "7",
        },
    }
    text = _run(tool, payload).to_text()

    assert "Mod 冲突检测报告" in text
    assert "Alpha" in text and "Beta" in text
    assert "Content/S.uasset" in text
    assert "ChunkID" in text
    assert "== 结论 ==" in text


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _run(tool: audit.ToolLocation, payload: dict) -> audit.AuditReport:
    """直接用合成载荷构造报告，跳过进程调用。"""
    mods = audit._parse_mods(payload, tool, audit.DEFAULT_MODS_FOLDER)
    return audit.AuditReport(
        tool=tool,
        folders=[audit.DEFAULT_MODS_FOLDER],
        started_at=datetime.now(),
        duration_s=0.0,
        mods=mods,
        conflicts=audit._build_conflicts(mods),
    )
