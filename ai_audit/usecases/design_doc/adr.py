"""
ADR（Architecture Decision Record）の逆生成（large ティア）

git 履歴から設計に影響しそうなコミットを選び、システム要約と合わせて LLM に ADR を書かせる。
コミットの選び方（スコア）:
  変更ファイル数が多い / ファイルの追加・削除・移動 / 依存定義（package.json 等）の変更 /
  件名・本文のキーワード（refactor, migrate, 導入, 移行 など）
"""
import re

from ...index.gitinfo import commit_log
from ...pipeline.engine import StepFailed
from ...pipeline.llm_steps import compact_json, reduce_json
from ...wears import design as W

MAX_COMMITS = 60
_MANIFEST_RE = re.compile(r"(^|/)(package\.json|requirements[^/]*\.txt|pyproject\.toml|setup\.py|"
                          r"pubspec\.yaml|go\.mod|pom\.xml|build\.gradle|Cargo\.toml|Dockerfile)$")
_KEYWORD_RE = re.compile(r"refactor|architect|migrat|introduc|replac|switch|split|merge|rewrite|"
                         r"breaking|deprecat|設計|移行|導入|置き換|置換|リファクタ|分割|統合|刷新|廃止",
                         re.IGNORECASE)


def score_commit(c: dict) -> int:
    files = c["files"]
    score = 0
    if len(files) >= 5:
        score += 2
    if len(files) >= 15:
        score += 2
    score += min(3, sum(1 for s, _ in files if s in ("A", "D", "R")))
    if any(_MANIFEST_RE.search(p) for _, p in files):
        score += 3
    if _KEYWORD_RE.search(c["subject"] + " " + c["body"]):
        score += 2
    return score


def select_commits(commits: list[dict], limit: int = MAX_COMMITS) -> list[dict]:
    scored = [(score_commit(c), i, c) for i, c in enumerate(commits)]
    picked = [x for x in scored if x[0] >= 3]
    if not picked:  # 小さいリポジトリ: スコア上位をそのまま使う
        picked = sorted(scored, key=lambda x: (-x[0], x[1]))[:min(limit, 10)]
    picked = sorted(picked, key=lambda x: (-x[0], x[1]))[:limit]
    return [c for _, _, c in sorted(picked, key=lambda x: -x[1])]  # 古い順


def _render_commit(c: dict) -> list[str]:
    files = ", ".join(f"{s}:{p}" for s, p in c["files"][:15])
    more = f" 他{len(c['files']) - 15}件" if len(c["files"]) > 15 else ""
    full = f"{c['hash']} {c['date']} {c['subject']}\n{c['body'][:400]}\n変更: {files}{more}"
    short = f"{c['hash']} {c['date']} {c['subject']}（{len(c['files'])} ファイル）"
    return [full, short]


def render_adrs(decisions: list[dict]) -> dict[str, str]:
    files = {}
    index = ["# 設計判断の記録（ADR）", "",
             "> git 履歴から推定した記録です。「（推測）」の部分は履歴から確定できない内容です。", "",
             "| No | 決定 | 日付 | 状態 |", "|---|---|---|---|"]
    for i, d in enumerate(decisions, 1):
        name = f"{i:04d}.md"
        commits = ", ".join(d.get("commits") or []) or "-"
        files[name] = "\n".join([
            f"# ADR-{i:04d}: {d.get('title', '')}", "",
            f"- 日付: {d.get('date', '-')}", f"- 状態: {d.get('status', '-')}",
            f"- 関連コミット: {commits}", "",
            "## 背景", "", d.get("context", "") or "-", "",
            "## 決定", "", d.get("decision", "") or "-", "",
            "## 結果・影響", "", d.get("consequences", "") or "-", "",
            "## 代替案", "", d.get("alternatives", "") or "-", "",
        ])
        index.append(f"| {i} | [{d.get('title', '')}]({name}) | {d.get('date', '-')} | {d.get('status', '-')} |")
    files["README.md"] = "\n".join(index) + "\n"
    return files


def run_adr(pipe, rt, root: str, system: dict) -> dict[str, str]:
    commits = select_commits(commit_log(root))
    if not commits:
        pipe.log("[INFO] ADR: 対象となるコミットがないためスキップしました。")
        return {}
    pipe.stage("ADR 逆生成", len(commits))
    sys_prompt = W.with_rules(W.ADR_WRITER, rt.tier, pipe.lang)
    merge = W.with_rules(W.ADR_MERGE, rt.tier, pipe.lang)
    header = [("system", "システム要約", [compact_json({k: system.get(k) for k in (
        "name", "overview", "architecture", "layers", "external_systems")}),
        compact_json({k: system.get(k) for k in ("name", "overview")})])]
    items = [(c["hash"], _render_commit(c)) for c in commits]
    try:
        out = reduce_json(pipe, rt, "adr", sys_prompt, merge, header, items, W.ADR_SCHEMA,
                          item_title="コミット")
    except StepFailed:
        return {}
    known = {c["hash"] for c in commits}
    decisions = []
    for d in out["result"].get("decisions", []):
        if isinstance(d, dict) and d.get("title"):
            d["commits"] = [h for h in (d.get("commits") or []) if h in known]
            decisions.append(d)
    return render_adrs(decisions)
