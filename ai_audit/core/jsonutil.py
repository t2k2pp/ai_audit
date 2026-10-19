"""
LLM 出力の JSON 抽出・修復・簡易スキーマ検証
"""
import json
import re

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


def strip_thinking(text: str) -> str:
    """<think>...</think> ブロック（思考過程）を除去する。閉じタグのみの場合はそれ以降を返す。"""
    if not text:
        return ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    return text.strip()


def _candidates(raw: str):
    yield raw
    for m in _FENCE_RE.finditer(raw):
        yield m.group(1)
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start, end = raw.find(open_c), raw.rfind(close_c)
        if start != -1 and end > start:
            yield raw[start:end + 1]


def extract_json(raw: str):
    """
    文字列から JSON を取り出す。コードフェンス・前後の説明文・末尾カンマに対応する。
    取り出せない場合は ValueError。
    """
    raw = strip_thinking(raw)
    for cand in _candidates(raw):
        cand = cand.strip()
        if not cand:
            continue
        for text in (cand, _TRAILING_COMMA_RE.sub(r"\1", cand)):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                continue
    raise ValueError(f"JSON を抽出できませんでした: {raw[:200]!r}")


_TYPES = {
    "object": dict, "array": list, "string": str, "integer": int,
    "number": (int, float), "boolean": bool,
}


def validate(data, schema: dict, path: str = "$") -> list[str]:
    """
    JSON Schema の基本部分（type / required / properties / items / enum）だけを検証し、
    エラーメッセージのリストを返す（空なら妥当）。
    """
    errors: list[str] = []
    types = schema.get("type")
    if types:
        allowed = types if isinstance(types, list) else [types]
        py = tuple(t for name in allowed if name != "null" for t in
                   (_TYPES[name] if isinstance(_TYPES[name], tuple) else (_TYPES[name],)))
        if data is None and "null" in allowed:
            return errors
        if not isinstance(data, py) or (isinstance(data, bool) and "boolean" not in allowed):
            return [f"{path}: 型が {allowed} ではありません"]
    if "enum" in schema and data not in schema["enum"]:
        errors.append(f"{path}: {data!r} は {schema['enum']} のいずれでもありません")
    if isinstance(data, dict):
        for key in schema.get("required", []):
            if key not in data:
                errors.append(f"{path}.{key}: 必須項目がありません")
        for key, sub in schema.get("properties", {}).items():
            if key in data:
                errors.extend(validate(data[key], sub, f"{path}.{key}"))
    if isinstance(data, list) and "items" in schema:
        for i, item in enumerate(data):
            errors.extend(validate(item, schema["items"], f"{path}[{i}]"))
    return errors
