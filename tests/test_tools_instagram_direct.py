"""Tests for src/tools/instagram_direct.py"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.tools.instagram_direct import _target_to_username, search_instagram_profiles


def test_target_to_username_handles_urls_and_handles():
    assert _target_to_username("https://www.instagram.com/pogruzhenye.official/") == "pogruzhenye.official"
    assert _target_to_username("@brand") == "brand"


@pytest.mark.asyncio
async def test_search_instagram_profiles_returns_posts_and_comments():
    comment = SimpleNamespace(
        text="Классный квест",
        created_at_utc=datetime(2026, 6, 8, 10, 0, tzinfo=timezone.utc),
        owner=SimpleNamespace(username="client"),
        id=123,
    )
    post = SimpleNamespace(
        shortcode="ABC123",
        date_utc=datetime(2026, 6, 8, 9, 0),
        caption="Пост про Погружение",
        get_comments=MagicMock(return_value=[comment]),
    )
    profile = SimpleNamespace(get_posts=MagicMock(return_value=iter([post])))
    fake_instaloader = SimpleNamespace(
        Instaloader=MagicMock(return_value=SimpleNamespace(context=object())),
        Profile=SimpleNamespace(from_username=MagicMock(return_value=profile)),
    )

    with patch.dict("sys.modules", {"instaloader": fake_instaloader}):
        results = await search_instagram_profiles(["pogruzhenye.official"])

    assert results[0]["source"] == "instagram"
    assert results[0]["url"] == "https://www.instagram.com/p/ABC123/"
    assert results[0]["published_date"].startswith("2026-06-08T09:00:00")
    assert results[1]["source"] == "instagram_comment"
    assert results[1]["text"] == "Классный квест"
