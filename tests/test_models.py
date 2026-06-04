
"""Tests for src/models.py"""
import uuid
import pytest
from src.models import Mention, Sentiment, SourceType

class TestSourceTypeEnum:
    def test_all_values_present(self):
        expected = {"web", "news", "vk", "yandex_maps", "telegram", "other"}
        assert {e.value for e in SourceType} == expected

    def test_is_string_enum(self):
        assert isinstance(SourceType.web, str)
        assert SourceType.web == "web"

    def test_vk_value(self):
        assert SourceType.vk == "vk"

    def test_yandex_maps_value(self):
        assert SourceType.yandex_maps == "yandex_maps"

class TestSentimentEnum:
    def test_all_values_present(self):
        expected = {"positive", "negative", "neutral"}
        assert {e.value for e in Sentiment} == expected

    def test_is_string_enum(self):
        assert isinstance(Sentiment.positive, str)

    def test_negative_value(self):
        assert Sentiment.negative == "negative"

class TestMentionModel:
    def test_mention_repr(self, sample_mention):
        r = repr(sample_mention)
        assert "Mention" in r
        assert str(sample_mention.id) in r

    def test_mention_has_required_fields(self, sample_mention):
        assert sample_mention.id is not None
        assert sample_mention.url_hash is not None
        assert sample_mention.raw_text is not None

    def test_sentiment_is_enum(self, sample_mention):
        assert isinstance(sample_mention.sentiment, Sentiment)

    def test_source_type_is_enum(self, sample_mention):
        assert isinstance(sample_mention.source_type, SourceType)

    def test_is_alert_sent_default(self, sample_mention):
        assert sample_mention.is_alert_sent is False

    def test_url_can_be_none(self, neutral_mention):
        assert neutral_mention.url is None

    def test_table_name(self):
        assert Mention.__tablename__ == "mentions"

    def test_id_is_uuid(self, sample_mention):
        assert isinstance(sample_mention.id, uuid.UUID)

    def test_positive_sentiment(self, sample_mention):
        assert sample_mention.sentiment == Sentiment.positive

    def test_negative_sentiment(self, negative_mention):
        assert negative_mention.sentiment == Sentiment.negative

    def test_neutral_sentiment(self, neutral_mention):
        assert neutral_mention.sentiment == Sentiment.neutral
