# TODO — Доработки проекта parser-ai

## ✅ Что сделано

- [x] Добавлена LLM-проверка релевантности (`is_relevant: true/false`) в `analyze_with_llm`
- [x] Нерелевантные упоминания пропускаются в `analyze_mentions` (логируются как `Skipped`)
- [x] Промпт (`ANALYZE_PROMPT`) обогащён описанием компании и явной подсказкой про многозначность слова «Погружение»
- [x] `search_monitoring_phrases` теперь принимает `brand=` и добавляет название компании к каждому поисковому запросу
- [x] Добавлено поле `COMPANY_DESCRIPTION` в `Settings`
- [x] Обновлён `.env`: улучшены `KEYWORDS`, добавлены новые `SEARCH_PHRASES`, заполнен `COMPANY_DESCRIPTION`
- [x] Все 113 тестов проходят
- [x] **[БАГ] Исправлен Markdown в Telegram**: `**bold**` → `*bold*` (aiogram ParseMode.MARKDOWN = MarkdownV1)
- [x] **[PERF] Параллельный сбор данных** в `collect_mentions` через `asyncio.gather()`
- [x] **[PERF] Параллельный LLM-анализ** в `analyze_mentions` через `asyncio.gather()` + семафор
- [x] **[FEAT] Фильтрация алертов по тональности**: настройка `ALERT_ON_SENTIMENT` (all / negative_only / negative_neutral)
- [x] **[FEAT] Параметр `TAVILY_SEARCH_DAYS`** в Settings — чтобы глубина поиска Tavily соответствовала `MAX_ARTICLE_AGE_DAYS`
- [x] **[FEAT] High-water mark (watermark)**: таблица `run_checkpoints`, watermark per source, буфер перекрытия `WATERMARK_OVERLAP_MINUTES=60`
- [x] **[FEAT] Content Hash Change Detection**: таблица `content_snapshots`, динамические источники (VK, Яндекс.Карты) отслеживают появление новых комментариев на старых страницах; алерт показывает "💬 Обновление: новые комментарии"
- [x] **[FEAT] Event Type классификация**: LLM определяет тип контента (article/review/comment/post/forum/mention), сохраняется в БД, отображается в алерте
- [x] **[FEAT] ai_reason сохраняется в БД** и отображается в Telegram как "⚡ Причина: ..."
- [x] **[FEAT] Полностью переработан формат Telegram-алерта**: event emoji + тип, тональность с label, причина, мета-строка, читаемое название источника
- [x] **[FEAT] Кнопка "❌ Не касается нас"**: inline keyboard на каждом алерте; нажатие → `is_ignored=True` в БД + URL в блок-лист `ignored_urls`; сообщение обновляется, кнопка исчезает
- [x] **[FEAT] `bot_handler.py`**: callback router на aiogram v3, polling запускается параллельно со scheduler через `asyncio.gather()`
- [x] **[FEAT] Graceful shutdown**: SIGTERM/SIGINT через `signal.add_signal_handler` в daemon
- [x] **[FEAT] Self-learning agent**: таблица `feedback_lessons`; при нажатии кнопки LLM анализирует почему ошибся и сохраняет правило
- [x] **[FEAT] Dynamic system prompt**: `build_system_prompt()` включает профиль компании + накопленные уроки из `feedback_lessons`; уроки инжектируются в каждый LLM-вызов
- [x] **[FEAT] Разделение system/user prompt**: system prompt (стабильный) — для prompt caching у провайдера; user prompt — только текст для анализа

- [x] **[БАГ] bot_handler.py отсутствовал в Docker-образе**: образ собирался до появления файла — polling не запускался, ошибка молча глоталась через `return_exceptions=True`. Пересобрали образ.
- [x] **[FEAT] Кликабельное название источника в алерте**: вместо `📡 Веб` теперь `📡 [Ведомости](url)` — реальное название сайта, извлечённое из URL; словарь `DOMAIN_LABELS` на 40+ доменов (СМИ, соцсети, карты, квест-агрегаторы); для незнакомых доменов — капитализированный SLD (habr.com → Habr)
- [x] **[FEAT] Время публикации в алерте**: было `📅 05.06.2026`, стало `📅 05.06.2026 17:30` (если время известно); при отсутствии даты пишет `📅 дата не указана`
- [x] **[FEAT] Разделение "дата публикации" vs "дата добавления"**: `🕐 добавлено 05.06.2026 14:06` — теперь понятно что это дата когда бот нашёл материал
- [x] **[FEAT] Удалена лишняя строка `🔗 Открыть источник`**: ссылка перенесена в название источника — алерт стал компактнее
- [x] **[FEAT] Извлечение даты из Firecrawl метаданных**: `scrape_url_with_meta()` запрашивает Open Graph / schema.org теги страницы; дата записывается в `published_at` если Tavily её не вернул — уменьшает количество упоминаний без даты
- [x] **[FEAT] Human feedback dialog**: после нажатия "❌ Не касается нас" бот задаёт вопрос "Почему нерелевантно?" с 5 быстрыми кнопками (Другой город / Другая компания / Слово не в контексте / Не по теме / Конкурент) + возможность ответить свободным текстом через reply; LLM получает объяснение оператора → более точный урок; таймаут 10 мин — fallback на автоанализ без объяснения; поле `user_explanation` добавлено в `feedback_lessons` (миграция 008)

---

## 🔴 Критические (баги)

- [ ] Добавить retry-логику для Tavily/Firecrawl/Apify при 429/5xx ошибках (сейчас падает сразу)
- [x] В `send_alerts_bulk` добавлен `asyncio.sleep(0.05)` между отправками (rate-limit Telegram ≤30 msg/s)

---

## 🟡 Важные улучшения

- [ ] **Источник: 2ГИС** — добавить Apify actor `drobnikj/2gis-reviews-scraper` (упомянут в ТЗ, не реализован)
- [ ] **Источник: Telegram-каналы** — `SourceType.telegram` есть в модели, но tool отсутствует; добавить Apify actor для парсинга Telegram
- [ ] **Умное разбиение дайджеста**: текущий split по 4000 символов режет Markdown в середине блока; разбивать по `\n\n` (абзацы)
- [ ] **Алерт для позитива**: добавить отдельный emoji/формат для позитивных упоминаний (сейчас 🟢 есть, но логика та же что и для негатива — можно добавить краткий комментарий)
- [ ] **Health-check endpoint**: для Docker добавить простой HTTP `/health` на aiohttp/FastAPI (сейчас HEALTHCHECK в docker-compose падает через 30s)

---

## 🟢 Средний приоритет

- [ ] **Кэш `get_settings()` и тесты**: `@lru_cache` не сбрасывается между тестами — в conftest добавить `get_settings.cache_clear()`
- [ ] **Настраиваемые Actor ID для Apify**: `apify/vk-search-scraper` захардкожен; вынести в `Settings` (`APIFY_VK_ACTOR_ID`, `APIFY_YANDEX_MAPS_ACTOR_ID`)
- [ ] **Логирование без `source_published_at`**: добавить `logger.warning` для упоминаний, у которых дата публикации отсутствует (сейчас тихо пропускается)
- [ ] **Метрики pipeline**: логировать время выполнения каждой стадии (collect/dedup/analyze/save) для мониторинга производительности
- [ ] **Graceful shutdown в daemon**: `asyncio.Event().wait()` не обрабатывает SIGTERM корректно в Docker — добавить `signal.add_signal_handler`

---

## 🔵 Низкий приоритет / Nice-to-have

- [ ] **Web UI / Dashboard**: простая страница на FastAPI + Jinja2 для просмотра упоминаний из БД
- [ ] **Экспорт в CSV/Excel**: команда `python -m src.main export --days 30`
- [ ] **Поддержка нескольких компаний**: сейчас одна компания на инстанс; добавить мультитенантность
- [ ] **Slack/Discord уведомления** как альтернатива Telegram
- [ ] **Тесты для agent nodes**: `test_agent_nodes.py` существует, но судя по структуре — покрытие неполное; добавить integration test с mock LLM

---

## 🔧 Операционные задачи

- [ ] Вставить `LLM_API_KEY` в `.env` (ключ OpenAI → https://platform.openai.com/api-keys)
- [ ] Запустить бота и проверить на реальных данных
- [ ] Проверить логи: смотреть строки `Skipped (not relevant)` — убедиться что фильтр работает корректно
- [ ] При необходимости — поднять модель с `gpt-4o-mini` до `gpt-4o` если качество фильтрации недостаточно
- [ ] **[БАГ] Применить DB-миграцию для колонки `ai_reason`**: в БД отсутствует колонка `ai_reason` в таблице `mentions` — контейнер упал с `UndefinedColumnError`. Выполнить: `ALTER TABLE mentions ADD COLUMN IF NOT EXISTS ai_reason TEXT;`

---

## Команды для запуска

```bash
# Запустить
docker compose -f /root/parser-ai/docker/docker-compose.yml up -d

# Смотреть логи
docker compose -f /root/parser-ai/docker/docker-compose.yml logs -f

# Остановить
docker compose -f /root/parser-ai/docker/docker-compose.yml down
```
