#!/usr/bin/env python3
"""
ai_audit - 120Bモデル×4096トークン環境向け 生成AI活用CLIツール

サブコマンド:
  config                        LLM設定の表示・変更（モデル選択・最大トークン）
  audit <path>                  ユースケースA: ファイルまたはフォルダの多重マイクロ監査
  extract_why <directory>       ユースケースB: 設計思想の抽出・蓄積
  search_why "<query>"          ユースケースB: 設計思想の自然言語検索
  review_architecture <dir>     ユースケースC: ASTスケルトンによるアーキテクチャレビュー
"""
import argparse
import io
import os
import sys

# Windows の CP932 端末で絵文字等を含む UTF-8 出力が失敗するのを防ぐ
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# config コマンド
# ---------------------------------------------------------------------------

def cmd_config(args: argparse.Namespace) -> None:
    from ai_audit.config_manager import (
        fetch_ollama_models,
        load_config,
        print_model_list,
        update_env_var,
        validate_env,
    )

    # --- サブアクション: model ---
    if args.config_action == "model":
        validate_env()
        config = load_config()
        print(f"Ollama ({config['api_base_url']}) からモデルリストを取得中...")
        try:
            models = fetch_ollama_models(config["api_base_url"])
        except RuntimeError as e:
            print(f"[ERROR] {e}", file=sys.stderr)
            sys.exit(1)

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
            if not (0 <= idx < len(models)):
                raise ValueError
        except ValueError:
            print(f"[ERROR] 0〜{len(models)-1} の番号を入力してください。", file=sys.stderr)
            sys.exit(1)

        new_model = models[idx]["name"]
        update_env_var("LLM_MODEL_NAME", new_model)
        print(f"[OK] モデルを {new_model} に変更しました。(.env を更新)")

    # --- サブアクション: output-tokens ---
    elif args.config_action == "output-tokens":
        if args.value is None:
            # 表示のみ（validate_env 不要）
            from dotenv import load_dotenv
            load_dotenv()
            raw = os.getenv("LLM_MAX_OUTPUT_TOKENS", "").strip()
            if raw:
                print(f"現在の最大出力トークン数: {raw}")
            else:
                print("現在の最大出力トークン数: モデルのデフォルト最大値（指定なし）")
        elif args.value.lower() in ("auto", "none", ""):
            # None = .env からキーをコメントアウト
            update_env_var("LLM_MAX_OUTPUT_TOKENS", None)
            print("[OK] 最大出力トークン数をモデルのデフォルト最大値（指定なし）に設定しました。(.env を更新)")
        else:
            try:
                new_tokens = int(args.value)
                if new_tokens < 64:
                    raise ValueError("最小値は 64 です。")
            except ValueError as e:
                print(f"[ERROR] {e}", file=sys.stderr)
                sys.exit(1)
            update_env_var("LLM_MAX_OUTPUT_TOKENS", str(new_tokens))
            print(f"[OK] 最大出力トークン数を {new_tokens} に変更しました。(.env を更新)")

    # --- サブアクション: show (デフォルト) ---
    else:
        from dotenv import load_dotenv
        load_dotenv()
        env_path = os.path.join(os.getcwd(), ".env")

        api_url   = os.getenv("LLM_API_BASE_URL", "（未設定）")
        api_key   = os.getenv("LLM_API_KEY",      "（未設定）")
        model     = os.getenv("LLM_MODEL_NAME",   "（未設定）")
        raw_max   = os.getenv("LLM_MAX_OUTPUT_TOKENS", "").strip()
        max_out   = raw_max if raw_max else "モデルのデフォルト最大値（指定なし）"

        # APIキーは末尾4文字だけ表示（セキュリティ配慮）
        if api_key not in ("（未設定）",) and len(api_key) > 4:
            api_key_display = "*" * (len(api_key) - 4) + api_key[-4:]
        else:
            api_key_display = api_key

        print("\n現在の設定:（すべて .env から読み込み）")
        print(f"  API URL          : {api_url}")
        print(f"  API Key          : {api_key_display}")
        print(f"  モデル名         : {model}")
        print(f"  最大出力トークン : {max_out}")
        print(f"  設定ファイル     : {env_path}")
        print()
        print("変更するには:")
        print("  python main.py config model                      # モデルをインデックスで選択")
        print("  python main.py config output-tokens 4096         # 最大出力トークン数を変更")
        print("  python main.py config output-tokens auto         # モデルのデフォルト最大値を使用")
        print()
        print("  ※ API URL / API Key は .env を直接編集してください。")

        # 未設定項目があれば警告（LLM_API_KEY は任意のため除外）
        missing = [v for v in ("LLM_API_BASE_URL", "LLM_MODEL_NAME")
                   if not os.getenv(v, "").strip()]
        if missing:
            print()
            print("[WARNING] 以下の必須項目が未設定です:")
            for v in missing:
                print(f"  {v}")
            print("  .env.example を参考に .env を設定してください。")


# ---------------------------------------------------------------------------
# audit コマンド
# ---------------------------------------------------------------------------

def cmd_audit(args: argparse.Namespace) -> None:
    from ai_audit.config_manager import validate_env
    validate_env()

    from ai_audit.usecase_a import audit_directory, audit_file, save_audit_json

    path = args.path

    if os.path.isdir(path):
        audit_directory(path, force=args.force, output_dir=args.output_dir)
    elif os.path.isfile(path):
        results = audit_file(path, force=args.force)
        if results:
            save_audit_json(path, results)
    else:
        print(f"[ERROR] ファイルまたはディレクトリが見つかりません: {path}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# その他コマンド
# ---------------------------------------------------------------------------

def cmd_extract_why(args: argparse.Namespace) -> None:
    from ai_audit.config_manager import validate_env
    validate_env()
    from ai_audit.usecase_b import extract_why
    extract_why(args.directory)


def cmd_search_why(args: argparse.Namespace) -> None:
    from ai_audit.config_manager import validate_env
    validate_env()
    from ai_audit.usecase_b import print_search_results, search_why
    results = search_why(args.query, top_k=args.top_k)
    print_search_results(results)


def cmd_review_architecture(args: argparse.Namespace) -> None:
    from ai_audit.config_manager import validate_env
    validate_env()
    from ai_audit.usecase_c import review_architecture
    report = review_architecture(args.directory, output_file=args.output)
    if not args.output:
        print(report)


# ---------------------------------------------------------------------------
# パーサー定義
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai_audit",
        description="120Bモデル×4096トークン環境向け 生成AI活用CLIツール",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  # 設定確認・変更
  python main.py config                           # 現在の設定を表示
  python main.py config model                     # モデル一覧を表示してインデックスで切り替え
  python main.py config output-tokens 4096        # 最大出力トークン数を 4096 に変更
  python main.py config output-tokens auto        # モデルのデフォルト最大値を使用（指定なし）

  # ユースケースA: 監査
  python main.py audit src/user_service.py        # 単一ファイル
  python main.py audit ./src                      # フォルダ一括
  python main.py audit ./src --output-dir ./audit_results

  # ユースケースB
  python main.py extract_why ./src
  python main.py search_why "レガシーAPIとの互換性のために複雑な実装をしている箇所"

  # ユースケースC
  python main.py review_architecture ./src --output report.md
""",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- config ---
    p_config = subparsers.add_parser(
        "config",
        help="LLM設定の表示・変更（モデル選択・最大トークン数）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
サブアクション:
  (なし)                   現在の設定を表示（.env の内容）
  model                    Ollamaモデル一覧をインデックス付きで表示し、番号で切り替え
                             → .env の LLM_MODEL_NAME を更新する
  output-tokens [値]       最大出力トークン数を表示または変更
                             → .env の LLM_MAX_OUTPUT_TOKENS を更新する
                             整数値: その値を上限として指定
                             auto  : モデルのデフォルト最大値を使用（指定なし）

  ※ API URL / API Key は .env を直接編集してください。

例:
  python main.py config
  python main.py config model
  python main.py config output-tokens
  python main.py config output-tokens 4096
  python main.py config output-tokens auto
""",
    )
    p_config.add_argument(
        "config_action",
        nargs="?",
        choices=["model", "output-tokens", "show"],
        default="show",
        help="実行するアクション（省略時: show）",
    )
    p_config.add_argument(
        "value",
        nargs="?",
        default=None,
        help="output-tokens アクション時の設定値",
    )
    p_config.set_defaults(func=cmd_config)

    # --- audit ---
    p_audit = subparsers.add_parser(
        "audit",
        help="ユースケースA: ファイルまたはフォルダをセキュリティ・クリーンコードの観点で多重監査する",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
引数:
  path にファイルを指定 → 単一ファイルを監査し <ファイル名>_audit.json を出力
  path にフォルダを指定 → 配下の全 .py ファイルを一括監査し _summary_audit.json も出力

出力先:
  --output-dir 未指定: 各ファイルと同じディレクトリに _audit.json を出力
  --output-dir 指定:   ディレクトリ構造を保持して指定先に集約出力
""",
    )
    p_audit.add_argument("path", help="監査対象のPythonファイルまたはディレクトリ")
    p_audit.add_argument("--force", action="store_true", default=False, help="キャッシュを無視して再監査する")
    p_audit.add_argument("--output-dir", dest="output_dir", default=None, metavar="DIR",
                         help="結果JSONの出力先ディレクトリ（フォルダ一括監査時のみ有効）")
    p_audit.set_defaults(func=cmd_audit)

    # --- extract_why ---
    p_extract = subparsers.add_parser(
        "extract_why",
        help="ユースケースB: ディレクトリ内の全関数の設計思想を抽出してDBに蓄積する",
    )
    p_extract.add_argument("directory", help="スキャン対象のディレクトリ")
    p_extract.set_defaults(func=cmd_extract_why)

    # --- search_why ---
    p_search = subparsers.add_parser(
        "search_why",
        help="ユースケースB: 蓄積した設計思想を自然言語クエリで検索する",
    )
    p_search.add_argument("query", help="検索クエリ（自然言語）")
    p_search.add_argument("--top-k", type=int, default=5, help="返す結果の最大件数（デフォルト: 5）")
    p_search.set_defaults(func=cmd_search_why)

    # --- review_architecture ---
    p_review = subparsers.add_parser(
        "review_architecture",
        help="ユースケースC: ASTスケルトン解析でアーキテクチャをレビューする",
    )
    p_review.add_argument("directory", help="レビュー対象のディレクトリ")
    p_review.add_argument("--output", "-o", default=None,
                          help="レポートを保存するMarkdownファイルパス（省略時は標準出力）")
    p_review.set_defaults(func=cmd_review_architecture)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
