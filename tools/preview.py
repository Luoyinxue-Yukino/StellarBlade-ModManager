"""生成 README 用的界面截图。

在临时沙箱里造一份假的《剑星》环境（游戏目录、Mod、分组、压缩包），渲染各页面与
对话框并导出到 ``docs/screenshots/``。不需要真的装游戏，也不会碰你的真实数据。

    .venv\\Scripts\\python.exe tools\\preview.py                  # 输出到 docs/screenshots
    .venv\\Scripts\\python.exe tools\\preview.py build\\preview    # 输出到别处
    .venv\\Scripts\\python.exe tools\\preview.py --offscreen      # 无头渲染（仅看布局）

**默认走原生渲染**：离屏后端下 Qt 找不到字体，中文会全部变成豆腐块，
那种截图放进文档没有意义。只有在没有图形环境时才用 ``--offscreen``。
"""

from __future__ import annotations

import os
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# 必须在导入 Qt 之前决定平台后端
if "--offscreen" in sys.argv:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from stellar_mod_manager import __version__  # noqa: E402
from stellar_mod_manager.core import paths  # noqa: E402
from stellar_mod_manager.core.audit import (  # noqa: E402
    AuditReport,
    Conflict,
    ConflictKind,
    ModAssetSet,
    ToolLocation,
)
from stellar_mod_manager.core.config import AppConfig  # noqa: E402
from stellar_mod_manager.ui.main_window import MainWindow  # noqa: E402
from stellar_mod_manager.ui.theme import get_palette, stylesheet  # noqa: E402

WINDOW_SIZE = (1360, 860)

#: 假 Mod 的名字按真实库的观感取：有 CNS 风格的长名字，也有短名字
MOD_SAMPLES = (
    ("1. Fantasy Nude Body - CNS-2674-1-1", "IoStore", True),
    ("Belly Dance Outfit cns-2242", "IoStore", True),
    ("Infinity Blade CNS-3194", "IoStore", True),
    ("Lemi21_CustomizationMenu_Version2-2", "IoStore", False),
    ("School Uniform CNS-2595-1-3", "Pak", True),
    ("First-person_MOD_1.1", "IoStore", False),
)

#: 演示用的分组结构：(名称, 父分组名, 组内 Mod 序号)
GROUP_SAMPLES = (
    ("服装", None, [0, 1, 4]),
    ("连衣裙", "服装", [0]),
    ("武器", None, [2]),
    ("玩法", None, [3, 5]),
    ("其他", None, []),
)


# ---------------------------------------------------------------------------
# 假数据
# ---------------------------------------------------------------------------


def build_fake_game(root: Path) -> tuple[Path, Path]:
    """造一个结构合法的假游戏目录，并铺上假 Mod。"""
    game = root / "SteamLibrary" / "steamapps" / "common" / "StellarBlade"
    paks = game / "SB" / "Content" / "Paks"
    mods = paks / "~mods"
    mods.mkdir(parents=True)
    (game / "SB" / "Binaries" / "Win64").mkdir(parents=True)
    (game / "SB.exe").write_bytes(b"fake")

    size = 1024 * 1024  # 1 MB，让大小列有区分度
    for index, (name, kind, deployed) in enumerate(MOD_SAMPLES):
        folder = mods / name if deployed else root / "library" / name
        folder.mkdir(parents=True, exist_ok=True)
        stem = name.split(" CNS")[0]
        payload = [(f"{stem}.pak", size * (index + 3))]
        if kind == "IoStore":
            payload = [
                (f"{stem}.pak", 4096),
                (f"{stem}.utoc", size * (index + 1)),
                (f"{stem}.ucas", size * (index + 11)),
            ]
        for filename, length in payload:
            (folder / filename).write_bytes(b"\0" * length)
        (folder / f"{stem}.dekcns.json").write_text("[]", encoding="utf-8")

    return game, root / "library"


def build_fake_archives(root: Path) -> Path:
    """造几个压缩包，供「压缩包」页扫描。"""
    folder = root / "Downloads"
    folder.mkdir(parents=True, exist_ok=True)

    def make_zip(name: str, layout: str) -> None:
        with zipfile.ZipFile(folder / name, "w") as zf:
            zf.writestr(f"{layout}/Skin_P.pak", b"\0" * 8192)
            zf.writestr(f"{layout}/Skin_P.utoc", b"\0" * 2048)
            zf.writestr(f"{layout}/readme.txt", "安装说明")

    make_zip("Eve_Nano_Suit_v1.2.zip", "SB/Content/Paks/~mods/Eve_Nano_Suit")
    make_zip("Raven_Outfit.zip", "~mods/Raven_Outfit")
    make_zip("Cyber_Glasses.7z", "~mods/Cyber_Glasses")
    (folder / "broken.zip").write_bytes(b"this is not really a zip")
    return folder


def seed_groups(window: MainWindow) -> None:
    """按 GROUP_SAMPLES 建分组并归类。

    页面首次刷新时已经建好了默认分类（服装/武器/玩法/其他），这里**复用同名的**，
    只补建像「连衣裙」这样默认没有的子分组。
    """
    groups = window.context.groups
    created: dict[str, object] = {}
    for name, parent, _members in GROUP_SAMPLES:
        existing = groups.find_by_name(name, None) if parent is None else None
        if existing is None and parent is not None:
            existing = groups.find_by_name(name, created[parent].id)  # type: ignore[union-attr]
        created[name] = existing or groups.create(
            name, created[parent].id if parent else None  # type: ignore[union-attr]
        )
    for name, _parent, members in GROUP_SAMPLES:
        mods = [MOD_SAMPLES[i][0] for i in members]
        if mods:
            groups.assign_many(mods, created[name].id)  # type: ignore[union-attr]


def fake_report() -> AuditReport:
    """合成一份检测报告，用来渲染冲突对话框。

    注意 ``Conflict.owners`` 里放的必须是 **``.utoc`` 的路径**，而且要和各
    ``ModAssetSet.utoc`` 完全一致——对话框靠 ``AuditReport.owners_of`` 把
    utoc 映射回 Mod 名，路径对不上就会退化成父目录名，两个不同的 Mod 还会被
    去重成一个。
    """
    tool = ToolLocation(directory=Path("DekPakModAudit"), executable=Path("DekPakModAudit.exe"))
    aio = Path("~mods/All in One - CNS Version/All in One - CNS Version.utoc")
    belly = Path("~mods/Belly Dance Outfit cns-2242/Belly Dance Outfit cns-2242.utoc")
    blade = Path("~mods/Infinity Blade CNS-3194/Infinity Blade CNS-3194.utoc")
    quiet = Path("~mods/School Uniform CNS-2595-1-3/School Uniform CNS-2595-1-3.utoc")
    owners = [aio, belly]

    return AuditReport(
        tool=tool,
        folders=["~mods", "LogicMods"],
        started_at=datetime.now(),
        duration_s=4.8,
        mods=[
            ModAssetSet(utoc=aio, mod_name="All in One - CNS Version", chunk_id="A1B2C3D4",
                        overrides_default=True,
                        overridden_assets=[f"/Game/Art/Character/PC/CH_P_EVE_58/uv{i}" for i in range(4)]),
            ModAssetSet(utoc=belly, mod_name="Belly Dance Outfit cns-2242", chunk_id="A1B2C3D4",
                        overridden_assets=["/Game/Art/Character/PC/CH_P_EVE_31/body"],
                        unique_assets=["/Game/Art/Character/PC/CH_P_EVE_31/cloth"] * 2),
            ModAssetSet(utoc=blade, mod_name="Infinity Blade CNS-3194",
                        unique_assets=["/Game/Art/Weapon/WP_Blade/mesh"] * 3),
            ModAssetSet(utoc=quiet, mod_name="School Uniform CNS-2595-1-3",
                        unique_assets=["/Game/Art/Character/PC/CH_P_EVE_39/uniform"] * 5),
        ],
        conflicts=[
            Conflict(kind=ConflictKind.CHUNK_ID, key="A1B2C3D4", owners=owners),
            Conflict(
                kind=ConflictKind.BASE_OVERRIDE,
                key="/Game/Art/Character/PC/CH_P_EVE_58/SKIN_A",
                owners=[aio, belly],
            ),
            Conflict(
                kind=ConflictKind.SHARED_ASSET,
                key="/Game/Art/UI/Texture/Item/NanoSuit/NanoSuit_Icon_BS_20",
                owners=[aio, belly, blade],
            ),
        ],
        raw_log="DekPakModAudit 演示输出",
    )


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------


def settle(app: QApplication, ms: int = 220) -> None:
    """跑一小段事件循环，让布局、样式与动画落定后再截图。

    只调 ``processEvents()`` 不够：首次绘制与开关动画都需要真实时间流逝，
    否则会截到半成品。
    """
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    app.processEvents()


def save(widget, out_dir: Path, name: str) -> None:
    target = out_dir / name
    widget.grab().save(str(target))
    print(f"  {target.relative_to(ROOT) if out_dir.is_relative_to(ROOT) else target}")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out_dir = Path(args[0]) if args else ROOT / "docs" / "screenshots"
    out_dir.mkdir(parents=True, exist_ok=True)

    sandbox = Path(tempfile.mkdtemp(prefix="smm-preview-"))

    # 配置与实际数据目录都指到沙箱，避免污染真实环境
    paths.config_path = lambda: sandbox / "appdata" / "config.json"  # type: ignore[assignment]
    paths.app_data_dir = lambda: sandbox / "appdata"  # type: ignore[assignment]

    # 断言重定向生效：宁可报错，也不要往真实的 %LOCALAPPDATA%\StellarModManager 写假 Mod
    resolved = paths.app_data_dir().resolve()
    if sandbox.resolve() not in resolved.parents:
        raise RuntimeError(f"沙箱重定向失效，拒绝继续：{resolved}")
    paths.ensure_app_dirs()

    game, library = build_fake_game(sandbox)
    downloads = build_fake_archives(sandbox)

    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")

    config = AppConfig.load()
    config.game_root = str(game)
    config.library_dir = str(library)
    config.staging_dir = str(sandbox / "cache")
    config.theme = "dark"
    config.show_disabled_mods = True

    app.setStyleSheet(stylesheet(get_palette(config.theme)))

    window = MainWindow(config)
    window.resize(*WINDOW_SIZE)
    window.show()
    settle(app)

    seed_groups(window)
    library_page = window._pages["library"]
    library_page.refresh()
    library_page._apply_filter()
    settle(app, 400)

    print("页面：")
    for key, name in (
        ("library", "01-library.png"),
        ("import", "02-import.png"),
        ("archives", "03-archives.png"),
        ("settings", "04-settings.png"),
    ):
        window._switch_page(key, force=True)
        settle(app)
        save(window, out_dir, name)

    # 压缩包页扫出内容
    archives_page = window._pages["archives"]
    archives_page.folder_edit.setText(str(downloads))
    archives_page._scan()
    settle(app, 700)
    save(window, out_dir, "03-archives.png")

    # 导入页塞进队列
    import_page = window._pages["import"]
    import_page._add_paths([str(p) for p in sorted(downloads.glob("*.zip"))])
    window._switch_page("import", force=True)
    settle(app)
    save(window, out_dir, "02-import.png")

    print("对话框：")
    from stellar_mod_manager.ui.dialogs.classify_dialog import ClassifyDialog
    from stellar_mod_manager.ui.dialogs.conflict_dialog import ConflictDialog
    from stellar_mod_manager.ui.dialogs.error_dialog import ErrorDialog
    from stellar_mod_manager.ui.dialogs.log_dialog import LogDialog

    colors = get_palette(config.theme)

    dialog = ClassifyDialog(
        colors,
        suggestions={
            "服装": [m[0] for m in MOD_SAMPLES[:3]],
            "武器": ["Infinity Blade CNS-3194"],
            "玩法": ["First-person_MOD_1.1", "Lemi21_CustomizationMenu_Version2-2"],
            "其他": [],
        },
        missing_groups=set(),
        total=5,
    )
    dialog.resize(600, 480)
    dialog.show()
    settle(app, 300)
    save(dialog, out_dir, "05-classify.png")
    dialog.close()

    conflict = ConflictDialog(fake_report(), colors)
    conflict.resize(820, 620)
    conflict.show()
    settle(app, 300)
    save(conflict, out_dir, "06-conflict.png")
    conflict.close()

    # 日志查看器需要有些内容
    import logging

    log = logging.getLogger("preview")
    log.info("已启用「School Uniform CNS-2595-1-3」")
    log.info("已停用「Lemi21_CustomizationMenu_Version2-2」（移除 4 个文件，库中副本保留）")
    log.warning("压缩包 broken.zip 无法解析，已跳过")
    log.error("写入 Mod 库失败：磁盘空间不足")
    logs = LogDialog(colors)
    logs.resize(940, 560)
    logs.show()
    settle(app, 300)
    save(logs, out_dir, "07-log.png")
    logs.close()

    error = ErrorDialog(
        "LibraryError: 无法写入 Mod 库：磁盘空间不足",
        "Traceback (most recent call last):\n"
        '  File "library.py", line 512, in import_to_library\n'
        "    shutil.copy2(path, destination)\n"
        "OSError: [Errno 28] No space left on device",
        colors,
    )
    error.resize(680, 440)
    error.show()
    settle(app, 300)
    save(error, out_dir, "08-error.png")
    error.close()

    window.close()
    print(f"\n沙箱目录（可直接删除）：{sandbox}")
    print(f"版本：{__version__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
