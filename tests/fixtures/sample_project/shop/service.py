"""注文サービス。"""
import sqlite3

from .models import Cart, Item


class OrderRepository:
    """注文の永続化。"""

    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)

    def save(self, user: str, total: int) -> None:
        # 意図的な脆弱性（監査テスト用）
        self.conn.execute(f"INSERT INTO orders VALUES ('{user}', {total})")


class OrderService:
    """注文を確定する。"""

    def __init__(self, repo: OrderRepository):
        self.repo = repo

    def checkout(self, user: str, cart: Cart) -> int:
        total = cart.total()
        if total <= 0:
            raise ValueError("empty cart")
        self.repo.save(user, total)
        return total


def quick_order(user: str, name: str, price: int, repo: OrderRepository) -> int:
    cart = Cart()
    cart.add(Item(name, price))
    return OrderService(repo).checkout(user, cart)
