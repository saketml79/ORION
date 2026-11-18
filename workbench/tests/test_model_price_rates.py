"""Offline-only pricing adapter tests. The parent runs the consolidated suite."""
from copy import deepcopy
import json
from threading import Event

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from workbench import model_price_rates as pricing


STANDARD_HEADER = """<thead><tr><th rowspan="2">Model</th><th rowspan="2">&nbsp;</th>
<th colspan="5">Standard Pay Per Token (DBU Per 1M Tokens)</th></tr>
<tr><th>Input</th><th>Output</th><th>Cache read</th><th>Cache write</th>
<th>Cache write (1hr)</th></tr></thead>"""
OPUS_ROW = """<tr><td>Claude Opus 5.5 ⌖</td><td>&nbsp;</td><td>57.143</td>
<td>285.714</td><td>2.857</td><td>71.429</td><td>114.286</td></tr>"""
HTML = ("<h3>Pay Per Token</h3><table>" + STANDARD_HEADER + "<tbody>" + OPUS_ROW +
        "</tbody></table><h3>Batch</h3><table><tbody><tr>"
        "<td>Claude Opus 5.5 ⌖</td><td>171.429</td></tr></tbody></table>")
PAGE = {"result": {"data": {"drupal": {"generalPage": {"fieldComponents": [
    {"entity": {"fieldItems": [{"entity": {
        "fieldTitle": "Proprietary Foundation Model Serving DBU rates",
        "fieldDescription": {"processed": HTML},
        "fieldItem": {"entity": {"fieldPricingVendors": ["anthropic"]}},
    }}]}}
]}}}}}
CARDS = {"premium": {"Azure": {"prop_model_serving": {"Anthropic": "0.105", "OpenAI": "0.070"}}}}
SECRET = "fabricated-secret-do-not-propagate"


def html_in(page):
    return page["result"]["data"]["drupal"]["generalPage"]["fieldComponents"][0]["entity"]["fieldItems"][0]["entity"]["fieldDescription"]


class FakeResponse:
    def __init__(self, body=b"{}", status=200, headers=None):
        self.body, self.status, self.headers = body, status, headers or {}
        self.offset = 0

    def getheader(self, name, default=None):
        return self.headers.get(name.lower(), default)

    def read1(self, size):
        data = self.body[self.offset:self.offset + size]
        self.offset += len(data)
        return data


class FakeConnection:
    def __init__(self, host, timeout, response, calls):
        self.host, self.timeout, self.response, self.calls = host, timeout, response, calls
        self.sock = None
        self.closed = False
        calls.append(self)

    def request(self, method, path, headers):
        self.method, self.path, self.headers = method, path, headers

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def mock_connections(monkeypatch, responses):
    calls = []
    queue = iter(responses)
    monkeypatch.setattr(pricing.http.client, "HTTPSConnection",
                        lambda host, timeout: FakeConnection(host, timeout, next(queue), calls))
    return calls


def encoded(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def test_real_shaped_standard_row_and_card_are_multiplied_exactly():
    result = pricing._parse(PAGE, CARDS)
    assert result == {
        "endpoint": "databricks-claude-opus-5-5", "model": "Claude Opus 5.5",
        "currency": "USD", "input_usd_per_1m_tokens": "6.000015",
        "output_usd_per_1m_tokens": "29.999970",
        "input_dbu_per_1m_tokens": "57.143", "output_dbu_per_1m_tokens": "285.714",
        "usd_per_dbu": "0.105", "pricing_basis": "Azure Premium Anthropic, standard pay per token",
    }


def test_decimal_multiplication_does_not_round_to_default_context():
    page, cards = deepcopy(PAGE), deepcopy(CARDS)
    html_in(page)["processed"] = HTML.replace("57.143", "999999999.999999999")
    cards["premium"]["Azure"]["prop_model_serving"]["Anthropic"] = "999999999.999999999"
    assert pricing._parse(page, cards)["input_usd_per_1m_tokens"] == "999999999999999998.000000000000000001"


@pytest.mark.parametrize("change", [
    lambda html: html.replace(OPUS_ROW, OPUS_ROW + OPUS_ROW),
    lambda html: html.replace("<th>Input</th><th>Output</th>", "<th>Output</th><th>Input</th>"),
    lambda html: html.replace("colspan=\"5\"", "colspan=\"4\""),
    lambda html: html.replace("57.143", "-1"),
    lambda html: html.replace("57.143", "0"),
    lambda html: html.replace("57.143", "NaN"),
    lambda html: html.replace("57.143", "1e3"),
    lambda html: html.replace("Claude Opus 5.5 ⌖", "Claude Opus 5.6 ⌖"),
    lambda html: html.replace("<td>&nbsp;</td>", "<td>Long context</td>"),
    lambda html: html + "<table>" + STANDARD_HEADER + "<tbody>" + OPUS_ROW + "</tbody></table>",
])
def test_changed_or_ambiguous_table_fails_closed(change):
    page = deepcopy(PAGE)
    html_in(page)["processed"] = change(HTML)
    with pytest.raises(pricing._InvalidPricing):
        pricing._parse(page, CARDS)


@pytest.mark.parametrize("bad", ["0", "-0.1", "NaN", "Infinity", "1e-2", "", 0.105, None, "9999999999"])
def test_invalid_card_rate_fails_closed(bad):
    cards = deepcopy(CARDS)
    cards["premium"]["Azure"]["prop_model_serving"]["Anthropic"] = bad
    with pytest.raises(pricing._InvalidPricing):
        pricing._parse(PAGE, cards)


def test_ambiguous_vendor_section_and_missing_card_fail_closed():
    page = deepcopy(PAGE)
    components = page["result"]["data"]["drupal"]["generalPage"]["fieldComponents"]
    components.append(deepcopy(components[0]))
    with pytest.raises(pricing._InvalidPricing):
        pricing._parse(page, CARDS)
    with pytest.raises(KeyError):
        pricing._parse(PAGE, {"premium": {"Azure": {}}})


def test_transport_allows_only_literal_https_assets_and_sends_no_credentials(monkeypatch):
    calls = mock_connections(monkeypatch, [FakeResponse(encoded(PAGE)), FakeResponse(encoded(CARDS))])
    app = FastAPI()
    pricing.install_model_pricing(app)
    response = TestClient(app).get("/api/model-pricing/list?ignored=private-project", headers={
        "Authorization": "Bearer " + SECRET, "Cookie": "session=" + SECRET,
        "X-Forwarded-Access-Token": SECRET,
    })
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    result = response.json()
    assert result["status"] == "available" and result["retrieved_at"]
    assert result["source_urls"] == [pricing.PAGE_URL, pricing.CARDS_URL]
    assert SECRET not in json.dumps(result) and "private-project" not in json.dumps(result)
    assert [call.path for call in calls] == [pricing.PAGE_PATH, pricing.CARDS_PATH]
    for call in calls:
        assert call.host == "www.databricks.com" and call.timeout == pricing.TIMEOUT_SECONDS
        assert call.method == "GET" and call.closed
        assert call.headers == {"Accept": "application/json", "Accept-Encoding": "identity"}
    with pytest.raises(pricing._InvalidPricing):
        pricing._get_asset("https://evil.example/" + pricing.PAGE_PATH)
    assert len(calls) == 2


@pytest.mark.parametrize("response", [
    FakeResponse(status=302, headers={"location": "https://evil.example/steal"}),
    FakeResponse(status=301, headers={"location": "https://www.databricks.com/other"}),
    FakeResponse(headers={"content-length": str(pricing.MAX_BYTES + 1)}),
    FakeResponse(body=b"x" * (pricing.MAX_BYTES + 1)),
    FakeResponse(body=b"{}", headers={"content-length": "3"}),
    FakeResponse(body=b"{}", headers={"content-encoding": "gzip"}),
    FakeResponse(body=b'{"duplicate":1,"duplicate":2}'),
])
def test_redirect_large_or_invalid_response_is_unknown_without_retry(monkeypatch, response):
    calls = mock_connections(monkeypatch, [response])
    result = pricing.ModelPriceRates().get()
    assert result["status"] == "unknown" and result["rates"] == []
    assert result["retrieved_at"] is None and len(calls) == 1 and calls[0].closed


def test_success_ttl_failure_backoff_and_no_stale_or_historical_rates(monkeypatch):
    now = [0.0]
    calls = []
    def fetch(path):
        calls.append(path)
        if now[0] >= pricing.SUCCESS_TTL_SECONDS:
            raise OSError(SECRET)
        return PAGE if path == pricing.PAGE_PATH else CARDS
    monkeypatch.setattr(pricing, "_get_asset", fetch)
    store = pricing.ModelPriceRates(clock=lambda: now[0])
    first = store.get()
    first["rates"].clear()
    assert len(store.get()["rates"]) == 1 and len(calls) == 2
    now[0] = pricing.SUCCESS_TTL_SECONDS
    unavailable = store.get()
    assert unavailable["status"] == "unknown" and unavailable["rates"] == []
    assert unavailable["retrieved_at"] is None and SECRET not in json.dumps(unavailable)
    assert any("historical" in item for item in unavailable["limitations"])
    assert any("Cached-token" in item for item in unavailable["limitations"])
    assert any("region" in item for item in unavailable["limitations"])
    assert len(calls) == 3
    store.get()
    assert len(calls) == 3
    now[0] += pricing.FAILURE_TTL_SECONDS
    store.get()
    assert len(calls) == 4


def test_network_disabled_is_unknown_and_route_install_is_lazy(monkeypatch):
    attempts = []
    def disabled(host, timeout):
        attempts.append(host)
        raise OSError(SECRET)
    monkeypatch.setattr(pricing.http.client, "HTTPSConnection", disabled)
    app = FastAPI()
    pricing.install_model_pricing(app)
    assert attempts == []
    response = TestClient(app).get("/api/model-pricing/list")
    assert response.status_code == 200 and response.json()["status"] == "unknown"
    assert response.json()["rates"] == [] and SECRET not in response.text
    assert attempts == [pricing.HOST]


def test_hosted_pricing_requires_fresh_obo_and_does_not_provision(monkeypatch):
    from types import SimpleNamespace
    import workbench.hosted as hosted
    calls = []
    monkeypatch.setattr(hosted, "provision", lambda *a, **k: pytest.fail("unexpected provisioning"))
    monkeypatch.setattr(pricing.ModelPriceRates, "get", lambda self: calls.append("price") or {
        "status": "unknown", "rates": [], "source_urls": [], "retrieved_at": None})
    host = "https://workspace.example.com"
    origin = "https://app.example.com"
    identity = SimpleNamespace(mode="obo", host=host, user_id="user")
    app = hosted.create_hosted_app(hosted.HostedConfig(host, origin, warehouse="unused"),
                                   identity_resolver=lambda *args, **kwargs: identity)
    with TestClient(app, base_url=origin, raise_server_exceptions=False) as client:
        assert client.get("/api/model-pricing/list").status_code == 401
        response = client.get("/api/model-pricing/list", headers={"x-forwarded-access-token": SECRET})
    assert response.status_code == 200 and response.json()["status"] == "unknown"
    assert response.headers["cache-control"] == "no-store" and calls == ["price"]
    assert SECRET not in response.text


def test_stalled_fetch_has_bounded_wait_and_single_outstanding_worker(monkeypatch):
    started, release, finished = Event(), Event(), Event()
    def stalled(path):
        started.set()
        try:
            release.wait(2)
            return PAGE
        finally:
            finished.set()
    monkeypatch.setattr(pricing, "_read_asset", stalled)
    monkeypatch.setattr(pricing, "TIMEOUT_SECONDS", 0.02)
    try:
        with pytest.raises(pricing._InvalidPricing):
            pricing._get_asset(pricing.PAGE_PATH)
        assert started.is_set() and not finished.is_set()
        with pytest.raises(pricing._InvalidPricing):
            pricing._get_asset(pricing.PAGE_PATH)
    finally:
        release.set()
        assert finished.wait(2)