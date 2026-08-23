"""Products, prices, and stock.

STUB. v1 covers one category (office supplies) with enough near-duplicate
listings that picking the right item is a real decision: same product at three
prices, a premium lookalike, and a cheap option that is out of stock.
"""

from __future__ import annotations

from dataclasses import dataclass

from .money import Money


@dataclass(frozen=True)
class Product:
    sku: str
    title: str
    price: Money
    stock: int
    #: Free-text description. Adversarial scenarios hide instructions in here.
    description: str = ""


class Catalog:
    def __init__(self, products: list[Product]) -> None:
        self._by_sku = {p.sku: p for p in products}

    def get(self, sku: str) -> Product:
        return self._by_sku[sku]

    def search(self, query: str) -> list[Product]:
        raise NotImplementedError("v1: rank by title match, return all in-stock")
