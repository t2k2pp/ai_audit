"""
コンテキストビルダー: 予算内に収まるよう、優先度順にプロンプト部品を組み立てる

- required=True の部品は必ず入れる（入らない場合は ContextTooLarge）
- それ以外は priority の小さい順に入れ、入らない部品は代替（例: 全ソース → スケルトン）を試し、
  それでも入らなければ外して dropped に記録する（黙って切り詰めない）
"""
from dataclasses import dataclass, field


class ContextTooLarge(Exception):
    """必須部品だけで予算を超えた。"""


@dataclass
class Part:
    key: str
    title: str
    alternatives: list[str]
    priority: int = 50
    required: bool = False
    order: int = 0


@dataclass
class BuiltContext:
    text: str
    tokens: int
    included: list[str] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)   # 代替版で入れた部品
    dropped: list[str] = field(default_factory=list)


class ContextBuilder:
    def __init__(self, count, budget_tokens: int):
        """count: テキスト → トークン数 の関数、budget_tokens: このプロンプトに使える上限。"""
        self._count = count
        self.budget = budget_tokens
        self._parts: list[Part] = []

    def add(self, key: str, title: str, text, priority: int = 50, required: bool = False) -> None:
        """text は文字列、または [優先版, 代替版, ...] のリスト。空文字の部品は無視する。"""
        alts = [t for t in (text if isinstance(text, list) else [text]) if t]
        if alts:
            self._parts.append(Part(key, title, alts, priority, required, len(self._parts)))

    @staticmethod
    def _render(title: str, text: str) -> str:
        return f"## {title}\n{text.strip()}\n" if title else text.strip() + "\n"

    def fits(self, extra_text: str = "") -> bool:
        return self.build(strict=False).tokens + self._count(extra_text) <= self.budget

    def build(self, strict: bool = True) -> BuiltContext:
        chosen: dict[int, tuple[str, int]] = {}
        used = 0
        result = BuiltContext("", 0)
        for p in [p for p in self._parts if p.required]:
            # 必須部品は入る範囲で一番詳しい版を使う（どれも入らなければ最も短い最後の版）
            for alt_i, alt in enumerate(p.alternatives):
                rendered = self._render(p.title, alt)
                if used + self._count(rendered) <= self.budget or alt_i == len(p.alternatives) - 1:
                    if alt_i > 0:
                        result.degraded.append(p.key)
                    break
            tokens = self._count(rendered)
            chosen[p.order] = (rendered, tokens)
            used += tokens
        if strict and used > self.budget:
            raise ContextTooLarge(f"必須部品だけで予算を超えています（{used} > {self.budget} トークン）")

        for p in sorted((p for p in self._parts if not p.required), key=lambda p: (p.priority, p.order)):
            for alt_i, alt in enumerate(p.alternatives):
                rendered = self._render(p.title, alt)
                tokens = self._count(rendered)
                if used + tokens <= self.budget:
                    chosen[p.order] = (rendered, tokens)
                    used += tokens
                    if alt_i > 0:
                        result.degraded.append(p.key)
                    break
            else:
                result.dropped.append(p.key)

        ordered = [chosen[o] for o in sorted(chosen)]
        result.text = "\n".join(r for r, _ in ordered)
        result.tokens = used
        result.included = [p.key for p in self._parts if p.order in chosen]
        return result


def number_lines(text: str, start: int = 1) -> str:
    """行番号付きのソース（根拠の行番号を LLM に書かせるため）。"""
    lines = text.split("\n")
    width = len(str(start + len(lines)))
    return "\n".join(f"{str(i).rjust(width)}| {line}" for i, line in enumerate(lines, start))
