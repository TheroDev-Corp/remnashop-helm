# Remnashop (remnashop-helm) — AI Agent & Developer Guidelines

Этот репозиторий представляет собой Telegram-бота для продажи VPN-подписок с интеграцией панели **Remnawave 3.x**, Clean Architecture / DDD структурой на **FastAPI**, **Aiogram 3.x**, **aiogram-dialog**, **Dishka** DI, **SQLAlchemy 2.0 (Async)** и развертыванием через **Helm**.

---

## 🏛 Архитектура и структура проекта

```text
src/
├── core/                  # Бизнес-сущности, enum'ы (SubscriptionStatus, Role, etc.), конфигурация
├── application/           # Бизнес-логика (Use Cases / Interactors, DTO, интерфейсы сервисов)
│   ├── common/            # Базовые классы (Interactor, DAO interfaces, UoW)
│   ├── services/          # Сервисы приложения (Event Bus, Sync, Webhooks)
│   └── use_cases/         # Интеракторы (user, subscription, payment, promo, access, remnawave)
├── infrastructure/        # Реализация внешних интерфейсов
│   ├── database/          # SQLAlchemy модели, DAO, UoW, репозитории
│   ├── payment_gateways/  # Интеграции платёжек (YooMoney, CryptoBot, Telegram Stars, Heleket, etc.)
│   ├── services/          # Внешние сервисы (Remnawave, EventBus, Notification, Translator, S3)
│   └── di/                # Dishka DI контейнеры и провайдеры
├── telegram/              # Презентационный слой Telegram (Aiogram 3 + aiogram-dialog)
│   ├── middlewares/       # Мидлвари (Access, User, Throttling, Rules, Channel, Error)
│   └── routers/           # Роутеры и диалоги (dashboard, subscription, payment, admin, etc.)
└── web/                   # Презентационный слой FastAPI (Webhooks, WebApp API, Healthcheck)
    └── endpoints/         # Маршруты Telegram webhook, Remnawave webhook, Payment webhooks
helm/                      # Helm чарт для развертывания в Kubernetes
migrations/                # Alembic миграции базы данных
tests/                     # Юнит- и интеграционные тесты (pytest)
```

---

## ⚙️ Технологический стек

* **Язык**: Python 3.12+ (управление через `uv`)
* **Telegram Framework**: `aiogram 3.x`, `aiogram-dialog 2.x`
* **Web Framework**: `FastAPI`, `Uvicorn`
* **DI Контейнер**: `Dishka` (строгая типизация зависимостей)
* **ORM & DB**: `SQLAlchemy 2.0` (asyncpg / PostgreSQL), `Alembic`
* **VPN Панель**: `Remnawave 3.x` (SDK `remnapy` + кастомный HTTP-клиент для v3 API)
* **CI/CD & Деплой**: GitHub Actions (`ghcr.io`), Helm Chart, Kubernetes

---

## 📌 Ключевые правила разработки

### 1. Dishka Dependency Injection
* **Никакого ручного извлечения из `middleware_data` в диалогах:**
  * ❌ `i18n = middleware_data["i18n"]` — **ЗАПРЕЩЕНО**, вызовет `KeyError`.
  * ✅ Всегда инжектить зависимости через Dishka: `i18n: FromDishka[TranslatorRunner]`, `remnawave: FromDishka[Remnawave]`, `dao: FromDishka[UserDao]`.
* Все интеракторы (`Interactor`) и DAO должны регистрироваться в `src/infrastructure/di/` с соответствующим `Scope` (`Scope.REQUEST` или `Scope.APP`).

### 2. Пользователи и различие идентификаторов (ID Mapping)
В проекте сосуществуют три различных типа идентификаторов:
* `UserDto.id` / `User.id` — внутренний авто-инкрементный Primary Key в PostgreSQL базе данных бота.
* `UserDto.telegram_id` / `User.telegram_id` — реальный 64-битный ID пользователя в Telegram.
* `SubscriptionDto.user_remna_id` — числовой идентификатор пользователя в панели Remnawave.

**Важно при работе с Remnawave 3.x:**
* Всегда реализовывать **fallback по `telegram_id`** и **auto-healing**: если при запросе по `user_remna_id` панель возвращает 404 или пользователя с другим `telegram_id` (сдвиг ID при миграциях), бот должен найти юзера через фильтр `telegramId` и автоматически обновить `user_remna_id` в PostgreSQL.

### 3. Обработка платежей и вебхуков
* Обработчики вебхуков платежных шлюзов (`src/infrastructure/payment_gateways/`) должны корректно обрабатывать тестовые пинги и технические уведомления (например, `test-notification` в YooMoney):
  * Возвращать `None` (`200 OK`) без выбрасывания `ValueError` или генерации ложных `#ErrorEvent`.

### 4. Уведомления и ошибки доставки в Telegram
* В сервисе уведомлений (`src/infrastructure/services/notification.py`) ошибки `TelegramNotFound`, `TelegramBadRequest` со статусами `"chat not found"` или `"user not found"` (когда пользователь удалил Telegram-аккаунт или заблокировал бота) должны логироваться на уровне `INFO`/`DEBUG` и **не должны** спамить трейсбеками в канал администратора.

---

## 🧪 Тестирование и команды

Всегда запускать тесты перед фиксацией изменений:
```powershell
uv run pytest
```

Локальный запуск линтеров / форматирования:
```powershell
uv run ruff check .
uv run ruff format .
```

---

## 🚀 Процесс релиза

При выпуске новой версии:
1. Обновить версию в `helm/Chart.yaml`:
   * `version: "0.1.X"` (версия чарта)
   * `appVersion: "0.9.X"` (версия приложения)
2. Обновить тег образа в `helm/values.yaml`:
   * `app.image.tag: "v0.9.X"`
3. Создать коммит, запушить в `main`, поставить тег `v0.9.X` и выпустить релиз:
   ```powershell
   git add src/ helm/
   git commit -m "feat/fix: description"
   git push origin main
   git tag v0.9.X
   git push origin v0.9.X
   gh release create v0.9.X --generate-notes --title "Remnashop v0.9.X"
   ```
4. GitHub Actions автоматически соберет и запушит образ в `ghcr.io/therodev-corp/remnashop-helm:v0.9.X`.
