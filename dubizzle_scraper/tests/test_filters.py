import io
import json
import urllib.parse

import pytest

from dubizzle_scraper import AlgoliaClient, service
from dubizzle_scraper.mcp_server import TOOLS, Server, serve
from dubizzle_scraper.query import parse_range, parse_where, range_clause, resolve


class FakeAlgolia:
    """Answers facet lookups the resolver needs, and records every request."""

    FACETS = {
        "category_v2.slug_paths": {"motors": 10, "motors/used-cars": 10, "motors/used-cars/lexus": 5,
                                   "motors/used-cars/lexus/rx-series": 3, "motors/used-cars/lexus/lx-series": 2},
        "seo_links.location_links": {"property-for-rent/residential/in/dubai-marina/63": 50,
                                     "property-for-rent/residential/in/dubai-marina/63/marina-gate/99": 5,
                                     "property-for-rent/residential/in/jumeirah-village-circle-jvc/61984": 80,
                                     "property-for-rent/residential/in/jumeirah-village-circle-jvc/61984/"
                                     "jvc-district-10/63119": 20},
    }
    SPEC_HITS = [
        {"value": "motors/used-cars/s/fuel-type/hybrid/", "count": 9},
        {"value": "motors/used-cars/lexus/s/fuel-type/hybrid/", "count": 4},
        {"value": "motors/used-cars/s/fuel-type/plug-in-hybrid/", "count": 2},
        {"value": "classified/phones/apple/s/condition/pre-owned/", "count": 7},
        {"value": "classified/phones/samsung/s/condition/pre-owned/", "count": 3},
    ]

    def __init__(self):
        self.requests = []

    def __call__(self, url, body, headers, timeout):
        params = dict(urllib.parse.parse_qsl(json.loads(body)["params"]))
        self.requests.append((url, params))
        if "/facets/" in url:
            q = params["facetQuery"].replace(" ", "-")
            return {"facetHits": [h for h in self.SPEC_HITS if q in h["value"]]}
        facets = json.loads(params.get("facets", "[]"))
        return {"hits": [], "nbHits": 42, "nbPages": 0,
                "facets": {f: self.FACETS.get(f, {}) for f in facets}}


@pytest.fixture
def fake():
    return FakeAlgolia()


@pytest.fixture
def client(fake):
    return AlgoliaClient("APP", "KEY", delay=0, transport=fake)


@pytest.mark.parametrize("value,expected", [
    (2021, (2021, 2021)), ("2021", (2021, 2021)), ("2019..2022", (2019, 2022)), ("3+", (3, None)),
    ("..60000", (None, 60000)), ("50,000..90,000", (50000, 90000)), ([1, None], (1, None)),
    ({"min": 2, "max": 4}, (2, 4)), ("3-7", (3, 7)), (None, (None, None)),
])
def test_parse_range(value, expected):
    assert parse_range(value) == expected


def test_range_clause():
    assert range_clause("year", "2021") == "year = 2021"
    assert range_clause("price", "..90000") == "price <= 90000"
    assert range_clause("price", "5000+") == "price >= 5000"
    assert range_clause("size", "800..1200") == "size:800 TO 1200"


@pytest.mark.parametrize("expr,expected", [
    ("bedrooms>=2", "bedrooms >= 2"),
    ("site.en=Dubai|Sharjah", '(site.en:"Dubai" OR site.en:"Sharjah")'),
    ("seller_type!=DL", 'NOT seller_type:"DL"'),
    ("year=2019..2022", "year:2019 TO 2022"),
    ("furnished=true", "furnished:true"),
    ('details.Year.en.value="2021"', 'details.Year.en.value:"2021"'),
    ("details.Employment Type.en.value=Full Time", '"details.Employment Type.en.value":"Full Time"'),
    ("price < 100 AND year > 2020", "price < 100 AND year > 2020"),
])
def test_parse_where(expr, expected):
    assert parse_where(expr) == expected


def test_resolve_cars_make_model_city_and_ranges(client):
    r = resolve(client, {"target": "cars", "make": "lexus", "model": "rx", "city": "dubai|sharjah",
                         "year": "2021", "km": "..60000", "price": [None, 120000], "since_days": 7})
    assert r.category == "motors/used-cars/lexus/rx-series"
    f = r.filters
    assert 'category_v2.slug_paths:"motors/used-cars/lexus/rx-series"' in f
    assert "(site.id = 2 OR site.id = 12)" in f
    assert "year = 2021" in f and "kilometers <= 60000" in f and "price <= 120000" in f
    assert "added >= " in f
    assert any("rx-series" in n for n in r.notes)


def test_resolve_spec_prefers_exact_value_and_top_level_link(client):
    r = resolve(client, {"target": "cars", "spec": {"fuel-type": "hybrid"}})
    assert 'seo_links.attribute_links:"motors/used-cars/s/fuel-type/hybrid/"' in r.filters
    assert "plug-in" not in r.filters and "lexus/s/" not in r.filters


def test_resolve_spec_ors_per_brand_links(client):
    r = resolve(client, {"target": "classifieds", "spec": "condition=pre-owned"})
    assert ('(seo_links.attribute_links:"classified/phones/apple/s/condition/pre-owned/" OR '
            'seo_links.attribute_links:"classified/phones/samsung/s/condition/pre-owned/")') in r.filters


def test_resolve_property_area_prefers_broadest_whole_word_match(client):
    r = resolve(client, {"target": "property-rent", "area": "jvc", "beds": "2+", "furnished": "yes",
                         "rent_period": "yearly", "seller": "landlord"})
    f = r.filters
    assert 'seo_links.location_links:"property-for-rent/residential/in/jumeirah-village-circle-jvc/61984"' in f
    assert "bedrooms >= 2" in f and "furnished:true" in f
    assert 'rent_is_paid.name.en:"Yearly"' in f and 'listed_by.value:"LA"' in f


def test_resolve_rejects_misapplied_options(client):
    with pytest.raises(ValueError):
        resolve(client, {"target": "cars", "beds": 2})
    with pytest.raises(ValueError):
        resolve(client, {"target": "cars", "city": "atlantis"})
    with pytest.raises(ValueError):
        resolve(client, {"target": "cars", "colour": "red"})


def test_classifieds_city_uses_site_tree(client):
    r = resolve(client, {"target": "classifieds", "city": "abu dhabi"})
    assert r.filters == 'site_categories_slug_tree:"abudhabi/classified"'


def test_sort_hits():
    hits = [{"price": 5}, {"price": None}, {"price": 1}, {"price": 3}]
    assert [h["price"] for h in service.sort_hits(hits, "cheapest")] == [1, 3, 5, None]
    assert [h["price"] for h in service.sort_hits(hits, "price:desc")] == [5, 3, 1, None]
    assert [h["price"] for h in service.sort_hits(hits, "-price")] == [5, 3, 1, None]


def test_mcp_protocol_roundtrip(client):
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "count_listings", "arguments": {"target": "cars", "make": "lexus"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "count_listings", "arguments": {"target": "cars", "beds": 3}}},
        {"jsonrpc": "2.0", "id": 5, "method": "nope"},
    ]
    server = Server(client)
    replies = [server.handle(m) for m in lines]
    assert replies[1] is None  # notification
    assert replies[0]["result"]["protocolVersion"] == "2025-03-26"
    assert {t["name"] for t in replies[2]["result"]["tools"]} == {t["name"] for t in TOOLS}
    ok = replies[3]["result"]
    assert ok["isError"] is False and json.loads(ok["content"][0]["text"])["count"] == 42
    assert replies[4]["result"]["isError"] is True
    assert replies[5]["error"]["code"] == -32601


def test_mcp_tool_schemas_use_single_types():
    for tool in TOOLS:
        for name, prop in tool["inputSchema"]["properties"].items():
            assert isinstance(prop.get("type"), str), f"{tool['name']}.{name}"


def test_serve_reads_and_writes_lines(monkeypatch, client):
    monkeypatch.setattr(service, "make_client", lambda *a, **k: client)
    out = io.StringIO()
    serve(stdin=io.StringIO('{"jsonrpc":"2.0","id":7,"method":"ping"}\n\nnot json\n'), stdout=out)
    replies = [json.loads(line) for line in out.getvalue().splitlines()]
    assert replies[0] == {"jsonrpc": "2.0", "id": 7, "result": {}}
    assert replies[1]["error"]["code"] == -32700
