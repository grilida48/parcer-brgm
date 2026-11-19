"""Official organization APIs. License permissions are opt-in, not inferred from a key."""

import asyncio
import os
import time
import weakref
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timezone
from urllib.parse import quote

import httpx


class SourceError(Exception):
    pass


def enabled(name: str) -> bool:
    return os.getenv(name, "false").lower() in {"1", "true", "yes"}


SOURCE_CONFIG = {
    "yandex": ("Яндекс Карты", "YANDEX_API_KEY", "YANDEX"),
    "2gis": ("2ГИС", "DGIS_API_KEY", "DGIS"),
}


def source_status() -> list[dict]:
    statuses = []
    for source, (name, key_name, prefix) in SOURCE_CONFIG.items():
        configured = bool(os.getenv(key_name))
        permitted = enabled(f"{prefix}_ALLOW_STORAGE")
        reason = None
        if not configured:
            reason = f"Не задан {key_name}. Доступен импорт CSV и XLSX."
        elif not permitted:
            reason = "Не подтверждено право сохранять данные в CRM по вашему договору с источником."
        statuses.append({
            "id": source, "name": name, "configured": configured,
            "persistence_allowed": permitted,
            "export_allowed": enabled(f"{prefix}_ALLOW_EXPORT"),
            "available": configured and permitted, "reason": reason,
            "limitations": (
                "Рейтинг, отзывы и сетевой статус отсутствуют в документированном ответе поиска."
                if source == "yandex" else
                "Контакты требуют дополнительных прав ключа. Для выбора города нужен Regions API; можно задать отдельный DGIS_REGIONS_API_KEY."
            ),
        })
    return statuses


class RequestLimiter:
    def __init__(self, interval: float):
        self.interval = interval
        self.last = 0.0
        self.lock = asyncio.Lock()

    async def wait(self):
        async with self.lock:
            await asyncio.sleep(max(0, self.last + self.interval - time.monotonic()))
            self.last = time.monotonic()


_limiters = weakref.WeakKeyDictionary()


def _cancelled(callback: Callable | None):
    if callback and callback():
        raise SourceError("Поиск остановлен пользователем.")


async def _json(client, endpoint, params, source, budget, cancelled):
    interval = max(0.1, float(os.getenv("API_REQUEST_INTERVAL_SECONDS", "1")))
    loop_limiters = _limiters.setdefault(asyncio.get_running_loop(), {})
    limiter = loop_limiters.setdefault(source, RequestLimiter(interval))
    for attempt in range(3):
        _cancelled(cancelled)
        if budget["remaining"] <= 0:
            raise SourceError("Исчерпан лимит запросов API для этого поиска. Промежуточные результаты сохранены.")
        await limiter.wait()
        _cancelled(cancelled)
        budget["remaining"] -= 1
        budget["used"] = budget.get("used", 0) + 1
        try:
            response = await client.get(endpoint, params=params)
            if response.status_code in {429, 500, 502, 503, 504}:
                if attempt < 2:
                    await asyncio.sleep(min(8, 2 ** attempt))
                    continue
                raise SourceError("Источник временно недоступен или ограничил частоту запросов.")
            if response.status_code in {401, 403}:
                raise SourceError("Источник отклонил ключ API или запрошенные права доступа.")
            response.raise_for_status()
            payload = response.json()
            code = payload.get("meta", {}).get("code", 200)
            if code == 404:
                return {"result": {"items": [], "total": 0}}
            if code != 200:
                raise SourceError(f"Источник вернул ошибку API: {code}.")
            return payload
        except (httpx.TimeoutException, httpx.NetworkError):
            if attempt == 2:
                raise SourceError("Не удалось соединиться с источником после трёх попыток.") from None
            await asyncio.sleep(2 ** attempt)
        except (httpx.HTTPStatusError, ValueError):
            raise SourceError("Источник вернул неожиданный HTTP-ответ или некорректный JSON.") from None


def _base(source: str, item_id: str, name: str, city: str | None, category: str | None):
    prefix = SOURCE_CONFIG[source][2]
    return {
        "name": name, "source": source, "source_id": str(item_id),
        "category": category, "city": city,
        "address": None, "phone": None, "website": None, "rating": None,
        "review_count": None, "socials": [], "email": None,
        "website_status": "not_listed", "is_chain": None, "closed": False,
        "export_allowed": enabled(f"{prefix}_ALLOW_EXPORT"),
        "collected_at": datetime.now(timezone.utc),
    }


def normalize_yandex(feature: dict) -> dict | None:
    props = feature.get("properties", {})
    item = props.get("CompanyMetaData", {})
    if not item.get("id") or not item.get("name"):
        return None
    address = item.get("Address", {})
    if address.get("country_code") not in {None, "RU"}:
        return None
    city = next((x.get("name") for x in address.get("Components", []) if x.get("kind") == "locality"), None)
    category = next((x.get("name") for x in item.get("Categories", []) if x.get("name")), None)
    row = _base("yandex", item["id"], item["name"], city, category)
    row.update(address=item.get("address"), website=item.get("url"),
               phone=next((p.get("formatted") for p in item.get("Phones", [])
                           if p.get("type") in {None, "phone"} and p.get("formatted")), None),
               card_url=f"https://yandex.ru/maps/org/{quote(str(item['id']), safe='')}/")
    if row["website"]:
        row["website_status"] = "unchecked"
    return row


def normalize_2gis(item: dict) -> dict | None:
    if not item.get("id") or not item.get("name"):
        return None
    city = next((x.get("name") for x in item.get("adm_div", []) if x.get("type") in {"city", "settlement"}), None)
    category = next((x.get("name") for x in item.get("rubrics", []) if x.get("name")), None)
    row = _base("2gis", item["id"], item["name"], city, category)
    row["address"] = item.get("full_address_name") or item.get("address_name")
    row["card_url"] = f"https://2gis.ru/firm/{quote(str(item['id']), safe='')}"
    reviews = item.get("reviews") or {}
    row["rating"] = reviews.get("general_rating")
    row["review_count"] = reviews.get("general_review_count")
    branch_count = (item.get("brand") or {}).get("branch_count")
    if isinstance(branch_count, int):
        row["is_chain"] = branch_count > 1
    for group in item.get("contact_groups", []):
        for contact in group.get("contacts", []):
            kind = contact.get("type")
            value = contact.get("value") or contact.get("url") or contact.get("text")
            if not value:
                continue
            if kind == "phone" and not row["phone"]:
                row["phone"] = value
            elif kind == "email" and not row["email"]:
                row["email"] = value.removeprefix("mailto:")
            elif kind == "website" and not row["website"]:
                row["website"] = contact.get("url") or value
            elif kind in {"vkontakte", "telegram", "instagram", "facebook", "twitter", "youtube", "odnoklassniki", "social"}:
                row["socials"].append(contact.get("url") or value)
    if row["website"]:
        row["website_status"] = "unchecked"
    return row


def _same_city(left: str, right: str) -> bool:
    return left.casefold().replace("ё", "е").strip() == right.casefold().replace("ё", "е").strip()


async def _region_id(client, city, key, budget, cancelled):
    payload = await _json(client, "https://catalog.api.2gis.com/2.0/region/search",
                          {"key": os.getenv("DGIS_REGIONS_API_KEY") or key, "q": city,
                           "country_code_filter": "ru", "locale": "ru_RU",
                           "fields": "items.country_code,items.settlements,items.satellites"},
                          "2gis", budget, cancelled)
    regions = [item for item in payload.get("result", {}).get("items", [])
               if item.get("country_code") == "ru" and item.get("id")]
    exact = []
    for region in regions:
        names = [region.get("name", ""), *region.get("settlements", [])]
        names += [sat.get("name", "") for sat in region.get("satellites", [])]
        if any(isinstance(name, str) and _same_city(name, city) for name in names):
            exact.append(region)
    candidates = exact or regions
    if len(candidates) != 1:
        raise SourceError("2ГИС не определил однозначный регион России для этого города. Уточните название или используйте импорт.")
    return candidates[0]["id"]


async def search_source(source: str, city: str, category: str, limit: int, *,
                        request_budget: int = 100, cancelled: Callable | None = None,
                        budget: dict | None = None) -> AsyncIterator[dict]:
    status = next((s for s in source_status() if s["id"] == source), None)
    if not status:
        raise SourceError("Неизвестный источник данных.")
    if not status["available"]:
        raise SourceError(status["reason"])
    if budget is None:
        budget = {"remaining": request_budget, "used": 0}
    key = os.environ[SOURCE_CONFIG[source][1]]
    size = min(50, max(1, limit))
    offset, emitted = 0, 0
    seen = set()
    async with httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False) as client:
        region_id = await _region_id(client, city, key, budget, cancelled) if source == "2gis" else None
        while emitted < limit:
            _cancelled(cancelled)
            if source == "yandex":
                if offset > 1000:
                    raise SourceError("Достигнут предел Яндекс API: skip не более 1000. Все полученные результаты сохранены.")
                endpoint = "https://search-maps.yandex.ru/v1/"
                params = {"apikey": key, "text": f"Россия, {city}, {category}",
                          "type": "biz", "lang": "ru_RU", "results": size, "skip": offset}
            else:
                endpoint = "https://catalog.api.2gis.com/3.0/items"
                fields = "items.adm_div,items.rubrics,items.brand,items.reviews,items.full_address_name"
                if enabled("DGIS_CONTACTS_ENABLED"):
                    fields += ",items.contact_groups"
                params = {"key": key, "q": f"{city}, {category}", "type": "branch", "region_id": region_id,
                          "locale": "ru_RU", "page_size": size, "page": offset // size + 1,
                          "fields": fields}
            payload = await _json(client, endpoint, params, source, budget, cancelled)
            items = payload.get("features", []) if source == "yandex" else payload.get("result", {}).get("items", [])
            if not items:
                break
            fresh = 0
            for item in items:
                row = normalize_yandex(item) if source == "yandex" else normalize_2gis(item)
                if not row or row["source_id"] in seen:
                    continue
                seen.add(row["source_id"])
                fresh += 1
                # A free-text city query can return a neighboring locality; never relabel it.
                if not row["city"] or not _same_city(row["city"], city):
                    continue
                yield row
                emitted += 1
                if emitted >= limit:
                    break
            offset += len(items)
            if len(items) < size or not fresh:
                break
