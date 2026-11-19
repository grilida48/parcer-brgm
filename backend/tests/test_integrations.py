import asyncio

import httpx
import pytest

from app import site_audit, sources


def test_source_license_is_not_implied_by_api_key(monkeypatch):
    monkeypatch.setenv("YANDEX_API_KEY", "not-a-real-key")
    monkeypatch.delenv("YANDEX_ALLOW_STORAGE", raising=False)
    status = next(s for s in sources.source_status() if s["id"] == "yandex")
    assert status["configured"] is True
    assert status["available"] is False
    assert status["export_allowed"] is False


def test_missing_fields_remain_unknown():
    lead = sources.normalize_yandex({"properties": {"CompanyMetaData": {"id": "1", "name": "Тест"}}})
    assert lead["website"] is None
    assert lead["website_status"] == "not_listed"
    assert lead["rating"] is None
    assert lead["review_count"] is None
    assert lead["city"] is None
    assert lead["is_chain"] is None


def test_actual_city_and_chain_evidence():
    lead = sources.normalize_2gis({"id": "1", "name": "Тест", "brand": {"branch_count": 3},
                                   "adm_div": [{"type": "city", "name": "Казань"}],
                                   "reviews": {"general_rating": 4.7, "general_review_count": 32}})
    assert lead["city"] == "Казань"
    assert lead["is_chain"] is True
    assert lead["rating"] == 4.7


def test_2gis_region_is_russian_and_city_is_not_relabelled(monkeypatch):
    monkeypatch.setenv("DGIS_API_KEY", "test")
    monkeypatch.setenv("DGIS_ALLOW_STORAGE", "true")
    calls = []

    async def payload(client, endpoint, params, source, budget, cancelled):
        calls.append((endpoint, params))
        if endpoint.endswith("region/search"):
            assert params["country_code_filter"] == "ru"
            return {"result": {"items": [{"id": "32", "name": "Москва", "country_code": "ru"}]}}
        assert params["region_id"] == "32"
        return {"result": {"items": [
            {"id": "1", "name": "A", "adm_div": [{"type": "city", "name": "Москва"}]},
            {"id": "2", "name": "B", "adm_div": [{"type": "city", "name": "Химки"}]},
            {"id": "3", "name": "C"},
        ]}}

    monkeypatch.setattr(sources, "_json", payload)

    async def scenario():
        return [row async for row in sources.search_source("2gis", "Москва", "Кафе", 10)]

    rows = asyncio.run(scenario())
    assert len(rows) == 1
    assert rows[0]["city"] == "Москва"
    assert rows[0]["source_id"] == "1"
    assert len(calls) == 2


def test_request_budget_counts_retries(monkeypatch):
    monkeypatch.setenv("API_REQUEST_INTERVAL_SECONDS", "0.1")
    calls = []

    async def handler(request):
        calls.append(request)
        return httpx.Response(503)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            budget = {"remaining": 1, "used": 0}
            with pytest.raises(sources.SourceError, match="лимит"):
                await sources._json(client, "https://example.org", {}, "yandex", budget, None)
            assert budget == {"remaining": 0, "used": 1}

    asyncio.run(scenario())
    assert len(calls) == 1


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://localhost", "http://127.0.0.1:9000",
                                  "https://user:secret@example.org", "http://foo.internal"])
def test_unsafe_url_syntax(url):
    with pytest.raises(site_audit.UnsafeURL):
        site_audit.validate_url(url)


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "::ffff:127.0.0.1", "224.0.0.1"])
def test_non_public_ip_rejected(ip):
    with pytest.raises(site_audit.UnsafeURL):
        asyncio.run(site_audit.resolve_public(ip, 443))


def test_mixed_public_and_private_dns_answers_are_rejected(monkeypatch):
    async def scenario():
        async def resolve(*args, **kwargs):
            return [(2, 1, 6, "", ("8.8.8.8", 443)), (2, 1, 6, "", ("10.1.2.3", 443))]

        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
        with pytest.raises(site_audit.UnsafeURL):
            await site_audit.resolve_public("mixed.example.org", 443)

    asyncio.run(scenario())


def test_html_audit_is_explicit_heuristic(monkeypatch):
    async def fetch(url):
        return {"status": 200, "url": url, "content_type": "text/html", "truncated": False,
                "response_ms": 90,
                "body": b'<html><title>Company</title><meta name="description" content="Business">'
                        b'<meta name="viewport" content="width=device-width, initial-scale=1">'
                        b'<a href="tel:+79990000000">Call</a><form></form></html>'}

    monkeypatch.setattr(site_audit, "_fetch", fetch)
    result = asyncio.run(site_audit.audit_site("https://example.org"))
    assert result["website_status"] == "working"
    assert result["mobile_friendly"] is True
    assert result["has_form"] is True
    assert result["has_contacts"] is True
    assert result["has_seo"] is True
    assert "JavaScript" in result["method"]


def test_404_needs_two_checks_and_503_stays_unknown(monkeypatch):
    calls = []

    async def fetch(url):
        calls.append(url)
        return {"status": 404, "url": url, "content_type": "text/html", "truncated": False,
                "response_ms": 20, "body": b""}

    monkeypatch.setattr(site_audit, "_fetch", fetch)
    assert asyncio.run(site_audit.audit_site("https://example.org"))["website_status"] == "unavailable"
    assert len(calls) == 2

    async def failure(url):
        return {"status": 503, "url": url, "content_type": "text/html", "truncated": False,
                "response_ms": 20, "body": b""}

    monkeypatch.setattr(site_audit, "_fetch", failure)
    assert asyncio.run(site_audit.audit_site("https://example.org"))["website_status"] == "check_error"


def test_connection_is_pinned_and_redirect_is_revalidated(monkeypatch):
    client_class = httpx.AsyncClient
    seen = []

    async def handler(request):
        seen.append(request)
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})

    def client_factory(**kwargs):
        return client_class(**kwargs, transport=httpx.MockTransport(handler))

    original_resolve = site_audit.resolve_public

    async def resolve(host, port):
        if host == "example.org":
            return "8.8.8.8"
        return await original_resolve(host, port)

    monkeypatch.setattr(site_audit.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(site_audit, "resolve_public", resolve)
    with pytest.raises(site_audit.UnsafeURL):
        asyncio.run(site_audit._fetch("https://example.org/"))
    assert len(seen) == 1
    assert seen[0].url.host == "8.8.8.8"
    assert seen[0].headers["Host"] == "example.org"
    assert seen[0].extensions["sni_hostname"] == "example.org"
