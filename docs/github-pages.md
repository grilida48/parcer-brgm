# Публикация на github.io

GitHub Pages размещает статический HTML, CSS и JavaScript. FastAPI, PostgreSQL, Redis и Celery должны работать на отдельном сервере. Это ограничение [GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages).

## 1. Подготовить backend

Разместите проект на сервере с Docker Compose. Назначьте backend публичный HTTPS-адрес, например `https://api.example.ru`. Прокси должен передавать запросы приложению на порту 8000. API-ключи задаются только в `.env` этого сервера.

Создайте администратора до открытия сервера внешним посетителям: через `ADMIN_EMAIL` и `ADMIN_PASSWORD` или командой `python -m app.cli create-admin`. Иначе первый посетитель сможет пройти начальную настройку.

В окружении backend задайте:

```dotenv
COOKIE_SECURE=true
COOKIE_SAMESITE=none
ALLOWED_ORIGINS=https://YOUR_LOGIN.github.io
```

Не добавляйте путь репозитория в `ALLOWED_ORIGINS`. Origin содержит только протокол и домен. Если браузер блокирует сторонние cookies, сессия между github.io и отдельным доменом API не сохранится. Для стабильной работы назначьте Pages собственный домен `leads.example.ru`, а API домен `api.example.ru`, установите `COOKIE_SAMESITE=lax`, обновите разрешённый origin. Два HTTPS-поддомена одного регистрируемого домена будут работать в одном site.

## 2. Загрузить исходники в GitHub

Создайте репозиторий и загрузите файлы проекта. `.env`, базы данных, зависимости и файлы `.runtime` должны оставаться вне Git. `.env.example` можно публиковать: он не содержит действующих ключей.

Если репозиторий называется `YOUR_LOGIN.github.io`, сайт будет доступен в корне. Для другого имени адрес будет `https://YOUR_LOGIN.github.io/REPO/`.

## 3. Включить публикацию

1. В `Settings > Pages > Build and deployment` выберите `Source: GitHub Actions`.
2. В `Settings > Secrets and variables > Actions > Variables` создайте `VITE_API_BASE_URL` со значением `https://api.example.ru` без `/api` в конце.
3. Откройте `Actions > Интерфейс DUS Leads на GitHub Pages > Run workflow` или отправьте изменения в `main`.
4. После успешного запуска откройте ссылку из environment `github-pages` и войдите в созданный аккаунт.

Workflow сам задаёт базовый путь ассетов для корневого сайта или сайта проекта по [документации Vite](https://vite.dev/guide/static-deploy.html#github-pages).

`VITE_API_BASE_URL` является публичным адресом сервера и попадёт в сборку. Никогда не передавайте ключи Яндекса/2ГИС или пароли через переменные `VITE_*`.

Репозиторий и сервер в этой сессии ещё не выбраны. Файлы публикации готовы; действующего адреса github.io до настройки и выполнения workflow нет.
