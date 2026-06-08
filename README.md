# Brand Mentions AI Agent

AI-агент для мониторинга упоминаний бренда в интернете.

**Архитектура:** Agentic Workflow — LLM как ядро принятия решений + инструменты поиска и free-first извлечения веб-страниц.

## Как работает

1. **Сбор:** агент параллельно опрашивает Tavily Search (веб), VK API и источники отзывов
2. **Проверка свежести:** deep-read URL через `trafilatura` → Jina Reader → optional Firecrawl
3. **Дедупликация:** проверка URL/текста по хэшу в PostgreSQL
4. **Анализ:** LLM определяет тональность и делает саммари каждого нового упоминания
5. **Уведомление:** алерт в Telegram (особенно для негатива)
6. **Дайджест:** раз в неделю — аналитический отчёт за 7 дней

## Быстрый старт

### 1. Ключи API

Получи API-ключи:
- [Tavily](https://tavily.com) — поиск по вебу
- SearXNG — бесплатный self-host fallback для поиска по вебу
- [Jina Reader](https://jina.ai/reader/) — бесплатный fallback для глубокого чтения страниц
- [Firecrawl](https://firecrawl.dev) — optional paid fallback для сложных страниц
- [Apify](https://apify.com) — optional источник для карт/отзывов, если прямого API нет
- [OpenAI](https://platform.openai.com) — LLM для анализа
- [BotFather](https://t.me/botfather) — Telegram-бот

### 2. Конфигурация

```bash
cp config/.env.example .env
# Заполни .env своими ключами
```

### 3. Запуск через Docker

```bash
docker compose -f docker/docker-compose.yml up -d
```

### 4. Запуск без Docker

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Разовый запуск
python -m src.main run

# Демон (каждые N минут + дайджест по понедельникам)
python -m src.main daemon

# Только дайджест
python -m src.main digest
```

## Переменные окружения

| Переменная | Описание |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Токен Telegram-бота |
| `TELEGRAM_TARGET_CHAT_ID` | ID чата для уведомлений |
| `COMPANY_NAME` | Название компании |
| `KEYWORDS` | Ключевые слова через запятую |
| `DATABASE_URL` | URL PostgreSQL |
| `LLM_API_KEY` | API-ключ LLM |
| `LLM_MODEL` | Модель (по умолчанию `gpt-4o-mini`) |
| `LLM_ENDPOINT` | Endpoint (для кастомных провайдеров) |
| `TAVILY_API_KEY` | API-ключ Tavily |
| `ENABLE_SEARXNG` | Включить self-host SearXNG поиск |
| `SEARXNG_BASE_URL` | URL SearXNG внутри Docker (`http://searxng:8080`) |
| `JINA_READER_BASE_URL` | URL Jina Reader (`https://r.jina.ai` или self-host) |
| `ENABLE_FIRECRAWL_FALLBACK` | Включить платный Firecrawl fallback (`false` по умолчанию) |
| `FIRECRAWL_API_KEY` | Optional API-ключ Firecrawl для fallback |
| `APIFY_API_KEY` | API-ключ Apify |
| `VK_TARGETS` | VK handles/URL страниц, посты которых нужно мониторить |
| `ENABLE_INSTAGRAM` | Включить Instagram parser на Instaloader |
| `INSTAGRAM_TARGETS` | Instagram профили/URL через запятую |
| `INSTAGRAM_SESSION_FILE` | Optional Instaloader session file |
| `YANDEX_MAPS_ORG_ID` | ID организации на Яндекс.Картах (число из URL) |
| `SEARCH_PHRASES` | Фразы для независимого поиска (через запятую) |
| `MONITORING_LOCATION` | Город/регион для фильтрации поиска |
| `SCHEDULE_INTERVAL_MINUTES` | Интервал мониторинга (по умолчанию 60) |

## Стек

- Python 3.12 + LangGraph
- PostgreSQL + SQLAlchemy (async)
- OpenAI API (GPT-4o-mini)
- Tavily Search API
- trafilatura + Jina Reader для deep-read URL
- Firecrawl API как optional fallback
- Apify API как optional источник
- aiogram (Telegram)
- Docker
