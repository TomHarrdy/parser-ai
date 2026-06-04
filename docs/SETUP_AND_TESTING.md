# Setup, Deploy & Testing Guide

## Что было сделано (лог сессии)

### 1. Добавлен Firecrawl API ключ
- Файл: `.env`
- Поле: `FIRECRAWL_API_KEY=fc-8ebd...`
- Логика Firecrawl уже была готова в `src/tools/firecrawl.py`
- Агент автоматически использует Firecrawl когда текст упоминания короче 200 символов — делает deep-read страницы и возвращает чистый Markdown

### 2. Исправлен баг с импортами
- `src/agent.py` импортировал `Mention`, `SourceType`, `Sentiment` из `src.db`
- На самом деле эти классы живут в `src.models`
- Фикс: разделили импорты на `from src.models import ...` и `from src.db import ...`

### 3. Docker запущен
- Порт 5432 на хосте был занят локальным PostgreSQL
- Решение: изменили маппинг на `5433:5432` в `docker/docker-compose.yml`
- Агент подключается к БД через внутреннюю Docker-сеть (`postgres:5432`) — хостовый порт ему не нужен

### 4. Проект залит на GitHub
- Репо: https://github.com/TomHarrdy/parser-ai (публичное)
- `.env` исключён из репо (есть в `.gitignore`)
- Созданы шаблоны `.env.example` и `config/.env.example` с заглушками
- Ключевые слова и токены удалены

---

## Как протестировать прямо сейчас

### Статус контейнеров

```bash
docker compose -f docker/docker-compose.yml ps
```

Должно показать:
```
docker-agent-1     Up    # агент работает
docker-postgres-1  Up (healthy)  # БД работает
```

### Просмотр логов в реальном времени

```bash
docker logs -f docker-agent-1
```

### Тест 1 — Разовый запуск пайплайна вручную

Запускает полный цикл один раз (сбор → дедупликация → анализ → уведомление):

```bash
docker exec docker-agent-1 python -m src.main run
```

Что ожидать в логах:
```
INFO  Tavily (brand): N results
INFO  Dedup: N raw → N new (skipped N)
INFO  Analyzed: https://... → positive/negative/neutral
INFO  Saved N mentions
```

### Тест 2 — Проверить что ключ Firecrawl работает

```bash
docker exec docker-agent-1 python -c "
import asyncio
from src.settings import get_settings
from src.tools.firecrawl import scrape_url

async def test():
    s = get_settings()
    result = await scrape_url(s.firecrawl_api_key, 'https://example.com')
    print('OK, длина контента:', len(result) if result else 0)

asyncio.run(test())
"
```

### Тест 3 — Проверить подключение к БД

```bash
docker exec docker-postgres-1 psql -U brandmon -d brandmon -c "SELECT COUNT(*) FROM mentions;"
```

Если таблица не создана — она создаётся автоматически при первом старте агента.

### Тест 4 — Дайджест вручную

```bash
docker exec docker-agent-1 python -m src.main digest
```

Отправит еженедельный аналитический отчёт в Telegram.

### Тест 5 — Посмотреть упоминания в БД

```bash
docker exec docker-postgres-1 psql -U brandmon -d brandmon -c \
  "SELECT sentiment, ai_summary, url, created_at FROM mentions ORDER BY created_at DESC LIMIT 10;"
```

---

## Что нужно для полноценной работы

| Переменная | Статус | Где взять |
|---|---|---|
| `FIRECRAWL_API_KEY` | Заполнен | https://firecrawl.dev |
| `TAVILY_API_KEY` | Заполнен | https://tavily.com |
| `TELEGRAM_BOT_TOKEN` | Заполнен | @BotFather в Telegram |
| `TELEGRAM_TARGET_CHAT_ID` | Заполнен | @userinfobot в Telegram |
| `LLM_API_KEY` | **Пусто** | https://platform.openai.com |
| `APIFY_API_KEY` | Пусто (опционально) | https://apify.com |
| `YANDEX_MAPS_ORG_ID` | Пусто (опционально) | ID из URL на maps.yandex.ru |

> Без `LLM_API_KEY` агент упадёт на шаге анализа. Нужен ключ OpenAI (или другого провайдера).

### Добавить LLM ключ

```bash
# Отредактировать .env
nano /root/parser-ai/.env
# Вставить: LLM_API_KEY=sk-...

# Перезапустить агента
docker compose -f docker/docker-compose.yml up -d --force-recreate agent
```

---

## Архитектура пайплайна

```
[APScheduler каждые 60 мин]
         │
         ▼
   [collect_mentions]
   ├── Tavily Search (веб + поисковые фразы)
   ├── Apify VK (если задан APIFY_API_KEY)
   └── Apify Yandex Maps (если задан YANDEX_MAPS_ORG_ID)
         │
         ▼
   [deduplicate]
   └── Проверка url_hash / text_hash в PostgreSQL
         │
         ▼
   [analyze_mentions]
   ├── Если текст < 200 символов → Firecrawl deep-read
   └── LLM (GPT-4o-mini) → {sentiment, reason, summary}
         │
         ▼
   [save_and_notify]
   ├── Сохранить в PostgreSQL
   └── Telegram алерт (для всех новых упоминаний)
```

---

## Структура проекта

```
parser-ai/
├── src/
│   ├── main.py          # Точка входа (run / daemon / digest)
│   ├── agent.py         # LangGraph пайплайн
│   ├── models.py        # SQLAlchemy модели (Mention, SourceType, Sentiment)
│   ├── db.py            # Работа с БД (init, session, dedup-проверки)
│   ├── settings.py      # Pydantic Settings (читает .env)
│   ├── telegram.py      # Отправка уведомлений
│   ├── digest.py        # Еженедельный дайджест
│   ├── logging_config.py
│   └── tools/
│       ├── __init__.py  # Высокоуровневые функции поиска
│       ├── tavily.py    # Tavily Search API
│       ├── firecrawl.py # Firecrawl (deep-read URL → Markdown)
│       └── apify.py     # Apify (VK, Yandex Maps)
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml  # postgres:5433 (хост), агент
├── migrations/
│   └── 001_mentions.sql
├── docs/
│   ├── TZ.md                    # Техническое задание
│   └── SETUP_AND_TESTING.md     # Этот файл
├── config/
│   └── .env.example
├── .env.example
├── .env                # НЕ в git! Реальные ключи
├── .gitignore
├── requirements.txt
└── README.md
```
