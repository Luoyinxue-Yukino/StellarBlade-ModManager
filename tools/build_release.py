"""一键产出可分发的免安装压缩包。

    .venv\\Scripts\\python.exe tools\\build_release.py

做四件事：

1. 用 PyInstaller 按 ``SB-Mod-Manager.spec`` 打成 onedir 目录；
2. 往目录里补 ``使用说明.txt`` 与 ``LICENSE``——用户解压后第一眼该看到它们；
3. 打成 zip（放进一个同名顶层文件夹，避免解压时散落一地）；
4. 打印体积与校验值。

产物在 ``dist/SB-Mod-Manager-v<版本>-win64.zip``。
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stellar_mod_manager import APP_NAME, __version__  # noqa: E402

DIST = ROOT / "dist"
BUNDLE_NAME = "SB-Mod-Manager"
PLATFORM_TAG = "win64"

#: 解压后第一眼看到的说明。写短一点——太长的没人看。
QUICKSTART = """\
{app} v{version}
{rule}

一、这是什么
  一个《剑星》(Stellar Blade) 的 Mod 管理器：把 Mod 压缩包拖进来，
  自动解压、存进独立的 Mod 库，需要时再一键部署进游戏。
  库里的副本永远保留 —— 停用只是删掉游戏目录那一份，不怕丢文件。

二、怎么用
  1. 双击 SB-Mod-Manager.exe
  2. 首次启动会自动找游戏目录；没找到就到「设置」里手动指定
  3. 到「导入安装」页把 Mod 压缩包拖进去

  游戏目录默认是：
    ...\\steamapps\\common\\StellarBlade
  Mod 会被装到它下面的 SB\\Content\\Paks\\~mods\\

三、数据放在哪
  配置与日志： %LOCALAPPDATA%\\StellarModManager\\
  Mod 库    ： 游戏目录旁边，默认叫 StellarBladeModLibrary
  「从库中删除」是不可逆的，删除前请确认。

四、冲突检测（可选）
  「Mod 库」页的「冲突检测」需要一个第三方工具 DekPakModAudit。
  它不随本程序分发（作者未公开许可证），首次点击时会有引导对话框，
  按里面的三步走即可。不装它不影响其它功能。

五、出问题怎么办
  按 F12 打开运行日志，点「复制全部」，把内容发到：
  {issues}

六、免责声明
  非官方第三方工具，与 Shift Up / Sony 无隶属关系。
  不包含也不分发任何游戏文件或 Mod 文件。
  使用本工具修改游戏文件的风险由你自行承担，建议先备份存档。

{rule}
本程序以 MIT 许可证发布，完整条款见同目录的 LICENSE。
"""


def run_pyinstaller() -> Path:
    """调用 PyInstaller，返回产物目录。"""
    spec = ROOT / f"{BUNDLE_NAME}.spec"
    if not spec.is_file():
        raise SystemExit(f"找不到 spec：{spec}")

    cmd = [
        str(ROOT / ".venv" / "Scripts" / "pyinstaller.exe"),
        "--noconfirm",
        "--clean",
        str(spec),
    ]
    print("→ 正在打包（首次会比较慢）…")
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, errors="replace")
    if result.returncode != 0:
        tail = "\n".join(result.stderr.splitlines()[-25:])
        raise SystemExit(f"PyInstaller 失败（exit {result.returncode}）：\n{tail}")

    bundle = DIST / BUNDLE_NAME
    if not bundle.is_dir():
        raise SystemExit(f"打包完成但没找到产物目录：{bundle}")
    return bundle


def write_extras(bundle: Path) -> None:
    """把说明与许可证放进包里。"""
    rule = "=" * 62
    text = QUICKSTART.format(
        app=APP_NAME,
        version=__version__,
        rule=rule,
        issues="https://github.com/yukinozsy411-debug/StellarBlade-ModManager/issues",
    )
    # 用 UTF-8 with BOM：Windows 记事本打开中文 txt 才不会乱码
    (bundle / "使用说明.txt").write_text(text, encoding="utf-8-sig")

    license_src = ROOT / "LICENSE"
    if license_src.is_file():
        shutil.copy2(license_src, bundle / "LICENSE")


def make_zip(bundle: Path) -> Path:
    """打包成 zip。顶层套一层同名文件夹，解压不会散落。"""
    target = DIST / f"{BUNDLE_NAME}-v{__version__}-{PLATFORM_TAG}.zip"
    if target.exists():
        target.unlink()

    files = sorted(p for p in bundle.rglob("*") if p.is_file())
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in files:
            zf.write(path, Path(BUNDLE_NAME) / path.relative_to(bundle))
    print(f"  已写入 {len(files)} 个文件")
    return target


def main() -> int:
    bundle = run_pyinstaller()
    write_extras(bundle)

    raw = sum(p.stat().st_size for p in bundle.rglob("*") if p.is_file())
    print(f"  目录体积 {raw / 1024 / 1024:.1f} MB")

    target = make_zip(bundle)
    size = target.stat().st_size
    digest = hashlib.sha256(target.read_bytes()).hexdigest()

    print()
    print(f"产物：{target.relative_to(ROOT)}")
    print(f"  压缩后 {size / 1024 / 1024:.1f} MB")
    print(f"  SHA256 {digest}")
    print()
    print("记得在干净机器上解压试跑一次——那才是用户拿到的东西。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
