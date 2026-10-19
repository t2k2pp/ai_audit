"""
LLM を呼ぶステップの共通処理

- ステップの入力ハッシュ = 実際に送るプロンプト（system + user）。同じプロンプトなら再実行しない。
- コンテキスト超過がサーバから返った場合は、予算を縮めて組み立て直して再試行する。
- 大きすぎる入力は分割して部分結果を作り、統合する（reduce）。
"""
import json

from ..context.builder import ContextBuilder
from ..core.llm_client import ContextOverflowError


def user_budget(rt, system: str) -> int:
    return max(256, rt.budget.input_budget - rt.count(system))


def new_builder(rt, system: str) -> ContextBuilder:
    return ContextBuilder(rt.count, user_budget(rt, system))


def _with_overflow_retry(rt, builder: ContextBuilder, call):
    built = builder.build(strict=False)
    for attempt in range(3):
        try:
            return built, call(built.text)
        except ContextOverflowError:
            if attempt == 2:
                raise
            # 推定が甘かった: 補正係数を上げ、予算を縮めて組み立て直す
            rt.counter._factor *= 1.3
            builder.budget = int(builder.budget * 0.7)
            built = builder.build(strict=False)
    raise AssertionError("unreachable")


def json_step(pipe, rt, name: str, system: str, builder: ContextBuilder, schema: dict) -> dict:
    """JSON を返す LLM ステップ。戻り値: {"result", "dropped", "degraded", "truncated"}"""
    first = builder.build(strict=False)

    def run():
        built, resp = _with_overflow_retry(
            rt, builder, lambda text: rt.llm.chat_json(system, text, schema=schema,
                                                         max_tokens=rt.budget.output_reserve))
        return {"result": resp.data, "dropped": built.dropped, "degraded": built.degraded,
                "truncated": resp.truncated}

    return pipe.step(name, {"system": system, "user": first.text}, run)


def text_step(pipe, rt, name: str, system: str, builder: ContextBuilder) -> dict:
    """テキスト（Markdown）を返す LLM ステップ。戻り値: {"result", "dropped", "degraded", "truncated"}"""
    first = builder.build(strict=False)

    def run():
        built, resp = _with_overflow_retry(
            rt, builder, lambda text: rt.llm.chat(system, text, max_tokens=rt.budget.output_reserve))
        return {"result": resp.text, "dropped": built.dropped, "degraded": built.degraded,
                "truncated": resp.truncated}

    return pipe.step(name, {"system": system, "user": first.text}, run)


def compact_json(data) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def reduce_json(pipe, rt, name: str, system_map: str, system_merge: str,
                header: list[tuple[str, str, object]], items: list[tuple[str, list[str]]],
                schema: dict, item_title: str = "入力", depth: int = 0) -> dict:
    """
    header（必須部品）＋ items（全件入れたい部品）で 1 回の JSON 生成を行う。
    入りきらない場合は items をバッチに分けて部分結果を作り、部分結果を統合する（多段）。
    戻り値は json_step と同じ形（dropped には外した部品、degraded には縮約した部品）。
    """
    system = system_map if depth == 0 else system_merge

    def make_builder(batch):
        b = new_builder(rt, system)
        for key, title, text in header:
            b.add(key, title, text, required=True)
        for key, alts in batch:
            b.add(key, f"{item_title}: {key}", alts, priority=20)
        return b

    whole = make_builder(items)
    if not whole.build(strict=False).dropped or len(items) <= 1 or depth >= 6:
        return json_step(pipe, rt, f"{name}/d{depth}", system, whole, schema)

    # バッチ分割（貪欲法）
    batches, current = [], []
    for item in items:
        trial = make_builder(current + [item]).build(strict=False)
        if trial.dropped and current:
            batches.append(current)
            current = [item]
        else:
            current.append(item)
    if current:
        batches.append(current)
    if len(batches) == 1:  # 1 件ずつでも入らない場合は縮約版で処理する
        return json_step(pipe, rt, f"{name}/d{depth}", system, whole, schema)

    partials, dropped, degraded = [], [], []
    for i, batch in enumerate(batches):
        out = json_step(pipe, rt, f"{name}/d{depth}/b{i}", system, make_builder(batch), schema)
        partials.append((f"部分結果{i + 1}", [compact_json(out["result"])]))
        dropped += out["dropped"]
        degraded += out["degraded"]
    merged = reduce_json(pipe, rt, name, system_map, system_merge, header, partials, schema,
                         item_title="部分結果", depth=depth + 1)
    merged["dropped"] = dropped + merged["dropped"]
    merged["degraded"] = degraded + merged["degraded"]
    return merged
