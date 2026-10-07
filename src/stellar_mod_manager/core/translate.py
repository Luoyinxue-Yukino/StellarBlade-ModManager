"""CNS Mod 翻译：解析 ``.dekcns.json``、批量翻译、生成写回后的文本。

为什么不能用标准 JSON 解析
--------------------------
社区作者的 ``.dekcns.json`` 普遍带**尾随逗号**（游戏端的解析器比标准 JSON 宽松），
实测 67 个文件里有 12 个是 ``json.loads`` 打不开的。而且即便能解析，用
``json.load`` → ``json.dumps`` 往返也会重排整个文件，改动面远大于实际需要。

所以这里自带一个**宽容扫描器**：只负责定位「哪些字符串值位于哪些字段下」以及
它们在原文中的位置，写回时按位置倒序替换。结果是文件除了被翻译的那几个字符串
之外**逐字节不变**——注释、缩进、键序、尾随逗号全部原样保留。

本模块是纯逻辑：不碰 Qt，也不负责文件安全（断开硬链接、备份、重新部署都在
:mod:`core.library` 里做）。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol

from ..logging_setup import get_logger
logger = get_logger(__name__)

#: 需要翻译的字段名。``Requirement`` / ``FitMeshType`` 之类是查找用的枚举值，
#: 翻译了反而会让 Mod 失效，绝不能碰。
TARGET_KEYS: tuple[str, ...] = ("DisplayName", "Description")

#: 这些字段是字符串数组，数组元素同样会显示给玩家。
TARGET_LIST_KEYS: tuple[str, ...] = ("OutfitNames",)

#: **标识符字段**：它们的值是 CNS 内部用来互相引用的键，不是展示文案。
#:
#: 为什么必须保护
#: --------------
#: CNS 的 ``DisplayName`` 身兼两职——既是给玩家看的名字，**也是 ``ControlledBy``
#: 用来串联控件父子关系的键**。实测踩到的坑：某 Mod 有一串
#: ``Stockings → Stockings_Bot_Mask → Stockings_Bot_N → …`` 的引用链，把链上的
#: ``DisplayName`` 翻成中文后引用全部落空，CNS 解析不出控件树，
#: **该 Mod 在游戏里的所有显示名称都会消失**。
#:
#: 所以规则是：只要某段文本出现在下列任一字段里，它就当过一次「键」，
#: 一律不翻译。宁可留几句英文，也不能让 Mod 失效。
REFERENCE_KEYS: tuple[str, ...] = (
    "ControlledBy",
    "UniqueFitID",
    "Requirement",
    "ParamName",
)

#: 明显不需要翻译的短值（枚举、纯符号）。
_SKIP_VALUES = frozenset({"", "none", "null", "true", "false", "disabled", "enabled"})

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_TIMEOUT_S = 60
#: 每次请求送多少条文本。太大容易触发输出长度上限导致整批失败。
DEFAULT_BATCH_SIZE = 30


class TranslationError(RuntimeError):
    """翻译失败（网络、鉴权、响应格式等）。"""


# ---------------------------------------------------------------------------
# 宽容扫描
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TextSpan:
    """原文里一段可翻译的字符串。"""

    path: tuple[str, ...]
    key: str
    text: str
    start: int
    """内容在原文中的起始下标（引号之后）。"""
    end: int
    """内容结束下标（引号之前），不含引号。"""
    protected: bool = False
    """该文本同时被用作内部引用键，翻译会让 Mod 失效，必须原样保留。"""

    @property
    def json_path(self) -> str:
        return " > ".join(self.path) if self.path else self.key

    @property
    def is_mod_name(self) -> bool:
        """顶层 DisplayName 就是 Mod 在游戏里显示的名字。"""
        return self.key == "DisplayName" and len(self.path) == 1

    @property
    def category(self) -> str:
        if self.is_mod_name:
            return "Mod 名称"
        if len(self.path) == 1:
            return "Mod 描述"
        return "组件名称" if self.key == "DisplayName" else "组件说明"

    @property
    def skip_reason(self) -> str:
        if not self.protected:
            return ""
        return "该名称被其它控件的 ControlledBy 引用，翻译会导致整个控件树失效"


def scan_document(text: str, keys: tuple[str, ...] = TARGET_KEYS,
                  list_keys: tuple[str, ...] = TARGET_LIST_KEYS) -> list[TextSpan]:
    """扫描 JSON 文本，返回全部候选字符串及其位置。

    只容忍结构上的宽松（尾随逗号、``//`` 与 ``/* */`` 注释）；值的转义仍然按
    JSON 规则解析，这样写回时才能保证长度变化不影响其它位置。

    扫描是**两遍语义**的：先收集所有出现在 :data:`REFERENCE_KEYS` 里的文本
    （它们当过内部引用键），再据此把候选标记为 :attr:`TextSpan.protected`。
    """
    spans: list[TextSpan] = []
    #: 出现在标识符字段里的文本，一律不能翻译
    referenced: set[str] = set()
    length = len(text)
    pos = 0

    def skip() -> None:
        """跳过空白、注释与逗号。

        顺带跳过逗号是刻意的：这样 ``[a, b,]`` 这种尾随逗号自然被接受，
        而我们本来也不需要校验分隔符。
        """
        nonlocal pos
        while pos < length:
            ch = text[pos]
            if ch in " \t\r\n,":
                pos += 1
            elif text.startswith("//", pos):
                newline = text.find("\n", pos)
                pos = length if newline < 0 else newline + 1
            elif text.startswith("/*", pos):
                end = text.find("*/", pos + 2)
                pos = length if end < 0 else end + 2
            else:
                return

    def read_string() -> tuple[str, int, int]:
        """读取一个字符串字面量，返回 ``(值, 内容起, 内容止)``。"""
        nonlocal pos
        pos += 1  # 开引号
        start = pos
        chunks: list[str] = []
        while pos < length:
            ch = text[pos]
            if ch == "\\":
                chunks.append(text[start:pos])
                nxt = text[pos + 1] if pos + 1 < length else ""
                chunks.append(_unescape(nxt, text[pos:pos + 2]))
                pos += 2
                start = pos
            elif ch == '"':
                chunks.append(text[start:pos])
                end = pos
                pos += 1
                return "".join(chunks), start, end
            else:
                pos += 1
        chunks.append(text[start:pos])
        return "".join(chunks), start, pos

    def parse_value(path: tuple[str, ...], key: str) -> None:
        nonlocal pos
        skip()
        if pos >= length:
            return
        ch = text[pos]
        if ch == "{":
            parse_object(path)
        elif ch == "[":
            parse_array(path, key)
        elif ch == '"':
            value, start, end = read_string()
            if key in REFERENCE_KEYS and value.strip():
                referenced.add(value)
            if key in keys and _worth_translating(value):
                spans.append(TextSpan(path=path, key=key, text=value, start=start, end=end))
        else:
            # 数字 / true / false / null：读到分隔符为止
            while pos < length and text[pos] not in ",}] \t\r\n":
                pos += 1

    def parse_object(path: tuple[str, ...]) -> None:
        nonlocal pos
        pos += 1  # {
        while True:
            skip()
            if pos >= length:
                return
            if text[pos] == "}":
                pos += 1
                return
            if text[pos] != '"':
                pos += 1  # 结构异常时跳过，尽量把文件读完
                continue
            name, _, _ = read_string()
            skip()
            if pos < length and text[pos] == ":":
                pos += 1
            parse_value(path + (name,), name)

    def parse_array(path: tuple[str, ...], key: str) -> None:
        nonlocal pos
        pos += 1  # [
        while True:
            skip()
            if pos >= length:
                return
            if text[pos] == "]":
                pos += 1
                return
            if key in list_keys and text[pos] == '"':
                value, start, end = read_string()
                if _worth_translating(value):
                    spans.append(
                        TextSpan(path=path, key=key, text=value, start=start, end=end)
                    )
            else:
                parse_value(path, key)

    parse_value((), "")

    if not referenced:
        return spans
    # 第二遍：把「同时也是引用键」的候选标出来（保留在列表里，
    # 界面要能显示它们为什么被跳过，而不是凭空少几行）
    return [
        span if span.text not in referenced else replace(span, protected=True)
        for span in spans
    ]


_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f",
            "n": "\n", "r": "\r", "t": "\t"}


def _unescape(char: str, raw: str) -> str:
    if char == "u" and len(raw) == 2:
        return raw  # \uXXXX 原样保留，反正写回时会重新转义
    return _ESCAPES.get(char, raw)


def _worth_translating(value: str) -> bool:
    """过滤掉空串、纯符号与明显是枚举的短值。"""
    text = value.strip()
    if not text or text.lower() in _SKIP_VALUES:
        return False
    # 纯数字 / 纯符号（如 "P1"、"#"）没有翻译价值
    return any(ch.isalpha() for ch in text)


# ---------------------------------------------------------------------------
# 文档
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DekcnsDocument:
    """一个 ``.dekcns.json`` 的解析结果与写回逻辑。"""

    path: Path
    text: str
    spans: list[TextSpan] = field(default_factory=list)
    has_bom: bool = False

    @classmethod
    def load(cls, path: Path | str, *, keys: tuple[str, ...] = TARGET_KEYS) -> "DekcnsDocument":
        target = Path(path)
        raw = target.read_bytes()
        has_bom = raw.startswith(b"\xef\xbb\xbf")
        text = raw.decode("utf-8-sig")
        return cls(
            path=target,
            text=text,
            spans=scan_document(text, keys),
            has_bom=has_bom,
        )

    @property
    def mod_name(self) -> str:
        """顶层 DisplayName —— 也就是游戏里显示的 Mod 名。"""
        for span in self.spans:
            if span.is_mod_name:
                return span.text
        return self.path.parent.name

    @property
    def translatable(self) -> list[TextSpan]:
        """可以安全翻译的文本（排除被当作引用键的那些）。"""
        return [span for span in self.spans if not span.protected]

    @property
    def protected(self) -> list[TextSpan]:
        """被 `ControlledBy` 之类引用的文本，必须原样保留。"""
        return [span for span in self.spans if span.protected]

    def render(self, translations: dict[int, str]) -> str:
        """按 ``{span 下标: 译文}`` 生成新文本。

        从后往前替换，这样前面段落的偏移量不会因为长度变化而失效。
        """
        result = self.text
        for index in sorted(translations, reverse=True):
            if not 0 <= index < len(self.spans):
                continue
            span = self.spans[index]
            replacement = _escape(translations[index])
            result = result[: span.start] + replacement + result[span.end :]
        return result

    def encode(self, text: str) -> bytes:
        payload = text.encode("utf-8")
        return (b"\xef\xbb\xbf" + payload) if self.has_bom else payload


def _escape(value: str) -> str:
    """把译文转义成可放进 JSON 字符串字面量的形式。"""
    return json.dumps(value, ensure_ascii=False)[1:-1]


def find_documents(root: Path | str) -> list[Path]:
    """找出目录下全部 ``.dekcns.json``。"""
    base = Path(root)
    if not base.is_dir():
        return []
    return sorted(p for p in base.rglob("*.dekcns.json") if p.is_file())


# ---------------------------------------------------------------------------
# 翻译缓存
# ---------------------------------------------------------------------------


class TranslationCache:
    """原文 → 译文的持久缓存。

    实测 1025 处文本只有 463 条不重复（重复率 55%），所以缓存既省 token
    又保证同一个词在各 Mod 里译法一致。
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else None
        self._entries: dict[str, str] = {}
        self._dirty = False

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, source: str) -> str | None:
        return self._entries.get(source)

    def put(self, source: str, translated: str) -> None:
        if source and translated and self._entries.get(source) != translated:
            self._entries[source] = translated
            self._dirty = True

    def put_many(self, pairs: dict[str, str]) -> None:
        for source, translated in pairs.items():
            self.put(source, translated)

    def load(self) -> "TranslationCache":
        if self.path is None or not self.path.is_file():
            return self
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("翻译缓存损坏，已忽略: %s", exc)
            return self
        if isinstance(data, dict):
            self._entries = {str(k): str(v) for k, v in data.items()}
        return self

    def save(self, *, force: bool = False) -> None:
        if self.path is None or (not self._dirty and not force):
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self._entries, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            self._dirty = False
        except OSError as exc:
            logger.warning("写入翻译缓存失败: %s", exc)

    def as_dict(self) -> dict[str, str]:
        return dict(self._entries)


# ---------------------------------------------------------------------------
# 翻译引擎（OpenAI 兼容规格）
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are a translator for a video game mod manager. "
    "Translate the given English UI strings into Simplified Chinese (简体中文).\n"
    "Rules:\n"
    "1. Output ONLY a JSON array of strings, same length and order as the input.\n"
    "2. Keep game-specific proper nouns (character names, brand names, mod author "
    "names) unchanged unless they have a well-known Chinese name.\n"
    "3. Translate concisely, as these are in-game menu labels. No punctuation at "
    "the end unless the source has it.\n"
    "4. If a string is already Chinese or is a symbol/number, return it unchanged.\n"
    "5. Never add explanations, markdown fences, or extra text."
)


class Translator(Protocol):
    """翻译引擎接口。"""

    @property
    def name(self) -> str: ...

    def translate(self, texts: list[str]) -> list[str]: ...


class OpenAITranslator:
    """任何遵循 OpenAI ``/chat/completions`` 规格的服务。

    DeepSeek、Moonshot、通义、本地 vLLM/Ollama 的 OpenAI 兼容层都能直接用，
    只要给出 ``base_url`` / ``api_key`` / ``model``。
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        timeout_s: int = DEFAULT_TIMEOUT_S,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_s = timeout_s
        self.batch_size = max(1, batch_size)

    @property
    def name(self) -> str:
        return f"{self.model} @ {self.base_url}"

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def translate(self, texts: list[str]) -> list[str]:
        """批量翻译。按 :attr:`batch_size` 分批，尽量不让单批失败拖垮全部。"""
        if not texts:
            return []

        results: list[str] = []
        for start in range(0, len(texts), self.batch_size):
            chunk = texts[start : start + self.batch_size]
            results.extend(self._translate_chunk(chunk))
        return results

    def _translate_chunk(self, chunk: list[str]) -> list[str]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(chunk, ensure_ascii=False)},
            ],
            "temperature": 0.2,
            "stream": False,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
            except Exception:  # noqa: BLE001
                pass
            raise TranslationError(
                f"接口返回 {exc.code}：{_explain_http(exc.code)}"
                + (f"\n{detail}" if detail else "")
            ) from exc
        except urllib.error.URLError as exc:
            raise TranslationError(f"无法连接翻译接口：{exc.reason}") from exc
        except TimeoutError as exc:
            raise TranslationError(f"翻译请求超时（{self.timeout_s} 秒）") from exc

        content = _extract_content(raw)
        parsed = _parse_string_array(content)
        if len(parsed) != len(chunk):
            raise TranslationError(
                f"接口返回了 {len(parsed)} 条译文，与请求的 {len(chunk)} 条对不上"
            )
        return parsed


def _explain_http(code: int) -> str:
    return {
        401: "API Key 无效或未配置",
        402: "账户余额不足",
        403: "该 Key 无权访问这个模型",
        404: "接口地址不对（base_url 应形如 https://api.deepseek.com/v1）",
        429: "请求过于频繁，稍后再试",
    }.get(code, "请检查接口地址、Key 与模型名")


def _error_message(data: object) -> str | None:
    """从响应里提取错误文案（兼容 OpenAI 的 ``{"error": {...}}`` 形状）。"""
    if not isinstance(data, dict) or "error" not in data:
        return None
    error = data["error"]
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(error)


def _extract_content(raw: str) -> str:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TranslationError(f"接口返回的不是 JSON：{raw[:200]}") from exc

    message = _error_message(data)
    if message is not None and not (
        isinstance(data, dict) and "choices" in data
    ):
        raise TranslationError(f"接口报错：{message}")

    try:
        return str(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as exc:
        raise TranslationError(f"接口响应结构异常：{raw[:200]}") from exc


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def _parse_string_array(content: str) -> list[str]:
    """从模型回复里抠出字符串数组。

    模型有时会加 Markdown 围栏或多说两句，这里尽量宽容，但**绝不猜**：
    数量对不上就报错，避免把错位的译文写进 Mod。
    """
    text = _FENCE_RE.sub("", content.strip()).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("["), text.rfind("]")
        if start < 0 or end <= start:
            raise TranslationError(f"接口没有返回 JSON 数组：{text[:200]}") from None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise TranslationError(f"接口返回的数组无法解析：{text[:200]}") from exc

    # 有的服务会把错误对象塞进 content 里，别把它当成「格式不对」糊弄过去
    message = _error_message(data)
    if message is not None:
        raise TranslationError(f"接口报错：{message}")

    if not isinstance(data, list):
        raise TranslationError(f"接口返回的不是数组：{text[:200]}")
    return [item if isinstance(item, str) else str(item) for item in data]


# ---------------------------------------------------------------------------
# 编排：收集 → 翻译 → 生成新文本
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TranslationPlan:
    """一次翻译任务的完整计划，供界面预览与确认。"""

    documents: list[DekcnsDocument] = field(default_factory=list)
    #: ``span 全局编号`` → 译文
    translations: dict[int, str] = field(default_factory=dict)
    skipped_cached: int = 0
    requested: int = 0

    @property
    def total(self) -> int:
        return sum(len(doc.spans) for doc in self.documents)

    @property
    def protected_count(self) -> int:
        return sum(len(doc.protected) for doc in self.documents)

    def items(self) -> list[tuple[DekcnsDocument, int, TextSpan, str]]:
        """展开成 ``(文档, span 下标, span, 译文)`` 列表。"""
        rows: list[tuple[DekcnsDocument, int, TextSpan, str]] = []
        offset = 0
        for doc in self.documents:
            for index, span in enumerate(doc.spans):
                rows.append((doc, index, span, self.translations.get(offset + index, "")))
            offset += len(doc.spans)
        return rows


def build_plan(
    documents: list[DekcnsDocument],
    translator: Translator | None,
    cache: TranslationCache | None = None,
    *,
    progress: callable | None = None,
) -> TranslationPlan:
    """收集全部待译文本，命中缓存的直接取用，其余交给翻译引擎。"""
    plan = TranslationPlan(documents=list(documents))

    pending: dict[str, list[int]] = {}
    index = 0
    for doc in documents:
        for span in doc.spans:
            # protected 的段落照样占一个下标，保证编号与 doc.spans 对齐
            if not span.protected:
                cached = cache.get(span.text) if cache is not None else None
                if cached:
                    plan.translations[index] = cached
                    plan.skipped_cached += 1
                else:
                    pending.setdefault(span.text, []).append(index)
            index += 1

    if not pending:
        return plan
    if translator is None:
        raise TranslationError("尚未配置翻译引擎，请先在「设置」里填写 API")

    sources = list(pending)
    plan.requested = len(sources)
    if progress is not None:
        progress(0, len(sources), "正在翻译…")

    translated = translator.translate(sources)
    for source, result in zip(sources, translated, strict=True):
        for slot in pending[source]:
            plan.translations[slot] = result
        if cache is not None:
            cache.put(source, result)

    if progress is not None:
        progress(len(sources), len(sources), "翻译完成")
    return plan
