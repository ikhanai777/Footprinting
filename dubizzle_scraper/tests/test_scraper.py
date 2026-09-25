import io
import json
import urllib.parse

import pytest

from dubizzle_scraper import AlgoliaClient, build_filters, count, flatten, iter_listings, resolve
from dubizzle_scraper import scraper
from dubizzle_scraper.client import encode_params
from dubizzle_scraper.export import write


class FakeIndex:
    """Mimics Algolia: numeric `added` range filters and a pagination cap."""

    def __init__(self, n, cap=100):
        self.docs = [{"objectID": f"item:{i}", "id": i, "added": 1_000 + i,
                      "name": {"en": f"Listing {i}", "ar": "x"}, "price": float(i)} for i in range(n)]
        self.cap = cap
        self.calls = []

    def __call__(self, url, body, headers, timeout):
        params = dict(urllib.parse.parse_qsl(json.loads(body)["params"]))
        self.calls.append(params)
        docs = self.docs
        for f in json.loads(params.get("numericFilters", "[]")):
            if " TO " in f:
                field, rng = f.split(":")
                lo, hi = map(int, rng.split(" TO "))
                docs = [d for d in docs if lo <= d[field] <= hi]
        docs = sorted(docs, key=lambda d: -d["added"])
        hpp = int(params.get("hitsPerPage", 20))
        page = int(params.get("page", 0))
        reachable = docs[: self.cap]
        pages = 0 if hpp == 0 else -(-len(reachable) // hpp)
        hits = [] if hpp == 0 else reachable[page * hpp:(page + 1) * hpp]
        return {"hits": hits, "nbHits": len(docs), "nbPages": pages, "hitsPerPage": hpp}


@pytest.fixture
def small_limits(monkeypatch):
    monkeypatch.setattr(scraper, "MAX_WINDOW", 100)
    monkeypatch.setattr(scraper, "SPLIT_ABOVE", 80)
    monkeypatch.setattr(scraper, "HITS_PER_PAGE", 30)


def client(fake):
    return AlgoliaClient("APP", "KEY", delay=0, transport=fake)


def test_small_result_is_paged_without_splitting(small_limits):
    fake = FakeIndex(70)
    hits = list(iter_listings(client(fake), "idx"))
    assert len(hits) == 70
    assert not any("numericFilters" in c for c in fake.calls)


def test_large_result_is_split_and_complete(small_limits):
    fake = FakeIndex(1000)
    ids = [h["id"] for h in iter_listings(client(fake), "idx")]
    assert sorted(ids) == list(range(1000))
    assert len(ids) == len(set(ids))
    assert ids[0] == 999  # newest window first


def test_limit_stops_early(small_limits):
    fake = FakeIndex(1000)
    assert len(list(iter_listings(client(fake), "idx", limit=45))) == 45


def test_count_and_filters():
    fake = FakeIndex(5)
    assert count(client(fake), "idx") == 5
    assert build_filters("motors/used-cars", "year >= 2020") == \
        'category_v2.slug_paths:"motors/used-cars" AND (year >= 2020)'
    assert build_filters() is None
    assert resolve("cars") == ("motors.com", "motors/used-cars")
    assert resolve("some.index") == ("some.index", None)


def test_encode_params():
    q = dict(urllib.parse.parse_qsl(encode_params({"a": [1, "x"], "b": False, "c": None, "d": "hi there"})))
    assert q == {"a": '[1,"x"]', "b": "false", "d": "hi there"}


def test_flatten_localizes_and_unwraps_specs():
    hit = {
        "id": 1,
        "name": {"en": "Car", "ar": "سيارة"},
        "details": {"Doors": {"en": {"label": "Doors", "value": "2 door"}, "ar": {"label": "x", "value": "y"}}},
        "photos": ["a.jpg", "b.jpg"],
        "_highlightResult": {"name": {}},
    }
    assert flatten(hit) == {"id": 1, "name": "Car", "details.Doors": "2 door", "photos": "a.jpg | b.jpg"}
    assert flatten(hit, lang="ar")["name"] == "سيارة"


def test_write_csv_and_jsonl():
    hits = [{"id": 1, "name": {"en": "A"}}, {"id": 2, "price": 5}]
    out = io.StringIO()
    assert write(hits, out, "csv") == 2
    assert out.getvalue().splitlines() == ["id,name,price", "1,A,", "2,,5"]
    out = io.StringIO()
    write(hits, out, "jsonl", fields=["id", "name"])
    assert [json.loads(l) for l in out.getvalue().splitlines()] == [{"id": 1, "name": "A"}, {"id": 2, "name": None}]
