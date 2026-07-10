"""SHOT_000 (shot_id=0 sentinel) 専用回帰テストスイート — subtask_092a (軍師献策)

cmd_088 → cmd_090 → cmd_091(a/c) と4回連続で「shot_id=0 の truthy/falsy 誤判定」
(`if shot_id:` / `shot_id or 1` 等が 0 を『値なし』と誤認する) が横展開漏れとして
再発した。本ファイルはその同型バグの再発を横断的に検知するための回帰テスト群。

対象コードパス (cmd_088/090/091/091c 実修正箇所の一覧):
  - pages_project_detail.py: 「📋 (SHOT 紐付けなし)」バケットへの id=0 割当
    (SHOT_000 sentinel の発生源・cmd_088)
  - pages_shot.py: GET /shot/{id}・GET /task/{task_id} の shot_id=0 fallback
    (project_id 経由 get_tasks_by_project フォールバック・cmd_088)
  - pages_qc.py: GET /qc/{id} の shot_id=0 fallback + _can_judge 活性化 (cmd_091)
  - pages_director.py: GET /director_retake_input の shot_id=0 解決スキップ防止 +
    `shot_id or 1` によるデータ破壊 (SHOT_000が実shot#1に化ける) 防止 (cmd_091c)
  - bff_write.py: post_retakes / post_qc_approve_bff (SHOT-ZERO-APPROVE-400) /
    post_qc_notify_existing の shot_id=0 truthy 誤判定是正 (cmd_091c)
  - pages_dashboard.py: 受信 QC/Review 依頼の qc_url 抽出 (/qc/0... も正しく抽出)

既存テスト (test_pages_shot.py の test_shot_detail_unlinked_bucket_lists_shotless_tasks、
test_pages_qc.py の test_qc_viewer_shot_unlinked_task_fallback_via_task_id 等) で既に
個別に回帰確認されている箇所は重複させず、本ファイルは「SHOT_000 という一つの筋」を
横断的に一本のスイートとして束ねることに主眼を置く。
"""
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import jwt
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")

from app.deps import get_actor_id
from app.main import app

_SECRET = "test_secret_key_32bytes_minimum!"
_RESOLVED_ACTOR_ID = "42"


def _make_token(sub: str = "sato@studio.jp") -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=1)
    return jwt.encode({"sub": sub, "exp": exp}, _SECRET, algorithm="HS256")


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {_make_token()}"}


@pytest.fixture(autouse=True)
def patch_jwt_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", _SECRET)


@pytest.fixture()
def client():
    """app.main.app は全 router 結線済 (main.py 参照)。SHOT_000 は複数 router を
    横断するため、router 単位の分離アプリではなく統合 app を共有 client として使う。"""
    app.dependency_overrides[get_actor_id] = lambda: _RESOLVED_ACTOR_ID
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ─── ① SHOT_000 sentinel の発生源: pages_project_detail.py ──────────────────

class TestProjectDetailShotZeroOrigin:
    def test_shotless_bucket_assigned_shot_id_zero_not_none(self, client, monkeypatch):
        """cmd_088 回帰防止: shotID 未設定 task の集約バケット (「📋 (SHOT 紐付けなし)」)
        は id=None のままだと project_detail.html が `/shot/None` という遷移不能な
        リンクを生成してしまう。get_shot_detail 側の SHOT_000 (id=0) sentinel に
        合わせ、バケットにも id=0 が割り当てられ `/shot/0?project_id=...` へ正しく
        遷移できることを確認する (SHOT_000 sentinel 全体の発生源)。"""
        import app.adapters.calendar_client as cc

        monkeypatch.setattr("app.routers.pages_project_detail.resolve_project_name", lambda pid, uid, **kw: "Score検証")
        monkeypatch.setattr("app.routers.pages_project_detail.resolve_project_members", lambda *a, **kw: [])
        monkeypatch.setattr(cc.CalendarClient, "get_shots", lambda self, *a, **kw: [])
        monkeypatch.setattr(cc.CalendarClient, "get_my_tasks", lambda self, *a, **kw: [])
        monkeypatch.setattr(cc.CalendarClient, "get_my_project_detail", lambda self, *a, **kw: {})
        monkeypatch.setattr(cc.CalendarClient, "get_users", lambda self, *a, **kw: [])
        monkeypatch.setattr(
            cc.CalendarClient, "get_tasks_by_project",
            lambda self, *a, **kw: [
                {"id": 3282, "name": "Score修正3日分", "type": "other", "status": "wip",
                 "shotID": None, "seqID": "SEQ_PM", "assigned_to": None,
                 "status_color": None, "status_label": None, "status_category": None},
            ],
        )

        resp = client.get("/project_detail/80", headers=_auth_headers())

        assert resp.status_code == 200
        assert "/shot/0?project_id=80" in resp.text
        assert "/shot/None" not in resp.text


# ─── ② shot_detail 表示 (タスク一覧・ステータス算出): pages_shot.py ─────────

class TestShotDetailPageShotZero:
    def test_task_detail_isolated_shot_zero_breadcrumb_has_project_id(self, client, monkeypatch):
        """cmd_088 回帰防止: shot 紐付なし task を GET /task/{task_id} (isolated_task=True)
        で直接開いた場合、パンくずの SHOT_000 リンクは project_id クエリ付きで
        `/shot/0?project_id=...` へ遷移すること (project_id 無しだと /shot/0 単独では
        tasks が復元不可能で振り出しに戻ってしまう)。"""
        import app.adapters.calendar_client as cc

        monkeypatch.setattr("app.routers.pages_shot.resolve_project_name", lambda pid, uid, **kw: "Score検証")
        monkeypatch.setattr("app.routers.pages_shot.resolve_project_members", lambda *a, **kw: [])
        monkeypatch.setattr(cc.CalendarClient, "get_users", lambda self, *a, **kw: [])
        monkeypatch.setattr(cc.CalendarClient, "get_tasks_by_project", lambda self, *a, **kw: [])
        monkeypatch.setattr(cc.CalendarClient, "next_version", lambda self, *a, **kw: "v001")
        monkeypatch.setattr(cc.CalendarClient, "get_assets_by_task", lambda self, *a, **kw: [])

        def _fake_get_task(self, task_id, *a, **kw):
            return {
                "id": task_id, "shot_id": None, "shotID": None, "seqID": None,
                "project_id": 80, "type": "other", "name": "Score修正3日分",
                "status": "wip",
            }
        monkeypatch.setattr(cc.CalendarClient, "get_task", _fake_get_task)

        resp = client.get("/task/3282", headers=_auth_headers())

        assert resp.status_code == 200
        assert "/shot/0?project_id=80" in resp.text

    def test_task_detail_isolated_shot_zero_not_corrupted_to_shot_one(self, client, monkeypatch):
        """cmd_091c 回帰防止: shot_detail.html の Look 配布・レビュー依頼 POST body は
        旧実装 `{{ shot_id or 1 }}` により SHOT_000 (shot_id=0) が実 shot#1 のデータに
        すり替わる重大なデータ破壊バグがあった。isolated task view (SHOT_000) で
        `shot_id: 0` が正しく埋め込まれ、`shot_id: 1` への誤すり替えが再発していない
        ことを確認する。"""
        import app.adapters.calendar_client as cc

        monkeypatch.setattr("app.routers.pages_shot.resolve_project_name", lambda pid, uid, **kw: "Score検証")
        monkeypatch.setattr("app.routers.pages_shot.resolve_project_members", lambda *a, **kw: [])
        monkeypatch.setattr(cc.CalendarClient, "get_users", lambda self, *a, **kw: [])
        monkeypatch.setattr(cc.CalendarClient, "get_tasks_by_project", lambda self, *a, **kw: [])
        monkeypatch.setattr(cc.CalendarClient, "next_version", lambda self, *a, **kw: "v001")
        monkeypatch.setattr(cc.CalendarClient, "get_assets_by_task", lambda self, *a, **kw: [])

        def _fake_get_task(self, task_id, *a, **kw):
            return {
                "id": task_id, "shot_id": None, "shotID": None, "seqID": None,
                "project_id": 80, "type": "other", "name": "Score修正3日分",
                "status": "wip",
            }
        monkeypatch.setattr(cc.CalendarClient, "get_task", _fake_get_task)

        resp = client.get("/task/3282", headers=_auth_headers())

        assert resp.status_code == 200
        assert "shot_id: 0," in resp.text
        assert "shot_id: 1," not in resp.text


# ─── ③ QC ビューア表示 (Task 件数・SHOT.status) 耐性確認: pages_qc.py ───────

class TestQcViewerShotZeroResilience:
    def test_qc_viewer_bare_shot_zero_no_query_params_does_not_crash(self, client, monkeypatch):
        """task_id/project_id いずれも無い `/qc/0` 単独アクセス (陳腐化した bookmark 等)
        でも 500 にならず「0 件」の安全な空表示に収束すること (shot=None・
        project_id 解決不能時のガード回帰)。"""
        monkeypatch.setattr("app.routers.pages_qc.get_actor_role", lambda actor_id: "director")
        with patch("app.routers.pages_qc.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_shot.return_value = None
            mock_inst.get_tasks.return_value = []
            mock_inst.get_assets_by_task.return_value = []
            MockClient.return_value = mock_inst

            resp = client.get("/qc/0", headers=_auth_headers())

        assert resp.status_code == 200
        assert "0 件</strong>" in resp.text


# ─── ④ director_retake_input: pages_director.py ─────────────────────────────

class TestDirectorRetakeInputShotZero:
    def test_shot_zero_resolution_not_skipped(self, client, monkeypatch):
        """cmd_091c 回帰防止: 旧実装 `if shot_id:` は shot_id=0 を falsy として
        shot/asset 解決ブロック全体をスキップしていた。`if shot_id is not None:` へ
        の是正により、shot_id=0 でも client.get_shot(0, ...) が実際に呼ばれる
        (解決ロジックがスキップされない) ことを確認する。"""
        monkeypatch.setattr("app.routers.pages_director.get_actor_role", lambda actor_id: "director")
        with patch("app.routers.pages_director.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_me.return_value = None
            mock_inst.get_my_projects.return_value = []
            mock_inst.get_shots.return_value = []
            mock_inst.get_shot.return_value = None
            mock_inst.get_shot_detail.return_value = {}
            mock_inst.get_tasks.return_value = []
            MockClient.return_value = mock_inst

            resp = client.get("/director_retake_input?shot_id=0&task_id=3282", headers=_auth_headers())

        assert resp.status_code == 200
        mock_inst.get_shot.assert_called_once_with(0, actor_user_id=_RESOLVED_ACTOR_ID)

    def test_shot_zero_not_corrupted_to_shot_one(self, client, monkeypatch):
        """cmd_091c 回帰防止 (最重要): director_retake_input.html の Retake 送信
        FormData と「QC ビューアに戻る」リンクは旧実装 `{{ shot_id or 1 }}` により
        SHOT_000 (shot_id=0) が実 shot#1 に化ける重大なデータ破壊バグがあった。
        `{{ shot_id or 0 }}` 是正後、shot_id=0 が 1 にすり替わらず送信・遷移される
        ことを確認する。"""
        monkeypatch.setattr("app.routers.pages_director.get_actor_role", lambda actor_id: "director")
        with patch("app.routers.pages_director.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_me.return_value = None
            mock_inst.get_my_projects.return_value = []
            mock_inst.get_shots.return_value = []
            mock_inst.get_shot.return_value = None
            mock_inst.get_shot_detail.return_value = {}
            mock_inst.get_tasks.return_value = []
            MockClient.return_value = mock_inst

            resp = client.get("/director_retake_input?shot_id=0&task_id=3282", headers=_auth_headers())

        assert resp.status_code == 200
        assert "fd.append('shot_id', 0);" in resp.text
        assert "fd.append('shot_id', 1);" not in resp.text
        assert '/qc/0?task_id=3282' in resp.text

    def test_non_authorized_role_redirects_to_qc_zero_not_dashboard(self, client, monkeypatch):
        """cmd_091c 回帰防止: 権限なきロールの shot_id=0 アクセスは旧実装
        `if shot_id:` だと falsy 判定で `/dashboard` へ誤誘導されていた。
        `if shot_id is not None:` 是正後は `/qc/0...` へ正しく誘導されることを
        確認する (303 リダイレクト先を検証・qc_delegation は未委任前提)。"""
        monkeypatch.setattr("app.routers.pages_director.get_actor_role", lambda actor_id: "artist")
        monkeypatch.setattr("app.routers.pages_director.is_qc_delegated", lambda *a, **kw: False)

        resp = client.get(
            "/director_retake_input?shot_id=0&task_id=3282",
            headers=_auth_headers(),
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert resp.headers["location"] == "/qc/0?task_id=3282"


# ─── ⑤ Retake POST: bff_write.py post_retakes ───────────────────────────────

class TestBffWriteRetakesShotZero:
    def test_post_retakes_shot_zero_resolution_not_skipped(self, client, monkeypatch):
        """cmd_091c 回帰防止: post_retakes の階層解決は旧実装 `if shot_id:` により
        shot_id=0 (SHOT_000) 時は丸ごとスキップされていた。`if shot_id is not None:`
        是正後は shot_id=0 でも client.get_shot_detail(0, ...) が実際に呼ばれる
        (=「値あり」として扱われる) ことを確認する。"""
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_shot_detail.return_value = {}
            mock_inst.get_shot.return_value = None
            mock_inst.get_tasks.return_value = []
            # cmd_092b: pid 解決 fallback (get_task) 新設に伴い、shot 系 lookup 失敗後に
            # 実際に呼ばれるようになった。dict を返さないと以降の文字列結合処理で
            # MagicMock が混入し無関係な TypeError で 500 化するため明示的に設定する。
            mock_inst.get_task.return_value = {"project_id": 80, "seqID": "SEQ_PM", "type": "other"}
            mock_inst.get_project.return_value = {"name": "Score検証"}
            mock_inst.get_project_roles.return_value = {"pm": 3, "director": 7, "lead": 9}
            mock_inst.get_me.return_value = None
            mock_inst.post_dm_thread.return_value = {"thread_id": 900}
            mock_inst.post_dm.return_value = {}
            mock_inst.post_retakes.return_value = {"ok": True}
            MockClient.return_value = mock_inst

            resp = client.post(
                "/api/bff/retakes",
                json={"shot_id": 0, "task_id": 3282, "direction": "全体的に色味調整"},
                headers=_auth_headers(),
            )

        assert resp.status_code == 200
        mock_inst.get_shot_detail.assert_called_once_with(0, actor_user_id=_RESOLVED_ACTOR_ID)


class TestBffWriteRetakesFallbackPmMisroute:
    """subtask_092a 新規発見 POST-RETAKES-FALLBACK-PM-MISROUTE の回帰防止。

    cmd_091c は post_retakes の shot_id=0 truthy 誤判定 (`if shot_id:`) を是正した
    が、shot_id=0 (実在しない shot) の場合に project_id を解決する fallback が
    存在しなかった。結果 pid=None のまま→通知先がハードコード FALLBACK_PM(uid52)
    に誤フォールバックし、実 PM/Director/Lead に Retake 発令通知が届かない。
    400/クラッシュにならない静かな誤送信のため、参加者リストの中身まで検証する。
    """

    def test_shot_zero_resolves_real_roles_not_fallback_pm(self, client, monkeypatch):
        """task_id→project_id fallback (notify_existing と同一設計) 是正後は、
        shot_id=0 でも task_id 経由で実プロジェクトの PM/Director/Lead が解決され、
        参加者リストに FALLBACK_PM(52) が混入しないことを確認する。"""
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_shot_detail.return_value = {}
            mock_inst.get_shot.return_value = None
            mock_inst.get_tasks.return_value = []
            mock_inst.get_task.return_value = {"project_id": 80, "seqID": "SEQ_PM", "type": "comp"}
            mock_inst.get_project.return_value = {"name": "Score検証"}
            mock_inst.get_project_roles.return_value = {"pm": 3, "director": 7, "lead": 9}
            mock_inst.get_me.return_value = None
            mock_inst.post_dm_thread.return_value = {"thread_id": 950}
            mock_inst.post_dm.return_value = {}
            mock_inst.post_retakes.return_value = {"ok": True}
            MockClient.return_value = mock_inst

            resp = client.post(
                "/api/bff/retakes",
                json={"shot_id": 0, "task_id": 3282, "direction": "色味調整"},
                headers=_auth_headers(),
            )

        assert resp.status_code == 200
        # pid=None のまま呼ばれる旧経路ではここが呼ばれない (=FALLBACK_PM 52 に頼る)。
        # 呼ばれていること自体が fallback 新設の直接証拠。
        mock_inst.get_project_roles.assert_called_once_with(80, actor_user_id=_RESOLVED_ACTOR_ID)
        participants = resp.json()["participants"]
        assert 52 not in participants, "FALLBACK_PM(uid52)へ誤フォールバックしている (POST-RETAKES-FALLBACK-PM-MISROUTE 再発)"
        assert {3, 7, 9} <= set(participants), "実 PM/Director/Lead が参加者に含まれていない"

    def test_shot_nonzero_ignores_task_id_fallback_pid(self, client, monkeypatch):
        """正の対照実験: 通常 shot (shot_id=5) は get_shot_detail が project_id=33 を
        直接返すため、新設した task_id→project_id fallback は使われない。get_task の
        戻り値に罠値 project_id=999 を仕込み、fallback が誤って割り込んで pid を
        上書きしていないことを get_project_roles の呼び出し引数で保証する
        (get_task 自体は既存の assignee 自動追加処理で呼ばれ得るため、その戻り値の
        project_id が pid 解決に混入しないことのみを検証する)。"""
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_shot_detail.return_value = {"shotID": "SH010", "seqID": "SEQ01", "project_id": 33}
            mock_inst.get_tasks.return_value = []
            mock_inst.get_task.return_value = {"project_id": 999}
            mock_inst.get_project.return_value = {"name": "Score検証"}
            mock_inst.get_project_roles.return_value = {"pm": 3, "director": 7, "lead": 9}
            mock_inst.get_me.return_value = None
            mock_inst.post_dm_thread.return_value = {"thread_id": 951}
            mock_inst.post_dm.return_value = {}
            mock_inst.post_retakes.return_value = {"ok": True}
            MockClient.return_value = mock_inst

            resp = client.post(
                "/api/bff/retakes",
                json={"shot_id": 5, "task_id": 10, "direction": "色味調整"},
                headers=_auth_headers(),
            )

        assert resp.status_code == 200
        mock_inst.get_project_roles.assert_called_once_with(33, actor_user_id=_RESOLVED_ACTOR_ID)


# ─── ⑥ Approve POST: bff_write.py post_qc_approve_bff (SHOT-ZERO-APPROVE-400) ──

class TestBffWriteQcApproveShotZero:
    def test_shot_zero_does_not_400(self, client, monkeypatch):
        """cmd_091c 中核修正 (SHOT-ZERO-APPROVE-400) の回帰防止: 旧実装
        `if not shot_id:` は shot_id=0 を「必須パラメータ欠落」と誤判定し
        HTTP 400 (shot_id 必須) を返していた (091a で判定ボタンは活性化したが
        Approve 実行自体が拒否される新規バグだった)。`if shot_id is None:` 是正後は
        shot_id=0 が 400 を引き起こさないことを確認する。"""
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_tasks.return_value = []
            mock_inst.get_my_dm_threads.return_value = []
            MockClient.return_value = mock_inst

            resp = client.post(
                "/api/bff/qc/approve",
                json={"shot_id": 0, "task_id": 3282, "comment": "OKです"},
                headers=_auth_headers(),
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["shot_id"] == 0

    def test_shot_zero_task_auto_resolve_called(self, client, monkeypatch):
        """cmd_075 の task_id 未指定時 auto-resolve (shot 内の判定待ち task を検索)
        は旧実装 `if not task_id and shot_id:` により shot_id=0 では falsy 判定で
        auto-resolve 自体がスキップされていた。`shot_id is not None` 是正後は
        shot_id=0 でも client.get_tasks(0, ...) が呼ばれ、判定待ち task が
        auto-resolve されて patch_task が正しい task_id で呼ばれることを確認する。"""
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_tasks.return_value = [
                {"id": 3282, "status": "qc"},
            ]
            mock_inst.patch_task.return_value = {}
            mock_inst.get_my_dm_threads.return_value = []
            MockClient.return_value = mock_inst

            resp = client.post(
                "/api/bff/qc/approve",
                json={"shot_id": 0, "comment": ""},
                headers=_auth_headers(),
            )

        assert resp.status_code == 200
        mock_inst.get_tasks.assert_called_once_with(0, actor_user_id=_RESOLVED_ACTOR_ID)
        mock_inst.patch_task.assert_called_once_with(3282, {"status": "ap"}, actor_user_id=_RESOLVED_ACTOR_ID)
        assert resp.json()["task_id"] == 3282

    def test_shot_positive_control_still_works(self, client, monkeypatch):
        """control: shot_id が正の実値の場合も従来どおり 200 で機能すること
        (shot_id=0 専用の分岐追加が非ゼロ shot_id の既存経路を壊していないか対照確認)。"""
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_tasks.return_value = []
            mock_inst.get_my_dm_threads.return_value = []
            MockClient.return_value = mock_inst

            resp = client.post(
                "/api/bff/qc/approve",
                json={"shot_id": 5, "task_id": 10, "comment": ""},
                headers=_auth_headers(),
            )

        assert resp.status_code == 200
        assert resp.json()["shot_id"] == 5


# ─── ⑦ notify_existing 系 (project_id fallback 含む): bff_write.py ─────────

class TestBffWriteQcNotifyExistingShotZero:
    def test_shot_zero_does_not_400(self, client, monkeypatch):
        """cmd_091c 回帰防止: 旧実装 `if not shot_id:` は shot_id=0 を「asset から
        shot_id 解決不可」400 として誤拒否していた。`if shot_id is None:` 是正後は
        shot_id=0 が 400 を引き起こさないことを確認する (Director 解決は task_id
        経由 project_id fallback で成立させる)。"""
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_shot_detail.return_value = {}
            mock_inst.get_shot.return_value = None
            mock_inst.get_task.return_value = {"project_id": 80, "seqID": "SEQ_PM", "type": "other"}
            mock_inst.get_tasks.return_value = []
            mock_inst.get_my_projects.return_value = []
            mock_inst.get_project.return_value = {"name": "Score検証"}
            mock_inst.get_project_roles.return_value = {"director": 7, "pm": 3}
            mock_inst.get_me.return_value = None
            mock_inst.get_users.return_value = []
            mock_inst.post_dm_thread.return_value = {"thread_id": 901}
            mock_inst.post_dm.return_value = {}
            MockClient.return_value = mock_inst

            resp = client.post(
                "/api/bff/qc/notify_existing",
                json={"asset_id": 500, "shot_id": 0, "task_id": 3282, "submission_type": "qc"},
                headers=_auth_headers(),
            )

        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_shot_zero_task_id_resolves_project_id_via_get_task_fallback(self, client, monkeypatch):
        """cmd_091c 追加修正 (091 単独では不十分だった対): shot_id=0 の shot 系
        lookup は常に空を返すため、pid (project_id) が解決できず director_uid も
        永久に引けない。task_id から client.get_task() で project_id を直接解決する
        fallback が実際に呼ばれ、Director 未設定 400 を回避できることを確認する。"""
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_shot_detail.return_value = {}
            mock_inst.get_shot.return_value = None
            mock_inst.get_task.return_value = {"project_id": 80, "seqID": "SEQ_PM", "type": "other"}
            mock_inst.get_tasks.return_value = []
            mock_inst.get_my_projects.return_value = []
            mock_inst.get_project.return_value = {"name": "Score検証"}
            mock_inst.get_project_roles.return_value = {"director": 7, "pm": 3}
            mock_inst.get_me.return_value = None
            mock_inst.get_users.return_value = []
            mock_inst.post_dm_thread.return_value = {"thread_id": 901}
            mock_inst.post_dm.return_value = {}
            MockClient.return_value = mock_inst

            resp = client.post(
                "/api/bff/qc/notify_existing",
                json={"asset_id": 500, "shot_id": 0, "task_id": 3282, "submission_type": "qc"},
                headers=_auth_headers(),
            )

        assert resp.status_code == 200
        mock_inst.get_task.assert_called_once_with(3282, actor_user_id=_RESOLVED_ACTOR_ID)
        mock_inst.get_project_roles.assert_called_once_with(80, actor_user_id=_RESOLVED_ACTOR_ID)


# ─── ⑧ 受信 QC/Review 依頼のクリック先: pages_dashboard.py qc_url 抽出 ──────

class TestPagesDashboardQcUrlShotZero:
    def test_qc_url_regex_matches_shot_zero_link(self, client, monkeypatch):
        """cmd_090 回帰防止 (SHOT_000 特化): 受信 QC/Review 依頼の本文埋込済リンクが
        `/qc/0?task_id=..&asset_id=..` (SHOT_000 sentinel) の場合でも、qc_url 抽出
        正規表現 `r'/qc/\\d+\\S*'` が "0" を正しく1桁の数字として拾い、SHOT thread
        (/messages?thread=..) への誤遷移にならず QC ビューアへ直接遷移できることを
        確認する (既存の test_thread_qc_request_links_to_qc_viewer_not_thread は
        shot_id=5 の通常ケースのみ検証・本テストは shot_id=0 境界値を追加検証)。"""
        from app.adapters.dto import CalendarUser

        mock_user = CalendarUser(user_id=5, email="sato@studio.jp", role="Compositor", name="Sato")
        with patch("app.routers.pages_dashboard.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_me.return_value = mock_user
            mock_inst.get_my_dm_threads.return_value = [
                {
                    "thread_id": 88,
                    "updated_at": "2026-07-10T23:00:00",
                    "participants": [5, 6, 7],
                    "last_message": (
                        "🔍 QC 依頼\n"
                        "Score検証 / 📋 プロジェクト管理タスク / 📋 (SHOT 紐付けなし) / other\n"
                        "\n"
                        "既存 v01 (fix_v01.mov) の御確認を御願い致します。\n"
                        "\n"
                        "/qc/0?task_id=3282&asset_id=500\n"
                        "\n"
                        "— Sato"
                    ),
                },
            ]
            MockClient.return_value = mock_inst
            resp = client.get("/dashboard", headers=_auth_headers())

        assert resp.status_code == 200
        assert "/qc/0?task_id=3282&asset_id=500" in resp.text


# ─── ⑨ Troubles POST (POST-TROUBLES-SHOTLESS-REACHABLE): bff_write.py post_troubles ──

class TestBffWriteTroublesShotZero:
    """subtask_092a 新規発見 POST-TROUBLES-SHOTLESS-REACHABLE の回帰防止。

    cmd_091c の target_gunshi_qc では「shotless 運用有無不明ゆえ保留」とされて
    いたが、subtask_092a で shot_detail.html:1110 の🚨技術トラブル報告ボタンが
    isolated_task 条件でガードされず GET /task/{task_id} (SHOT_000) から常時
    到達可能と確定した。旧実装は shot_id=0 を `if shot_id:` で falsy 誤判定して
    Lead 解決を丸ごとスキップし (SHOT-ZERO 系と同型バグ)、is not None 化だけでは
    task_id fallback が無いため no-op のままだった (実在しない shot の project_id
    は解決できないため)。post_retakes/notify_existing と同一設計の
    task_id→project_id fallback を新設し、shotless task からの報告でも正しい
    Lead へ到達することを検証する。
    """

    def test_shot_zero_task_id_resolves_lead_no_400(self, client, monkeypatch):
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_shot_detail.return_value = {}
            mock_inst.get_task.return_value = {"project_id": 80}
            mock_inst.get_project_roles.return_value = {"lead": 9}
            mock_inst.post_troubles.return_value = {"ok": True, "id": 1}
            MockClient.return_value = mock_inst

            resp = client.post(
                "/api/bff/troubles",
                json={"shot_id": 0, "task_id": 3282, "title": "レンダリング異常", "body": "詳細説明テキスト", "mentions": []},
                headers=_auth_headers(),
            )

        assert resp.status_code == 200
        # 旧実装は `if shot_id:` が 0 を falsy 誤判定し get_shot_detail 自体を
        # スキップしていた。0 が「値あり」として扱われ実際に呼ばれることを確認。
        mock_inst.get_shot_detail.assert_called_once_with(0, actor_user_id=_RESOLVED_ACTOR_ID)
        mock_inst.get_task.assert_called_once_with(3282, actor_user_id=_RESOLVED_ACTOR_ID)
        mock_inst.get_project_roles.assert_called_once_with(80, actor_user_id=_RESOLVED_ACTOR_ID)
        # Lead(uid9) が Calendar への payload に正しく引き継がれることまで確認
        sent_payload = mock_inst.post_troubles.call_args.args[0]
        assert sent_payload["lead_uid"] == 9

    def test_shot_nonzero_resolves_lead_directly_without_fallback(self, client, monkeypatch):
        """正の対照実験: 通常 shot (shot_id=5) は get_shot_detail が project_id を
        直接返すため task_id→project_id fallback は使われない。get_task の戻り値に
        罠値 project_id=999 を仕込み、fallback 新設が既存の正常経路に割り込んで
        いないことを get_project_roles の呼び出し引数と get_task 不呼び出しの
        両面で保証する (post_troubles は post_retakes と異なり get_task の呼び出し
        箇所が fallback のみのため、不呼び出しを直接断言できる)。"""
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_shot_detail.return_value = {"project_id": 33}
            mock_inst.get_task.return_value = {"project_id": 999}
            mock_inst.get_project_roles.return_value = {"lead": 9}
            mock_inst.post_troubles.return_value = {"ok": True, "id": 2}
            MockClient.return_value = mock_inst

            resp = client.post(
                "/api/bff/troubles",
                json={"shot_id": 5, "title": "その他", "body": "詳細説明テキスト", "mentions": []},
                headers=_auth_headers(),
            )

        assert resp.status_code == 200
        mock_inst.get_project_roles.assert_called_once_with(33, actor_user_id=_RESOLVED_ACTOR_ID)
        mock_inst.get_task.assert_not_called()
        assert "/messages?thread=88" not in resp.text
