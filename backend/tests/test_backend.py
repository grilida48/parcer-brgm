import os
import tempfile
import atexit

test_directory = tempfile.TemporaryDirectory(prefix='dus-tests-')
test_database = test_directory.name.replace('\\', '/') + '/test.db'
os.environ['DATABASE_URL'] = 'sqlite:///' + test_database
from fastapi.testclient import TestClient
from app.main import app
from app.models import Base, SessionLocal, engine
from app.scoring import DEFAULTS, score_lead
from app.models import Lead
from app.services import fingerprint
import pytest

assert engine.url.database == test_database, 'Refusing to test against a non-isolated database'
atexit.register(test_directory.cleanup)
atexit.register(engine.dispose)


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    with TestClient(app) as client:
        yield client


def login(client):
    response = client.post('/api/auth/setup', json={'name': 'Тест', 'email': 'admin@example.test', 'password': 'valid-password-123'})
    assert response.status_code == 200, response.text


def test_auth_and_setup_race(client):
    assert client.get('/api/leads').status_code == 401
    login(client)
    assert client.get('/api/auth/me').json()['role'] == 'admin'
    assert client.post('/api/auth/setup', json={'name': 'Второй', 'email': 'other@example.test', 'password': 'valid-password-123'}).status_code == 409
    assert client.post('/api/leads', headers={'Origin': 'https://foreign.example'}, json={'name': 'X'}).status_code == 403
    assert client.post('/api/leads', json={'name': 'Ссылка', 'website': 'javascript:alert(1)'}).status_code == 422


def test_lead_version_consent_and_dashboard(client):
    login(client)
    lead = client.post('/api/leads', json={'name': 'Ремонт', 'city': 'Казань', 'category': 'Автосервисы', 'rating': 4.8, 'review_count': 40}).json()
    assert lead['score'] == 70
    changed = client.patch(f"/api/leads/{lead['id']}", json={'version': 1, 'status': 'Оплатил', 'consent': 'granted', 'order_value': 15000})
    assert changed.status_code == 200, changed.text
    assert client.patch(f"/api/leads/{lead['id']}", json={'version': 1, 'notes': 'stale'}).status_code == 409
    assert client.get('/api/dashboard').json()['paid_today'] == 1
    assert client.get('/api/dashboard').json()['revenue'] == 15000
    assert client.post(f"/api/leads/{lead['id']}/interactions", json={'kind': 'call', 'comment': 'Обсудили задачу'}).status_code == 200
    assert client.patch(f"/api/leads/{lead['id']}", json={'version': 2, 'consent': 'denied'}).status_code == 200
    assert client.post(f"/api/leads/{lead['id']}/interactions", json={'kind': 'call', 'comment': 'Повтор'}).status_code == 409


def test_import_dedup_export_injection_and_license(client):
    login(client)
    content = 'name,city,address,phone\n=HYPERLINK("x"),Казань,Дом 1,70000000001\n'
    result = client.post('/api/import', files={'file': ('leads.csv', content.encode(), 'text/csv')})
    assert result.status_code == 200, result.text
    assert result.json()['imported'] == 1
    assert client.post('/api/import', files={'file': ('leads.csv', content.encode(), 'text/csv')}).json()['duplicates'] == 1
    assert "'=HYPERLINK" in client.get('/api/export?format=csv').text
    with SessionLocal() as db:
        lead = db.get(Lead, 1)
        lead.export_allowed = False
        db.commit()
    assert client.get('/api/export?format=json').status_code == 403


def test_assignment_concurrency(client):
    login(client)
    lead = client.post('/api/leads', json={'name': 'Клиент'}).json()
    body = {'ids': [lead['id']], 'action': 'assign', 'owner_id': 1, 'versions': {str(lead['id']): 1}}
    assert client.post('/api/leads/bulk', json=body).status_code == 200
    assert client.post('/api/leads/bulk', json=body).status_code == 409


def test_export_respects_table_filters_and_selection(client):
    login(client)
    first = client.post('/api/leads', json={'name': 'Альфа', 'city': 'Казань', 'category': 'Автосервисы', 'rating': 4.9, 'review_count': 50}).json()
    second = client.post('/api/leads', json={'name': 'Бета', 'city': 'Москва', 'category': 'Кафе'}).json()
    response = client.get('/api/export', params={'format': 'json', 'city': 'Казань', 'min_score': 60, 'category': 'Автосервисы', 'q': 'Альфа', 'status': 'Новый', 'website_state': 'not_listed', 'sort': 'name'})
    assert response.status_code == 200, response.text
    assert [item['id'] for item in response.json()] == [first['id']]
    selected = client.get('/api/export', params={'format': 'json', 'city': 'Казань', 'ids': str(second['id'])})
    assert selected.json() == []


def test_no_false_merge_branches():
    base = {'name': 'Сеть', 'phone': '88001001010', 'city': 'Москва'}
    assert fingerprint({**base, 'address': 'Улица 1'}) != fingerprint({**base, 'address': 'Улица 2'})


def test_scoring_unknown_and_clamp():
    lead = Lead(name='X', website='https://example.org', rating=None, review_count=None, website_status='check_error', audit={})
    assert score_lead(lead, DEFAULTS) == 0
    lead.website = None
    lead.rating = 4.9
    lead.review_count = 80
    lead.category = 'Автосервисы'
    config = {**DEFAULTS, 'weights': {k: 100 for k in DEFAULTS['weights']}}
    assert score_lead(lead, config) == 100


def test_staff_permissions_and_null_validation(client):
    login(client)
    first = client.post('/api/leads', json={'name': 'Чужой клиент'}).json()
    assigned = client.patch(f"/api/leads/{first['id']}", json={'version': 1, 'owner_id': 1})
    assert assigned.status_code == 200
    assert client.patch(f"/api/leads/{first['id']}", json={'version': 2, 'status': None}).status_code == 422
    assert client.post('/api/users', json={'name': 'Сотрудник', 'email': 'staff@example.test', 'password': 'staff-password-123', 'role': 'staff'}).status_code == 200
    client.post('/api/auth/logout')
    assert client.post('/api/auth/login', json={'email': 'staff@example.test', 'password': 'staff-password-123'}).status_code == 200
    assert client.patch(f"/api/leads/{first['id']}", json={'version': 2, 'notes': 'Не мой'}).status_code == 403
    assert client.post('/api/leads/bulk', json={'ids': [first['id']], 'action': 'delete'}).status_code == 403
    assert client.post('/api/users', json={'name': 'Другой', 'email': 'another@example.test', 'password': 'staff-password-123'}).status_code == 403


def test_search_partial_failures_existing_results_and_budget(client, monkeypatch):
    import asyncio
    import app.sources
    from app.tasks import run_search
    from app.models import Search, SearchLead
    from app.services import insert_lead
    login(client)
    with SessionLocal() as db:
        lead, _ = insert_lead(db, {'name': 'Уже найден', 'city': 'Казань', 'address': 'Дом 1'})
        criteria = {'cities': ['Казань'], 'categories': ['Ремонт'], 'sources': ['yandex', '2gis'], 'min_rating': 0, 'min_reviews': 0, 'max_results': 10, 'without_website': False, 'broken_website': False, 'with_phone': False, 'exclude_existing': False, 'exclude_chains': False, 'analyze_websites': False}
        job = Search(criteria=criteria, created_by=1)
        db.add(job)
        db.commit()
        job_id = job.id
    budgets = []
    async def fake_search(source, city, category, limit, **kwargs):
        budgets.append(kwargs['budget'])
        if source == 'yandex':
            yield {'name': 'Уже найден', 'city': 'Казань', 'address': 'Дом 1'}
            yield {'name': 'Новый', 'city': 'Казань', 'address': 'Дом 2'}
        else:
            raise RuntimeError('Ошибка одного источника')
    monkeypatch.setattr(app.sources, 'search_source', fake_search)
    asyncio.run(run_search(job_id))
    with SessionLocal() as db:
        job = db.get(Search, job_id)
        assert job.status == 'completed'
        assert job.found == 2
        assert len(job.errors) == 1
        assert db.get(SearchLead, (job_id, lead.id)) is not None
    assert len(budgets) == 2 and budgets[0] is budgets[1]
    assert client.get(f'/api/leads?search_id={job_id}').json()['total'] == 2
    assert len(client.get(f'/api/export?format=json&search_id={job_id}').json()) == 2
