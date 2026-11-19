import argparse
import getpass
from sqlalchemy import select
from .auth import hash_password
from .config import settings
from .models import Base, SessionLocal, User, engine
from .services import insert_lead


def main():
    parser = argparse.ArgumentParser(description='Управление DUS Leads')
    parser.add_argument('command', choices=['create-admin', 'demo'])
    parser.add_argument('--email')
    parser.add_argument('--name', default='Администратор')
    args = parser.parse_args()
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        if args.command == 'create-admin':
            email = args.email or input('Почта: ').strip()
            password = getpass.getpass('Пароль (минимум 12 символов): ')
            if len(password) < 12:
                parser.error('Слишком короткий пароль')
            if db.scalar(select(User).where(User.email == email.casefold())):
                parser.error('Почта уже используется')
            db.add(User(name=args.name, email=email.casefold(), password_hash=hash_password(password), role='admin'))
            db.commit()
            print('Администратор создан')
        else:
            if not settings.demo_mode:
                parser.error('Демонстрационные данные доступны только при DEMO_MODE=true. Используйте отдельную БД')
            for index, category in enumerate(['Автосервисы', 'Мебель на заказ', 'Стоматологии']):
                insert_lead(db, {'name': f'Демо-организация {index + 1}', 'category': category, 'city': 'Демонстрационный город', 'address': f'Учебный адрес, {index + 1}', 'rating': 4.7, 'review_count': 45, 'source': 'demo', 'notes': 'Синтетическая запись. Контактных данных нет.'})
            db.commit()
            print('Демо-данные добавлены')


if __name__ == '__main__':
    main()
