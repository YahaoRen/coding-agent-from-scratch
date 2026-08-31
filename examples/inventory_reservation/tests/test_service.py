"""Tests for atomic inventory reservation."""

from __future__ import annotations

import unittest

from shop.inventory import Inventory, InsufficientStockError
from shop.service import reserve_order


class ReservationTests(unittest.TestCase):
    def test_successful_order_reduces_each_item(self) -> None:
        inventory = Inventory({"keyboard": 2, "mouse": 3})

        reserve_order(inventory, {"keyboard": 1, "mouse": 2})

        self.assertEqual(inventory.snapshot(), {"keyboard": 1, "mouse": 1})

    def test_failed_order_does_not_change_any_stock(self) -> None:
        inventory = Inventory({"keyboard": 2, "mouse": 1})
        before = inventory.snapshot()

        with self.assertRaisesRegex(InsufficientStockError, "mouse"):
            reserve_order(inventory, {"keyboard": 1, "mouse": 2})

        self.assertEqual(inventory.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
