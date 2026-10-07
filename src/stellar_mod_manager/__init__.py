"""SB Mod Manager —— 《剑星》(Stellar Blade) 的 Mod 管理器。"""

__version__ = "0.1.0"
#: 界面与窗口标题上显示的名字。
APP_NAME = "SB Mod Manager"
#: 全称，用于副标题与程序元数据。
APP_NAME_LONG = "Stellar Blade Mod Manager"
APP_NAME_EN = "SB Mod Manager"

#: 是否在界面上暴露「Mod 翻译」。
#:
#: 该功能的**核心实现完整保留**（``core/translate.py``、翻译对话框、全部测试），
#: 只是暂时不接入界面：实测发现 CNS 的 ``DisplayName`` 同时被 ``ControlledBy``
#: 当作引用键，翻译它会让控件树失效（详见 ``core/translate.py`` 的
#: :data:`~core.translate.REFERENCE_KEYS`）。保护机制已经补上，但先把入口收起来。
#:
#: 改回 ``True`` 即可恢复全部入口（设置卡片 + Mod 卡片菜单项）。
TRANSLATION_ENABLED = False

__all__ = ["__version__", "APP_NAME", "APP_NAME_EN", "APP_NAME_LONG", "TRANSLATION_ENABLED"]
