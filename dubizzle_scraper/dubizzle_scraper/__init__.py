"""Search, filter, analyse and export dubizzle UAE listings through its Algolia search API."""

__version__ = "0.2.0"

from .client import AlgoliaClient, AlgoliaError  # noqa: E402
from .export import flatten  # noqa: E402
from .query import resolve as resolve_options  # noqa: E402
from .scraper import ALIASES, build_filters, categories, count, iter_listings, resolve  # noqa: E402
from .service import make_client  # noqa: E402
from . import service  # noqa: E402

__all__ = ["AlgoliaClient", "AlgoliaError", "ALIASES", "build_filters", "categories", "count", "flatten",
           "iter_listings", "make_client", "resolve", "resolve_options", "service"]
