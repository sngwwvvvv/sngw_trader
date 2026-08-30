"""Write market data into ParquetDataCatalog.

Do not place orders here. Do not start LiveNode here.
"""

from __future__ import annotations

from pathlib import Path


def write_placeholder_note(catalog_path: Path) -> Path:
    catalog_path.mkdir(parents=True, exist_ok=True)
    note = catalog_path / "README.txt"
    note.write_text(
        "Put Nautilus parquet catalog files here.\n"
        "Use ParquetDataCatalog.write_instruments / write_bars / write_quote_ticks.\n"
        "Do not call OKX order APIs from this module.\n",
        encoding="utf-8",
    )
    return note
