"""
SARIF 2.1.0 出力（GitHub Code Scanning や VSCode の SARIF Viewer で表示できる）
"""
import os

from .. import __version__

_LEVEL = {"high": "error", "medium": "warning", "low": "note"}


def to_sarif(reports: dict[str, dict], root: str) -> dict:
    """reports: リポジトリ相対パス → 監査レポート（audit._file_report の形式）"""
    rules: dict[str, dict] = {}
    results = []
    for rel, report in sorted(reports.items()):
        for chunk in report["chunks"]:
            for issue in chunk["issues"]:
                rule_id = f"ai_audit/{issue['type']}"
                rules.setdefault(rule_id, {
                    "id": rule_id, "name": issue["type"],
                    "shortDescription": {"text": f"ai_audit {issue['type']} audit"},
                })
                text = issue["description"]
                if issue.get("suggestion"):
                    text += f"\n修正案: {issue['suggestion']}"
                results.append({
                    "ruleId": rule_id,
                    "level": _LEVEL.get(issue["severity"], "note"),
                    "message": {"text": text},
                    "locations": [{"physicalLocation": {
                        "artifactLocation": {"uri": rel, "uriBaseId": "SRCROOT"},
                        "region": {"startLine": issue["line"]},
                    }}],
                    "properties": {"confidence": issue.get("confidence"), "symbol": chunk["chunk_id"]},
                })
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {"name": "ai_audit", "version": __version__,
                                "informationUri": "https://github.com/t2k2pp/ai_audit",
                                "rules": sorted(rules.values(), key=lambda r: r["id"])}},
            "originalUriBaseIds": {"SRCROOT": {"uri": "file://" + os.path.abspath(root).replace(os.sep, "/") + "/"}},
            "results": results,
        }],
    }
