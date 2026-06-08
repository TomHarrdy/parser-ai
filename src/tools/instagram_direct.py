"""Instagram profile parser based on Instaloader.

This is an unofficial low-frequency adapter. It is intended for configured
public/owned profiles, not broad Instagram crawling.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import List, Optional


def _target_to_username(target: str) -> str:
    target = target.strip().rstrip("/")
    if not target:
        return ""
    if "instagram.com/" in target:
        target = target.split("instagram.com/", 1)[1]
    return target.strip("/").split("/", 1)[0].lstrip("@")


def _to_utc(value) -> Optional[datetime]:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _search_instagram_profiles_sync(
    targets: List[str],
    max_posts: int,
    max_comments: int,
    session_username: str = "",
    session_file: str = "",
) -> List[dict]:
    import instaloader

    loader = instaloader.Instaloader(
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
        quiet=True,
    )
    if session_username and session_file:
        loader.load_session_from_file(session_username, filename=session_file)

    items: List[dict] = []
    for target in targets:
        username = _target_to_username(target)
        if not username:
            continue
        profile = instaloader.Profile.from_username(loader.context, username)
        for index, post in enumerate(profile.get_posts()):
            if index >= max_posts:
                break
            post_url = f"https://www.instagram.com/p/{post.shortcode}/"
            posted_at = _to_utc(getattr(post, "date_utc", None))
            caption = getattr(post, "caption", None) or ""
            items.append({
                "url": post_url,
                "text": caption,
                "published_date": posted_at.isoformat() if posted_at else None,
                "source": "instagram",
                "username": username,
            })

            comments_seen = 0
            try:
                comments = post.get_comments()
            except Exception:
                comments = []
            for comment in comments:
                if comments_seen >= max_comments:
                    break
                text = getattr(comment, "text", "") or ""
                if not text.strip():
                    continue
                created_at = _to_utc(
                    getattr(comment, "created_at_utc", None)
                    or getattr(comment, "created_at", None)
                )
                owner = getattr(getattr(comment, "owner", None), "username", "")
                comment_id = getattr(comment, "id", None)
                items.append({
                    "url": f"{post_url}?comment_id={comment_id}" if comment_id else post_url,
                    "text": text,
                    "published_date": created_at.isoformat() if created_at else None,
                    "source": "instagram_comment",
                    "username": username,
                    "comment_owner": owner,
                })
                comments_seen += 1
    return items


async def search_instagram_profiles(
    targets: List[str],
    max_posts: int = 5,
    max_comments: int = 20,
    session_username: str = "",
    session_file: str = "",
) -> List[dict]:
    if not targets:
        return []
    return await asyncio.to_thread(
        _search_instagram_profiles_sync,
        targets,
        max_posts,
        max_comments,
        session_username,
        session_file,
    )
