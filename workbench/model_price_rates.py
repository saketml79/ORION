"""Current public list prices, not workspace billing or historical prices.

The upstream website assets are undocumented and may change without notice. A
schema change fails closed. The host must enforce its own authentication/OBO
before exposing this route; this module neither receives nor forwards identity.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from html.parser import HTMLParser
import http.client
import json
import re
from threading import BoundedSemaphore, Event, Lock, Thread, Timer
from time import monotonic

from fastapi import FastAPI, Response

HOST = "www.databricks.com"
PAGE_PATH = "/en-pricing-assets/page-data/product/pricing/proprietary-foundation-model-serving/page-data.json"
CARDS_PATH = "/en-pricing-assets/data/pricing/cards.json"
PAGE_URL = "https://" + HOST + PAGE_PATH
CARDS_URL = "https://" + HOST + CARDS_PATH
ENDPOINT = "databricks-claude-opus-5-5"
MODEL = "Claude Opus 5.5"
SONNET_ENDPOINT = "databricks-claude-sonnet-5-5"
SONNET_MODEL = "Claude Sonnet 5.5"
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 5
SUCCESS_TTL_SECONDS = 900
FAILURE_TTL_SECONDS = 60
_NUMBER = re.compile(r"(?:0|[1-9][0-9]{0,8})(?:\.[0-9]{1,9})?\Z")
_FETCH_SLOT = BoundedSemaphore(1)
_LIMITATIONS = [
    "Provisional current published list prices, not actual billing, contracted prices, or historical rates.",
    "Cached-token usage and cache pricing are not measured or included.",
    "Workspace region and regional conditions are not measured or verified.",
    "Endpoint identity, token usage, taxes, and other service costs are not verified by this adapter.",
]


class _InvalidPricing(ValueError):
    pass


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidPricing()
        result[key] = value
    return result


def _get_asset(path):
    """Bound the caller even if the operating system's DNS resolver stalls."""
    if path not in (PAGE_PATH, CARDS_PATH):
        raise _InvalidPricing()
    if not _FETCH_SLOT.acquire(blocking=False):
        raise _InvalidPricing()
    done = Event()
    result = []

    def fetch():
        try:
            result.append(_read_asset(path))
        except Exception:
            pass
        finally:
            _FETCH_SLOT.release()
            done.set()

    try:
        Thread(target=fetch, daemon=True).start()
    except Exception:
        _FETCH_SLOT.release()
        raise _InvalidPricing() from None
    if not done.wait(TIMEOUT_SECONDS) or len(result) != 1:
        raise _InvalidPricing()
    return result[0]


def _read_asset(path):
    """Use direct HTTPS, without proxies, credential handlers, or redirects."""
    connection = http.client.HTTPSConnection(HOST, timeout=TIMEOUT_SECONDS)
    deadline = monotonic() + TIMEOUT_SECONDS
    timer = None
    try:
        connection.request("GET", path, headers={"Accept": "application/json", "Accept-Encoding": "identity"})
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise _InvalidPricing()
        # A socket timeout alone restarts on every byte, so also interrupt a
        # response that trickles headers or body beyond the absolute deadline.
        def interrupt():
            sock = connection.sock
            if sock is not None:
                try:
                    sock.shutdown(2)
                except OSError:
                    pass
        timer = Timer(remaining, interrupt)
        timer.daemon = True
        timer.start()
        response = connection.getresponse()
        if response.status != 200 or response.getheader("Content-Encoding", "identity").lower() != "identity":
            raise _InvalidPricing()
        length = response.getheader("Content-Length")
        if length is not None and (not length.isascii() or not length.isdecimal() or int(length) > MAX_BYTES):
            raise _InvalidPricing()
        chunks = []
        total = 0
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise _InvalidPricing()
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
            chunk = response.read1(min(65536, MAX_BYTES + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_BYTES:
                raise _InvalidPricing()
            chunks.append(chunk)
        if length is not None and total != int(length):
            raise _InvalidPricing()
        return json.loads(b"".join(chunks).decode("utf-8"), object_pairs_hook=_unique_pairs)
    finally:
        if timer is not None:
            timer.cancel()
        connection.close()


def _rate(value):
    if not isinstance(value, str) or not _NUMBER.fullmatch(value):
        raise _InvalidPricing()
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise _InvalidPricing() from None
    if not number.is_finite() or number <= 0:
        raise _InvalidPricing()
    return number


def _text(value):
    return " ".join(value.split())


class _Tables(HTMLParser):
    """Read text and spans, retaining enough structure to validate columns."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self.table = self.row = self.cell = None
        self.section = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table":
            if self.table is not None:
                raise _InvalidPricing()
            self.table = []
        elif self.table is not None and tag in ("thead", "tbody"):
            self.section = tag
        elif self.table is not None and tag == "tr":
            if self.row is not None:
                raise _InvalidPricing()
            self.row = [self.section, []]
        elif self.row is not None and tag in ("th", "td"):
            if self.cell is not None:
                raise _InvalidPricing()
            self.cell = [tag, attrs.get("rowspan", "1"), attrs.get("colspan", "1"), ""]

    def handle_data(self, data):
        if self.cell is not None:
            self.cell[3] += data

    def handle_endtag(self, tag):
        if tag in ("th", "td") and self.cell is not None:
            if self.cell[0] != tag or self.row is None:
                raise _InvalidPricing()
            self.cell[3] = _text(self.cell[3])
            self.row[1].append(self.cell)
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.cell is not None or self.table is None:
                raise _InvalidPricing()
            self.table.append(self.row)
            self.row = None
        elif tag in ("thead", "tbody"):
            self.section = None
        elif tag == "table" and self.table is not None:
            if self.row is not None:
                raise _InvalidPricing()
            self.tables.append(self.table)
            self.table = None


def _objects(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _objects(item)
    elif isinstance(value, list):
        for item in value:
            yield from _objects(item)


def _parse(page, cards, *, model=MODEL, endpoint=ENDPOINT):
    sections = [item for item in _objects(page)
                if item.get("fieldTitle") == "Proprietary Foundation Model Serving DBU rates"
                and item.get("fieldItem", {}).get("entity", {}).get("fieldPricingVendors") == ["anthropic"]]
    if len(sections) != 1:
        raise _InvalidPricing()
    html = sections[0]["fieldDescription"]["processed"]
    if not isinstance(html, str):
        raise _InvalidPricing()
    parser = _Tables()
    parser.feed(html)
    parser.close()
    if parser.table is not None:
        raise _InvalidPricing()
    first = [["th", "2", "1", "Model"], ["th", "2", "1", ""],
             ["th", "1", "5", "Standard Pay Per Token (DBU Per 1M Tokens)"]]
    second = [["th", "1", "1", name] for name in
              ("Input", "Output", "Cache read", "Cache write", "Cache write (1hr)")]
    tables = [table for table in parser.tables if len(table) >= 2
              and table[0] == ["thead", first] and table[1] == ["thead", second]]
    if len(tables) != 1:
        raise _InvalidPricing()
    rows = [cells for section, cells in tables[0][2:] if section == "tbody" and cells
            and cells[0][3] in (model, model + " ⌖")]
    if len(rows) != 1 or len(rows[0]) != 7:
        raise _InvalidPricing()
    row = rows[0]
    if any(cell[:3] != ["td", "1", "1"] for cell in row) or row[1][3] != "":
        raise _InvalidPricing()
    input_dbu, output_dbu = _rate(row[2][3]), _rate(row[3][3])
    card = _rate(cards["premium"]["Azure"]["prop_model_serving"]["Anthropic"])
    with localcontext() as context:
        context.prec = 40
        input_usd = format(input_dbu * card, "f")
        output_usd = format(output_dbu * card, "f")
    return {
        "endpoint": endpoint,
        "model": model,
        "currency": "USD",
        "input_usd_per_1m_tokens": input_usd,
        "output_usd_per_1m_tokens": output_usd,
        "input_dbu_per_1m_tokens": format(input_dbu, "f"),
        "output_dbu_per_1m_tokens": format(output_dbu, "f"),
        "usd_per_dbu": format(card, "f"),
        "pricing_basis": "Azure Premium Anthropic, standard pay per token",
    }


class ModelPriceRates:
    """One public, bounded, process-local cache, with failed-fetch backoff."""
    def __init__(self, *, clock=monotonic):
        self._clock = clock
        self._lock = Lock()
        self._expires = 0.0
        self._cached = None

    def get(self):
        with self._lock:
            if self._cached is not None and self._clock() < self._expires:
                return deepcopy(self._cached)
            result = {
                "status": "unknown",
                "rates": [],
                "source_urls": [PAGE_URL, CARDS_URL],
                "retrieved_at": None,
                "limitations": list(_LIMITATIONS),
            }
            try:
                page = _get_asset(PAGE_PATH)
                cards = _get_asset(CARDS_PATH)
                rates = []
                for model, endpoint in ((MODEL, ENDPOINT), (SONNET_MODEL, SONNET_ENDPOINT)):
                    try:
                        rates.append(_parse(page, cards, model=model, endpoint=endpoint))
                    except _InvalidPricing:
                        # A missing or ambiguous row cannot invalidate another verified model.
                        continue
                if not rates:
                    raise _InvalidPricing()
                result.update(status="available", rates=rates,
                              retrieved_at=datetime.now(timezone.utc).isoformat())
                ttl = SUCCESS_TTL_SECONDS
            except Exception:
                # Do not expose remote responses, secrets in exception text, or stale rates.
                ttl = FAILURE_TTL_SECONDS
            self._cached = result
            self._expires = self._clock() + ttl
            return deepcopy(result)


def install_model_pricing(app: FastAPI) -> ModelPriceRates:
    """Install the public-snapshot route; hosted callers must enforce OBO separately."""
    prices = ModelPriceRates()

    @app.get("/api/model-pricing/list")
    def model_pricing_list(response: Response):
        response.headers["Cache-Control"] = "no-store"
        return prices.get()

    return prices