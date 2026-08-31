"""Restore the intentionally buggy service before a new demo run."""

from __future__ import annotations

from pathlib import Path


BUGGY_SOURCE = '''"""Order inventory operations."""

from __future__ import annotations

from collections.abc import Mapping

from shop.inventory import Inventory


def reserve_order(inventory: Inventory, items: Mapping[str, int]) -> None:
    """Reserve every item, or leave the complete inventory unchanged."""

    # The demo intentionally starts with a bug: earlier items may be removed
    # before a later item is found to have insufficient stock.
    for sku, quantity in items.items():
        inventory.remove(sku, quantity)
'''


def main() -> None:
    target = (
        Path(__file__).resolve().parent
        / "inventory_reservation"
        / "shop"
        / "service.py"
    )
    target.write_text(BUGGY_SOURCE, encoding="utf-8", newline="\n")
    print("Demo restored: the initial test should fail again.")


if __name__ == "__main__":
    main()
