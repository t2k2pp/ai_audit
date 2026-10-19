#!/usr/bin/env python3
"""
ai_audit - ローカル LLM によるコード監査・アーキテクチャ解析・設計書生成ツール

4096 トークン環境から 256K 級の大コンテキスト環境まで、コンテキスト長に応じて
機能を上乗せする（ティア: compact / standard / large）。

サブコマンド:
  config                        設定の表示・変更、サーバ検出結果の確認
  audit <path>                  監査（ファイルまたはフォルダ）
  extract_why <directory>       設計思想の抽出・蓄積
  search_why "<query>"          設計思想の自然言語検索
  list_why                      蓄積済み設計思想の一覧
  review_architecture <dir>     アーキテクチャレビュー
  generate_design_doc <dir>     設計書の生成（概要・詳細・モジュール別・図・ADR）
  status [<dir>]                直近ジョブの進捗表示
"""
import argparse
import io
import json
import os
import sys
import time

# Windows の CP932 端末で UTF-8 出力が失敗するのを防ぐ
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

TIER_CHOICES = ["compact", "standard", "large"]


def _fail(msg: str) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


def _validated_settings():
    from ai_audit.core.config import load_settings, validate_env
    validate_env()
    try:
        return load_settings()
    except ValueError as e:
        _fail(str(e))


def _runtime(tier: str | None = None):
    from ai_audit.core.runtime import create_runtime
    return create_runtime(_validated_settings(), tier=tier)


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------
def cmd_config(args: argparse.Namespace) -> None:
    from ai_audit.core.config import (
        fetch_ollama_models,
        get_env_path,
        load_env,
        load_settings,
        print_model_list,
        update_env_var,
    )

    if args.config_action == "model":
        settings = _validated_settings()
        print(f"Ollama ({settings.api_base_url}) からモデルリストを取得中...")
        try:
            models = fetch_ollama_models(settings.api_base_url)
        except RuntimeError as e:
            _fail(str(e))
        print_model_list(models)
        try:
            idx_str = input("切り替えるモデルの番号を入力してください (Enterでキャンセル): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nキャンセルしました。")
            return
        if not idx_str:
            print("変更なし。")
            return
        try:
            idx = int(idx_str)
            if not 0 <= idx < len(models):
                raise ValueError
        except ValueError:
            _fail(f"0〜{len(models) - 1} の番号を入力してください。")
        update_env_var("LLM_MODEL_NAME", models[idx]["name"])
        print(f"[OK] モデルを {models[idx]['name']} に変更しました。(.env を更新)")
        return

    if args.config_action == "output-tokens":
        load_env()
        if args.value is None:
            raw = os.getenv("LLM_MAX_OUTPUT_TOKENS", "").strip()
            print(f"現在の最大出力トークン数: {raw or 'ティアに応じた既定値（指定なし）'}")
        elif args.value.lower() in ("auto", "none", ""):
            update_env_var("LLM_MAX_OUTPUT_TOKENS", None)
            print("[OK] 最大出力トークン数を自動（ティアに応じた既定値）にしました。(.env を更新)")
        else:
            try:
                n = int(args.value)
                if n < 64:
                    raise ValueError("最小値は 64 です。")
            except ValueError as e:
                _fail(str(e))
            update_env_var("LLM_MAX_OUTPUT_TOKENS", str(n))
            print(f"[OK] 最大出力トークン数を {n} に変更しました。(.env を更新)")
        return

    if args.config_action == "probe":
        from ai_audit.core.budget import compute_budget
        from ai_audit.core.server_probe import probe_server
        settings = _validated_settings()
        profile = probe_server(settings, use_cache=False)
        budget = compute_budget(settings, profile)
        print(f"サーバ種別       : {profile.server_type}")
        print(f"最大コンテキスト : {profile.context_length:,}（取得元: {profile.context_source}）")
        print(f"トークナイザ     : {profile.tokenize or 'なし（推定値を使用）'}")
        print(budget.describe())
        for w in profile.warnings:
            print(f"[WARN] {w}")
        return

    # show
    load_env()
    try:
        s = load_settings()
    except ValueError as e:
        _fail(str(e))
    key = s.api_key
    key_display = ("*" * (len(key) - 4) + key[-4:]) if len(key) > 4 else (key or "（未設定）")
    print("\n現在の設定:（.env / 環境変数から読み込み）")
    rows = [
        ("API URL", s.api_base_url or "（未設定）"),
        ("API Key", key_display),
        ("モデル名", s.model_name or "（未設定）"),
        ("最大出力トークン", s.max_output_tokens or "自動"),
        ("最大コンテキスト", s.context_length or "自動取得"),
        ("実効予算の上限", s.context_budget or "自動"),
        ("ティア", s.tier or "自動判定"),
        ("思考モード", s.reasoning),
        ("追加パラメータ", json.dumps(s.extra_body, ensure_ascii=False) if s.extra_body else "なし"),
        ("同時リクエスト数", s.parallel),
        ("埋め込みモデル", s.embedding_model or "（未設定: ChromaDB 既定）"),
        ("設定ファイル", get_env_path()),
    ]
    for label, value in rows:
        print(f"  {label:<10}: {value}")
    print("\n変更・確認:")
    print("  python main.py config model                # モデルを一覧から選択")
    print("  python main.py config output-tokens 4096   # 最大出力トークン数を変更")
    print("  python main.py config probe                # サーバ検出結果とティアを表示")
    print("  ※ その他の項目は .env を直接編集してください（.env.example 参照）。")
    missing = [v for v in ("LLM_API_BASE_URL", "LLM_MODEL_NAME") if not os.getenv(v, "").strip()]
    if missing:
        print("\n[WARNING] 必須項目が未設定です: " + ", ".join(missing))


# ---------------------------------------------------------------------------
# 各ユースケース
# ---------------------------------------------------------------------------
def cmd_audit(args: argparse.Namespace) -> None:
    from ai_audit.usecases.audit import audit_directory, audit_file
    if not os.path.exists(args.path):
        _fail(f"ファイルまたはディレクトリが見つかりません: {args.path}")
    rt = _runtime(args.tier)
    try:
        if os.path.isdir(args.path):
            audit_directory(args.path, force=args.force, output_dir=args.output_dir, runtime=rt,
                            verify=not args.no_verify, sarif=args.sarif)
        else:
            audit_file(args.path, force=args.force, runtime=rt, verify=not args.no_verify, root=args.root)
    except (FileNotFoundError, ValueError) as e:
        _fail(str(e))


def cmd_extract_why(args: argparse.Namespace) -> None:
    if not os.path.isdir(args.directory):
        _fail(f"ディレクトリが見つかりません: {args.directory}")
    rt = _runtime()
    from ai_audit.usecases.why import extract_why
    extract_why(args.directory, force=args.force, runtime=rt)


def cmd_search_why(args: argparse.Namespace) -> None:
    from ai_audit.core.config import load_env, load_settings
    from ai_audit.usecases.why import print_search_results, search_why
    load_env()
    results = search_why(args.query, directory=args.dir, top_k=args.top_k, settings=load_settings())
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=1))
    else:
        print_search_results(results)


def cmd_list_why(args: argparse.Namespace) -> None:
    from ai_audit.usecases.why import list_why, print_list_results
    results = list_why(args.dir)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=1))
    else:
        print_list_results(results)


def cmd_review_architecture(args: argparse.Namespace) -> None:
    rt = _runtime(args.tier)
    from ai_audit.usecases.architecture import review_architecture
    if not os.path.isdir(args.directory):
        _fail(f"ディレクトリが見つかりません: {args.directory}")
    report = review_architecture(args.directory, output_file=args.output, force=args.force,
                                 runtime=rt, lang=args.lang)
    if not args.output:
        print(report)


def cmd_generate_design_doc(args: argparse.Namespace) -> None:
    rt = _runtime(args.tier)
    from ai_audit.usecases.design_doc import generate_design_doc
    adr = True if args.adr else (False if args.no_adr else None)
    try:
        out = generate_design_doc(args.directory, output_dir=args.output_dir, force=args.force,
                                  tier=args.tier, layout=args.layout, lang=args.lang,
                                  verify=not args.no_verify, adr=adr, runtime=rt)
    except (FileNotFoundError, ValueError) as e:
        _fail(str(e))
    if not out["files"]:
        sys.exit(1)
    print(f"[DONE] 設計書: {out['output_dir']}")
    if not out["ok"]:
        print("[WARN] 一部のステップが失敗しました。同じコマンドを再実行すると続きから処理します。",
              file=sys.stderr)
        sys.exit(2)


def cmd_status(args: argparse.Namespace) -> None:
    from ai_audit.pipeline.jobstore import JobStore, work_base
    base = work_base(args.directory)
    if not os.path.isdir(base):
        print("ジョブの記録がありません。")
        return
    for name in sorted(os.listdir(base)):
        if name == "steps" or not os.path.isfile(os.path.join(base, name, "job.json")):
            continue
        st = JobStore(base, name).read_status()
        if not st:
            continue
        started = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st["started_at"]))
        state = "完了" if st.get("finished_at") else "実行中または中断"
        progress = f"{st['done']}/{st['total']}" if st.get("total") else "-"
        print(f"[{name}] {state} / 開始 {started} / 段階: {st['stage']} / 進捗 {progress} / "
              f"再利用 {st.get('reused', 0)} / 失敗 {st.get('failed', 0)}")


# ---------------------------------------------------------------------------
# パーサー定義
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai_audit",
        description="ローカル LLM によるコード監査・アーキテクチャ解析・設計書生成ツール",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  python main.py config                            # 現在の設定を表示
  python main.py config probe                      # サーバ検出結果・ティアを表示
  python main.py generate_design_doc ./src         # 設計書を ./src/_design/ に生成
  python main.py generate_design_doc ./src --tier compact   # 4096 環境向けの動作を強制
  python main.py status ./src                      # 直近ジョブの進捗
  python main.py audit ./src
  python main.py review_architecture ./src --output report.md
""",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("config", help="設定の表示・変更、サーバ検出結果の確認")
    p.add_argument("config_action", nargs="?", choices=["show", "model", "output-tokens", "probe"],
                   default="show", help="show（既定）/ model / output-tokens / probe")
    p.add_argument("value", nargs="?", default=None, help="output-tokens の値（整数 または auto）")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("audit", help="ファイルまたはフォルダを監査する")
    p.add_argument("path", help="監査対象のファイルまたはディレクトリ")
    p.add_argument("--force", action="store_true", help="キャッシュを無視して再監査する")
    p.add_argument("--output-dir", dest="output_dir", default=None, metavar="DIR",
                   help="結果 JSON の出力先（フォルダ監査時）")
    p.add_argument("--tier", choices=TIER_CHOICES, default=None, help="ティアを指定（既定: 自動判定）")
    p.add_argument("--no-verify", action="store_true", help="指摘の検証パスを行わない")
    p.add_argument("--sarif", default=None, metavar="FILE", help="SARIF 形式でも出力する（フォルダ監査時）")
    p.add_argument("--root", default=None, metavar="DIR",
                   help="単一ファイル監査で文脈解析に使うルート（既定: git のルート）")
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("extract_why", help="設計思想を抽出して DB に蓄積する")
    p.add_argument("directory", help="対象ディレクトリ")
    p.add_argument("--force", action="store_true", help="変更の有無に関わらず全件を再抽出する")
    p.set_defaults(func=cmd_extract_why)

    p = sub.add_parser("search_why", help="蓄積した設計思想を自然言語で検索する")
    p.add_argument("query", help="検索クエリ")
    p.add_argument("--dir", default=".", help="抽出済みのプロジェクトディレクトリ（既定: カレント）")
    p.add_argument("--top-k", type=int, default=5, help="最大件数（既定: 5）")
    p.add_argument("--json", action="store_true", help="JSON で出力する")
    p.set_defaults(func=cmd_search_why)

    p = sub.add_parser("list_why", help="蓄積済み設計思想の一覧を表示する")
    p.add_argument("--dir", default=".", help="抽出済みのプロジェクトディレクトリ（既定: カレント）")
    p.add_argument("--json", action="store_true", help="JSON で出力する")
    p.set_defaults(func=cmd_list_why)

    p = sub.add_parser("review_architecture", help="アーキテクチャをレビューする")
    p.add_argument("directory", help="対象ディレクトリ")
    p.add_argument("--output", default=None, help="結果を保存する Markdown ファイル")
    p.add_argument("--force", action="store_true", help="保存済みの途中結果を使わず再実行する")
    p.add_argument("--tier", choices=TIER_CHOICES, default=None, help="ティアを指定（既定: 自動判定）")
    p.add_argument("--lang", choices=["ja", "en"], default="ja", help="出力言語（既定: ja）")
    p.set_defaults(func=cmd_review_architecture)

    p = sub.add_parser(
        "generate_design_doc", help="ソースコードから設計書を生成する",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
出力（既定: <directory>/_design/）:
  README.md        目次・生成条件・カバレッジ
  overview.md      概要設計書（非エンジニア・再実装用）
  detail.md        詳細設計書（保守担当者向け）
  modules/*.md     モジュール別の設計
  diagrams/*.mmd   静的解析から生成した Mermaid 図
  adr/*.md         設計判断の記録（large ティア＋git 管理下のとき）
  _meta/coverage.json

再開: 途中で止まっても、同じコマンドを再実行すると完了済みの部分を再利用して続きから処理します。
      コードを変更した場合は、影響する部分だけを再生成します。--force で全再生成。
""",
    )
    p.add_argument("directory", help="対象ディレクトリ")
    p.add_argument("--output-dir", dest="output_dir", default=None, metavar="DIR", help="出力先")
    p.add_argument("--force", action="store_true", help="保存済みの途中結果を使わず全再生成する")
    p.add_argument("--tier", choices=TIER_CHOICES, default=None, help="ティアを指定（既定: 自動判定）")
    p.add_argument("--layout", choices=["v2", "legacy"], default="v2",
                   help="出力レイアウト（legacy: v0.3 互換の _design_detail.md / _design_overview.md）")
    p.add_argument("--lang", choices=["ja", "en"], default="ja", help="出力言語（既定: ja）")
    p.add_argument("--no-verify", action="store_true", help="整合性レビューを行わない")
    p.add_argument("--adr", action="store_true", help="ADR を必ず生成する（git 履歴を使用）")
    p.add_argument("--no-adr", action="store_true", help="ADR を生成しない")
    p.set_defaults(func=cmd_generate_design_doc)

    p = sub.add_parser("status", help="直近ジョブの進捗を表示する")
    p.add_argument("directory", nargs="?", default=".", help="対象ディレクトリ（既定: カレント）")
    p.set_defaults(func=cmd_status)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
