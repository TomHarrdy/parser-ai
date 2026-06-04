
"""Tests for src/settings.py"""
import pytest
from src.settings import Settings


class TestSettingsDefaults:
    def _s(self, **kw):
        """Create Settings ignoring the .env file."""
        return Settings(_env_file=None, DATABASE_URL="postgresql+asyncpg://u:p@h/db", **kw)

    def test_default_app_env(self):
        assert self._s().app_env == "development"

    def test_default_log_level(self):
        assert self._s().log_level == "INFO"

    def test_default_schedule_interval(self):
        assert self._s().schedule_interval_minutes == 60

    def test_default_llm_model(self):
        assert self._s().llm_model == "gpt-4o-mini"

    def test_empty_keys_by_default(self):
        s = self._s()
        assert s.llm_api_key == ""
        assert s.tavily_api_key == ""
        assert s.firecrawl_api_key == ""


class TestKeywordList:
    def _s(self, **kw):
        return Settings(_env_file=None, DATABASE_URL="postgresql+asyncpg://u:p@h/db", **kw)

    def test_single_keyword(self):
        assert self._s(KEYWORDS="TestBrand").keyword_list == ["TestBrand"]

    def test_multiple_keywords(self):
        assert self._s(KEYWORDS="a,b,c").keyword_list == ["a", "b", "c"]

    def test_keywords_with_spaces(self):
        assert self._s(KEYWORDS=" a , b ").keyword_list == ["a", "b"]

    def test_empty_keywords(self):
        assert self._s(KEYWORDS="").keyword_list == []

    def test_whitespace_only_keywords(self):
        assert self._s(KEYWORDS="   ").keyword_list == []


class TestSearchPhraseList:
    def _s(self, **kw):
        return Settings(_env_file=None, DATABASE_URL="postgresql+asyncpg://u:p@h/db", **kw)

    def test_single_phrase(self):
        assert self._s(SEARCH_PHRASES="test phrase").search_phrase_list == ["test phrase"]

    def test_multiple_phrases(self):
        assert len(self._s(SEARCH_PHRASES="p1,p2,p3").search_phrase_list) == 3

    def test_empty_phrases(self):
        assert self._s(SEARCH_PHRASES="").search_phrase_list == []


class TestLanguageList:
    def _s(self, **kw):
        return Settings(_env_file=None, DATABASE_URL="postgresql+asyncpg://u:p@h/db", **kw)

    def test_single_language(self):
        assert self._s(MONITORING_LANGUAGES="ru").language_list == ["ru"]

    def test_multiple_languages(self):
        assert self._s(MONITORING_LANGUAGES="ru,en").language_list == ["ru", "en"]

    def test_language_strips_spaces(self):
        assert self._s(MONITORING_LANGUAGES=" ru , en ").language_list == ["ru", "en"]
