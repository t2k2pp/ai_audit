# 実装アプローチ

正式設計は `docs/6. 詳細設計書_v0.4_大コンテキスト対応.md`。ここには実装上の判断のみ記録する。

## 実装判断ログ（新しいものを下に追記）
- 2026-10-19: パッケージを `ai_audit/core|index|pipeline|context|usecases|wears|render` に再編する。CLI コマンド名・オプションは維持。
- 2026-10-19: `vscode-extension/python/` は Git 管理から外し、ビルドスクリプトでコピーする。
- 2026-10-19: テストは pytest。LLM はテスト内で起動する HTTP ダミーサーバ（`tests/fake_llm.py`）で代替する。
- 2026-10-19: 作業用ブランチ `claude/project-review-advanced-analysis-cbfoor` のリモート削除はプロキシで 403 となり実施不可。main に全内容取り込み済みのため放置（GitHub 画面から削除可能）。
