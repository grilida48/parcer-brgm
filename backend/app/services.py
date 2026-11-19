import hashlib
import re
from sqlalchemy import select
from .models import Activity, AppSetting, Lead
from .scoring import DEFAULTS, score_lead


def get_settings(db):
    row = db.get(AppSetting, 'config')
    return row.value if row else DEFAULTS.copy()


def normalize(value):
    return re.sub(r'[^\w]', '', (value or '').casefold())


def fingerprint(data):
    phone = re.sub(r'\D', '', data.get('phone') or '')
    if len(phone) == 11 and phone.startswith('8'):
        phone = '7' + phone[1:]
    if phone:
        value = 'phone:' + phone + ':' + ':'.join(normalize(data.get(k)) for k in ('name', 'city', 'address'))
    else:
        value = ':'.join(normalize(data.get(k)) for k in ('name', 'city', 'address'))
    return hashlib.sha256(value.encode()).hexdigest()


def find_duplicate(db, data):
    key = fingerprint(data)
    found = db.scalar(select(Lead).where(Lead.fingerprint == key))
    if found:
        return found
    name, city, address = (normalize(data.get(k)) for k in ('name', 'city', 'address'))
    if name and city and address:
        for lead in db.scalars(select(Lead).where(Lead.city == data.get('city'))):
            if normalize(lead.name) == name and normalize(lead.address) == address:
                return lead
    return None


def insert_lead(db, data, search_id=None):
    if data.get('closed'):
        return None, False
    if existing := find_duplicate(db, data):
        return existing, False
    allowed = set(Lead.__table__.columns.keys()) - {'id', 'fingerprint', 'version', 'score', 'score_reasons', 'owner_id', 'status'}
    clean = {k: v for k, v in data.items() if k in allowed}
    if isinstance(clean.get('collected_at'), str):
        from datetime import datetime
        clean['collected_at'] = datetime.fromisoformat(clean['collected_at'].replace('Z', '+00:00'))
    clean['website_status'] = clean.get('website_status') or ('unchecked' if clean.get('website') else 'not_listed')
    clean.setdefault('order_value', get_settings(db)['default_order_value'])
    lead = Lead(**clean, fingerprint=fingerprint(data), search_id=search_id)
    score_lead(lead, get_settings(db))
    db.add(lead)
    db.flush()
    return lead, True


def log_activity(db, user_id, action, **details):
    db.add(Activity(user_id=user_id, action=action, details=details))


def as_dict(obj):
    return {col.name: getattr(obj, col.name) for col in obj.__table__.columns}
