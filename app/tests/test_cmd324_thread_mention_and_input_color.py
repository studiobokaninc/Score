"""cmd_324: ①淡色テーマで白文字の入力欄が読めぬ件 ②タスク詳細の thread 投稿欄で @mention。
いずれもテンプレート/CSS の静的な仕込みを確かめる(実画面は開発機で別途確認)。"""
from pathlib import Path

_APP = Path(__file__).resolve().parent.parent
_THEME = (_APP / "static" / "theme.css").read_text(encoding="utf-8")
_SHOT = (_APP / "templates" / "shot_detail.html").read_text(encoding="utf-8")
_MSG = (_APP / "templates" / "messages.html").read_text(encoding="utf-8")


def test_light_theme_white_text_inputs_get_dark_text():
    assert 'html:not(.dark) textarea[class*="text-white"]' in _THEME
    assert 'html:not(.dark) select[class*="text-white"] option' in _THEME
    assert "#0f172a" in _THEME.split("cmd_324")[1]


def test_task_detail_thread_fields_reuse_existing_mention_handlers():
    # 新規の別作りではなく既存 handleMentionInput / handleMentionKeydown を掛ける
    assert "_attachThreadMention(document.getElementById('asset-name-memo')" in _SHOT
    assert "#reviewRequestModal textarea[name=\"message\"]" in _SHOT
    assert "#distributeModal textarea[name=\"message\"]" in _SHOT
    assert _SHOT.count("window.handleMentionInput = ") == 1
    assert "window.handleMentionInput(ev, ddId)" in _SHOT


def test_mention_dropdown_name_not_overridden_by_dark_theme_text_class():
    # 地は inline 白。暗色の text-slate-7xx → 明色 !important 上書きを受ける class を使わぬ
    assert 'class="font-bold text-slate-700"' not in _SHOT
    assert 'font-bold text-slate-700 block' not in _MSG
