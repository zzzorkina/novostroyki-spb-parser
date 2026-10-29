"""
Проверяем логику парсера без интернета: вместо настоящего сайта —
«поддельная» сессия, которая отдаёт заранее подготовленные ответы.
"""
import pytest
import requests

import config
import domrf_parser


class FakeResponse:
    def __init__(self, payload=None, status=200, text=None):
        self._payload = payload
        self.status_code = status
        self.text = text if text is not None else ""

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    """Отдаёт объекты страницами по offset/limit, как настоящий API."""

    def __init__(self, objects, ignore_region=False, fail_first=0, page_cap=None):
        self.objects = objects
        self.ignore_region = ignore_region
        self.fail_first = fail_first
        self.page_cap = page_cap      # сайт может отдавать меньше, чем просят
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(dict(params))
        if self.fail_first > 0:
            self.fail_first -= 1
            return FakeResponse(status=503)
        items = self.objects
        if "place" in params and not self.ignore_region:
            code = str(params["place"]).split("-")[-1]
            items = [o for o in items if str(o["rpdRegionCd"]) == code]
        start, size = params["offset"], min(params["limit"], self.page_cap or params["limit"])
        return FakeResponse({"data": {"list": items[start:start + size], "total": len(items)}})


def make_objects(n_spb=250, n_other=120):
    spb = [{"objId": i, "rpdRegionCd": 78, "objAddr": "Санкт-Петербург"} for i in range(n_spb)]
    other = [{"objId": 10_000 + i, "rpdRegionCd": 50, "objAddr": "Московская обл"} for i in range(n_other)]
    return spb + other


@pytest.fixture(autouse=True)
def fast_and_isolated(monkeypatch, tmp_path):
    """Без пауз и без записи в настоящую папку data/."""
    monkeypatch.setattr(domrf_parser, "polite_pause", lambda: None)
    monkeypatch.setattr(domrf_parser.time, "sleep", lambda s: None)
    monkeypatch.setattr(domrf_parser, "robots_allows", lambda url: True)
    monkeypatch.setattr(config, "RAW_DIR", tmp_path / "raw")


def test_extract_items_variants():
    assert domrf_parser.extract_items({"data": {"list": [1, 2], "total": 5}}) == ([1, 2], 5)
    assert domrf_parser.extract_items({"items": [1]}) == ([1], None)
    assert domrf_parser.extract_items([1, 2, 3]) == ([1, 2, 3], None)
    assert domrf_parser.extract_items({"data": [7], "total": 1}) == ([7], 1)
    assert domrf_parser.extract_items({"error": "x"}) == ([], None)


def test_collect_with_region_filter(monkeypatch):
    session = FakeSession(make_objects())
    monkeypatch.setattr(domrf_parser, "make_session", lambda: session)
    items = domrf_parser.collect("78")
    assert len(items) == 250
    assert all(i["rpdRegionCd"] == 78 for i in items)
    # 1 проверочный запрос + 5 страниц по 50 объектов
    assert len(session.calls) == 6


def test_collect_falls_back_when_site_ignores_region(monkeypatch):
    objects = make_objects()
    objects.sort(key=lambda o: o["rpdRegionCd"])  # первыми идут «чужие» регионы
    session = FakeSession(objects, ignore_region=True)
    monkeypatch.setattr(domrf_parser, "make_session", lambda: session)
    items = domrf_parser.collect("78")
    assert len(items) == 250
    assert "place" not in session.calls[-1]   # перешли на полный каталог


def test_retry_on_server_error(monkeypatch):
    session = FakeSession(make_objects(10, 0), fail_first=2)
    payload = domrf_parser.fetch_page(session, 0)
    assert len(payload["data"]["list"]) == 10
    assert len(session.calls) == 3   # две ошибки 503 + успешный запрос


def test_not_json_is_saved_for_debugging():
    class HtmlSession:
        def get(self, *a, **kw):
            return FakeResponse(payload=None, text="<html>captcha</html>")

    with pytest.raises(domrf_parser.NotJsonResponse):
        domrf_parser.fetch_page(HtmlSession(), 0)
    assert (config.RAW_DIR / "debug_response.html").read_text(encoding="utf-8") == "<html>captcha</html>"


def test_max_pages_limit(monkeypatch):
    session = FakeSession(make_objects(1000, 0))
    monkeypatch.setattr(domrf_parser, "make_session", lambda: session)
    items = domrf_parser.collect("78", max_pages=2)
    assert len(items) == 2 * config.PAGE_SIZE


def test_site_returns_fewer_items_than_requested(monkeypatch):
    """Сайт отдаёт по 50 объектов, хотя мы просим 100, — парсер не должен останавливаться."""
    session = FakeSession(make_objects(230, 0), page_cap=50)
    monkeypatch.setattr(domrf_parser, "make_session", lambda: session)
    items = domrf_parser.collect("78")
    assert len(items) == 230
    assert [c["offset"] for c in session.calls[1:]] == [0, 50, 100, 150, 200]


def test_region_by_address_when_code_missing():
    item = {"objId": 1, "objAddr": "Город Санкт-Петербург, Набережная Октябрьская, д. 42"}
    assert domrf_parser.region_matches(item, "78")
    assert not domrf_parser.region_matches({"objAddr": "г Москва, ул. Тверская"}, "78")
    # код региона в отдельном поле
    assert domrf_parser.region_matches({"rpdRegionCd": 78, "objAddr": "Шушары"}, "78")
