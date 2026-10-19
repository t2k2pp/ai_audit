"""
実行時コンテキスト: 設定・サーバプロファイル・予算・トークン計測・LLM クライアントをまとめる
"""
import sys
from dataclasses import dataclass, field

from .budget import Budget, TokenCounter, compute_budget
from .config import Settings, load_settings
from .llm_client import LLMClient, LLMResponse
from .server_probe import ServerProfile, probe_server


@dataclass
class UsageStats:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    elapsed: float = 0.0

    def add(self, resp: LLMResponse) -> None:
        self.calls += 1
        self.prompt_tokens += resp.prompt_tokens
        self.completion_tokens += resp.completion_tokens
        self.elapsed += resp.elapsed


@dataclass
class Runtime:
    settings: Settings
    profile: ServerProfile
    budget: Budget
    counter: TokenCounter
    llm: LLMClient
    usage: UsageStats = field(default_factory=UsageStats)

    @property
    def tier(self) -> str:
        return self.budget.tier

    def count(self, text: str) -> int:
        return self.counter.count(text)

    def close(self) -> None:
        self.counter.save()


def create_runtime(settings: Settings | None = None, tier: str | None = None,
                   quiet: bool = False) -> Runtime:
    """設定を読み込み、サーバを検出して Runtime を作る。tier 指定時は自動判定より優先する。"""
    settings = settings or load_settings()
    if tier:
        settings.tier = tier
    profile = probe_server(settings)
    budget = compute_budget(settings, profile)
    counter = TokenCounter(settings, profile)
    usage = UsageStats()
    llm = LLMClient(settings, counter=counter, on_usage=usage.add)
    if not quiet:
        print(f"[INFO] サーバ: {profile.server_type} / {budget.describe()}")
        for w in profile.warnings:
            print(f"[WARN] {w}", file=sys.stderr)
    return Runtime(settings, profile, budget, counter, llm, usage)
