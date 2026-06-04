# Brand Mentions AI Agent

AI-агент для мониторинга упоминаний бренда в интернете.

**Архитектура:** Agentic Workflow — LLM как ядро принятия решений + MCP-инструменты для доступа к вебу.

## Как работает

1. **Сбор:** агент параллельно опрашивает Tavily Search (веб) и Apify (ВКонтакте)
2. **Дедупликация:** проверка URL/текста по хэшу в PostgreSQL
3. **Анализ:** LLM определяет тональность и делает саммари каждого нового упоминания
4. **Уведомление:** алерт в Telegram (особенно для негатива)
5. **Дайджест:** раз в неделю — аналитический отчёт за 7 дней

## Быстрый старт

### 1. Ключи API

Получи API-ключи:
- [Tavily](https://tavily.com) — поиск по вебу
- [Firecrawl](https://firecrawl.dev) — глубокий парсинг страниц
- [Apify](https://apify.com) — парсинг соцсетей и карт
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
| `FIRECRAWL_API_KEY` | API-ключ Firecrawl |
| `APIFY_API_KEY` | API-ключ Apify |
| `YANDEX_MAPS_ORG_ID` | ID организации на Яндекс.Картах (число из URL) |
| `SEARCH_PHRASES` | Фразы для независимого поиска (через запятую) |
| `MONITORING_LOCATION` | Город/регион для фильтрации поиска |
| `SCHEDULE_INTERVAL_MINUTES` | Интервал мониторинга (по умолчанию 60) |

## Стек

- Python 3.12 + LangGraph
- PostgreSQL + SQLAlchemy (async)
- OpenAI API (GPT-4o-mini)
- Tavily Search API
- Firecrawl API
- Apify API
- aiogram (Telegram)
- Docker
