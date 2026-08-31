"""Inventory storage and validation."""

from __future__ import annotations

from collections.abc import Mapping


class InsufficientStockError(ValueError):
    """Raised when an item cannot be reserved."""


class Inventory:
    def __init__(self, initial_stock: Mapping[str, int]) -> None:
        self._stock = dict(initial_stock)

    def available(self, sku: str) -> int:
        return self._stock.get(sku, 0)

    def ensure_available(self, sku: str, quantity: int) -> None:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        available = self.available(sku)
        if available < quantity:
            raise InsufficientStockError(
                f"{sku}: requested {quantity}, available {available}"
            )

    def remove(self, sku: str, quantity: int) -> None:
        self.ensure_available(sku, quantity)
        self._stock[sku] -= quantity

    def snapshot(self) -> dict[str, int]:
        return dict(self._stock)
