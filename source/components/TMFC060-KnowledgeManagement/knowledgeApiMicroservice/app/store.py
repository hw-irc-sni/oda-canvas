"""The pyoxigraph store. The API process is its only writer (see the design: single-writer RocksDB)."""
from functools import lru_cache

from pyoxigraph import Store


@lru_cache
def get_store(path: str) -> Store:
    return Store(path)
