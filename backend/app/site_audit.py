"""Bounded HTML inspection with DNS pinning; this does not run a browser or Lighthouse."""

import asyncio
import ipaddress
import re
import socket
import time
import weakref
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx


MAX_BYTES = 1_048_576
MAX_REDIRECTS = 5
_semaphores = weakref.WeakKeyDictionary()


class UnsafeURL(ValueError):
    pass


def validate_url(value: str) -> str:
    if not value or len(value) > 2048 or any(ord(c) < 32 for c in value):
        raise UnsafeURL("Некорректный адрес сайта.")
    if "://" not in value:
        value = "https://" + value
    parts = urlsplit(value)
    try:
        port = parts.port
    except ValueError:
        raise UnsafeURL("Некорректный порт сайта.") from None
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise UnsafeURL("Допустимы только HTTP(S)-адреса без учётных данных.")
    if port not in {None, 80, 443}:
        raise UnsafeURL("Проверка разрешена только на портах 80 и 443.")
    host = parts.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".home", ".lan")):
        raise UnsafeURL("Проверка внутренних адресов запрещена.")
    return value


def public_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%")[0])
    return ip.is_global and not ip.is_multicast and not ip.is_reserved and not ip.is_unspecified


async def resolve_public(host: str, port: int) -> str:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        answers = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
    else:
        addresses = [str(ip)]
    if not addresses or any(not public_ip(address) for address in addresses):
        raise UnsafeURL("Домен указывает на внутренний или служебный IP-адрес.")
    return addresses[0]


class PageFacts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.viewport = False
        self.form = False
        self.title = False
        self.description = False
        self.contacts = False
        self.in_title = False
        self.text = []

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if tag == "meta":
            name = (data.get("name") or "").lower()
            content = (data.get("content") or "").lower()
            if name == "viewport" and "width=device-width" in content.replace(" ", ""):
                self.viewport = True
            if name == "description" and content.strip():
                self.description = True
        if tag == "title":
            self.in_title = True
        if tag == "form":
            self.form = True
        if tag == "a" and (data.get("href") or "").lower().startswith(("tel:", "mailto:")):
            self.contacts = True

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title and data.strip():
            self.title = True
        self.text.append(data)


async def _fetch(url: str) -> dict:
    current = validate_url(url)
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=httpx.Timeout(8, connect=5), trust_env=False,
                                 follow_redirects=False, limits=httpx.Limits(max_connections=1)) as client:
        for _ in range(MAX_REDIRECTS + 1):
            current = validate_url(current)
            target = httpx.URL(current)
            host = target.host
            address = await resolve_public(host, target.port or (443 if target.scheme == "https" else 80))
            pinned = target.copy_with(host=address)
            # Connect to the validated address; Host and TLS certificate verification retain the original name.
            headers = {"Host": target.netloc.decode("ascii"), "User-Agent": "DUS-Leads-SiteCheck/1.0",
                       "Accept": "text/html", "Accept-Encoding": "identity"}
            async with client.stream("GET", pinned, headers=headers, extensions={"sni_hostname": host}) as response:
                status = response.status_code
                if status in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("Сервер вернул перенаправление без адреса.")
                    current = urljoin(current, location)
                    continue
                body = bytearray()
                if response.headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
                    raise ValueError("Сервер проигнорировал запрет сжатия; HTML не проверен.")
                truncated = False
                async for chunk in response.aiter_raw():
                    remaining = MAX_BYTES - len(body)
                    body.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        truncated = True
                        break
                return {"status": status, "url": current,
                        "content_type": response.headers.get("content-type", ""),
                        "body": bytes(body), "truncated": truncated,
                        "response_ms": round((time.monotonic() - started) * 1000)}
    raise ValueError("Превышен лимит перенаправлений.")


async def _audit(url: str | None) -> dict:
    result = {"website_status": "not_listed" if not url else "check_error",
              "http_status": None, "https": None, "response_ms": None, "load_ms": None,
              "mobile_friendly": None, "has_contacts": None, "has_form": None,
              "has_seo": None, "seo_title": None, "seo_description": None,
              "errors": [], "checked_at": datetime.now(timezone.utc).isoformat(),
              "method": "Статический HTML: viewport, контакты, form, title и description. JavaScript не выполняется.",
              "limitations": "Время HTTP-ответа не равно полной загрузке страницы. Viewport не подтверждает качество мобильной вёрстки."
              }
    if not url:
        result["errors"] = ["Сайт не указан в источнике. Наличие собственного сайта не установлено."]
        return result
    first_status = None
    for attempt in range(2):
        try:
            page = await asyncio.wait_for(_fetch(url), timeout=20)
            status = page["status"]
            result.update(http_status=status, https=urlsplit(page["url"]).scheme == "https",
                          response_ms=page["response_ms"], load_ms=page["response_ms"], final_url=page["url"])
            if status in {404, 410}:
                if first_status == status:
                    result["website_status"] = "unavailable"
                    result["errors"] = [f"HTTP {status} подтверждён двумя последовательными проверками."]
                    return result
                first_status = status
                await asyncio.sleep(0.5)
                continue
            if status >= 400:
                result["errors"] = [f"HTTP {status}: временная ошибка или ограничение доступа, сайт не признан неработающим."]
                if attempt == 0 and status in {429, 500, 502, 503, 504}:
                    await asyncio.sleep(0.5)
                    continue
                return result
            if not 200 <= status < 300:
                result["errors"] = [f"Неожиданный HTTP-статус {status}; доступность сайта не установлена."]
                return result
            result["website_status"] = "working"
            if "html" not in page["content_type"].lower():
                result["errors"] = ["Адрес доступен, но ответ не является HTML. Содержимое не оценено."]
                return result
            facts = PageFacts()
            facts.feed(page["body"].decode("utf-8", errors="replace"))
            text = " ".join(facts.text)
            contacts = facts.contacts or bool(re.search(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}|(?:\+7|8)\s*\(?\d{3}\)?[\s-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}", text))
            result.update(mobile_friendly=facts.viewport, has_contacts=contacts, has_form=facts.form,
                          has_seo=facts.title and facts.description,
                          seo_title=facts.title, seo_description=facts.description)
            if page["truncated"]:
                result["errors"].append("HTML ограничен первым 1 МиБ; отрицательные признаки могут быть неполными.")
                for field in ("mobile_friendly", "has_contacts", "has_form", "has_seo", "seo_title", "seo_description"):
                    if result[field] is False:
                        result[field] = None
            return result
        except UnsafeURL as exc:
            result["errors"] = [str(exc)]
            return result
        except (httpx.HTTPError, TimeoutError, OSError):
            result["errors"] = ["Сетевая ошибка, тайм-аут или ошибка TLS. Недоступность сайта не подтверждена."]
        except (ValueError, UnicodeError) as exc:
            result["errors"] = [str(exc)]
            return result
        if attempt == 0:
            await asyncio.sleep(0.5)
    return result


async def audit_site(url: str | None) -> dict:
    semaphore = _semaphores.setdefault(asyncio.get_running_loop(), asyncio.Semaphore(4))
    async with semaphore:
        return await _audit(url)
