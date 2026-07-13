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

    def test_shot_zero_asset_resolves_via_task_fallback_when_shot_detail_empty(self, client, monkeypatch):
        """cmd_093 回帰防止 (director_retake_input.png提出物誤表示): shotless task
        (SHOT_000) では get_shot_detail(0) が常に空 ({}) を返すため、旧実装は
        latest_asset が永久に None のまま「提出物が無し」を誤表示していた
        (実機: project 80 task_id=3282, get_shot_detail(0)={} だが
        get_assets_by_task(3282) には v001.png/v002.txt/v003.png が実在)。
        get_assets_by_task (cmd_058・next_version と同一設計) への fallback が
        機能し、created_at 最新の v003.png が latest_asset として解決され、
        画像プレビューに反映されることを確認する。"""
        monkeypatch.setattr("app.routers.pages_director.get_actor_role", lambda actor_id: "director")
        with patch("app.routers.pages_director.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_me.return_value = None
            mock_inst.get_my_projects.return_value = []
            mock_inst.get_shots.return_value = []
            mock_inst.get_shot.return_value = None
            mock_inst.get_shot_detail.return_value = {}
            mock_inst.get_tasks.return_value = []
            mock_inst.get_assets_by_task.return_value = [
                {"shot_id": None, "task_id": 3282, "version": "v001",
                 "file_path": "E:/calender/backend/static/assets/shot_none_task_3282_v001.png",
                 "id": 103, "created_at": "2026-07-10T15:56:37.056333"},
                {"shot_id": None, "task_id": 3282, "version": "v002",
                 "file_path": "E:/calender/backend/static/assets/shot_none_task_3282_v002.txt",
                 "id": 104, "created_at": "2026-07-10T16:34:17.046134"},
                {"shot_id": None, "task_id": 3282, "version": "v003",
                 "file_path": "E:/calender/backend/static/assets/shot_none_task_3282_v003.png",
                 "id": 107, "created_at": "2026-07-13T15:22:00.317116"},
            ]
            MockClient.return_value = mock_inst

            resp = client.get("/director_retake_input?shot_id=0&task_id=3282", headers=_auth_headers())

        assert resp.status_code == 200
        mock_inst.get_assets_by_task.assert_called_once_with(3282, actor_user_id=_RESOLVED_ACTOR_ID)
        assert "提出物が無し" not in resp.text
        assert "preview 非対応" not in resp.text
        assert "shot_none_task_3282_v003.png" in resp.text

    def test_shot_zero_still_shows_no_submission_when_task_truly_has_no_assets(self, client, monkeypatch):
        """上記 fallback 追加の regression ガード: get_assets_by_task も空を返す
        (＝本当に提出物が無いtask) 場合は、誤って何かをでっち上げず正しく
        「提出物が無し」のままであることを確認する (false-positive 防止)。"""
        monkeypatch.setattr("app.routers.pages_director.get_actor_role", lambda actor_id: "director")
        with patch("app.routers.pages_director.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_me.return_value = None
            mock_inst.get_my_projects.return_value = []
            mock_inst.get_shots.return_value = []
            mock_inst.get_shot.return_value = None
            mock_inst.get_shot_detail.return_value = {}
            mock_inst.get_tasks.return_value = []
            mock_inst.get_assets_by_task.return_value = []
            MockClient.return_value = mock_inst

            resp = client.get("/director_retake_input?shot_id=0&task_id=9999", headers=_auth_headers())

        assert resp.status_code == 200
        assert "提出物が無し" in resp.text

    def test_shot_nonzero_asset_resolution_unaffected_by_task_fallback(self, client, monkeypatch):
        """正の対照実験: 通常 shot (shot_id != 0) で get_shot_detail が既に
        asset_list を返している場合、cmd_093 で新設した get_assets_by_task
        fallback は一切呼ばれず、既存の shot 経由解決のみで latest_asset が
        決まること (fallback 新設が正常経路を破壊していないことの保証)。"""
        monkeypatch.setattr("app.routers.pages_director.get_actor_role", lambda actor_id: "director")
        with patch("app.routers.pages_director.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_me.return_value = None
            mock_inst.get_my_projects.return_value = []
            mock_inst.get_shots.return_value = []
            mock_inst.get_shot.return_value = None
            mock_inst.get_shot_detail.return_value = {
                "asset_list": [
                    {"task_id": 555, "version": "v002",
                     "file_path": "/assets/shot5_task555_v002.png",
                     "id": 1, "created_at": "2026-07-12T00:00:00"},
                ],
            }
            mock_inst.get_tasks.return_value = []
            MockClient.return_value = mock_inst

            resp = client.get("/director_retake_input?shot_id=5&task_id=555", headers=_auth_headers())

        assert resp.status_code == 200
        mock_inst.get_assets_by_task.assert_not_called()
        assert "shot5_task555_v002.png" in resp.text


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


# ─── ⑩ 自担当 QC 依頼カード (own task): pages_dashboard.py qc_requests ────────
# subtask_094a (SHOT000-PROACTIVE-AUDIT) 新規発見。既存 ⑧ (TestPagesDashboardQcUrlShotZero)
# は「受信 QC/Review 依頼」(thread_qc_requests・他者からの依頼) のみを検証しており、
# 「🔴 QC 依頼」(qc_requests・自分自身の担当 task が判定待ちの場合) カードは
# 全く別のコードパスかつ無検証だった。

class TestDashboardOwnQcRequestShotZero:
    def test_own_qc_request_shot_zero_links_to_qc_viewer_not_task_page(self, client, monkeypatch):
        """dashboard.html:129 は旧実装 `{% if tk.shot_id %}` が shot_id=0 (SHOT_000) を
        falsy 誤判定し、明示コメント「QC ビューアに遷移・task page でなく」に反して
        /task/{task_id} へ誤誘導していた。`is not none` 是正後は /qc/0?task_id=..
        へ遷移し、latest_asset_id も pages_dashboard.py 側の get_assets_by_task
        フォールバックで解決されることを確認する。"""
        from app.adapters.dto import CalendarUser

        mock_user = CalendarUser(user_id=42, email="sato@studio.jp", role="Compositor", name="Sato")
        with patch("app.routers.pages_dashboard.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_me.return_value = mock_user
            mock_inst.get_my_projects.return_value = []
            mock_inst.get_my_dm_threads.return_value = []
            mock_inst.get_my_tasks.return_value = [
                {"id": 3282, "shot_id": 0, "project_id": 80, "status": "qc",
                 "type": "other", "name": "Score検証 PM task"},
            ]
            mock_inst.get_shot_detail.return_value = {}
            mock_inst.get_assets_by_task.return_value = [
                {"id": 500, "task_id": 3282, "created_at": "2026-07-13T10:00:00"},
            ]
            MockClient.return_value = mock_inst

            resp = client.get("/dashboard", headers=_auth_headers())

        assert resp.status_code == 200
        mock_inst.get_shot_detail.assert_called_once_with(0, actor_user_id=_RESOLVED_ACTOR_ID)
        assert "/qc/0?task_id=3282&asset_id=500" in resp.text, "SHOT_000 own-task QC依頼が QC ビューアへ遷移していない (dashboard.html:129 falsy-zero 再発)"

    def test_own_qc_request_shot_nonzero_unaffected(self, client, monkeypatch):
        """正の対照実験: 通常 shot (shot_id=5) の own-task QC 依頼は従来通り
        /qc/5?task_id=.. へ遷移し、is not none 化による回帰が無いことを保証する。"""
        from app.adapters.dto import CalendarUser

        mock_user = CalendarUser(user_id=42, email="sato@studio.jp", role="Compositor", name="Sato")
        with patch("app.routers.pages_dashboard.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_me.return_value = mock_user
            mock_inst.get_my_projects.return_value = []
            mock_inst.get_my_dm_threads.return_value = []
            mock_inst.get_my_tasks.return_value = [
                {"id": 11, "shot_id": 5, "project_id": 33, "status": "qc", "type": "Lighting"},
            ]
            mock_inst.get_shot_detail.return_value = {
                "asset_list": [{"id": 700, "task_id": 11, "created_at": "2026-07-13T10:00:00"}],
            }
            MockClient.return_value = mock_inst

            resp = client.get("/dashboard", headers=_auth_headers())

        assert resp.status_code == 200
        assert "/qc/5?task_id=11&asset_id=700" in resp.text


# ─── ⑪ Retake 発令通知リンク: bff_write.py post_retakes qc_path ──────────────
# subtask_094a 新規発見。既存 ⑤ (TestBffWriteRetakesShotZero) は shot 系解決が
# スキップされないことのみ検証しており、通知本文に埋め込む qc_link (retake_view
# へ誘導すべきリンク) の SHOT_000 分岐は無検証だった。

class TestBffWriteRetakesQcPathShotZero:
    def test_shot_zero_retake_notification_links_to_retake_view_with_task_id(self, client, monkeypatch):
        """bff_write.py:249 は旧実装 `(shot_id and task_id)` が shot_id=0 を falsy
        誤判定し、task_id 付き /retake_view/0/{task_id} でなく task_id 無しの曖昧な
        /qc/0 (どの shotless task の Retake か特定不能) に誤フォールバックしていた。
        `(shot_id is not None and task_id)` 是正後の挙動を確認する。"""
        monkeypatch.delenv("SCORE_PUBLIC_URL", raising=False)
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_shot_detail.return_value = {}
            mock_inst.get_shot.return_value = None
            mock_inst.get_tasks.return_value = []
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
        qc_link = resp.json()["qc_link"]
        assert qc_link == "/retake_view/0/3282", f"SHOT_000 Retake 通知が曖昧な /qc/0 に誤フォールバックしている (実際: {qc_link})"

    def test_shot_nonzero_retake_notification_unaffected(self, client, monkeypatch):
        """正の対照実験: 通常 shot (shot_id=5) は従来通り /retake_view/5/{task_id}
        へ遷移し、is not None 化による回帰が無いことを保証する。"""
        monkeypatch.delenv("SCORE_PUBLIC_URL", raising=False)
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_shot_detail.return_value = {"shotID": "SHOT_005", "seqID": "SEQ01", "project_id": 33}
            mock_inst.get_shot.return_value = None
            mock_inst.get_tasks.return_value = []
            mock_inst.get_project.return_value = {"name": "Ramps"}
            mock_inst.get_project_roles.return_value = {"pm": 3, "director": 7, "lead": 9}
            mock_inst.get_me.return_value = None
            mock_inst.post_dm_thread.return_value = {"thread_id": 901}
            mock_inst.post_dm.return_value = {}
            mock_inst.post_retakes.return_value = {"ok": True}
            MockClient.return_value = mock_inst

            resp = client.post(
                "/api/bff/retakes",
                json={"shot_id": 5, "task_id": 11, "direction": "色味調整"},
                headers=_auth_headers(),
            )

        assert resp.status_code == 200
        assert resp.json()["qc_link"] == "/retake_view/5/11"


# ─── ⑫ 参考資料ビューア: pages_qc.py get_reference_viewer ────────────────────
# subtask_094a 新規発見。get_qc_viewer (cmd_091) の姉妹関数だが project_id の
# task_id フォールバックが未実装のまま残っていた。

class TestReferenceViewerShotZero:
    def test_shot_zero_project_id_resolves_via_task_not_hardcoded_33(self, client, monkeypatch):
        """pages_qc.py get_reference_viewer は旧実装だと shot_id=0 で
        client.get_shot(0) が None を返すため project_id が解決不能となり、
        breadcrumb がハードコード fallback (/project_detail/33) に誤誘導されて
        いた (get_qc_viewer の cmd_091 修正が本関数には未適用だった)。
        _resolve_task 経由の selected_task.project_id フォールバック是正後は
        実際の project_id (80) が使われることを確認する。"""
        with patch("app.routers.pages_qc.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_me.return_value = None
            mock_inst.get_tasks.return_value = []
            mock_inst.get_shot.return_value = None
            mock_inst.get_task.return_value = {
                "id": 3282, "shot_id": 0, "type": "other", "name": "Score検証 PM task",
                "status": "qc", "project_id": 80,
            }
            MockClient.return_value = mock_inst

            resp = client.get("/reference/0?task_id=3282", headers=_auth_headers())

        assert resp.status_code == 200
        assert "/project_detail/80" in resp.text
        assert "/project_detail/33" not in resp.text, "SHOT_000 の参考資料ビューアがハードコード fallback project 33 に誤遷移している"

    def test_shot_nonzero_project_id_unaffected(self, client, monkeypatch):
        """正の対照実験: 通常 shot (shot_id=5) は shot.project_id を直接使い、
        selected_task 経由の fallback には依存しないことを確認する。"""
        with patch("app.routers.pages_qc.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_me.return_value = None
            mock_inst.get_tasks.return_value = []
            mock_shot = MagicMock(project_id=33, seq_code="SEQ01")
            mock_inst.get_shot.return_value = mock_shot
            mock_inst.get_task.return_value = {"id": 11, "shot_id": 5, "type": "Lighting", "project_id": 999}
            MockClient.return_value = mock_inst

            resp = client.get("/reference/5?task_id=11", headers=_auth_headers())

        assert resp.status_code == 200
        assert "/project_detail/33" in resp.text


# ─── ⑬ Retake 内容 view: pages_director.py get_retake_view target_asset ──────
# subtask_094a 新規発見。get_director_retake_input (cmd_093) の姉妹関数だが
# get_assets_by_task フォールバックが未実装のまま残っていた。

class TestRetakeViewTargetAssetShotZero:
    def test_shot_zero_target_asset_resolves_via_get_assets_by_task(self, client, monkeypatch):
        """pages_director.py get_retake_view は shot_id=0 の場合
        get_shot_detail(0) が常に空を返すため target_asset が永久に None のまま
        (=Retake 対象の参照素材プレビューが表示されない) だった。
        get_director_retake_input と同一パターンの get_assets_by_task フォールバック
        是正後は target_asset/target_url が正しく解決されることを確認する。
        (target_asset プレビューは既存 Retake meta が見つかった場合のみ表示される
        仕様のため、/tmp/score_retake_refs/ に実 meta.json を用意して検証する)"""
        import json
        import time
        from pathlib import Path

        refs_dir = Path(f"/tmp/score_retake_refs/r_test094a_{int(time.time())}")
        refs_dir.mkdir(parents=True, exist_ok=True)
        (refs_dir / "meta.json").write_text(json.dumps({
            "retake_id": refs_dir.name, "shot_id": 0, "task_id": 3282,
            "submitted_at": "2026-07-13T10:00:00", "submitted_by": "1",
        }), encoding="utf-8")
        try:
            monkeypatch.setattr("app.routers.pages_director.get_actor_role", lambda actor_id: "director")
            with patch("app.routers.pages_director.get_calendar_client") as MockClient:
                mock_inst = MagicMock()
                mock_inst.get_me.return_value = None
                mock_inst.get_shot.return_value = None
                mock_inst.get_task.return_value = {"project_id": 80}
                mock_inst.get_tasks.return_value = []
                mock_inst.get_shot_detail.return_value = {}
                mock_inst.get_assets_by_task.return_value = [
                    {"id": 500, "task_id": 3282, "file_path": "/data/assets/fix_v01.mov", "created_at": "2026-07-13T10:00:00"},
                ]
                mock_inst.get_my_dm_threads.return_value = []
                mock_inst.get_users.return_value = []
                MockClient.return_value = mock_inst

                resp = client.get("/retake_view/0/3282", headers=_auth_headers())
        finally:
            (refs_dir / "meta.json").unlink(missing_ok=True)
            refs_dir.rmdir()

        assert resp.status_code == 200
        mock_inst.get_assets_by_task.assert_called_once_with(3282, actor_user_id=_RESOLVED_ACTOR_ID)
        assert "fix_v01.mov" in resp.text, "SHOT_000 retake_view の target_asset フォールバックが機能していない"


# ─── ⑭ 退勤報告: pages_auth.py read_exit_report ──────────────────────────────
# subtask_094a 新規発見。shot_map は実 shot のみで構築されるため shot_id=0
# (SHOT_000) task は project_name/shot_code が常に空文字になっていた。

class TestExitReportShotZero:
    def test_shot_zero_task_shows_project_name_and_shot_code(self, client, monkeypatch):
        """pages_auth.py read_exit_report は shot_map (実 shot のみで構築) に
        shot_id=0 のエントリが存在し得ないため、旧実装は SHOT_000 task の
        project_name が常に空文字だった (task 自体が持つ project_id を使っていな
        かったため)。project_id 直接キャプチャ + project_name_map フォールバック
        是正後は正しい project 名が表示されることを確認する。"""
        with patch("app.routers.pages_auth.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_me.return_value = None
            mock_inst.get_my_tasks.return_value = [
                {"id": 3282, "shot_id": 0, "project_id": 80, "status": "wip",
                 "type": "other", "name": "", "updated_at": "2026-07-13T09:00:00"},
            ]
            mock_inst.get_my_projects.return_value = [{"id": 80, "name": "Score検証"}]
            mock_inst.get_shots.return_value = []
            MockClient.return_value = mock_inst

            resp = client.get("/exit_report", headers=_auth_headers())

        assert resp.status_code == 200
        # my_tasks は window.__MY_TASKS__ = {{ my_tasks | tojson }}; として埋込済 (JSON は
        # 日本語を \uXXXX エスケープするため、素の文字列検索でなく JSON parse して検証する)。
        import json as _json
        import re as _re
        m = _re.search(r"window\.__MY_TASKS__ = (\[.*?\]);", resp.text)
        assert m, "window.__MY_TASKS__ が退勤報告ページに埋め込まれていない"
        my_tasks = _json.loads(m.group(1))
        task = next(t for t in my_tasks if t.get("task_id") == 3282)
        assert task["project_name"] == "Score検証", f"SHOT_000 task の project_name が解決されていない (実際: {task['project_name']!r})"
        assert task["shot_code"] == "SHOT_000", f"SHOT_000 task の shot_code フォールバックが機能していない (実際: {task['shot_code']!r})"
