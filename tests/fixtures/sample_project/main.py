"""CLI エントリポイント。"""
import argparse

from shop.service import OrderRepository, quick_order


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("user")
    args = parser.parse_args()
    print(quick_order(args.user, "apple", 100, OrderRepository(":memory:")))


if __name__ == "__main__":
    main()
