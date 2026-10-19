"""ドメインモデル。"""
from dataclasses import dataclass


@dataclass
class Item:
    """商品。"""
    name: str
    price: int

    def label(self) -> str:
        return f"{self.name} ({self.price}円)"


class Cart:
    """買い物かご。"""

    def __init__(self):
        self.items: list[Item] = []

    def add(self, item: Item) -> None:
        self.items.append(item)

    def total(self) -> int:
        return sum(i.price for i in self.items)
