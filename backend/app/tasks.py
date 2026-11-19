import asyncio
import logging
import weakref
from celery import Celery
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from .config import settings
from .models import Lead, Search, SearchLead, SessionLocal, now
from .services import get_settings, insert_lead
from .scoring import score_lead

logger = logging.getLogger(__name__)
celery = Celery('dus', broker=settings.redis_url, backend=settings.redis_url)
celery.conf.update(task_serializer='json', accept_content=['json'], result_serializer='json', worker_prefetch_multiplier=1, task_acks_late=True)
celery_app = celery
_search_locks = weakref.WeakKeyDictionary()


class SearchBudget(dict):
    def __init__(self, db, job, cap):
        self.db, self.job = db, job
        used = int(job.criteria.get('_api_requests_used', 0))
        cap = min(cap, int(job.criteria.get('_api_request_cap', cap)))
        job.criteria = {**job.criteria, '_api_request_cap': cap}
        db.commit()
        super().__init__(remaining=max(0, cap - used), used=used)

    def __setitem__(self, key, value):
        if key == 'used':
            # Reserve before sending HTTP so a worker restart cannot reset the paid-request budget.
            self.job.criteria = {**self.job.criteria, '_api_requests_used': value}
            self.db.commit()
        super().__setitem__(key, value)


async def run_search(search_id):
    lock = _search_locks.setdefault(asyncio.get_running_loop(), asyncio.Lock())
    async with lock:
        await _run_search(search_id)


async def _run_search(search_id):
    from .sources import search_source
    from .site_audit import audit_site
    with SessionLocal() as db:
        job = db.get(Search, search_id)
        if not job or job.status in ('cancelled', 'completed', 'failed'):
            return
        job.status = 'running'
        db.commit()
        criteria = job.criteria
        combos = [(source, city, category) for source in criteria['sources'] for city in criteria['cities'] for category in criteria['categories']]
        config = get_settings(db)
        cap = min(config['api_request_budget'], int(config.get('api_spend_limit_rub', 100) / config.get('api_request_cost_rub', 1)))
        budget = SearchBudget(db, job, cap)
        def cancelled():
            db.refresh(job)
            return job.status == 'cancelled'
        try:
            for index, (source, city, category) in enumerate(combos):
                if cancelled() or job.found >= criteria['max_results']:
                    break
                try:
                    async for data in search_source(source, city, category, criteria['max_results'] - job.found, request_budget=budget['remaining'], cancelled=cancelled, budget=budget):
                        if cancelled():
                            break
                        if criteria['min_rating'] and (data.get('rating') is None or data['rating'] < criteria['min_rating']):
                            continue
                        if criteria['min_reviews'] and (data.get('review_count') is None or data['review_count'] < criteria['min_reviews']):
                            continue
                        if criteria['with_phone'] and not data.get('phone'):
                            continue
                        if criteria['without_website'] and data.get('website'):
                            continue
                        if criteria['exclude_chains'] and data.get('is_chain') is True:
                            continue
                        if data.get('website') and (criteria['analyze_websites'] or criteria['broken_website']):
                            audit = await audit_site(data['website'])
                            data['audit'] = audit
                            data['website_status'] = audit['website_status']
                            if cancelled():
                                break
                        if criteria['broken_website'] and data.get('website_status') != 'unavailable':
                            continue
                        try:
                            with db.begin_nested():
                                lead, created = insert_lead(db, data, job.id)
                            included = lead is not None and (created or not criteria['exclude_existing'])
                            if included and not db.get(SearchLead, (job.id, lead.id)):
                                db.add(SearchLead(search_id=job.id, lead_id=lead.id))
                                job.found += 1
                            job.progress = min(99, int((index + min(1, job.found / criteria['max_results'])) / len(combos) * 100))
                            db.commit()
                        except (IntegrityError, ValueError):
                            db.rollback()
                            db.refresh(job)
                        if job.found >= criteria['max_results']:
                            break
                except Exception as error:
                    logger.exception('Search source failed for job %s', job.id)
                    job.errors = [*job.errors, f'{source}, {city}, {category}: {str(error)[:500]}']
                job.progress = int((index + 1) / len(combos) * 100)
                db.commit()
            db.refresh(job)
            if job.status != 'cancelled':
                job.status = 'completed' if job.found or not job.errors else 'failed'
                job.progress = 100
            job.finished_at = now()
            db.commit()
        except Exception:
            logger.exception('Search job failed: %s', search_id)
            db.rollback()
            job = db.get(Search, search_id)
            job.status = 'failed'
            job.errors = [*job.errors, 'Задача прервана внутренней ошибкой. Подробности в журнале сервера.']
            job.finished_at = now()
            db.commit()


@celery.task(name='dus.search')
def search_task(search_id):
    asyncio.run(run_search(search_id))
