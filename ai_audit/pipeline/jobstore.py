"""
ジョブストア: パイプラインの各ステップの成果物を入力ハッシュ付きで保存する

保存先: <対象>/.ai_audit/work/<パイプライン名>/
  steps/<ステップ名を安全化したもの>.json  {"step", "input_hash", "saved_at", "data"}
  job.json                                 直近ジョブの進捗（status コマンドで表示）

書き込みは一時ファイル → rename で行い、途中終了しても壊れたファイルを残さない。
"""
import hashlib
import json
import os
import re
import tempfile
import time


def stable_hash(obj) -> str:
    """dict / list / str などから安定したハッシュを作る。"""
    text = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def atomic_write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class JobStore:
    def __init__(self, work_dir: str):
        self.work_dir = work_dir
        self.steps_dir = os.path.join(work_dir, "steps")
        os.makedirs(self.steps_dir, exist_ok=True)

    def _path(self, step: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", step)[:80]
        return os.path.join(self.steps_dir, f"{safe}-{hashlib.sha1(step.encode()).hexdigest()[:8]}.json")

    def get(self, step: str, input_hash: str):
        """同じ入力ハッシュの成果物があれば (True, data)、なければ (False, None)。"""
        try:
            with open(self._path(step), "r", encoding="utf-8") as f:
                entry = json.load(f)
        except (OSError, ValueError):
            return False, None
        if entry.get("input_hash") != input_hash:
            return False, None
        return True, entry.get("data")

    def put(self, step: str, input_hash: str, data) -> None:
        entry = {"step": step, "input_hash": input_hash, "saved_at": time.time(), "data": data}
        atomic_write(self._path(step), json.dumps(entry, ensure_ascii=False, indent=1))

    # --- 進捗 ---
    def write_status(self, status: dict) -> None:
        atomic_write(os.path.join(self.work_dir, "job.json"),
                     json.dumps(status, ensure_ascii=False, indent=1))

    def read_status(self) -> dict | None:
        try:
            with open(os.path.join(self.work_dir, "job.json"), "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None
