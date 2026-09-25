"""Scrape dubizzle UAE listings through its Algolia search API."""

from .client import AlgoliaClient, AlgoliaError
from .export import flatten
from .scraper import ALIASES, build_filters, categories, count, iter_listings, resolve

__all__ = ["AlgoliaClient", "AlgoliaError", "ALIASES", "build_filters", "categories", "count",
           "flatten", "iter_listings", "resolve"]
__version__ = "0.1.0"
