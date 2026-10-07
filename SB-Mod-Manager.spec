# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：产出免安装的 onedir 目录。

为什么用 onedir 而不是 onefile
-----------------------------
onefile 每次启动都要把上百 MB 解压到临时目录，首次启动要等十几秒，
对「解压即用」的绿色包来说反而更差。onedir 启动快，也方便用户看到
目录里有什么。

为什么关掉 UPX
--------------
UPX 压缩后的 exe 会被不少杀毒软件按启发式规则报毒。对一个让用户下载的
工具来说，「被 Windows Defender 拦下来」比「体积大 30 MB」糟糕得多。

体积控制
--------
PySide6 装完有 200 MB，但本项目只用 QtCore / QtGui / QtWidgets。
排除清单里剔掉了 QML/Quick 全家桶、Designer、Qt 开发工具和网络/数据库模块。
注意这里排除的是 **Python 模块**；DLL 层面由 PyInstaller 自己分析依赖，
如果 Qt6Widgets.dll 真的需要某个被排除模块的 DLL，它仍会被带上——
所以排除是安全的，不会漏掉硬依赖。
"""

from pathlib import Path

# SPECPATH 由 PyInstaller 注入，**本身就是 spec 文件所在目录**，不要再取 parent
ROOT = Path(SPECPATH).resolve()
SRC = ROOT / "src"
ASSETS = SRC / "stellar_mod_manager" / "assets"

#: 用不到的 PySide6 模块。剔掉它们能省下大约一半体积。
EXCLUDED_QT_MODULES = [
    # QML / Quick 全家桶（本项目是纯 QWidget，一点 QML 都不用）
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQuickTest",
    # Qt 开发工具与设计器
    "PySide6.QtDesigner",
    "PySide6.QtUiTools",
    "PySide6.QtHelp",
    "PySide6.QtTest",
    # 本项目用不到的功能模块
    "PySide6.QtNetwork",      # 翻译走标准库 urllib，不用 QtNetwork
    "PySide6.QtSql",
    "PySide6.QtDBus",
    "PySide6.QtXml",
    "PySide6.QtConcurrent",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtPrintSupport",  # 没有打印功能
]

#: 与本项目无关的其它库，避免 PyInstaller 顺手带进来。
EXCLUDED_OTHER = [
    "tkinter",
    "unittest",
    "pydoc_data",
    "pytest",
    "PyInstaller",
    "setuptools",
    "pip",
]

a = Analysis(
    [str(ROOT / "tools" / "frozen_entry.py")],
    pathex=[str(SRC)],
    binaries=[],
    # 图标要落在 stellar_mod_manager/assets/ 下，
    # 因为 core/paths.py 的 asset_path() 是相对包目录解析的
    datas=[(str(ASSETS / "icon.ico"), "stellar_mod_manager/assets"),
           (str(ASSETS / "icon.png"), "stellar_mod_manager/assets")],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDED_QT_MODULES + EXCLUDED_OTHER,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SB-Mod-Manager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,           # 见文件头说明：UPX 容易被杀软误报
    console=False,       # GUI 程序，不弹黑框
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ASSETS / "icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SB-Mod-Manager",
)
