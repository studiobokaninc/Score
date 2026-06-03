import pytest
from app.i18n import get_translator, t


class TestI18n:
    def test_ja_translation(self):
        trans = get_translator("ja")
        assert "dashboard.title" in trans
        assert "shot.list" in trans
        assert "routine.title" in trans
        assert "messages.title" in trans
        assert "goodbye.title" in trans
        assert "exit_report.title" in trans
        assert "nav.dashboard" in trans
        assert "nav.shot_detail" in trans
        assert trans["dashboard.title"] == "ダッシュボード"

    def test_en_translation(self):
        trans = get_translator("en")
        assert "dashboard.title" in trans
        assert "shot.list" in trans
        assert "routine.title" in trans
        assert "messages.title" in trans
        assert "goodbye.title" in trans
        assert "exit_report.title" in trans
        assert "nav.dashboard" in trans
        assert "nav.shot_detail" in trans
        assert trans["dashboard.title"] == "Dashboard"

    def test_translation_fallback(self):
        trans = get_translator("ja")
        result = t("nonexistent.key", trans)
        assert result == "nonexistent.key"
