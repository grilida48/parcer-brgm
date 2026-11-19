import hashlib
import hmac
import secrets
from datetime import timedelta
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from .models import LoginSession, SessionLocal, User, now


def hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
    return f'{salt}${digest}'


def verify_password(password, stored):
    salt, expected = stored.split('$', 1)
    actual = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
    return hmac.compare_digest(expected, actual)


def db_session():
    with SessionLocal() as db:
        yield db


def current_user(request: Request, db=Depends(db_session)):
    token = request.cookies.get('dus_session', '')
    session = db.get(LoginSession, hashlib.sha256(token.encode()).hexdigest())
    if not session or session.expires_at.replace(tzinfo=now().tzinfo) < now():
        raise HTTPException(401, 'Войдите в аккаунт')
    user = db.get(User, session.user_id)
    if not user or not user.active:
        raise HTTPException(401, 'Аккаунт отключён')
    return user


def admin(user=Depends(current_user)):
    if user.role != 'admin':
        raise HTTPException(403, 'Требуются права администратора')
    return user


def issue_session(db, user, response, secure, samesite='strict'):
    token = secrets.token_urlsafe(48)
    db.add(LoginSession(token_hash=hashlib.sha256(token.encode()).hexdigest(), user_id=user.id, expires_at=now() + timedelta(days=7)))
    db.commit()
    response.set_cookie('dus_session', token, httponly=True, secure=secure, samesite=samesite, max_age=604800, path='/')


def user_dict(user):
    return {key: getattr(user, key) for key in ('id', 'name', 'email', 'role', 'active')}
