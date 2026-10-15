"""cmd_326c: タスク画面の task thread 釦(/messages?thread=数値)は /messages 以外では
notif-client.js が横取りして右ドロワーを開く。そのドロワーの入力欄にも @メンション候補を付ける。
静的な仕込みを確かめる(実画面は開発機の隔離環境で別途確認)。"""
from pathlib import Path

_APP = Path(__file__).resolve().parent.parent
_JS = (_APP / "static" / "notif-client.js").read_text(encoding="utf-8")
_BASE = (_APP / "templates" / "base.html").read_text(encoding="utf-8")


def test_drawer_input_has_mention_dropdown_and_handlers():
    assert 'id="score-drawer-mention-dropdown"' in _JS
    assert "inp.addEventListener('input', _drawerMentionInput)" in _JS
    # 候補は messages / shot_detail と同じ Calendar 全ユーザ API
    assert "fetch('/api/bff/users'" in _JS.split("_getDrawerMentionUsers")[1]


def test_drawer_enter_picks_candidate_before_sending():
    kd = _JS.split("inp.addEventListener('keydown'")[1].split("return wrap;")[0]
    assert kd.index("data-mention-idx") < kd.index("_drawerSendMessage()")


def test_drawer_candidate_name_color_inline_dark():
    # 暗色テーマの上書きで白地白文字にならぬよう文字色は inline の濃色
    seg = _JS.split("_drawerMentionInput(ev)")[1]
    assert "background-color:white" in seg and "color:#0f172a" in seg


def test_notif_client_url_versioned_to_bust_cache():
    assert "/static/notif-client.js?v=" in _BASE
