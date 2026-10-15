"""cmd_326d: タスク詳細内でthreadへ載る残りの欄(ステータス手動修正コメント・Retake入力の全体方針/
マーカー毎コメント)にも共通部品 mention.js で @候補。静的な仕込みを確かめる(実画面は隔離環境で別途確認)。"""
from pathlib import Path

_APP = Path(__file__).resolve().parent.parent
_JS = (_APP / "static" / "mention.js").read_text(encoding="utf-8")
_SHOT = (_APP / "templates" / "shot_detail.html").read_text(encoding="utf-8")
_RT = (_APP / "templates" / "director_retake_input.html").read_text(encoding="utf-8")
_NOTIF = (_APP / "static" / "notif-client.js").read_text(encoding="utf-8")


def test_shared_component_uses_same_user_api_and_delegation():
    assert "fetch('/api/bff/users'" in _JS
    assert "addEventListener('input', onInput, true)" in _JS  # 委譲: 動的生成の欄にも効く
    assert "color:#0f172a !important" in _JS


def test_status_manual_comment_has_mention():
    assert "/static/mention.js" in _SHOT
    assert "ScoreMention.attach('#status-manual-comment')" in _SHOT


def test_retake_input_fields_have_mention():
    assert "/static/mention.js" in _RT
    assert "ScoreMention.attach('#retake-direction')" in _RT
    assert "ScoreMention.attach('#comment-cards textarea')" in _RT


def test_drawer_fix_c9a2abd_untouched():
    assert "score-drawer-mention-dropdown" in _NOTIF
    assert "_drawerMentionInput" in _NOTIF


def test_drawer_shared_banner_cmd326e():
    """cmd_326e: 右ドロワーにも共用thread注記(共用のときだけ)。threads_meta が shared_task_ids を返す。"""
    assert "score-drawer-shared-banner" in _NOTIF
    assert "shared_task_ids" in _NOTIF
    assert "sh.length > 1" in _NOTIF  # 共用でなければ隠す
    _BFF = (_APP / "routers" / "bff_write.py").read_text(encoding="utf-8")
    assert '"shared_task_ids"' in _BFF
