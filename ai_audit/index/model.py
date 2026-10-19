"""
インデックスのデータモデル: ファイル・シンボル・import の表現

シンボル ID は「リポジトリ相対パス::修飾名」（例: shop/service.py::OrderService.checkout）。
別クラスの同名メソッドでも衝突せず、別マシンでも同じ ID になる。
"""
from dataclasses import dataclass, field


@dataclass
class Call:
    name: str          # 呼び出し式（例: self.repo.save / helper）
    line: int

    @property
    def simple_name(self) -> str:
        return self.name.rsplit(".", 1)[-1]


@dataclass
class Symbol:
    id: str
    file: str
    name: str
    qualname: str
    kind: str                      # class / function / method / interface / type / enum
    lang: str
    start_line: int
    end_line: int
    signature: str = ""
    doc: str = ""
    parent: str | None = None      # 親シンボル ID（メソッドならクラス）
    bases: list[str] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    exported: bool = True

    @property
    def loc(self) -> int:
        return self.end_line - self.start_line + 1


@dataclass
class ImportRef:
    module: str                    # import 対象（例: shop.models / ./util / package:x/y.dart）
    names: list[str] = field(default_factory=list)
    line: int = 0
    level: int = 0                 # Python の相対 import の深さ


@dataclass
class ParsedFile:
    path: str                      # リポジトリ相対パス（/ 区切り）
    lang: str
    sha256: str
    loc: int
    module_doc: str = ""
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[ImportRef] = field(default_factory=list)
    skeleton: str = ""
    error: str | None = None


def make_symbol_id(path: str, qualname: str) -> str:
    return f"{path}::{qualname}"


def split_symbol_id(symbol_id: str) -> tuple[str, str]:
    path, _, qualname = symbol_id.partition("::")
    return path, qualname
