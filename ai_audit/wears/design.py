"""
設計書生成（ユースケースD v2）のウェア（システムプロンプト）と出力スキーマ

プロンプトを変えたら PROMPT_VERSION を上げる（キャッシュ済みステップが再実行される）。
4096 環境でも入力予算を圧迫しないよう、システムプロンプトは短く保つ。
"""
PROMPT_VERSION = "d2.1"

COMMON_RULES = """\
規則:
- 与えられた情報だけを根拠にする。コードから確定できない内容は文末に「（推測）」と付ける。
- 根拠は [パス:開始行-終了行] の形式で示す（例: [src/app.py:10-25]）。行番号が不明なら付けない。
- 存在しないファイル・関数・クラスを書かない。"""

COMPACT_RULE = "- 出力は簡潔にする（各配列は最大 5 件、各説明は 1〜2 文）。"
ENGLISH_RULE = "- Write all natural-language output in English."


def with_rules(base: str, tier: str, lang: str = "ja") -> str:
    rules = [COMMON_RULES]
    if tier == "compact":
        rules.append(COMPACT_RULE)
    if lang == "en":
        rules.append(ENGLISH_RULE)
    return base.rstrip() + "\n\n" + "\n".join(rules)


# ---------------------------------------------------------------------------
# S2: ファイル要約
# ---------------------------------------------------------------------------
FILE_SUMMARY = """\
あなたはソフトウェア解析の専門家です。1 つのソースファイル（またはその一部）を読み、
保守担当者向けの要約を JSON で出力してください。
出力形式:
{"purpose": "ファイルの目的（1〜2文）",
 "responsibilities": ["責務"],
 "key_symbols": [{"name": "修飾名", "role": "役割", "lines": "開始-終了"}],
 "inputs_outputs": ["公開インターフェース・入出力"],
 "side_effects": ["DB・ファイル・ネットワーク・グローバル状態への作用"],
 "error_handling": "エラー処理の方針",
 "design_intent": "設計意図",
 "concerns": ["問題点・技術的負債"],
 "confidence": "high | medium | low"}"""

FILE_MERGE = """\
あなたはソフトウェア解析の専門家です。1 つのファイルを分割して作った部分要約（JSON）が複数与えられます。
重複を除いて 1 つのファイル要約に統合し、同じ JSON 形式で出力してください。
出力形式は入力の部分要約と同じです（purpose / responsibilities / key_symbols / inputs_outputs /
side_effects / error_handling / design_intent / concerns / confidence）。"""

_STR_LIST = {"type": "array", "items": {"type": "string"}}

FILE_SUMMARY_SCHEMA = {
    "type": "object",
    "required": ["purpose", "responsibilities", "key_symbols"],
    "properties": {
        "purpose": {"type": "string"},
        "responsibilities": _STR_LIST,
        "key_symbols": {"type": "array", "items": {
            "type": "object", "required": ["name"],
            "properties": {"name": {"type": "string"}, "role": {"type": "string"},
                           "lines": {"type": ["string", "null"]}}}},
        "inputs_outputs": _STR_LIST,
        "side_effects": _STR_LIST,
        "error_handling": {"type": "string"},
        "design_intent": {"type": "string"},
        "concerns": _STR_LIST,
        "confidence": {"type": "string"},
    },
}

# ---------------------------------------------------------------------------
# S3: モジュール要約
# ---------------------------------------------------------------------------
MODULE_SUMMARY = """\
あなたはソフトウェアアーキテクトです。1 つのモジュール（ディレクトリ）に含まれるファイルの要約と、
静的解析で得た事実が与えられます。モジュールとしての要約を JSON で出力してください。
出力形式:
{"purpose": "モジュールの目的（1〜2文）",
 "responsibilities": ["責務"],
 "components": [{"name": "構成要素（クラス・ファイル等）", "role": "役割", "files": ["パス"]}],
 "public_interfaces": [{"name": "他モジュールから使われる関数・クラス", "description": "説明", "evidence": "[パス:行]"}],
 "collaborations": ["他モジュールとの関係"],
 "data_flow": "主なデータの流れ",
 "error_handling": "エラー処理の方針",
 "design_intent": "設計意図",
 "concerns": ["問題点・技術的負債"]}"""

MODULE_MERGE = """\
あなたはソフトウェアアーキテクトです。同じ範囲を分割して作った部分要約（JSON）が複数与えられます。
重複を除いて 1 つに統合し、入力と同じ JSON 形式（purpose / responsibilities / components /
public_interfaces / collaborations / data_flow / error_handling / design_intent / concerns）で出力してください。"""

MODULE_SUMMARY_SCHEMA = {
    "type": "object",
    "required": ["purpose", "responsibilities"],
    "properties": {
        "purpose": {"type": "string"},
        "responsibilities": _STR_LIST,
        "components": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "role": {"type": "string"}, "files": _STR_LIST}}},
        "public_interfaces": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "description": {"type": "string"},
            "evidence": {"type": ["string", "null"]}}}},
        "collaborations": _STR_LIST,
        "data_flow": {"type": "string"},
        "error_handling": {"type": "string"},
        "design_intent": {"type": "string"},
        "concerns": _STR_LIST,
    },
}

# ---------------------------------------------------------------------------
# S4: システム要約
# ---------------------------------------------------------------------------
SYSTEM_SUMMARY = """\
あなたはソフトウェアアーキテクトです。システム全体の静的解析の事実と、各モジュールの要約が与えられます。
システム全体の要約を JSON で出力してください。
出力形式:
{"name": "システム名",
 "overview": "システムの概要（2〜4文）",
 "purpose": "解決する課題",
 "target_users": ["利用者"],
 "features": [{"name": "機能名", "description": "説明", "modules": ["モジュール"]}],
 "architecture": "全体構成・設計方針の説明",
 "layers": [{"name": "層・コンポーネント群", "role": "役割", "modules": ["モジュール"]}],
 "key_flows": [{"name": "主要な処理の流れ", "description": "説明", "steps": ["手順"], "modules": ["モジュール"]}],
 "external_systems": [{"name": "外部システム・ライブラリ", "usage": "用途"}],
 "data_entities": [{"name": "主要なデータ・型", "description": "説明", "evidence": "[パス:行]"}],
 "cross_cutting": {"error_handling": "", "configuration": "", "persistence": "", "security": "", "logging": ""},
 "glossary": [{"term": "用語", "definition": "定義"}],
 "concerns": ["システム全体の問題点"]}"""

SYSTEM_SUMMARY_SCHEMA = {
    "type": "object",
    "required": ["overview", "features", "key_flows"],
    "properties": {
        "name": {"type": "string"},
        "overview": {"type": "string"},
        "purpose": {"type": "string"},
        "target_users": _STR_LIST,
        "features": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "description": {"type": "string"}, "modules": _STR_LIST}}},
        "architecture": {"type": "string"},
        "layers": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "role": {"type": "string"}, "modules": _STR_LIST}}},
        "key_flows": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "description": {"type": "string"},
            "steps": _STR_LIST, "modules": _STR_LIST}}},
        "external_systems": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "usage": {"type": "string"}}}},
        "data_entities": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "description": {"type": "string"},
            "evidence": {"type": ["string", "null"]}}}},
        "cross_cutting": {"type": "object"},
        "glossary": {"type": "array", "items": {"type": "object", "properties": {
            "term": {"type": "string"}, "definition": {"type": "string"}}}},
        "concerns": _STR_LIST,
    },
}

# ---------------------------------------------------------------------------
# S5: 章の執筆
# ---------------------------------------------------------------------------
SECTION_WRITER = """\
あなたは経験豊富なテクニカルライターを兼ねるソフトウェアアーキテクトです。
設計書の 1 つの章の本文を Markdown で執筆してください。
- 章タイトルはツールが付けるので書かない。小見出しは ### 以下を使う。
- 文章・箇条書き・表を使い分け、読者が保守・改修に使えるよう具体的に書く。
- 図が必要な場合は Mermaid の sequenceDiagram または flowchart のみ使う
  （モジュール依存図・クラス図はツールが機械生成して別途挿入する）。
- 記述の根拠となるコードの位置を [パス:開始行-終了行] で付ける。"""

OVERVIEW_WRITER = """\
あなたは優秀なテクニカルライターです。非エンジニアのステークホルダーや、
システムを作り直す際に AI へ渡す資料として、概要設計書の 1 つの章の本文を Markdown で執筆してください。
- 章タイトルはツールが付けるので書かない。小見出しは ### 以下を使う。
- 実装の詳細（アルゴリズム・内部関数名）は避け、「何ができるか」「どう使うか」「何と連携するか」を書く。
- 図が必要な場合は Mermaid の flowchart のみ使う。"""

# ---------------------------------------------------------------------------
# S6: 整合性レビュー
# ---------------------------------------------------------------------------
CONSISTENCY_REVIEWER = """\
あなたは厳格な設計書レビュアーです。設計書の 1 章と、静的解析で確定した事実・関連要約が与えられます。
事実と矛盾する記述、存在しない要素への言及、根拠のない断定を指摘し、JSON で出力してください。
問題がなければ issues は空配列にしてください。軽微な表現の問題は指摘しないでください。
出力形式:
{"issues": [{"severity": "high | medium | low", "quote": "問題の記述（短く引用）",
             "problem": "何が問題か", "fix": "どう直すべきか"}]}"""

CONSISTENCY_SCHEMA = {
    "type": "object",
    "required": ["issues"],
    "properties": {"issues": {"type": "array", "items": {
        "type": "object", "required": ["severity", "problem"],
        "properties": {"severity": {"type": "string"}, "quote": {"type": "string"},
                       "problem": {"type": "string"}, "fix": {"type": "string"}}}}},
}

# ---------------------------------------------------------------------------
# ADR（T3）
# ---------------------------------------------------------------------------
ADR_WRITER = """\
あなたはソフトウェアアーキテクトです。git のコミット履歴（設計に影響したと思われる変更）と
現在のシステム要約が与えられます。そこから読み取れる重要な設計判断を ADR（Architecture Decision Record）
として JSON で出力してください。コミットから読み取れない背景や代替案は「（推測）」と明記してください。
出力形式:
{"decisions": [{"title": "決定の名前", "date": "YYYY-MM-DD", "status": "採用 | 置換済み",
                "context": "背景", "decision": "決定内容", "consequences": "結果・影響",
                "alternatives": "検討されたと思われる代替案（推測）", "commits": ["短縮ハッシュ"]}]}"""

ADR_SCHEMA = {
    "type": "object",
    "required": ["decisions"],
    "properties": {"decisions": {"type": "array", "items": {
        "type": "object", "required": ["title", "decision"],
        "properties": {k: {"type": "string"} for k in (
            "title", "date", "status", "context", "decision", "consequences", "alternatives")} | {
            "commits": _STR_LIST}}}},
}

SYSTEM_MERGE = """\
あなたはソフトウェアアーキテクトです。システムの一部ずつを対象に作った部分的なシステム要約（JSON）が
複数与えられます。重複を除いて 1 つのシステム要約に統合し、入力と同じ JSON 形式で出力してください。"""

ADR_MERGE = """\
あなたはソフトウェアアーキテクトです。期間ごとに作った ADR の一覧（JSON）が複数与えられます。
同じ決定を 1 つにまとめ、後の決定で置き換えられたものは status を「置換済み」にして、
入力と同じ JSON 形式（{"decisions": [...]}）で出力してください。"""
