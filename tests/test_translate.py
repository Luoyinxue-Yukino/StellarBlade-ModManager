"""CNS Mod 翻译测试：宽容解析、写回安全、OpenAI 兼容客户端。

最要紧的两条：
* 扫描器对真实社区文件（带尾随逗号）必须能用，且**不翻译时逐字节不变**；
* 写回译文**绝不能顺着硬链接改到用户的 Mod 备份**。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stellar_mod_manager.core import translate
from stellar_mod_manager.core.library import ModLibrary

# ---------------------------------------------------------------------------
# 样例
# ---------------------------------------------------------------------------

SAMPLE = """[
    {
        "UniqueFitID": "Bcase_CoolingSuit",
        "Requirement": "NikkeDLC",
        "DisplayName": "briefcasesharpie's Cooling Suit",
        "Description": "Cooling Suit Leotard Variations",
        "FitMeshType": "Body",
        "OutfitNames": ["Base", "Jacketless"],
        "UserConfigs": {
            "TextureOptions": [
                {
                    "DisplayName": "Skin Color",
                    "Description": "Skin Color",
                    "OptionNames": ["Default", "Tan"],
                    "Value": 0
                }
            ],
            "MaterialToggles": [
                {"DisplayName": "Shiny Rear", "Description": "Shiny thighs"}
            ]
        }
    }
]
"""

#: 社区文件里真实存在的宽松写法：尾随逗号 + 注释
LENIENT = """[
    {
        "DisplayName": "Trailing Comma Mod",
        "Description": "has a trailing comma",
        "OutfitDatas": [
            {
                "Mesh": "/Game/X.X",
                "Materials": [],
                "Parameters": [],     // 这里就是那个尾随逗号
            },
        ],
        /* 块注释也不该让解析失败 */
        "UserConfigs": {
            "TextureOptions": [
                {"DisplayName": "Shiny Rear",}
            ],
        }
    },
]
"""


@pytest.fixture
def dekcns(tmp_path: Path) -> Path:
    target = tmp_path / "Test.dekcns.json"
    # 必须用 write_bytes：write_text 在 Windows 上会把 \n 换成 \r\n，
    # 那样「渲染结果与原文逐字节一致」就永远测不出来了
    target.write_bytes(SAMPLE.encode("utf-8"))
    return target


#: 真实踩坑的形态：``ControlledBy`` 用 ``DisplayName`` 的文本串联控件父子关系。
#: 把链上的名字翻译掉，引用就会落空，整个控件树解析失败、
#: 该 Mod 在游戏里的所有显示名称都会消失。
REFERENCED = """[
    {
        "DisplayName": "Sexy Girl",
        "UserConfigs": {
            "MaterialToggles": [
                {"DisplayName": "Stockings"},
                {"DisplayName": "Stockings_Bot_Mask", "ControlledBy": "Stockings"},
                {"DisplayName": "Stockings_Bot_N", "ControlledBy": "Stockings_Bot_Mask"},
                {"DisplayName": "Skirt", "ControlledBy": "Outfit Colors"},
                {"DisplayName": "Outfit Colors"}
            ]
        }
    }
]
"""


def test_scan_protects_referenced_names() -> None:
    spans = translate.scan_document(REFERENCED)
    by_text = {s.text: s for s in spans}

    # 被 ControlledBy 指到的必须保护
    assert by_text["Stockings"].protected
    assert by_text["Stockings_Bot_Mask"].protected
    assert by_text["Outfit Colors"].protected
    # 没被任何引用指到的照常可翻
    assert not by_text["Sexy Girl"].protected
    assert not by_text["Skirt"].protected


def test_protected_reason_is_explained() -> None:
    span = next(s for s in translate.scan_document(REFERENCED) if s.text == "Stockings")
    assert "ControlledBy" in span.skip_reason


def test_translatable_excludes_protected() -> None:
    spans = translate.scan_document(REFERENCED)
    protected = {s.text for s in spans if s.protected}
    assert protected == {"Stockings", "Stockings_Bot_Mask", "Outfit Colors"}

    doc = translate.DekcnsDocument(path=Path("x"), text=REFERENCED, spans=spans)
    assert {s.text for s in doc.translatable} == {"Sexy Girl", "Stockings_Bot_N", "Skirt"}


def test_plan_skips_protected_entries() -> None:
    spans = translate.scan_document(REFERENCED)
    doc = translate.DekcnsDocument(path=Path("x"), text=REFERENCED, spans=spans)
    engine = _FakeTranslator()

    plan = translate.build_plan([doc], engine)

    sent = engine.calls[0]
    assert "Stockings" not in sent
    assert "Outfit Colors" not in sent
    assert "Sexy Girl" in sent
    # 编号仍与 doc.spans 对齐，写回时不会错位
    assert set(plan.translations) <= set(range(len(spans)))


def test_scan_protects_identifier_fields_too() -> None:
    """UniqueFitID / Requirement / ParamName 同样是键，撞名了也不能翻。"""
    text = (
        '[{"UniqueFitID": "Evelyn Outfit", "DisplayName": "Evelyn Outfit",'
        ' "Description": "Evelyn Outfit",'
        ' "Requirement": "NikkeDLC", "FitMeshType": "Body"}]'
    )
    by_text = {s.text: s for s in translate.scan_document(text)}

    assert by_text["Evelyn Outfit"].protected
    assert "NikkeDLC" not in by_text


def test_reference_protection_is_file_global() -> None:
    """引用可以跨数组，保护范围必须是整个文件。"""
    text = """[
        {
            "UserConfigs": {
                "MaterialToggles": [{"DisplayName": "Outfit Colors"}],
                "TextureOptions": [{"DisplayName": "Lower", "ControlledBy": "Outfit Colors"}]
            }
        }
    ]"""
    by_text = {s.text: s for s in translate.scan_document(text)}
    assert by_text["Outfit Colors"].protected, "被别的数组引用也要保护"


def test_write_layer_refuses_protected_spans(tmp_path: Path) -> None:
    """即便界面出了 bug 把受保护项送进来，写盘这一层也必须挡住。"""
    from stellar_mod_manager.core.library import LibraryError, ModLibrary

    game = tmp_path / "StellarBlade"
    (game / "SB" / "Content" / "Paks").mkdir(parents=True)
    lib = ModLibrary(game, tmp_path / "library")
    lib.ensure_dirs()

    target = lib.library_root / "Guard"
    target.mkdir(parents=True)
    (target / "Guard.dekcns.json").write_bytes(REFERENCED.encode("utf-8"))
    (target / "Guard_P.pak").write_bytes(b"pak")

    mod = lib.scan()[0]
    doc = translate.DekcnsDocument.load(target / "Guard.dekcns.json")
    index = next(i for i, s in enumerate(doc.spans) if s.text == "Stockings")

    with pytest.raises(LibraryError, match="被其它控件引用"):
        lib.apply_translation(mod, doc, {index: "丝袜"})

    # 文件必须原样未动
    assert (target / "Guard.dekcns.json").read_bytes() == REFERENCED.encode("utf-8")


# ---------------------------------------------------------------------------
# 扫描
# ---------------------------------------------------------------------------


def test_scan_finds_all_targets() -> None:
    spans = translate.scan_document(SAMPLE)
    texts = [s.text for s in spans]

    assert "briefcasesharpie's Cooling Suit" in texts
    assert "Skin Color" in texts
    assert "Shiny Rear" in texts
    # 枚举值与查找用的键绝不能进翻译队列
    assert "NikkeDLC" not in texts
    assert "Body" not in texts
    assert "Default" not in texts  # OptionNames 不是目标字段


def test_scan_marks_mod_name() -> None:
    spans = translate.scan_document(SAMPLE)
    names = [s for s in spans if s.is_mod_name]
    assert len(names) == 1
    assert names[0].text == "briefcasesharpie's Cooling Suit"
    assert names[0].category == "Mod 名称"


def test_scan_categorises_components() -> None:
    spans = translate.scan_document(SAMPLE)
    skin = [s for s in spans if s.text == "Skin Color"]
    assert {s.category for s in skin} == {"组件名称", "组件说明"}

    thighs = next(s for s in spans if s.text == "Shiny thighs")
    assert thighs.category == "组件说明"


def test_scan_handles_trailing_commas_and_comments() -> None:
    """社区文件普遍带尾随逗号，标准 json.loads 打不开，我们必须能读。"""
    with pytest.raises(json.JSONDecodeError):
        json.loads(LENIENT)

    spans = translate.scan_document(LENIENT)
    texts = {s.text for s in spans}
    assert "Trailing Comma Mod" in texts
    assert "has a trailing comma" in texts
    assert "Shiny Rear" in texts


def test_scan_skips_empty_and_symbolic_values() -> None:
    spans = translate.scan_document(
        '[{"DisplayName": "", "Description": "   ", "OutfitNames": ["#", "---", "Real Name"]}]'
    )
    assert [s.text for s in spans] == ["Real Name"]


def test_scan_records_accurate_offsets() -> None:
    for span in translate.scan_document(SAMPLE):
        assert SAMPLE[span.start : span.end] == span.text


# ---------------------------------------------------------------------------
# 写回
# ---------------------------------------------------------------------------


def test_render_without_translations_is_byte_identical(dekcns: Path) -> None:
    """不翻译时渲染结果必须与原文完全一致——这是「只改该改的」的前提。"""
    doc = translate.DekcnsDocument.load(dekcns)
    assert doc.render({}) == SAMPLE
    assert doc.encode(doc.render({})) == dekcns.read_bytes()


def test_render_replaces_only_targeted_strings(dekcns: Path) -> None:
    doc = translate.DekcnsDocument.load(dekcns)
    first = doc.spans[0]
    assert first.text == "briefcasesharpie's Cooling Suit"

    result = doc.render({0: "公文包鲨鱼的清凉套装"})

    assert "公文包鲨鱼的清凉套装" in result
    # 其余部分必须逐字节不变
    assert result.replace("公文包鲨鱼的清凉套装", first.text) == SAMPLE


def test_render_handles_multiple_spans_in_order(dekcns: Path) -> None:
    """从后往前替换，前面段落换长度也不会让偏移失效。"""
    doc = translate.DekcnsDocument.load(dekcns)
    translations = {i: f"译文{i}" * (i + 1) for i in range(len(doc.spans))}

    result = doc.render(translations)
    parsed = json.loads(result)

    assert parsed[0]["DisplayName"] == "译文0"


def test_render_escapes_special_characters(tmp_path: Path) -> None:
    """译文里带引号、换行也必须生成合法 JSON。"""
    target = tmp_path / "Esc.dekcns.json"
    target.write_text('[{"DisplayName": "Plain"}]', encoding="utf-8")

    doc = translate.DekcnsDocument.load(target)
    result = doc.render({0: 'He said "hi"\n并换行\\反斜杠'})

    assert json.loads(result)[0]["DisplayName"] == 'He said "hi"\n并换行\\反斜杠'


def test_document_reports_mod_name(dekcns: Path) -> None:
    assert translate.DekcnsDocument.load(dekcns).mod_name == "briefcasesharpie's Cooling Suit"


def test_bom_is_preserved(tmp_path: Path) -> None:
    target = tmp_path / "Bom.dekcns.json"
    target.write_bytes(b"\xef\xbb\xbf" + SAMPLE.encode("utf-8"))

    doc = translate.DekcnsDocument.load(target)
    assert doc.has_bom
    assert doc.encode(doc.render({})).startswith(b"\xef\xbb\xbf")
    assert doc.encode(doc.render({})) == target.read_bytes()


def test_find_documents_scans_recursively(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "One.dekcns.json").write_text("[]", encoding="utf-8")
    (tmp_path / "Two.dekcns.json").write_text("[]", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")

    found = translate.find_documents(tmp_path)
    assert {p.name for p in found} == {"One.dekcns.json", "Two.dekcns.json"}
    assert translate.find_documents(tmp_path / "missing") == []


# ---------------------------------------------------------------------------
# 缓存
# ---------------------------------------------------------------------------


def test_cache_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    cache = translate.TranslationCache(path)
    cache.put("Skin Color", "肤色")
    cache.save()

    reloaded = translate.TranslationCache(path).load()
    assert reloaded.get("Skin Color") == "肤色"
    assert len(reloaded) == 1


def test_cache_survives_corruption(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    path.write_text("{ not json", encoding="utf-8")
    assert len(translate.TranslationCache(path).load()) == 0


def test_cache_does_not_rewrite_unnecessarily(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    cache = translate.TranslationCache(path)
    cache.put("A", "甲")
    cache.save()
    stamp = path.stat().st_mtime_ns

    cache.save()  # 没有新内容，不该再写
    assert path.stat().st_mtime_ns == stamp


# ---------------------------------------------------------------------------
# 计划编排
# ---------------------------------------------------------------------------


class _FakeTranslator:
    def __init__(self, mapping: dict[str, str] | None = None) -> None:
        self.mapping = mapping or {}
        self.calls: list[list[str]] = []

    @property
    def name(self) -> str:
        return "fake"

    def translate(self, texts: list[str]) -> list[str]:
        self.calls.append(list(texts))
        return [self.mapping.get(t, f"[{t}]") for t in texts]


def test_build_plan_translates_everything(dekcns: Path) -> None:
    doc = translate.DekcnsDocument.load(dekcns)
    engine = _FakeTranslator()

    plan = translate.build_plan([doc], engine)

    assert len(plan.translations) == len(doc.spans)
    # requested 统计的是**去重后**要发给接口的条数
    unique = len({s.text for s in doc.spans})
    assert plan.requested == unique
    assert len(engine.calls[0]) == unique


def test_build_plan_uses_cache_and_skips_api(dekcns: Path, tmp_path: Path) -> None:
    doc = translate.DekcnsDocument.load(dekcns)
    cache = translate.TranslationCache(tmp_path / "c.json")
    for span in doc.spans:
        cache.put(span.text, f"缓存:{span.text}")

    engine = _FakeTranslator()
    plan = translate.build_plan([doc], engine, cache)

    assert engine.calls == [], "全部命中缓存时不该调用接口"
    assert plan.skipped_cached == len(doc.spans)


def test_build_plan_deduplicates_repeated_text(tmp_path: Path) -> None:
    """同一个词在多个 Mod 里出现时只翻一次，既省 token 又保证译法一致。"""
    target = tmp_path / "Dup.dekcns.json"
    target.write_text(
        '[{"DisplayName": "Same", "Description": "Same"},'
        ' {"DisplayName": "Same", "Description": "Other"}]',
        encoding="utf-8",
    )
    doc = translate.DekcnsDocument.load(target)
    engine = _FakeTranslator()

    plan = translate.build_plan([doc], engine)

    assert engine.calls == [["Same", "Other"]]
    assert len(plan.translations) == len(doc.spans)


def test_build_plan_requires_engine_when_cache_misses(dekcns: Path) -> None:
    doc = translate.DekcnsDocument.load(dekcns)
    with pytest.raises(translate.TranslationError, match="尚未配置翻译引擎"):
        translate.build_plan([doc], None)


def test_plan_items_expose_original_and_translation(dekcns: Path) -> None:
    doc = translate.DekcnsDocument.load(dekcns)
    plan = translate.build_plan([doc], _FakeTranslator())

    rows = plan.items()
    assert len(rows) == len(doc.spans)
    assert all(row[3] for row in rows), "每条都该有译文"


# ---------------------------------------------------------------------------
# OpenAI 兼容客户端
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self._payload = json.dumps(
            {"choices": [{"message": {"content": content}}]}
        ).encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> bool:
        return False


def _patch_urlopen(monkeypatch: pytest.MonkeyPatch, content: str) -> dict:
    captured: dict = {}

    def fake_urlopen(request, timeout=None):  # noqa: ANN001
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["auth"] = request.get_header("Authorization")
        return _FakeResponse(content)

    monkeypatch.setattr(translate.urllib.request, "urlopen", fake_urlopen)
    return captured


def test_openai_translator_sends_expected_request(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_urlopen(monkeypatch, '["肤色", "裙子"]')
    engine = translate.OpenAITranslator(
        "https://api.deepseek.com/v1", "sk-test", "deepseek-chat"
    )

    result = engine.translate(["Skin Color", "Skirt"])

    assert result == ["肤色", "裙子"]
    assert captured["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert captured["auth"] == "Bearer sk-test"
    assert captured["body"]["model"] == "deepseek-chat"
    assert json.loads(captured["body"]["messages"][1]["content"]) == ["Skin Color", "Skirt"]


def test_openai_translator_strips_markdown_fence(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_urlopen(monkeypatch, '```json\n["译文"]\n```')
    engine = translate.OpenAITranslator("https://x/v1", "k", "m")
    assert engine.translate(["Hello"]) == ["译文"]


def test_openai_translator_rejects_count_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """数量对不上必须报错——错位的译文比不翻译更糟。"""
    _patch_urlopen(monkeypatch, '["只有一条"]')
    engine = translate.OpenAITranslator("https://x/v1", "k", "m")

    with pytest.raises(translate.TranslationError, match="对不上"):
        engine.translate(["A", "B"])


def test_openai_translator_reports_auth_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error

    def fake_urlopen(request, timeout=None):  # noqa: ANN001
        raise urllib.error.HTTPError(
            request.full_url, 401, "Unauthorized", {}, None  # type: ignore[arg-type]
        )

    monkeypatch.setattr(translate.urllib.request, "urlopen", fake_urlopen)
    engine = translate.OpenAITranslator("https://x/v1", "bad", "m")

    with pytest.raises(translate.TranslationError, match="401"):
        engine.translate(["A"])


def _patch_raw_urlopen(monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    """让 urlopen 直接返回给定响应体（不包成 choices 结构）。"""

    class _Raw:
        def __init__(self) -> None:
            self._payload = body.encode("utf-8")

        def read(self) -> bytes:
            return self._payload

        def __enter__(self) -> "_Raw":
            return self

        def __exit__(self, *args: object) -> bool:
            return False

    monkeypatch.setattr(translate.urllib.request, "urlopen", lambda *a, **k: _Raw())


def test_openai_translator_reports_api_error_body(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_raw_urlopen(monkeypatch, '{"error": {"message": "insufficient balance"}}')
    engine = translate.OpenAITranslator("https://x/v1", "k", "m")
    with pytest.raises(translate.TranslationError, match="insufficient balance"):
        engine.translate(["A"])


def test_openai_translator_surfaces_api_error_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """有的服务把错误对象塞进 content，也要把原文透出来而不是说「格式不对」。"""
    _patch_urlopen(monkeypatch, '{"error": {"message": "insufficient balance"}}')
    engine = translate.OpenAITranslator("https://x/v1", "k", "m")
    with pytest.raises(translate.TranslationError, match="insufficient balance"):
        engine.translate(["A"])


def test_openai_translator_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_urlopen(monkeypatch, '["a", "b"]')
    engine = translate.OpenAITranslator("https://x/v1", "k", "m", batch_size=2)

    result = engine.translate(["1", "2", "3", "4"])

    assert result == ["a", "b", "a", "b"]
    assert captured["body"]["messages"][1]["content"] == '["3", "4"]'


def test_openai_translator_handles_empty_input() -> None:
    engine = translate.OpenAITranslator("https://x/v1", "k", "m")
    assert engine.translate([]) == []


# ---------------------------------------------------------------------------
# 写回安全：绝不能顺着硬链接改到用户的备份
# ---------------------------------------------------------------------------


@pytest.fixture
def library(fake_game: Path, isolated_app_data: Path, tmp_path: Path) -> ModLibrary:
    from stellar_mod_manager.core import paths

    paths.ensure_app_dirs()
    lib = ModLibrary(fake_game, tmp_path / "library")
    lib.ensure_dirs()
    return lib


def test_translation_does_not_touch_hardlinked_backup(
    library: ModLibrary, tmp_path: Path
) -> None:
    """核心安全承诺。

    库里的文件常常是硬链接进来的（例如用户自己的 Mod 备份目录）。写回译文时
    必须**原子替换**成新文件，从而断开链接——否则会把用户的备份一起改掉。
    """
    backup_dir = tmp_path / "MyModBackup" / "CoolSuit"
    backup_dir.mkdir(parents=True)
    original = backup_dir / "Cool.dekcns.json"
    original.write_text(SAMPLE, encoding="utf-8")
    (backup_dir / "Cool_P.pak").write_bytes(b"pak")

    # 用硬链接入库（和真实场景一致）
    mod = library.import_to_library(backup_dir, "CoolSuit", link=True)
    assert (library.library_root / "CoolSuit" / "Cool.dekcns.json").stat().st_nlink == 2

    doc = translate.DekcnsDocument.load(library.library_root / "CoolSuit" / "Cool.dekcns.json")
    library.apply_translation(mod, doc, {0: "清凉套装"})

    # 库里的改了
    assert "清凉套装" in (library.library_root / "CoolSuit" / "Cool.dekcns.json").read_text(
        encoding="utf-8"
    )
    # 用户的备份**一点没变**
    assert original.read_text(encoding="utf-8") == SAMPLE
    assert original.stat().st_nlink == 1, "备份应当已与库脱钩"


def test_translation_backs_up_original(library: ModLibrary, tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "B.dekcns.json").write_text(SAMPLE, encoding="utf-8")
    (stage / "B_P.pak").write_bytes(b"pak")
    mod = library.import_to_library(stage, "WithBackup")

    doc = translate.DekcnsDocument.load(library.library_root / "WithBackup" / "B.dekcns.json")
    result = library.apply_translation(mod, doc, {0: "备份用"})

    assert result.backup.is_file()
    assert result.backup.read_text(encoding="utf-8") == SAMPLE
    assert library.has_translation_backup(mod)


def test_translation_keeps_game_copy_in_sync(library: ModLibrary, tmp_path: Path) -> None:
    """已部署的 Mod 翻译后，游戏目录里那份也必须是中文。"""
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "G.dekcns.json").write_text(SAMPLE, encoding="utf-8")
    (stage / "G_P.pak").write_bytes(b"pak")

    mod = library.deploy(library.import_to_library(stage, "Deployed"))
    doc = translate.DekcnsDocument.load(library.library_root / "Deployed" / "G.dekcns.json")
    result = library.apply_translation(mod, doc, {0: "已部署"})

    assert result.redeployed
    game_json = library.mods_dir / "Deployed" / "G.dekcns.json"
    assert "已部署" in game_json.read_text(encoding="utf-8")
    assert "briefcasesharpie" not in game_json.read_text(encoding="utf-8")


def test_translation_can_be_reverted(library: ModLibrary, tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "R.dekcns.json").write_text(SAMPLE, encoding="utf-8")
    (stage / "R_P.pak").write_bytes(b"pak")
    mod = library.import_to_library(stage, "Revert")

    doc = translate.DekcnsDocument.load(library.library_root / "Revert" / "R.dekcns.json")
    library.apply_translation(mod, doc, {0: "可还原"})
    restored = library.revert_translation(library._reload("Revert"))

    assert restored == 1
    assert (library.library_root / "Revert" / "R.dekcns.json").read_text(
        encoding="utf-8"
    ) == SAMPLE


def test_translation_refuses_outside_library(library: ModLibrary, tmp_path: Path) -> None:
    outsider = tmp_path / "outside" / "X.dekcns.json"
    outsider.parent.mkdir(parents=True)
    outsider.write_text(SAMPLE, encoding="utf-8")

    from stellar_mod_manager.core.models import Mod

    doc = translate.DekcnsDocument.load(outsider)
    with pytest.raises(Exception, match="只能改写库中的文件"):
        library.apply_translation(Mod(name="X", in_library=True), doc, {0: "x"})


def test_translation_rejects_empty_payload(library: ModLibrary, tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "E.dekcns.json").write_text(SAMPLE, encoding="utf-8")
    (stage / "E_P.pak").write_bytes(b"pak")
    mod = library.import_to_library(stage, "Empty")

    doc = translate.DekcnsDocument.load(library.library_root / "Empty" / "E.dekcns.json")
    with pytest.raises(Exception, match="没有可写入的译文"):
        library.apply_translation(mod, doc, {})


def test_revert_without_backup_reports_clearly(library: ModLibrary, tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "N.dekcns.json").write_text(SAMPLE, encoding="utf-8")
    (stage / "N_P.pak").write_bytes(b"pak")
    mod = library.import_to_library(stage, "NoBackup")

    with pytest.raises(Exception, match="没有翻译备份"):
        library.revert_translation(mod)
