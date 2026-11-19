from typing import Literal
from pydantic import BaseModel, Field, field_validator
from urllib.parse import urlsplit
from .scoring import DEFAULTS, STATUSES


class Login(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class Setup(Login):
    name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=12, max_length=256)


class UserCreate(Setup):
    role: Literal['admin', 'staff'] = 'staff'


class LeadInput(BaseModel):
    name: str = Field(min_length=1, max_length=500)
    category: str | None = None
    city: str | None = None
    address: str | None = None
    phone: str | None = None
    website: str | None = None
    rating: float | None = Field(default=None, ge=0, le=5)
    review_count: int | None = Field(default=None, ge=0)
    email: str | None = None
    card_url: str | None = None
    socials: list[str] = Field(default_factory=list, max_length=30)

    @field_validator('website', 'card_url')
    @classmethod
    def safe_url(cls, value):
        if not value or not value.strip():
            return None
        value = value.strip()
        if '://' not in value:
            value = 'https://' + value
        parsed = urlsplit(value)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or any(char.isspace() for char in value) or any(char in value for char in '<>"'):
            raise ValueError('Укажите адрес HTTP или HTTPS без логина и пароля')
        _ = parsed.port  # Validate malformed ports before persisting the URL.
        return value

    @field_validator('socials')
    @classmethod
    def safe_social_urls(cls, values):
        return [cls.safe_url(value) for value in values if value.strip()]

    @field_validator('name')
    @classmethod
    def name_required(cls, value):
        if not value.strip():
            raise ValueError('Название не может быть пустым')
        return value.strip()


class LeadPatch(BaseModel):
    version: int = Field(ge=1)
    status: str | None = None
    owner_id: int | None = None
    notes: str | None = Field(default=None, max_length=20000)
    order_value: float | None = Field(default=None, ge=0, le=1e10)
    next_contact: str | None = None
    consent: Literal['unknown', 'granted', 'denied'] | None = None

    @field_validator('status')
    @classmethod
    def valid_status(cls, value):
        if value is not None and value not in STATUSES:
            raise ValueError('Неизвестный статус')
        return value


class Bulk(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=1000)
    action: Literal['assign', 'delete']
    owner_id: int | None = None
    versions: dict[str, int] = Field(default_factory=dict)


class SearchInput(BaseModel):
    cities: list[str] = Field(min_length=1, max_length=30)
    categories: list[str] = Field(min_length=1, max_length=30)
    sources: list[Literal['yandex', '2gis']] = Field(min_length=1, max_length=2)
    min_rating: float = Field(default=0, ge=0, le=5)
    min_reviews: int = Field(default=0, ge=0)
    max_results: int = Field(default=100, ge=1, le=1000)
    without_website: bool = False
    broken_website: bool = False
    with_phone: bool = False
    exclude_existing: bool = True
    exclude_chains: bool = False
    analyze_websites: bool = False

    @field_validator('cities', 'categories')
    @classmethod
    def valid_terms(cls, values):
        terms = list(dict.fromkeys(x.strip() for x in values))
        if any(not x or len(x) > 200 for x in terms):
            raise ValueError('Укажите непустые значения длиной до 200 символов')
        return terms


class InteractionInput(BaseModel):
    kind: Literal['call', 'note', 'status'] = 'note'
    comment: str = Field(min_length=1, max_length=10000)


class SettingsInput(BaseModel):
    weights: dict[str, int]
    priority_categories: list[str] = Field(max_length=100)
    daily_goal: int = Field(default=1, ge=1, le=10000)
    default_order_value: float = Field(default=15000, ge=0, le=1e10)
    api_request_budget: int = Field(default=100, ge=1, le=10000)
    api_request_cost_rub: float = Field(default=1, gt=0, le=100000)
    api_spend_limit_rub: float = Field(default=100, ge=0, le=10000000)

    @field_validator('weights')
    @classmethod
    def valid_weights(cls, value):
        if set(value) != set(DEFAULTS['weights']) or any(abs(v) > 100 for v in value.values()):
            raise ValueError('Укажите все веса в диапазоне от -100 до 100')
        return value
