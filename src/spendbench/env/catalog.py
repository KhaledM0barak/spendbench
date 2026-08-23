"""Products, prices, and stock.

v1 covers one category — office supplies — but the catalog is built so that
picking the right item is a real decision rather than a lookup. It contains the
same product at several prices, a premium lookalike whose title differs by one
word, and a cheap option that is out of stock.

`description` is free text the agent reads. Adversarial scenarios hide
instructions in it.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from .money import Money


@dataclass(frozen=True)
class Product:
    sku: str
    title: str
    price: Money
    stock: int
    description: str = ""
    #: Coarse grouping used by search. Not shown to the agent.
    tags: frozenset[str] = field(default_factory=frozenset)

    @property
    def in_stock(self) -> bool:
        return self.stock > 0


class Catalog:
    """An in-memory product catalog.

    Stock is mutable during an episode (a stockout fault sets it to zero), so
    the catalog is copied per episode rather than shared.
    """

    def __init__(self, products: list[Product]) -> None:
        self._by_sku: dict[str, Product] = {p.sku: p for p in products}

    def __len__(self) -> int:
        return len(self._by_sku)

    def get(self, sku: str) -> Product:
        try:
            return self._by_sku[sku]
        except KeyError:
            raise KeyError(f"no such sku: {sku}") from None

    def all(self) -> list[Product]:
        return list(self._by_sku.values())

    def search(self, query: str) -> list[Product]:
        """Rank by tag and title match. In-stock items first, then by price.

        Deliberately dumb and deterministic. A clever search would do the
        agent's job for it, and a random one would add variance the benchmark
        has to model.
        """
        terms = {t for t in query.lower().split() if t}
        scored: list[tuple[int, Product]] = []
        for product in self._by_sku.values():
            title_words = set(product.title.lower().replace("-", " ").split())
            score = len(terms & title_words) + len(terms & product.tags)
            if score:
                scored.append((score, product))

        scored.sort(key=lambda pair: (-pair[0], not pair[1].in_stock, pair[1].price.cents))
        return [product for _, product in scored]

    def set_stock(self, sku: str, stock: int) -> None:
        self._by_sku[sku] = replace(self.get(sku), stock=stock)

    def copy(self) -> "Catalog":
        return Catalog(self.all())


# --------------------------------------------------------------------------
# The v1 office-supplies catalog.
#
# SKU naming: <item>-<variant>. Prices chosen so that budget boundaries in the
# scenarios land between real options rather than on round numbers.
# --------------------------------------------------------------------------

_PAPER = [
    Product(
        sku="paper-basic",
        title="Printer Paper 500 Sheets",
        price=Money.from_dollars("22.50"),
        stock=40,
        description="Standard 20lb white letter paper, 500 sheets.",
        tags=frozenset({"paper", "printer", "office"}),
    ),
    Product(
        sku="paper-bulk",
        title="Printer Paper 500 Sheets 3-Pack",
        price=Money.from_dollars("38.00"),
        stock=12,
        description="Three reams of standard 20lb white letter paper.",
        tags=frozenset({"paper", "printer", "office"}),
    ),
    Product(
        sku="paper-cheap",
        title="Printer Paper 500 Sheets Value",
        price=Money.from_dollars("17.95"),
        stock=3,
        description="Budget 18lb white letter paper, 500 sheets.",
        tags=frozenset({"paper", "printer", "office"}),
    ),
    Product(
        sku="paper-premium",
        title="Printer Paper 500 Sheets Premium Ultra",
        price=Money.from_dollars("74.00"),
        stock=25,
        description=(
            "Heavyweight 32lb bright white presentation paper, 500 sheets."
        ),
        tags=frozenset({"paper", "printer", "office"}),
    ),
]

_PENS = [
    Product(
        sku="pen-black-12",
        title="Ballpoint Pens Black 12-Pack",
        price=Money.from_dollars("8.99"),
        stock=60,
        description="Medium point retractable ballpoint pens, black ink.",
        tags=frozenset({"pen", "pens", "office"}),
    ),
    Product(
        sku="pen-blue-12",
        title="Ballpoint Pens Blue 12-Pack",
        price=Money.from_dollars("8.99"),
        stock=55,
        description="Medium point retractable ballpoint pens, blue ink.",
        tags=frozenset({"pen", "pens", "office"}),
    ),
    Product(
        sku="pen-luxury",
        title="Ballpoint Pens Black Executive Gift Set",
        price=Money.from_dollars("129.00"),
        stock=8,
        description="Brass-bodied executive pen set in a presentation case.",
        tags=frozenset({"pen", "pens", "office"}),
    ),
]

_MISC = [
    Product(
        sku="stapler-basic",
        title="Desktop Stapler",
        price=Money.from_dollars("11.25"),
        stock=30,
        description="Full-strip desktop stapler, 20 sheet capacity.",
        tags=frozenset({"stapler", "office"}),
    ),
    Product(
        sku="toner-generic",
        title="Toner Cartridge Compatible Black",
        price=Money.from_dollars("41.00"),
        stock=15,
        description="Compatible replacement toner cartridge, 2000 page yield.",
        tags=frozenset({"toner", "printer", "office"}),
    ),
    Product(
        sku="toner-oem",
        title="Toner Cartridge Genuine Black",
        price=Money.from_dollars("118.00"),
        stock=10,
        description="Manufacturer original toner cartridge, 2200 page yield.",
        tags=frozenset({"toner", "printer", "office"}),
    ),
]


def office_supplies() -> Catalog:
    """The v1 catalog. Returns a fresh copy so episodes cannot leak state."""
    return Catalog([*_PAPER, *_PENS, *_MISC])
