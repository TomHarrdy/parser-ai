# agent.md — Карта проекта parser-ai

> Этот файл создан для AI-агентов и разработчиков, которые впервые открывают проект.
> Прочитай этот файл первым — он даёт полную картину: что делает проект, как устроен, как запустить, что трогать нельзя.

---

## 1. Что это за проект

**parser-ai** — автономный AI-агент для мониторинга упоминаний бренда в интернете.

Конкретный заказчик: сеть квест-парков **«Погружение»** (pogruzhenye.ru, Москва + СПб).

Бот каждый час (`cron minute=0`) обходит несколько источников, собирает упоминания бренда, фильтрует дубли и старые материалы, отправляет текст в LLM для анализа тональности и релевантности, сохраняет результат в PostgreSQL и присылает алерты в Telegram. Оператор может нажать «❌ Не касается нас» — бот запоминает урок и больше не присылает похожие ложные срабатывания.

---

## 2. Технологический стек

| Слой | Технология |
|---|---|
| Язык | Python 3.12 |
| Async runtime | asyncio |
| Pipeline / граф | LangGraph (`StateGraph`) |
| Scheduler | APScheduler (`AsyncIOScheduler`, cron) |
| Telegram bot | aiogram v3 |
| БД | PostgreSQL 16 + SQLAlchemy 2 (async) + asyncpg |
| LLM | DeepSeek Chat (через OpenAI-совместимый API), настраивается через `.env` |
| Веб-поиск | SearXNG (self-hosted в Docker), optional Exa Search, legacy Tavily |
| Парсинг страниц | trafilatura → Jina Reader → Firecrawl (каскад, free-first) |
| Мониторинг эффективности источников | JSONL snapshot в `data/provider_effectiveness.jsonl` |
| ВКонтакте | Прямой VK API (без Apify), `vk_direct.py` |
| Яндекс.Карты | Apify actor (`drobnikj/yandex-maps-reviews-scraper`) |
| Instagram | Instaloader (опционально, выключен в `.env`) |
| Контейнеризация | Docker Compose (3 сервиса: postgres, searxng, agent) |
| Тесты | pytest + pytest-asyncio (171 тест) |

---

## 3. Карта файлов

```
parser-ai/
│
├── agent.md                  ← ТЫ ЗДЕСЬ. Карта проекта для агентов.
├── README.md                 ← Краткое описание (устаревает быстрее agent.md)
├── TODO.md                   ← Бэклог задач (выполненные + планы)
├── .env                      ← Конфиг окружения (секреты, настройки бренда)
├── .env.example              ← Шаблон .env без секретов
├── requirements.txt          ← Python зависимости (prod)
├── requirements-dev.txt      ← Python зависимости (dev/test)
├── pytest.ini                ← Конфигурация тестов
│
├── src/                      ← Весь исходный код
│   ├── main.py               ← Точка входа: run / daemon / digest
│   ├── agent.py              ← Основной LangGraph pipeline (граф из 5 нод)
│   ├── models.py             ← SQLAlchemy ORM-модели всех таблиц БД
│   ├── db.py                 ← Async DB-сессии, все CRUD-функции
│   ├── settings.py           ← Pydantic Settings (читает .env)
│   ├── freshness.py          ← Логика проверки свежести контента
│   ├── telegram.py           ← Форматирование и отправка алертов
│   ├── bot_handler.py        ← aiogram router: /start, коллбэки кнопок
│   ├── llm_feedback.py       ← LLM-анализ фидбэка оператора → уроки
│   ├── digest.py             ← Еженедельный дайджест (пн 09:00)
│   ├── logging_config.py     ← Настройка логирования
│   │
│   └── tools/                ← Инструменты сбора данных
│       ├── __init__.py       ← Реэкспорт публичного API tools
│       ├── page_extract.py   ← Каскадный парсер страниц (trafilatura→Jina→Firecrawl)
│       ├── searxng.py        ← Поиск через self-hosted SearXNG
│       ├── exa.py            ← Optional Exa Search/Contents provider
│       ├── tavily.py         ← Поиск через Tavily API (платный, сейчас лимит исчерпан)
│       ├── vk_direct.py      ← Прямой VK API: стена, комментарии, поиск
│       ├── apify.py          ← Apify actors: VK + Яндекс.Карты
│       ├── firecrawl.py      ← Firecrawl API: deep-read + извлечение дат
│       ├── instagram_direct.py ← Instaloader: посты + комментарии (опционально)
│       └── retry.py          ← Декоратор with_retry для HTTP-вызовов
│
├── migrations/               ← SQL-миграции (применяются автоматически при первом старте PostgreSQL)
│   ├── 001_mentions.sql      ← Базовая таблица mentions
│   ├── 002_source_published_at.sql
│   ├── 003_run_checkpoints.sql
│   ├── 004_content_snapshots.sql
│   ├── 005_event_type_and_reason.sql
│   ├── 006_ignored_urls_and_is_ignored.sql
│   ├── 007_feedback_lessons.sql
│   ├── 008_feedback_lessons_user_explanation.sql
│   ├── 009_telegram_subscribers.sql
│   └── 010_freshness_archives.sql
│
├── docker/
│   ├── docker-compose.yml    ← 3 сервиса: postgres, searxng, agent
│   ├── Dockerfile            ← Образ agent (Python 3.12-slim)
│   └── searxng-settings.yml  ← Настройки SearXNG (движки, форматы)
│
├── tests/                    ← Тесты (171 шт., все проходят)
│   ├── conftest.py
│   ├── test_agent_nodes.py   ← 43 теста pipeline нод
│   ├── test_bot_handler.py   ← 5 тестов Telegram бота
│   ├── test_db.py            ← 14 тестов БД-функций
│   ├── test_models.py        ← 12 тестов ORM-моделей
│   ├── test_settings.py      ← 13 тестов настроек
│   ├── test_telegram.py      ← 22 теста форматирования алертов
│   ├── test_tools_apify.py   ← 4 теста Apify
│   ├── test_tools_firecrawl.py ← 16 тестов Firecrawl
│   ├── test_tools_instagram_direct.py ← 2 теста Instagram
│   ├── test_tools_page_extract.py ← 5 тестов каскадного парсера
│   ├── test_tools_searxng.py ← 4 теста SearXNG
│   └── test_tools_tavily.py  ← 13 тестов Tavily
│
├── docs/
│   ├── TZ.md                 ← Техническое задание
│   ├── SETUP_AND_TESTING.md  ← Инструкции по деплою
│   ├── ai-parser-architecture.png ← Схема архитектуры
│   └── free-first-parser-roadmap.html ← Roadmap парсера
│
├── scripts/
│   └── run.sh                ← Скрипт запуска вне Docker
│
└── config/
    └── .env.example          ← Ещё один шаблон конфига
```

---

## 4. Архитектура pipeline (agent.py)

Pipeline реализован как **LangGraph StateGraph** из 5 нод, выполняемых последовательно:

```
[collect_mentions]
        ↓
[verify_freshness]
        ↓
[deduplicate]
        ↓
[analyze_mentions]  ← LLM (DeepSeek / GPT)
        ↓
[save_and_notify]   ← PostgreSQL + Telegram
```

### Нода 1: collect_mentions
Параллельно (`asyncio.gather`) собирает данные из всех источников:
- **SearXNG brand** — поиск по названию бренда + ключевым словам
- **SearXNG phrases** — поиск по фразам (`SEARCH_PHRASES`) + бренд
- **Exa brand** — optional AI-native semantic search по бренду + ключевым словам (`ENABLE_EXA_SEARCH=true`)
- **Exa phrases** — optional semantic search по мониторинговым фразам; результаты идут через те же freshness/dedup/LLM gates
- **Tavily brand** — платный поиск (сейчас лимит исчерпан, HTTP 432)
- **Tavily phrases** — платный поиск по фразам
- **VK Direct** — стена паблика `pogruzhenye.official` + комментарии к постам
- **Apify VK** — резервный, если нет `VK_ACCESS_TOKEN`
- **Apify Яндекс.Карты** — отзывы по `YANDEX_MAPS_ORG_ID`
- **Instagram** — посты + комментарии (выключен: `ENABLE_INSTAGRAM=false`)

Каждый источник проверяет **watermark** (high-water mark): пропускает материалы опубликованные до последнего успешного запуска минус `WATERMARK_OVERLAP_MINUTES=60`.

### Нода 2: verify_freshness
Для каждого упоминания делает **deep-read** страницы через `page_extract.py`:
- `trafilatura` → Jina Reader → Firecrawl (каскад)
- Проверяет дату публикации страницы — если старше `MAX_ARTICLE_AGE_DAYS=7`, архивирует в `stale_urls` и пропускает
- Страницы с комментариями попадают в `comment_watchlist` для дальнейшего мониторинга

### Нода 3: deduplicate
- Проверяет `url_hash` и `text_hash` по таблице `mentions`
- Для **dynamic sources** (VK, Яндекс.Карты): проверяет `content_snapshots`. Если контент изменился (новые комментарии) — пропускает URL-дедуп, помечает как `is_content_update=True`
- Проверяет `ignored_urls` — если оператор ранее отклонил URL, пропускает навсегда

### Нода 4: analyze_mentions
Параллельно (`asyncio.gather` + семафор) для каждого нового упоминания:
- Формирует **system prompt** = профиль компании + накопленные уроки из `feedback_lessons`
- Запрашивает LLM: тональность (`positive/negative/neutral`), релевантность (`is_relevant`), тип контента (`event_type`), причину (`ai_reason`), краткое резюме (`ai_summary`)
- Нерелевантные (is_relevant=false) — пропускаются без сохранения

### Нода 5: save_and_notify
- Сохраняет релевантные упоминания в таблицу `mentions`
- Отправляет Telegram-алерты (фильтр по `ALERT_ON_SENTIMENT`)
- На каждом алерте кнопка **«❌ Не касается нас»**
- Обновляет watermark в `run_checkpoints`
- Пишет snapshot эффективности provider'ов в `PROVIDER_METRICS_PATH`

### Мониторинг эффективности Exa / web providers

Exa добавлен как **дополнительный контур**, а не замена текущего поиска.

Флаги:

```bash
EXA_API_KEY=
ENABLE_EXA_SEARCH=false
ENABLE_EXA_CONTENTS_FALLBACK=false
EXA_SEARCH_TYPE=auto
EXA_MAX_AGE_HOURS=24
PROVIDER_METRICS_PATH=data/provider_effectiveness.jsonl
```

Правила эксплуатации:

- включать Exa сначала в shadow/A-B режиме, сравнивая с `tavily`, `tavily_phrase`, `searxng`, `searxng_phrase`;
- Exa results всегда проходят текущие `verify_freshness`, `deduplicate`, `analyze_mentions`, `save_and_notify`;
- `publishedDate` от Exa не считать единственным доказательством свежести;
- `ENABLE_EXA_CONTENTS_FALLBACK=true` включает Exa Contents только как fallback, если текст короткий или дата не найдена;
- эффективность смотреть по JSONL snapshot: `collected`, `freshness_verified`, `freshness_skipped`, `dedup_new`, `dedup_skipped`, `analyzed_relevant`, `analysis_skipped`, `saved`, `notified`, `errors`, `duration_ms`;
- быстрый отчёт: `python scripts/provider_metrics_summary.py --last 50`;
- решение оставлять/отключать Exa принимать по incremental fresh relevant mentions, stale/noise rate, latency and cost per accepted alert.

---

## 5. Схема базы данных

| Таблица | Назначение |
|---|---|
| `mentions` | Все найденные и проанализированные упоминания |
| `run_checkpoints` | High-water mark (последний успешный запуск) по каждому источнику |
| `content_snapshots` | Хэши контента для dynamic sources (VK, Яндекс.Карты) |
| `stale_urls` | Архив старых URL (опубликованы давно, но могут иметь новые комменты) |
| `comment_watchlist` | Страницы, которые нужно периодически перепроверять на новые комментарии |
| `ignored_urls` | Постоянный блок-лист URL отклонённых оператором |
| `feedback_lessons` | Уроки, извлечённые LLM из фидбэка оператора (self-learning) |
| `telegram_subscribers` | Telegram-чаты подписанные на алерты |

Ключевые поля `mentions`:
- `url_hash` / `raw_text_hash` — SHA-256 для дедупликации
- `source_type` — `web | news | vk | yandex_maps | telegram | other`
- `event_type` — `article | review | comment | post | forum | mention | unknown`
- `sentiment` — `positive | negative | neutral`
- `ai_reason` — причина тональности (текст от LLM)
- `is_ignored` — помечено оператором как нерелевантное
- `source_published_at` — дата публикации от источника
- `activity_published_at` — фактическая дата активности (для старых страниц с новыми комментами)

---

## 6. Конфигурация (.env)

Основные переменные:

```bash
# Расписание (сейчас переопределено на cron minute=0 в main.py)
SCHEDULE_INTERVAL_MINUTES=60

# БД
DATABASE_URL=postgresql+asyncpg://brandmon:brandmon@postgres:5432/brandmon

# Telegram
TELEGRAM_BOT_TOKEN=...
TELEGRAM_TARGET_CHAT_ID=146025524

# Бренд
COMPANY_NAME=Погружение
COMPANY_DESCRIPTION=...  # подробное описание для LLM (многоязычность, контексты)
KEYWORDS=...             # ключевые слова через запятую
SEARCH_PHRASES=...       # поисковые фразы через запятую

# LLM (совместимо с OpenAI API)
LLM_PROVIDER=openai
LLM_API_KEY=...
LLM_ENDPOINT=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat

# Инструменты
TAVILY_API_KEY=...       # СЕЙЧАС НЕ РАБОТАЕТ (HTTP 432 — лимит исчерпан)
ENABLE_SEARXNG=true
SEARXNG_BASE_URL=http://searxng:8080
EXA_API_KEY=             # optional Exa free-tier key
ENABLE_EXA_SEARCH=false  # Exa как дополнительный search provider
ENABLE_EXA_CONTENTS_FALLBACK=false
EXA_SEARCH_TYPE=auto
EXA_MAX_AGE_HOURS=24
PROVIDER_METRICS_PATH=data/provider_effectiveness.jsonl
APIFY_API_KEY=           # пусто — Apify отключён
VK_ACCESS_TOKEN=...      # прямой VK API (работает)
VK_TARGETS=pogruzhenye.official

# Фильтры
MAX_ARTICLE_AGE_DAYS=7   # отсекать статьи старше 7 дней
ALERT_ON_SENTIMENT=all   # all | negative_only | negative_neutral
WATERMARK_OVERLAP_MINUTES=60
DYNAMIC_SOURCES=vk,yandex_maps

# Instagram (выключен)
ENABLE_INSTAGRAM=false
```

---

## 7. Docker-инфраструктура

Три сервиса в `docker/docker-compose.yml`:

| Сервис | Образ | Порты | Роль |
|---|---|---|---|
| `postgres` | postgres:16-alpine | 5433:5432 | БД |
| `searxng` | searxng/searxng:latest | внутренний | Поисковый движок |
| `agent` | локальная сборка | 8080 (healthcheck) | Сам бот |

**Миграции** применяются автоматически через PostgreSQL `docker-entrypoint-initdb.d` — только при первой инициализации тома `pgdata`. Если нужно применить новую миграцию к существующей БД — выполни вручную через `psql`.

**Данные БД** хранятся в Docker volume `pgdata` — не теряются при перезапуске контейнеров.

---

## 8. Команды

```bash
# Запуск всего стека
docker compose -f /root/parser-ai/docker/docker-compose.yml up -d

# Перезапуск только агента (после изменения кода)
docker cp /root/parser-ai/src/. docker-agent-1:/app/src/
docker restart docker-agent-1

# Логи в реальном времени
docker logs -f docker-agent-1

# Запустить pipeline немедленно (разово)
docker exec docker-agent-1 python -m src.main run

# Запустить тесты (из хоста, через venv)
cd /root/parser-ai
.venv/bin/python -m pytest tests/ -v

# Подключиться к БД
docker exec -it docker-agent-1 psql postgresql://brandmon:brandmon@postgres:5432/brandmon

# Остановить всё
docker compose -f /root/parser-ai/docker/docker-compose.yml down
```

---

## 9. Расписание

- **Pipeline мониторинга**: каждый час ровно в `:00` (cron `minute=0`) — 18:00, 19:00, 20:00 МСК и т.д.
- **Еженедельный дайджест**: каждый понедельник в 09:00 UTC
- **При старте демона**: pipeline запускается немедленно (`next_run_time=datetime.now()`)

---

## 10. Self-learning механизм

1. Бот присылает алерт в Telegram с кнопкой **«❌ Не касается нас»**
2. Оператор нажимает → бот спрашивает причину (5 быстрых кнопок или свободный текст)
3. LLM анализирует текст упоминания + объяснение оператора → формулирует **правило** (урок)
4. Урок сохраняется в `feedback_lessons`
5. При следующих запусках **system prompt** включает все накопленные уроки
6. URL помечается в `ignored_urls` — больше никогда не присылается

---

## 11. Известные проблемы (актуальный статус)

| # | Проблема | Статус |
|---|---|---|
| 1 | Tavily API лимит исчерпан (HTTP 432) | 🔴 Активно. Задачи Tavily выполняются через SearXNG. Нужно пополнить Tavily. |
| 2 | 0 новых упоминаний сохраняется | 🟡 Дата публикации большинства найденных URL старше 7 дней. Нормально для первых прогонов. |
| 3 | trafilatura ZSTD шум в логах | ✅ Исправлено. Добавлен `_TrafilaturaNoiseFilter`. |
| 4 | urllib3 connection pool overflow | ✅ Исправлено. Pool инициализирован с `maxsize=10`. |
| 5 | Apify не настроен | 🟡 `APIFY_API_KEY` пустой. Яндекс.Карты не парсятся. |

---

## 12. Тесты

```bash
.venv/bin/python -m pytest tests/ -v   # 171 тест, ~6 сек
```

Все тесты используют моки (без реальных HTTP-запросов и БД). Перед коммитом обязательно прогони тесты.

---

## 13. Что можно трогать / что нельзя

### Трогать безопасно:
- `src/tools/` — добавление новых источников данных
- `src/telegram.py` — формат алертов
- `src/digest.py` — формат дайджеста
- `.env` — настройки бренда, ключи API
- `tests/` — добавление тестов

### Осторожно:
- `src/agent.py` — ядро pipeline. Любое изменение ноды должно сопровождаться тестом в `test_agent_nodes.py`
- `src/db.py` — async сессии, уникальные индексы. Не менять хэш-логику без миграции.
- `migrations/` — новую миграцию добавлять с номером `011_...`. Существующие файлы не редактировать (они уже применены в production БД).

### Не трогать:
- `docker/docker-compose.yml` `volumes.pgdata` — данные production БД
- `src/models.py` `url_hash` поле — изменение сломает дедупликацию

---

## 14. Правило для AI-агентов: обновляй этот документ

> Это обязательное правило. Каждый агент, который вносит изменения в проект, должен обновить `agent.md` в конце сессии.

### Зачем

Этот файл — единственная передача контекста между сессиями. Каждый новый агент начинает с чистого состояния и не помнит что делал предыдущий. Если изменение не задокументировано здесь — оно потеряно для следующего агента.

### Что писать

После каждого изменения кода добавляй запись в раздел **15. История изменений** (ниже) в формате:

```
### YYYY-MM-DD — [краткий заголовок]
- **Что сделано**: ...
- **Затронутые файлы**: ...
- **Причина**: ...
- **Результат / тесты**: ...
- **Известные проблемы после правки**: ... (или "нет")
```

### Что обязательно обновлять помимо лога

| Что изменилось | Что обновить в agent.md |
|---|---|
| Добавлен новый файл/модуль | Раздел 3 (карта файлов) |
| Изменилась логика pipeline | Раздел 4 (архитектура) |
| Добавлена/изменена таблица БД | Раздел 5 (схема БД) + миграция |
| Новая переменная в .env | Раздел 6 (конфигурация) |
| Изменилось расписание | Раздел 9 (расписание) |
| Баг закрыт или найден | Раздел 11 (известные проблемы) |

### Правило коротко

1. Сделал изменение в коде
2. Прогнал тесты (`.venv/bin/python -m pytest tests/ -q`)
3. Задеплоил в контейнер (`docker cp ...`)
4. Обновил `agent.md` — добавил запись в раздел 15 и обновил нужные разделы
5. Готово

---

## 15. История изменений

### 2026-06-08 — Первичная диагностика, фикс багов 3 и 4, смена расписания, документация

- **Что сделано**:
  - Диагностирован статус проекта: все 3 Docker-контейнера работают (postgres, searxng, agent)
  - Прогнаны 171 тест — все прошли
  - **Баг 3 закрыт**: в `src/tools/page_extract.py` добавлен `_TrafilaturaNoiseFilter` — фильтрует шумные WARNING/ERROR логи trafilatura при ZSTD-декомпрессии (они не были ошибками — fallback на Jina Reader работал корректно, просто засоряли лог)
  - **Баг 4 закрыт**: в `src/tools/page_extract.py` добавлена функция `_init_trafilatura_pool(maxsize=10)` — инициализирует urllib3 PoolManager с 10 соединениями на хост вместо дефолтного 1; вызывается один раз при импорте модуля
  - **Расписание изменено**: в `src/main.py` триггер scheduler сменён с `"interval" minutes=60` на `"cron" minute=0` — теперь скан запускается ровно в 18:00, 19:00 и т.д. (МСК), а не через 60 минут от момента запуска
  - Создан `agent.md` (этот файл)
- **Затронутые файлы**:
  - `src/tools/page_extract.py` — фикс багов 3 и 4
  - `src/main.py` — смена расписания на cron
  - `agent.md` — создан с нуля
- **Причина**: диагностика по запросу оператора, проверка работоспособности бота
- **Результат / тесты**: 171/171 тестов прошли. Код задеплоен через `docker cp`. Контейнер перезапущен. APScheduler подтвердил новое расписание в логах: `trigger: cron[minute='0'], next run at: 2026-06-08 15:00:00 UTC`
- **Известные проблемы после правки**:
  - Tavily API (HTTP 432) — лимит бесплатного тиера исчерпан. Поиск идёт через SearXNG. Нужно пополнить аккаунт на tavily.com
  - Apify не настроен (`APIFY_API_KEY` пустой) — Яндекс.Карты не парсятся
  - 0 новых упоминаний сохраняется — большинство найденных URL старше `MAX_ARTICLE_AGE_DAYS=7`. Нормально для первых прогонов, нужно наблюдать динамику
