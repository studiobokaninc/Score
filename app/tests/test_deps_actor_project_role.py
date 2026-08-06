"""cmd_167 QC167A-1 (軍師検分・条件付きPASSの残条件): get_actor_project_role
(app/deps.py) の単体試験。既存の test_pages_director.py / test_shot_zero_regression.py
は get_actor_project_role 自体を monkeypatch で潰しており(戻り値を固定するのみ)、
関数本体のロジックを一切検証していなかった。本ファイルは関数を直接呼び出し、
系B解決ロジックそのものの検出力を持たせる。
"""
from unittest.mock import MagicMock

from app.deps import get_actor_project_role


def test_admin_fast_path_returns_admin_even_without_project_id(monkeypatch):
    """要件④(admin据え置き): 系A(get_actor_role)が"admin"を返す者は、
    project_id が未解決(None)であっても"admin"を返すこと。admin はスタジオ
    全体の据え置き権限であり案件に紐付かないため。"""
    monkeypatch.setattr("app.deps.get_actor_role", lambda actor_id: "admin")
    role = get_actor_project_role("99", None)
    assert role == "admin"


def test_no_matching_project_role_falls_back_to_user():
    """系Bでdirector/pm/leadいずれにも一致しない actor は "user" に倒れること
    (⑤fail-closed・昇格権限なしがデフォルト)。"""
    mock_client = MagicMock()
    mock_client.get_project_roles.return_value = {"director": 999, "pm": 888, "lead": 777}
    role = get_actor_project_role("42", 33, client=mock_client)
    assert role == "user"


def test_get_project_roles_exception_fails_closed_to_user():
    """系B取得自体が失敗(通信エラー等の例外)した場合も "user" に倒れること
    (⑤fail-closed・例外を握り潰して昇格させないことの確認)。"""
    mock_client = MagicMock()
    mock_client.get_project_roles.side_effect = Exception("network error")
    role = get_actor_project_role("42", 33, client=mock_client)
    assert role == "user"
