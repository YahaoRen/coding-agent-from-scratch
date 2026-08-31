"""Order inventory operations."""

from __future__ import annotations

from collections.abc import Mapping

from shop.inventory import Inventory


def reserve_order(inventory: Inventory, items: Mapping[str, int]) -> None:
    """Reserve every item, or leave the complete inventory unchanged."""

    # The demo intentionally starts with a bug: earlier items may be removed
    # before a later item is found to have insufficient stock.
    for sku, quantity in items.items():
        inventory.remove(sku, quantity)
