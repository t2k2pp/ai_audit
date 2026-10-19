"""ダミー LLM の応答（システムプロンプトの内容で出し分ける）。"""
import json

FILE = {"purpose": "テスト用の要約", "responsibilities": ["責務A"],
        "key_symbols": [{"name": "Cart.total", "role": "合計", "lines": "1-2"},
                        {"name": "NoSuch", "role": "存在しない"}],
        "inputs_outputs": [], "side_effects": [], "error_handling": "例外を送出",
        "design_intent": "単純さ優先（推測）", "concerns": [], "confidence": "medium"}
MODULE = {"purpose": "モジュールの目的", "responsibilities": ["注文処理"], "components": [],
          "public_interfaces": [], "collaborations": [], "data_flow": "", "error_handling": "",
          "design_intent": "", "concerns": ["SQL を文字列連結している"]}
SYSTEM = {"name": "shop", "overview": "ショップのサンプル", "purpose": "注文", "target_users": ["店員"],
          "features": [{"name": "注文", "description": "注文を確定", "modules": ["shop"]}],
          "architecture": "CLI → サービス → リポジトリ",
          "layers": [{"name": "サービス", "role": "業務", "modules": ["shop"]}],
          "key_flows": [{"name": "注文確定", "description": "かごを確定", "steps": ["合計", "保存"],
                         "modules": ["shop", "(root)"]}],
          "external_systems": [], "data_entities": [{"name": "Item", "description": "商品"}],
          "cross_cutting": {}, "glossary": [{"term": "かご", "definition": "Cart"}], "concerns": []}


def smart(req, review_issues=None):
    system = req["messages"][0]["content"]
    if "部分要約" in system or "1 つのソースファイル" in system:
        return json.dumps(FILE, ensure_ascii=False)
    if "モジュール（ディレクトリ）" in system or "同じ範囲を分割" in system:
        return json.dumps(MODULE, ensure_ascii=False)
    if "システム全体の静的解析" in system or "部分的なシステム要約" in system:
        return json.dumps(SYSTEM, ensure_ascii=False)
    if "設計書レビュアー" in system:
        return json.dumps({"issues": review_issues or []}, ensure_ascii=False)
    if "ADR" in system:
        return json.dumps({"decisions": [{"title": "初期構成", "date": "2026-01-01", "status": "採用",
                                          "context": "c", "decision": "d", "consequences": "e",
                                          "alternatives": "（推測）f", "commits": ["abc1234"]}]},
                          ensure_ascii=False)
    return ("### 説明\n注文は OrderService が確定する [shop/service.py:19-27]。"
            "存在しない根拠 [shop/nothing.py:1-2] と範囲外 [shop/models.py:900-901]。")
