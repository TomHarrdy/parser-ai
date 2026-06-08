"""
Прямой парсер ВКонтакте через официальный VK API.
Без Apify, без лишних затрат — только access_token и httpx.

Методы:
  - search_vk_direct(query, max_posts) — поиск по ключевым словам
  - get_vk_wall(targets, max_posts) — посты из конкретных пабликов
  - get_vk_comments(owner_id, post_id, max_comments) — комментарии к посту

Формат возврата — совместим с Apify-акторами, чтобы agent.py не переписывать.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

from src.tools.retry import with_retry

logger = logging.getLogger(__name__)

VK_API_BASE = "https://api.vk.com/method"
VK_API_VERSION = "5.199"
# Стандартный лимит VK API — 3 запроса/сек. Держим 0.35 для надёжности.
REQUEST_INTERVAL = 0.35


def _make_url(method: str) -> str:
    return f"{VK_API_BASE}/{method}"


class VKDirectClient:
    """HTTPX-based клиент VK API с throttling."""

    def __init__(self, access_token: str):
        if not access_token:
            raise ValueError("VK_ACCESS_TOKEN is empty")
        self._token = access_token
        self._last_request: float = 0.0

    def _throttle(self):
        """Соблюдаем rate-limit VK API: 3 запроса/сек."""
        now = time.monotonic()
        since_last = now - self._last_request
        if since_last < REQUEST_INTERVAL:
            time.sleep(REQUEST_INTERVAL - since_last)
        self._last_request = time.monotonic()

    def _call(self, method: str, params: dict) -> dict:
        """Синхронный вызов VK API (агент работает асинхронно, но вызов синхронный через run_in_executor)."""
        self._throttle()
        params.update({
            "access_token": self._token,
            "v": VK_API_VERSION,
        })
        resp = httpx.get(_make_url(method), params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        if "error" in data:
            err = data["error"]
            code = err.get("error_code", 0)
            msg = err.get("error_msg", "unknown")
            # 6 = слишком много запросов (rate limit), 1 = неизвестная ошибка
            if code in (6, 1):
                raise httpx.HTTPStatusError(
                    f"VK API rate limit (code {code}): {msg}",
                    request=resp.request,
                    response=resp,
                )
            logger.warning("VK API error [%d]: %s — params=%s", code, msg, params)
            return {"response": {"items": []}}

        return data

    def search_posts(self, query: str, max_posts: int = 10) -> list[dict]:
        """Поиск постов по ключевым словам через newsfeed.search."""
        data = self._call("newsfeed.search", {
            "q": query,
            "count": min(max_posts, 200),
            "extended": 1,
        })
        items = data.get("response", {}).get("items", [])
        return self._normalize_items(items)

    def get_wall_posts(self, targets: list[str], max_posts_per_target: int = 10) -> list[dict]:
        """Получить посты из конкретных пабликов через wall.get (если доступен) или newsfeed.search.
        targets — список domain (например ['durov', 'pogruzhenye.official'])"""
        results: list[dict] = []
        for target in targets:
            domain = self._parse_domain(target)
            # Пробуем wall.get — работает только с открытыми пабликами для сервисного ключа
            data = self._call("wall.get", {
                "domain": domain,
                "count": min(max_posts_per_target, 100),
                "extended": 1,
            })
            items = data.get("response", {}).get("items", [])
            if items:
                results.extend(self._normalize_items(items))
            else:
                # fallback: поищем посты из этого паблика через newsfeed.search
                logger.info("wall.get вернул пусто, пробуем newsfeed.search для %s", domain)
                data2 = self._call("newsfeed.search", {
                    "q": f"https://vk.com/{domain}",
                    "count": min(max_posts_per_target, 200),
                    "extended": 1,
                })
                items2 = data2.get("response", {}).get("items", [])
                results.extend(self._normalize_items(items2))
        return results

    def get_comments(self, owner_id: int, post_id: int, max_comments: int = 20) -> list[dict]:
        """Получить комментарии к посту через wall.getComments."""
        data = self._call("wall.getComments", {
            "owner_id": owner_id,
            "post_id": post_id,
            "count": min(max_comments, 100),
            "extended": 1,
        })
        items = data.get("response", {}).get("items", [])
        return self._normalize_comments(items, owner_id=owner_id, post_id=post_id)

    def search_posts_with_comments(self, query: str, max_posts: int = 5) -> list[dict]:
        """Поиск постов + сбор комментариев к каждому."""
        posts = self.search_posts(query, max_posts=max_posts)
        result = []
        for p in posts:
            owner_id = p.get("owner_id", 0)
            post_id = p.get("post_id", 0)
            if owner_id and post_id:
                try:
                    comments = self.get_comments(owner_id, post_id, max_comments=10)
                    p["vk_comments"] = comments
                except Exception as e:
                    logger.debug("Не удалось получить комменты к %s_%s: %s", owner_id, post_id, e)
            result.append(p)
        return result

    # ── Normalizers ────────────────────────────────────

    def _normalize_items(self, items: list[dict]) -> list[dict]:
        """Привести VK API-ответ к формату Apify для agent.py.

        Apify возвращает поля:
          url, text/postText, published_date/postedAt/date

        VK API возвращает:
          id, owner_id, text, date (unixtime), ..."""
        result = []
        for item in items:
            if not isinstance(item, dict):
                continue
            owner_id = item.get("owner_id", 0)
            post_id = item.get("id", 0)
            text = item.get("text", "") or ""
            ts = item.get("date", 0)
            pub_date = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else ""

            # Формируем URL поста
            if owner_id < 0:
                url = f"https://vk.com/club{abs(owner_id)}?w=wall{owner_id}_{post_id}"
            else:
                url = f"https://vk.com/id{owner_id}?w=wall{owner_id}_{post_id}"

            result.append({
                "url": url,
                "text": text,
                "published_date": pub_date,
                # Apify-совместимые алиасы
                "postUrl": url,
                "postText": text,
                "postedAt": pub_date,
                "date": str(ts),
                "owner_id": owner_id,
                "post_id": post_id,
            })
        return result

    def _normalize_comments(
        self,
        items: list[dict],
        owner_id: int = 0,
        post_id: int = 0,
    ) -> list[dict]:
        """Привести комментарии к формату, похожему на посты.

        owner_id / post_id — контекст родительского поста нужен для
        формирования корректного URL комментария.
        """
        result = []
        for item in items:
            if not isinstance(item, dict):
                continue
            text = item.get("text", "") or ""
            ts = item.get("date", 0)
            pub_date = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else ""
            cid = item.get("id", 0)

            # Правильный URL: wall{owner_id}_{post_id}?reply={comment_id}
            # owner_id — ID страницы/паблика (отрицательный для групп)
            # from_id (автор коммента) здесь НЕ используется — это была ошибка
            if owner_id and post_id and cid:
                url = f"https://vk.com/wall{owner_id}_{post_id}?reply={cid}"
            elif owner_id and post_id:
                url = f"https://vk.com/wall{owner_id}_{post_id}"
            else:
                url = ""

            result.append({
                "url": url,
                "text": text,
                "published_date": pub_date,
                "date": str(ts),
                "source": "vk_comment",
            })
        return result

    @staticmethod
    def _parse_domain(target: str) -> str:
        """Извлечь domain из VK-ссылки или хендла.
        Примеры: 'pogruzhenye.official' → 'pogruzhenye.official'
                 'https://vk.com/durov' → 'durov'
                 'https://vk.com/club123456' → 'club123456'"""
        target = target.strip().rstrip("/")
        if "vk.com/" in target:
            return target.split("vk.com/")[-1].split("?")[0].split("/")[0]
        return target


# ── Асинхронные врапперы для agent.py (поддержка async-интерфейса) ──

async def search_vk_direct(
    access_token: str,
    query: str,
    max_posts: int = 10,
    targets: Optional[list[str]] = None,
    with_comments: bool = False,
) -> list[dict]:
    """Асинхронный вход в VK парсер.

    Если targets заданы — парсит стены указанных пабликов.
    Если нет — поиск по ключевым словам.
    Если with_comments=True — также собирает комментарии к каждому посту.

    Возвращает список словарей в формате, совместимом с Apify.
    """
    import asyncio
    client = VKDirectClient(access_token)

    def _run():
        if targets:
            posts = client.get_wall_posts(targets, max_posts_per_target=max_posts)
        else:
            posts = client.search_posts(query, max_posts=max_posts)
        if with_comments:
            for p in posts:
                owner_id = p.get("owner_id", 0)
                post_id = p.get("post_id", 0)
                if owner_id and post_id:
                    try:
                        comments = client.get_comments(owner_id, post_id, max_comments=10)
                        p["vk_comments"] = comments
                    except Exception as e:
                        logger.debug("Комменты к %s_%s: %s", owner_id, post_id, e)
        return posts

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _run)
