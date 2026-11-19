import asyncio
import csv
import hashlib
import io
import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from openpyxl import Workbook, load_workbook
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from .auth import admin, current_user, db_session, hash_password, issue_session, user_dict, verify_password
from .config import settings
from .models import Activity, AppSetting, Base, Interaction, Lead, LoginSession, Search, SearchLead, SessionLocal, User, engine, now
from .schemas import Bulk, InteractionInput, LeadInput, LeadPatch, Login, SearchInput, SettingsInput, Setup, UserCreate
from .scoring import PAID, STATUSES, score_lead
from .services import as_dict, get_settings, insert_lead, log_activity
from .sources import source_status
from .tasks import run_search, search_task

logging.basicConfig(level=logging.INFO)
background_jobs = set()
login_attempts = {}


@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        if settings.admin_email and settings.admin_password and not db.scalar(select(User.id).limit(1)):
            if len(settings.admin_password) < 12:
                raise RuntimeError('ADMIN_PASSWORD должен содержать минимум 12 символов')
            db.add(User(name='Администратор', email=settings.admin_email.casefold(), password_hash=hash_password(settings.admin_password), role='admin'))
            db.commit()
        # Local tasks cannot survive a process restart; retain their partial results.
        if settings.task_mode == 'local':
            for job in db.scalars(select(Search).where(Search.status.in_(['running', 'queued']))):
                job.status = 'failed'
                job.errors = [*job.errors, 'Сервер перезапущен. Промежуточные результаты сохранены.']
                job.finished_at = now()
            db.commit()
    yield
    for task in background_jobs:
        task.cancel()


app = FastAPI(title='DUS Leads', lifespan=lifespan)
origins = [o.strip() for o in settings.allowed_origins.split(',') if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'], allow_headers=['Content-Type'])


@app.middleware('http')
async def origin_guard(request, call_next):
    if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
        origin = request.headers.get('origin')
        if origin and origin not in origins and origin != str(request.base_url).rstrip('/'):
            from fastapi.responses import JSONResponse
            return JSONResponse({'detail': 'Источник запроса не разрешён'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'same-origin'
    return response


@app.get('/api/health')
def health(db=Depends(db_session)):
    db.execute(select(1))
    return {'status': 'ok', 'demo_mode': settings.demo_mode, 'task_mode': settings.task_mode}


@app.get('/api/auth/status')
def auth_status(db=Depends(db_session)):
    return {'needs_setup': not bool(db.scalar(select(User.id).limit(1)))}


@app.post('/api/auth/setup')
def setup(data: Setup, response: Response, db=Depends(db_session)):
    if db.scalar(select(User.id).limit(1)):
        raise HTTPException(409, 'Администратор уже создан')
    # A unique setup marker serializes first-run setup without advancing PostgreSQL sequences by hand.
    db.add(AppSetting(key='bootstrap_complete', value={'created': True}))
    user = User(name=data.name, email=data.email.casefold(), password_hash=hash_password(data.password), role='admin')
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'Администратор уже создан')
    issue_session(db, user, response, settings.cookie_secure, settings.cookie_samesite)
    return user_dict(user)


@app.post('/api/auth/login')
def login(data: Login, request: Request, response: Response, db=Depends(db_session)):
    host = request.client.host if request.client else 'unknown'
    key = (host, data.email.casefold())
    attempts = [stamp for stamp in login_attempts.get(key, []) if stamp > time.monotonic() - 900]
    if len(attempts) >= 10:
        raise HTTPException(429, 'Слишком много попыток. Повторите через 15 минут')
    user = db.scalar(select(User).where(User.email == data.email.casefold()))
    if not user or not user.active or not verify_password(data.password, user.password_hash):
        login_attempts[key] = [*attempts, time.monotonic()]
        raise HTTPException(401, 'Неверная почта или пароль')
    login_attempts.pop(key, None)
    issue_session(db, user, response, settings.cookie_secure, settings.cookie_samesite)
    return user_dict(user)


@app.get('/api/auth/me')
def me(user=Depends(current_user)):
    return user_dict(user)


@app.post('/api/auth/logout')
def logout(request: Request, response: Response, user=Depends(current_user), db=Depends(db_session)):
    digest = hashlib.sha256(request.cookies.get('dus_session', '').encode()).hexdigest()
    db.execute(delete(LoginSession).where(LoginSession.token_hash == digest))
    db.commit()
    response.delete_cookie('dus_session', path='/')
    return {'ok': True}


@app.get('/api/sources')
def sources(user=Depends(current_user)):
    return source_status()


@app.get('/api/statuses')
def statuses(user=Depends(current_user)):
    return STATUSES


@app.get('/api/users')
def users(user=Depends(current_user), db=Depends(db_session)):
    return [user_dict(u) for u in db.scalars(select(User).order_by(User.name))]


@app.post('/api/users')
def create_user(data: UserCreate, user=Depends(admin), db=Depends(db_session)):
    created = User(name=data.name, email=data.email.casefold(), password_hash=hash_password(data.password), role=data.role)
    db.add(created)
    try:
        db.flush()
        log_activity(db, user.id, 'Создан сотрудник', employee_id=created.id)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'Эта почта уже используется')
    return user_dict(created)


@app.patch('/api/users/{user_id}')
def change_user(user_id: int, payload: dict, user=Depends(admin), db=Depends(db_session)):
    employee = db.get(User, user_id)
    if not employee:
        raise HTTPException(404, 'Сотрудник не найден')
    if user_id == user.id and (payload.get('active') is False or payload.get('role') == 'staff'):
        raise HTTPException(400, 'Нельзя отключить или понизить собственный аккаунт')
    if 'active' in payload:
        if not isinstance(payload['active'], bool):
            raise HTTPException(422, 'active должен быть логическим значением')
        employee.active = payload['active']
    if 'role' in payload:
        if payload['role'] not in ('admin', 'staff'):
            raise HTTPException(422, 'Неизвестная роль')
        employee.role = payload['role']
    log_activity(db, user.id, 'Изменён сотрудник', employee_id=user_id)
    db.commit()
    return user_dict(employee)


def lead_query(user, q='', status=None, city=None, category=None, min_score=0, website_state=None, owner_id=None, search_id=None):
    statement = select(Lead)
    if q:
        value = '%' + q.replace('%', '\\%').replace('_', '\\_') + '%'
        statement = statement.where(or_(*(column.ilike(value, escape='\\') for column in (Lead.name, Lead.phone, Lead.address, Lead.city))))
    for column, value in ((Lead.status, status), (Lead.city, city), (Lead.category, category), (Lead.website_status, website_state), (Lead.owner_id, owner_id)):
        if value is not None and value != '':
            statement = statement.where(column == value)
    if min_score:
        statement = statement.where(Lead.score >= min_score)
    if search_id is not None:
        statement = statement.where(Lead.id.in_(select(SearchLead.lead_id).where(SearchLead.search_id == search_id)))
    return statement


@app.get('/api/leads')
def leads(q: str = '', status: str | None = None, city: str | None = None, category: str | None = None, min_score: int = Query(0, ge=0, le=100), website_state: str | None = None, owner_id: int | None = None, search_id: int | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200), sort: str = '-id', user=Depends(current_user), db=Depends(db_session)):
    statement = lead_query(user, q, status, city, category, min_score, website_state, owner_id, search_id)
    total = db.scalar(select(func.count()).select_from(statement.subquery()))
    sort_column = {'id': Lead.id, 'name': Lead.name, 'score': Lead.score, 'rating': Lead.rating, 'city': Lead.city, 'collected_at': Lead.collected_at}.get(sort.lstrip('-'), Lead.id)
    statement = statement.order_by(sort_column.desc() if sort.startswith('-') else sort_column.asc())
    return {'items': [as_dict(l) for l in db.scalars(statement.offset((page - 1) * page_size).limit(page_size))], 'total': total, 'page': page, 'page_size': page_size}


@app.post('/api/leads')
def create_lead(data: LeadInput, user=Depends(current_user), db=Depends(db_session)):
    lead, created = insert_lead(db, data.model_dump())
    if not created:
        raise HTTPException(409, f'Организация уже есть в базе: №{lead.id}')
    log_activity(db, user.id, 'Добавлена организация', lead_id=lead.id)
    db.commit()
    return as_dict(lead)


def editable(db, lead_id, user):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, 'Организация не найдена')
    if user.role != 'admin' and lead.owner_id not in (None, user.id):
        raise HTTPException(403, 'Клиент назначен другому сотруднику')
    return lead


@app.patch('/api/leads/{lead_id}')
def patch_lead(lead_id: int, data: LeadPatch, user=Depends(current_user), db=Depends(db_session)):
    lead = editable(db, lead_id, user)
    values = data.model_dump(exclude_unset=True, exclude={'version'})
    previous_status = lead.status
    if any(value is None for key, value in values.items() if key not in ('owner_id', 'next_contact')):
        raise HTTPException(422, 'Статус, согласие, заметка и стоимость не могут быть null')
    if 'owner_id' in values:
        if user.role != 'admin' and values['owner_id'] != user.id:
            raise HTTPException(403, 'Распределять клиентов может администратор')
        if values['owner_id'] is not None:
            owner = db.get(User, values['owner_id'])
            if not owner or not owner.active:
                raise HTTPException(422, 'Выберите действующего сотрудника')
    if values.get('consent') == 'denied' or values.get('status') == 'Не связываться повторно':
        values.update(consent='denied', status='Не связываться повторно', next_contact=None)
    if lead.consent == 'denied' and values.get('consent') != 'granted' and (values.get('next_contact') or values.get('status', lead.status) != 'Не связываться повторно'):
        raise HTTPException(409, 'Коммуникация запрещена. Для возобновления зафиксируйте новое согласие')
    values['version'] = Lead.version + 1
    result = db.execute(update(Lead).where(Lead.id == lead_id, Lead.version == data.version).values(**values))
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(409, 'Запись уже изменена. Обновите данные')
    if data.status and data.status != previous_status:
        db.add(Interaction(lead_id=lead_id, user_id=user.id, kind='status', comment=f'Статус: {data.status}'))
    log_activity(db, user.id, 'Изменена организация', lead_id=lead_id, fields=list(values))
    db.commit()
    db.refresh(lead)
    return as_dict(lead)


@app.post('/api/leads/bulk')
def bulk(data: Bulk, user=Depends(current_user), db=Depends(db_session)):
    if user.role != 'admin':
        raise HTTPException(403, 'Требуются права администратора')
    selected = list(db.scalars(select(Lead).where(Lead.id.in_(data.ids)).with_for_update()))
    if len(selected) != len(set(data.ids)):
        raise HTTPException(404, 'Часть записей не найдена')
    if data.action == 'assign':
        owner = db.get(User, data.owner_id) if data.owner_id else None
        if data.owner_id and (not owner or not owner.active):
            raise HTTPException(422, 'Сотрудник не найден или отключён')
        for lead in selected:
            expected = data.versions.get(str(lead.id))
            if expected is None:
                raise HTTPException(422, 'Для назначения передайте версии выбранных записей')
            changed = db.execute(update(Lead).where(Lead.id == lead.id, Lead.version == expected).values(owner_id=data.owner_id, version=Lead.version + 1))
            if changed.rowcount != 1:
                db.rollback()
                raise HTTPException(409, 'Один из клиентов уже назначен. Обновите таблицу')
    else:
        db.execute(delete(Interaction).where(Interaction.lead_id.in_(data.ids)))
        db.execute(delete(SearchLead).where(SearchLead.lead_id.in_(data.ids)))
        db.execute(delete(Lead).where(Lead.id.in_(data.ids)))
    log_activity(db, user.id, 'Массовое действие', action_name=data.action, ids=data.ids)
    db.commit()
    return {'affected': len(selected)}


@app.get('/api/leads/{lead_id}/interactions')
def interactions(lead_id: int, user=Depends(current_user), db=Depends(db_session)):
    if not db.get(Lead, lead_id):
        raise HTTPException(404, 'Организация не найдена')
    return [as_dict(i) for i in db.scalars(select(Interaction).where(Interaction.lead_id == lead_id).order_by(Interaction.created_at.desc()))]


@app.post('/api/leads/{lead_id}/interactions')
def add_interaction(lead_id: int, data: InteractionInput, user=Depends(current_user), db=Depends(db_session)):
    lead = editable(db, lead_id, user)
    if data.kind == 'call' and (lead.consent != 'granted' or lead.status == 'Не связываться повторно'):
        raise HTTPException(409, 'Для регистрации рекламного звонка нужно согласие. При отказе повторные контакты запрещены')
    item = Interaction(lead_id=lead_id, user_id=user.id, **data.model_dump())
    db.add(item)
    log_activity(db, user.id, 'Добавлено взаимодействие', lead_id=lead_id, kind=data.kind)
    db.commit()
    return as_dict(item)


@app.post('/api/leads/{lead_id}/audit')
async def audit(lead_id: int, user=Depends(current_user), db=Depends(db_session)):
    from .site_audit import audit_site
    lead = editable(db, lead_id, user)
    version = lead.version
    result = await audit_site(lead.website or '')
    lead.audit = result
    lead.website_status = result['website_status']
    score_lead(lead, get_settings(db))
    values = {'audit': lead.audit, 'website_status': lead.website_status, 'score': lead.score, 'score_reasons': lead.score_reasons, 'version': version + 1}
    db.rollback()
    changed = db.execute(update(Lead).where(Lead.id == lead_id, Lead.version == version).values(**values))
    if changed.rowcount != 1:
        db.rollback()
        raise HTTPException(409, 'Запись изменилась во время проверки. Повторите проверку')
    log_activity(db, user.id, 'Проверен сайт', lead_id=lead_id)
    db.commit()
    db.refresh(lead)
    return as_dict(lead)


@app.get('/api/searches')
def searches(user=Depends(current_user), db=Depends(db_session)):
    return [as_dict(s) for s in db.scalars(select(Search).order_by(Search.id.desc()).limit(200))]


@app.get('/api/searches/{search_id}')
def search_detail(search_id: int, user=Depends(current_user), db=Depends(db_session)):
    job = db.get(Search, search_id)
    if not job:
        raise HTTPException(404, 'Поиск не найден')
    return as_dict(job)


@app.post('/api/searches')
async def start_search(data: SearchInput, user=Depends(current_user), db=Depends(db_session)):
    capabilities = {s['id']: s for s in source_status()}
    for source in data.sources:
        if not capabilities[source]['available']:
            raise HTTPException(422, capabilities[source]['reason'])
    if data.without_website and data.broken_website:
        raise HTTPException(422, 'Фильтры сайта несовместимы: выберите один')
    job = Search(criteria=data.model_dump(), created_by=user.id)
    db.add(job)
    db.flush()
    log_activity(db, user.id, 'Запущен поиск', search_id=job.id)
    db.commit()
    if settings.task_mode == 'celery':
        try:
            search_task.delay(job.id)
        except Exception:
            job.status = 'failed'
            job.errors = ['Не удалось отправить задачу в очередь. Проверьте Redis и worker.']
            db.commit()
            raise HTTPException(503, job.errors[0])
    else:
        task = asyncio.create_task(run_search(job.id))
        background_jobs.add(task)
        task.add_done_callback(background_jobs.discard)
    return as_dict(job)


@app.post('/api/searches/{search_id}/cancel')
def cancel_search(search_id: int, user=Depends(current_user), db=Depends(db_session)):
    job = db.get(Search, search_id)
    if not job:
        raise HTTPException(404, 'Поиск не найден')
    if job.created_by != user.id and user.role != 'admin':
        raise HTTPException(403, 'Можно остановить только свой поиск')
    if job.status in ('queued', 'running'):
        job.status = 'cancelled'
        job.finished_at = now()
        db.commit()
    return as_dict(job)


@app.get('/api/settings')
def read_settings(user=Depends(current_user), db=Depends(db_session)):
    return get_settings(db)


@app.put('/api/settings')
def write_settings(data: SettingsInput, user=Depends(admin), db=Depends(db_session)):
    config = data.model_dump()
    row = db.get(AppSetting, 'config')
    if row:
        row.value = config
    else:
        db.add(AppSetting(key='config', value=config))
    for lead in db.scalars(select(Lead)):
        score_lead(lead, config)
        lead.version += 1
    log_activity(db, user.id, 'Изменены настройки оценки')
    db.commit()
    return config


@app.get('/api/dashboard')
def dashboard(user=Depends(current_user), db=Depends(db_session)):
    records = list(db.scalars(select(Lead)))
    events = list(db.scalars(select(Interaction)))
    today = datetime.now(ZoneInfo('Europe/Moscow')).date()
    paid = [l for l in records if l.status in PAID]
    # Count the first recorded payment transition only, even after moving further through delivery.
    first_paid = {}
    for event in events:
        if event.kind == 'status' and event.comment.removeprefix('Статус: ') in PAID:
            first_paid[event.lead_id] = min(first_paid.get(event.lead_id, event.created_at), event.created_at)
    paid_today = sum(1 for key, stamp in first_paid.items() if key in {l.id for l in paid} and stamp.replace(tzinfo=now().tzinfo).astimezone(ZoneInfo('Europe/Moscow')).date() == today)
    def grouped(field):
        values = {}
        for lead in records:
            key = getattr(lead, field) or 'Не указано'
            values[key] = values.get(key, 0) + 1
        return [{'name': k, 'value': v} for k, v in sorted(values.items(), key=lambda item: -item[1])]
    employee_stats = []
    for employee in db.scalars(select(User)):
        owned = [l for l in records if l.owner_id == employee.id]
        employee_stats.append({'id': employee.id, 'name': employee.name, 'leads': len(owned), 'calls': sum(i.kind == 'call' and i.user_id == employee.id for i in events), 'paid': sum(l.status in PAID for l in owned), 'revenue': round(sum(l.order_value for l in owned if l.status in PAID), 2)})
    return {'total': len(records), 'without_website': sum(not l.website for l in records), 'problem_websites': sum(l.website_status == 'unavailable' for l in records), 'promising': sum(l.score >= 60 for l in records), 'new_today': sum(l.collected_at.replace(tzinfo=now().tzinfo).astimezone(ZoneInfo('Europe/Moscow')).date() == today for l in records), 'calls': sum(i.kind == 'call' for i in events), 'refusals': sum(l.status == 'Отказался' for l in records), 'interested': sum(l.status in ['Заинтересован', 'Отправлена демонстрация', 'Отправлено коммерческое предложение', 'Ведутся переговоры', 'Ожидается оплата'] for l in records), 'paid': len(paid), 'conversion': round(len(paid) / len(records) * 100, 1) if records else 0, 'revenue': round(sum(l.order_value for l in paid), 2), 'paid_today': paid_today, 'daily_goal': get_settings(db)['daily_goal'], 'by_city': grouped('city'), 'by_category': grouped('category'), 'by_status': grouped('status'), 'by_employee': employee_stats}


@app.get('/api/activities')
def activities(user=Depends(admin), db=Depends(db_session)):
    return [as_dict(a) for a in db.scalars(select(Activity).order_by(Activity.id.desc()).limit(300))]


@app.get('/api/filters')
def filters(user=Depends(current_user), db=Depends(db_session)):
    row = db.get(AppSetting, f'filters:{user.id}')
    return row.value.get('items', []) if row else []


@app.put('/api/filters')
def save_filters(payload: list[dict], user=Depends(current_user), db=Depends(db_session)):
    if len(payload) > 50 or len(json.dumps(payload)) > 50000:
        raise HTTPException(422, 'Слишком много сохранённых фильтров')
    key = f'filters:{user.id}'
    row = db.get(AppSetting, key)
    if row:
        row.value = {'items': payload}
    else:
        db.add(AppSetting(key=key, value={'items': payload}))
    db.commit()
    return payload


IMPORT_ALIASES = {'Название': 'name', 'Категория': 'category', 'Город': 'city', 'Адрес': 'address', 'Телефон': 'phone', 'Сайт': 'website', 'Рейтинг': 'rating', 'Отзывы': 'review_count', 'Почта': 'email', 'Карточка': 'card_url'}


@app.post('/api/import')
async def import_file(file: UploadFile = File(...), user=Depends(current_user), db=Depends(db_session)):
    raw = await file.read(10 * 1024 * 1024 + 1)
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(413, 'Максимальный размер файла: 10 МБ')
    try:
        if (file.filename or '').lower().endswith('.xlsx'):
            import zipfile
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                if sum(item.file_size for item in archive.infolist()) > 50 * 1024 * 1024:
                    raise ValueError('Распакованный файл превышает 50 МБ')
            workbook = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            rows = workbook.active.iter_rows(values_only=True)
            headers = [str(x or '').strip() for x in next(rows)]
            data_rows = (dict(zip(headers, row)) for row in rows)
        elif (file.filename or '').lower().endswith('.csv'):
            text = raw.decode('utf-8-sig')
            try:
                dialect = csv.Sniffer().sniff(text[:8192], delimiters=',;\t')
            except csv.Error:
                dialect = csv.excel
            data_rows = csv.DictReader(io.StringIO(text), dialect=dialect)
        else:
            raise HTTPException(422, 'Поддерживаются CSV UTF-8 и XLSX')
        imported, duplicates, errors = 0, 0, []
        for index, row in enumerate(data_rows, 2):
            if index > 10001:
                raise HTTPException(422, 'В одном файле допускается до 10 000 строк')
            try:
                mapped = {IMPORT_ALIASES.get(str(k).strip(), str(k).strip()): v for k, v in row.items() if k is not None and v is not None and str(v).strip()}
                for key in ('rating', 'review_count'):
                    if key in mapped:
                        mapped[key] = float(str(mapped[key]).replace(',', '.')) if key == 'rating' else int(mapped[key])
                for key in ('phone',):
                    if key in mapped:
                        mapped[key] = str(mapped[key])
                parsed = LeadInput.model_validate(mapped)
                with db.begin_nested():
                    _, created = insert_lead(db, parsed.model_dump())
                imported += int(created)
                duplicates += int(not created)
            except Exception:
                errors.append({'row': index, 'message': 'Проверьте название, рейтинг (0–5), количество отзывов и формат полей'})
        log_activity(db, user.id, 'Импорт организаций', imported=imported, duplicates=duplicates)
        db.commit()
        return {'imported': imported, 'duplicates': duplicates, 'errors': errors[:100]}
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(422, 'Не удалось прочитать файл. Проверьте формат и заголовки колонок')


def safe_cell(value):
    if value is None:
        return ''
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    if isinstance(value, datetime):
        value = value.isoformat()
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')):
        return "'" + value
    return value


@app.get('/api/export')
def export(format: str = 'csv', ids: str | None = None, search_id: int | None = None, q: str = '', status: str | None = None, city: str | None = None, category: str | None = None, min_score: int = Query(0, ge=0, le=100), website_state: str | None = None, owner_id: int | None = None, sort: str = '-id', user=Depends(current_user), db=Depends(db_session)):
    if format not in ('csv', 'xlsx', 'json'):
        raise HTTPException(422, 'Формат должен быть csv, xlsx или json')
    query = lead_query(user, q, status, city, category, min_score, website_state, owner_id, search_id)
    if ids:
        try:
            numbers = [int(i) for i in ids.split(',')]
            if len(numbers) > 1000:
                raise ValueError()
        except ValueError:
            raise HTTPException(422, 'Некорректный список организаций')
        query = query.where(Lead.id.in_(numbers))
    sort_column = {'id': Lead.id, 'name': Lead.name, 'score': Lead.score, 'rating': Lead.rating, 'city': Lead.city, 'collected_at': Lead.collected_at}.get(sort.lstrip('-'), Lead.id)
    records = list(db.scalars(query.order_by(sort_column.desc() if sort.startswith('-') else sort_column.asc())))
    if any(not l.export_allowed for l in records):
        raise HTTPException(403, 'В выборке есть данные источника без разрешения на экспорт. Выберите разрешённые записи')
    data = [jsonable_encoder(as_dict(l)) for l in records]
    if format == 'json':
        body = io.BytesIO(json.dumps(data, ensure_ascii=False, indent=2).encode())
        mime = 'application/json'
    else:
        columns = ['id', 'name', 'category', 'city', 'address', 'phone', 'website', 'rating', 'review_count', 'email', 'card_url', 'source', 'collected_at', 'website_status', 'score', 'status', 'owner_id', 'notes', 'order_value', 'next_contact', 'consent']
        if format == 'csv':
            stream = io.StringIO(newline='')
            writer = csv.writer(stream)
            writer.writerow(columns)
            writer.writerows([[safe_cell(row.get(k)) for k in columns] for row in data])
            body = io.BytesIO(stream.getvalue().encode('utf-8-sig'))
            mime = 'text/csv; charset=utf-8'
        else:
            workbook = Workbook(write_only=True)
            worksheet = workbook.create_sheet('Клиенты')
            worksheet.append(columns)
            for row in data:
                worksheet.append([safe_cell(row.get(k)) for k in columns])
            body = io.BytesIO()
            workbook.save(body)
            body.seek(0)
            mime = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    log_activity(db, user.id, 'Экспорт организаций', count=len(data), format=format)
    db.commit()
    return StreamingResponse(body, media_type=mime, headers={'Content-Disposition': f'attachment; filename="dus-leads.{format}"'})


frontend = Path(__file__).resolve().parents[2] / 'frontend' / 'dist'
if frontend.exists():
    from fastapi.staticfiles import StaticFiles
    app.mount('/assets', StaticFiles(directory=frontend / 'assets'), name='assets')

    @app.get('/{path:path}')
    def spa(path: str):
        if path.startswith('api/'):
            raise HTTPException(404, 'Метод API не найден')
        return FileResponse(frontend / 'index.html')
