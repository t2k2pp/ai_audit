"""
パイプラインエンジン: ステップの実行・再利用（再開）・並列実行・進捗記録

ステップの入力ハッシュ = ステップ固有の入力 + モデル名 + ティア + 共通バージョン。
同じ入力ハッシュの成果物があれば再実行しない。これにより
  - 中断後の再実行は完了済みステップを飛ばす
  - コード変更後は影響するステップだけ再実行する
"""
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .jobstore import JobStore, stable_hash


class StepFailed(Exception):
    pass


class Pipeline:
    def __init__(self, name: str, store: JobStore, runtime=None, force: bool = False,
                 version: str = "1", quiet: bool = False, lang: str = "ja"):
        self.name = name
        self.lang = lang
        self.store = store
        self.runtime = runtime
        self.force = force
        self.version = version
        self.quiet = quiet
        self.stats = {"run": 0, "reused": 0, "failed": 0}
        self.failures: list[tuple[str, str]] = []
        self._lock = threading.Lock()
        self._status = {"pipeline": name, "started_at": time.time(), "finished_at": None,
                        "stage": "", "done": 0, "total": 0, "failed": 0, "reused": 0}

    def _context(self) -> dict:
        if self.runtime is None:
            return {"v": self.version}
        return {"v": self.version, "model": self.runtime.settings.model_name, "tier": self.runtime.tier}

    def input_hash(self, inputs) -> str:
        return stable_hash({"ctx": self._context(), "inputs": inputs})

    def log(self, msg: str) -> None:
        if not self.quiet:
            print(msg, flush=True)

    def stage(self, title: str, total: int = 0) -> None:
        self._status.update(stage=title, done=0, total=total)
        self.store.write_status(self._status)
        self.log(f"[STAGE] {title}" + (f"（{total} 件）" if total else ""))

    def step(self, name: str, inputs, fn):
        """1 ステップ実行する。成果物を返す（失敗時は StepFailed）。"""
        h = self.input_hash(inputs)
        if not self.force:
            hit, data = self.store.get(name, h)
            if hit:
                with self._lock:
                    self.stats["reused"] += 1
                    self._status["reused"] += 1
                return data
        try:
            data = fn()
        except StepFailed:
            raise
        except Exception as e:  # ステップ単位で失敗を記録し、全体は継続できるようにする
            with self._lock:
                self.stats["failed"] += 1
                self._status["failed"] += 1
                self.failures.append((name, str(e)))
            print(f"  [ERROR] {name}: {e}", file=sys.stderr)
            raise StepFailed(name) from e
        self.store.put(name, h, data)
        with self._lock:
            self.stats["run"] += 1
        return data

    def map(self, items: list, name_fn, inputs_fn, fn, label_fn=None) -> list:
        """
        items の各要素についてステップを実行する（LLM_PARALLEL に応じて並列）。
        戻り値は items と同じ順序のリストで、失敗した要素は None。
        """
        parallel = self.runtime.settings.parallel if self.runtime else 1
        results = [None] * len(items)
        total = len(items)
        self._status.update(done=0, total=total)

        def run(i):
            item = items[i]
            return i, self.step(name_fn(item), inputs_fn(item), lambda: fn(item))

        def finish(i, ok):
            with self._lock:
                self._status["done"] += 1
                self.store.write_status(self._status)
            label = label_fn(items[i]) if label_fn else name_fn(items[i])
            self.log(f"  [{self._status['done']}/{total}] {'OK ' if ok else 'NG '} {label}")

        if parallel <= 1:
            for i in range(total):
                try:
                    _, results[i] = run(i)
                    finish(i, True)
                except StepFailed:
                    finish(i, False)
        else:
            with ThreadPoolExecutor(max_workers=parallel) as ex:
                futures = {ex.submit(run, i): i for i in range(total)}
                for fut in as_completed(futures):
                    i = futures[fut]
                    try:
                        _, results[i] = fut.result()
                        finish(i, True)
                    except StepFailed:
                        finish(i, False)
        return results

    def finish(self) -> None:
        self._status["finished_at"] = time.time()
        self._status["stage"] = "完了"
        self.store.write_status(self._status)
        self.log(f"[INFO] ステップ: 実行 {self.stats['run']} / 再利用 {self.stats['reused']} / "
                 f"失敗 {self.stats['failed']}")
        if self.failures:
            self.log("[WARN] 失敗したステップは次回実行時に再試行されます。")
