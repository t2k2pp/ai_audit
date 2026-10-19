"""
予算マネージャ: 1 リクエストで使えるトークン数（実効予算）とティアを決め、トークン数を計測する

実効予算（入力＋出力の合計）:
  - 最大コンテキスト 16K 以下: 最大コンテキストをそのまま使う（4096 環境向け）
  - それ以上: 最大コンテキストの 50%（下限 16K、上限 128K）
  - LLM_CONTEXT_BUDGET があればそれを上限とする
入力予算 = 実効予算 − 出力予約 − 安全マージン（5%）

ティア: 実効予算 8K 以下 = compact / 64K 以下 = standard / それ超 = large
"""
import json
import threading
from dataclasses import dataclass

import requests

from .config import Settings
from .server_probe import ServerProfile, _root_url

TIER_COMPACT = "compact"
TIER_STANDARD = "standard"
TIER_LARGE = "large"
TIERS = (TIER_COMPACT, TIER_STANDARD, TIER_LARGE)

_OUTPUT_RESERVE = {TIER_COMPACT: 1024, TIER_STANDARD: 4096, TIER_LARGE: 8192}
_REASONING_RESERVE = {"off": 0, "low": 1024, "medium": 4096, "high": 8192}
_MARGIN_RATIO = 0.05


def tier_rank(tier: str) -> int:
    return TIERS.index(tier)


@dataclass
class Budget:
    context_length: int
    effective: int
    output_reserve: int
    input_budget: int
    tier: str

    def describe(self) -> str:
        return (f"ティア={self.tier} / 最大コンテキスト={self.context_length:,} / "
                f"実効予算={self.effective:,} / 入力予算={self.input_budget:,} / 出力予約={self.output_reserve:,}")


def _auto_tier(effective: int) -> str:
    if effective <= 8192:
        return TIER_COMPACT
    if effective <= 65536:
        return TIER_STANDARD
    return TIER_LARGE


def compute_budget(settings: Settings, profile: ServerProfile) -> Budget:
    ctx = max(1024, profile.context_length)
    effective = ctx if ctx <= 16384 else max(16384, min(ctx // 2, 131072))
    if settings.context_budget:
        effective = min(effective, settings.context_budget)
    tier = settings.tier or _auto_tier(effective)

    output = settings.max_output_tokens or _OUTPUT_RESERVE[tier]
    output += _REASONING_RESERVE.get(settings.reasoning, 0)
    # 入力が実効予算の半分を下回らないよう出力予約を抑える
    output = min(output, effective // 2)
    margin = int(effective * _MARGIN_RATIO)
    return Budget(ctx, effective, output, effective - output - margin, tier)


def estimate_tokens(text: str) -> int:
    """トークン数を文字種から推定する（補正前）。"""
    ascii_count = sum(1 for c in text if ord(c) < 128)
    non_ascii = len(text) - ascii_count
    return int(ascii_count / 3.2 + non_ascii * 1.2) + 1


class TokenCounter:
    """
    トークン数の計測。サーバのトークナイザがあれば補正に使い、なければ推定値を使う。

    推定値には補正係数（実測 / 推定の移動平均、初期値 1.1 = 安全側）を掛ける。
    補正は (1) サーバの tokenize API、(2) API 応答の usage.prompt_tokens の両方から行う。
    """

    def __init__(self, settings: Settings | None = None, profile: ServerProfile | None = None):
        self._settings = settings
        self._profile = profile
        self._factor = 1.1
        self._samples = 0
        self._lock = threading.Lock()
        self._remote_checks = 0
        self._load()

    # --- 補正係数の永続化 ---
    def _calib_path(self):
        if not self._settings:
            return None
        return self._settings.data_dir / "token_calibration.json"

    def _load(self) -> None:
        path = self._calib_path()
        if not path or not path.exists():
            return
        try:
            entry = json.loads(path.read_text(encoding="utf-8")).get(self._settings.model_name)
            if entry:
                self._factor, self._samples = float(entry["factor"]), int(entry["samples"])
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def save(self) -> None:
        path = self._calib_path()
        if not path or self._samples == 0:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            data[self._settings.model_name] = {"factor": self._factor, "samples": self._samples}
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except (OSError, ValueError):
            pass

    @property
    def factor(self) -> float:
        return self._factor

    def observe(self, text_or_estimate, actual_tokens: int) -> None:
        """実測トークン数で補正係数を更新する。"""
        est = text_or_estimate if isinstance(text_or_estimate, int) else estimate_tokens(text_or_estimate)
        if est < 200 or actual_tokens <= 0:
            return  # 短すぎる入力は誤差が大きいので使わない
        ratio = max(0.5, min(3.0, actual_tokens / est))
        with self._lock:
            weight = 0.3 if self._samples < 10 else 0.1
            self._factor = self._factor * (1 - weight) + ratio * weight * 1.05  # 5% 安全側
            self._samples += 1

    def _remote_count(self, text: str) -> int | None:
        if not self._profile or not self._profile.tokenize or not self._settings:
            return None
        root = _root_url(self._settings.api_base_url)
        try:
            if self._profile.tokenize == "llamacpp":
                r = requests.post(f"{root}/tokenize", json={"content": text}, timeout=30)
                return len(r.json()["tokens"]) if r.status_code == 200 else None
            if self._profile.tokenize == "vllm":
                r = requests.post(f"{root}/tokenize",
                                  json={"model": self._settings.model_name, "prompt": text}, timeout=30)
                return int(r.json()["count"]) if r.status_code == 200 else None
        except (requests.RequestException, ValueError, KeyError):
            return None
        return None

    def count(self, text: str) -> int:
        """トークン数（補正済み推定値）を返す。最初の数回は大きな入力でサーバ実測による補正も行う。"""
        est = estimate_tokens(text)
        if est >= 1000 and self._remote_checks < 5 and self._profile and self._profile.tokenize:
            self._remote_checks += 1
            actual = self._remote_count(text)
            if actual:
                self.observe(est, actual)
                return actual
        return int(est * self._factor) + 1
