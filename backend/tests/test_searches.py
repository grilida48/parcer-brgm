import asyncio

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import site_audit, sources, tasks
from app.models import Base, Lead, Search, SearchLead, User
from app.schemas import SearchInput
from app.services import insert_lead


@pytest.fixture
def worker_db(monkeypatch):
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    sessions = sessionmaker(engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(tasks, 'SessionLocal', sessions)
    with sessions() as db:
        db.add(User(id=1, name='Тест', email='worker@example.test', password_hash='unused-in-worker-test'))
        db.commit()
    yield sessions
    engine.dispose()


def make_job(sessions, **overrides):
    criteria = SearchInput(cities=['Москва'], categories=['Кафе'], sources=['yandex'],
                           **overrides).model_dump()
    with sessions() as db:
        job = Search(criteria=criteria, created_by=1)
        db.add(job)
        db.commit()
        return job.id


def candidate(name, **overrides):
    return {'name': name, 'city': 'Москва', 'address': name, 'category': 'Кафе',
            'source': 'yandex', 'source_id': name, 'export_allowed': False, **overrides}


@pytest.mark.parametrize(('criteria', 'rows', 'expected'), [
    ({'min_rating': 4.5}, [candidate('unknown'), candidate('low', rating=4.4), candidate('valid', rating=4.6)], ['valid']),
    ({'min_reviews': 30}, [candidate('unknown'), candidate('low', review_count=29), candidate('valid', review_count=30)], ['valid']),
    ({'exclude_chains': True}, [candidate('unknown'), candidate('single', is_chain=False), candidate('chain', is_chain=True)], ['single', 'unknown']),
    ({'with_phone': True}, [candidate('unknown'), candidate('valid', phone='+79990000000')], ['valid']),
    ({'without_website': True}, [candidate('unknown'), candidate('has_site', website='https://example.org')], ['unknown']),
    ({}, [candidate('closed', closed=True), candidate('open')], ['open']),
])
def test_worker_filters_use_known_evidence(worker_db, monkeypatch, criteria, rows, expected):
    async def fake_source(*args, **kwargs):
        for row in rows:
            yield row

    monkeypatch.setattr(sources, 'search_source', fake_source)
    search_id = make_job(worker_db, **criteria)
    asyncio.run(tasks.run_search(search_id))
    with worker_db() as db:
        assert sorted(db.scalars(select(Lead.name))) == expected
        assert db.get(Search, search_id).found == len(expected)
        assert db.get(Search, search_id).status == 'completed'


def test_broken_filter_does_not_include_temporary_audit_errors(worker_db, monkeypatch):
    async def fake_source(*args, **kwargs):
        yield candidate('missing')
        yield candidate('temporary', website='https://temporary.example')
        yield candidate('confirmed', website='https://confirmed.example')

    async def fake_audit(url):
        return {'website_status': 'unavailable' if 'confirmed' in url else 'check_error'}

    monkeypatch.setattr(sources, 'search_source', fake_source)
    monkeypatch.setattr(site_audit, 'audit_site', fake_audit)
    search_id = make_job(worker_db, broken_website=True)
    asyncio.run(tasks.run_search(search_id))
    with worker_db() as db:
        assert list(db.scalars(select(Lead.name))) == ['confirmed']


def test_cancel_during_audit_keeps_intermediate_results(worker_db, monkeypatch):
    search_id = make_job(worker_db, analyze_websites=True)

    async def fake_source(*args, **kwargs):
        yield candidate('saved')
        yield candidate('pending', website='https://example.org')
        yield candidate('never')

    async def fake_audit(url):
        with worker_db() as db:
            job = db.get(Search, search_id)
            job.status = 'cancelled'
            db.commit()
        return {'website_status': 'working'}

    monkeypatch.setattr(sources, 'search_source', fake_source)
    monkeypatch.setattr(site_audit, 'audit_site', fake_audit)
    asyncio.run(tasks.run_search(search_id))
    with worker_db() as db:
        assert list(db.scalars(select(Lead.name))) == ['saved']
        job = db.get(Search, search_id)
        assert job.status == 'cancelled'
        assert job.found == 1
        assert job.finished_at is not None


@pytest.mark.parametrize('exclude_existing', [True, False])
def test_existing_records_can_join_new_search_without_duplicate_leads(worker_db, monkeypatch, exclude_existing):
    row = candidate('existing')
    with worker_db() as db:
        existing, _ = insert_lead(db, row)
        db.commit()
        lead_id = existing.id

    async def fake_source(*args, **kwargs):
        yield row
        yield row

    monkeypatch.setattr(sources, 'search_source', fake_source)
    search_id = make_job(worker_db, exclude_existing=exclude_existing)
    asyncio.run(tasks.run_search(search_id))
    with worker_db() as db:
        assert len(list(db.scalars(select(Lead)))) == 1
        assert (db.get(SearchLead, (search_id, lead_id)) is not None) == (not exclude_existing)
        assert db.get(Search, search_id).found == (0 if exclude_existing else 1)


def test_request_reservation_is_saved_before_network_and_survives_restart(worker_db, monkeypatch):
    search_id = make_job(worker_db)
    physical_requests = []

    async def handler(request):
        with worker_db() as db:
            assert db.get(Search, search_id).criteria['_api_requests_used'] == 1
        physical_requests.append(request)
        return httpx.Response(200, json={})

    async def fake_source(*args, **kwargs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await sources._json(client, 'https://example.org', {}, 'yandex', kwargs['budget'], None)
        yield candidate('saved')

    monkeypatch.setattr(sources, 'search_source', fake_source)
    with worker_db() as db:
        job = db.get(Search, search_id)
        job.criteria = {**job.criteria, '_api_request_cap': 1}
        db.commit()
    asyncio.run(tasks.run_search(search_id))
    with worker_db() as db:
        job = db.get(Search, search_id)
        assert job.criteria['_api_requests_used'] == 1
        job.status = 'running'
        db.commit()
    asyncio.run(tasks.run_search(search_id))
    assert len(physical_requests) == 1
    with worker_db() as db:
        assert db.get(Search, search_id).criteria['_api_requests_used'] == 1
        assert db.get(Search, search_id).found == 1
