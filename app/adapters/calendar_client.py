import os
import threading
from datetime import datetime, timezone, timedelta

import httpx

from .dto import CalendarShot, CalendarTask, CalendarUser


# Score mock user_id → Calendar 実 user_id mapping
# nibu 殿御回答 2026-05-26 §2.3 経由判明
# Score 内では引き続き mock uid (10/99/1/20/30/40) を使用
# Calendar API 側に渡す時は Calendar 実 uid (53/28/52/54/55/56) に変換
_SCORE_TO_CALENDAR_UID = {
    99: 28,  # ryoji@studiobokan.com (既存)
    1: 52,   # tanaka@studiobokan.com (PM)
    10: 53,  # yamada@studiobokan.com (Director)
    20: 54,  # kato@studiobokan.com (Lighting Lead)
    30: 55,  # sato@studiobokan.com (Compositor)
    40: 56,  # suzuki@studiobokan.com (Compositor)
}


def _to_calendar_uid(score_uid: str | int | None) -> int | None:
    """Score 内 uid → Calendar 実 uid 変換 (Phase 1+ 実機接続用)"""
    if score_uid is None:
        return None
    try:
        sid = int(score_uid)
    except (ValueError, TypeError):
        return None
    return _SCORE_TO_CALENDAR_UID.get(sid, sid)  # 未 mapping は元 uid


class CalendarClient:
    """Calendar API アダプタ。M2MトークンはENV経由で受け取り、ブラウザへ到達させない。
    Phase 1 (2026-05-27): nibu 殿御提供の 実 endpoint (192.168.44.253:8001) と M2M token 経由で接続テスト。"""

    def __init__(self) -> None:
        # nibu 殿御提供: CALENDAR_API_BASE_URL を環境変数で受領 (旧 CALENDAR_BASE_URL も互換維持)
        self.base_url = (
            os.environ.get("CALENDAR_API_BASE_URL")
            or os.environ.get("CALENDAR_BASE_URL")
            or "http://192.168.44.253:8001"
        )
        self.m2m_token = os.environ.get("CALENDAR_M2M_TOKEN", "studio_bokan_score_git_process_flow_calender")
        self._user_id_cache: dict[str, int | None] = {}
        self._admin_token: str | None = None
        self._token_expiry: datetime | None = None
        self._token_lock = threading.Lock()

    def _get_admin_token(self) -> str:
        """admin svc account JWT 取得。5am JST で daily 自動更新。"""
        jst = timezone(timedelta(hours=9))
        now = datetime.now(jst)
        today_5am = now.replace(hour=5, minute=0, second=0, microsecond=0)
        if today_5am > now:
            today_5am -= timedelta(days=1)
        with self._token_lock:
            if self._admin_token and self._token_expiry and self._token_expiry > today_5am:
                return self._admin_token
            email = os.environ.get("SCORE_CALENDAR_ADMIN_EMAIL", "")
            password = os.environ.get("SCORE_CALENDAR_ADMIN_PASSWORD", "")
            # nibu 殿 OAuth2 password flow: form-urlencoded + username/password (2026-06-03 hotfix)
            resp = httpx.post(
                f"{self.base_url}/api/auth/token",
                data={"username": email, "password": password},
                timeout=10.0,
            )
            resp.raise_for_status()
            self._admin_token = resp.json()["access_token"]
            self._token_expiry = datetime.now(jst)
            return self._admin_token

    def resolve_email_to_user_id(self, email: str) -> int | None:
        """GET /api/users (M2M) で全件取得し email に一致する整数 id を返す。不在は None (fail-closed)。"""
        if email in self._user_id_cache:
            return self._user_id_cache[email]
        resp = httpx.get(
            f"{self.base_url}/api/users",
            headers={"Authorization": f"Bearer {self.m2m_token}"},
        )
        resp.raise_for_status()
        users = resp.json()
        result: int | None = None
        for u in users:
            if u.get("email") == email:
                result = int(u["id"])
                break
        self._user_id_cache[email] = result
        return result

    def _headers(self, actor_user_id: str | None = None) -> dict:
        """Calendar API 用 headers 生成。
        CALENDAR_MOCK=1: 既存 m2m_token パス維持。
        CALENDAR_MOCK=0: admin JWT + X-Actor-User-Id。"""
        if os.environ.get("CALENDAR_MOCK", "0") == "1":
            h = {"Authorization": f"Bearer {self.m2m_token}"}
        else:
            # 殿御命 2026-06-08: admin token 取得失敗 (SCORE_CALENDAR_ADMIN_* 未設定等) 時 m2m token に fallback
            try:
                token = self._get_admin_token()
                h = {"Authorization": f"Bearer {token}"}
            except Exception:
                h = {"Authorization": f"Bearer {self.m2m_token}"}
        if actor_user_id is not None:
            calendar_uid = _to_calendar_uid(actor_user_id)
            if calendar_uid is not None:
                h["X-Actor-User-Id"] = str(calendar_uid)
        return h

    def _request_with_retry(self, method: str, url: str, **kwargs) -> httpx.Response:
        """401 時にトークン再取得して1回リトライ。"""
        resp = httpx.request(method, url, **kwargs)
        if resp.status_code == 401 and os.environ.get("CALENDAR_MOCK", "0") != "1":
            with self._token_lock:
                self._admin_token = None
            kwargs["headers"] = self._headers(kwargs.get("headers", {}).get("X-Actor-User-Id"))
            resp = httpx.request(method, url, **kwargs)
        return resp

    def _abs_avatar(self, url):
        """殿御命 2026-06-10 (ニブ Q1 回答対応): Calendar が返す相対 avatar_url
        (/static/uploads/avatars/.. ・/uploads/avatars/.. ・/api/users/{id}/avatar) を
        Calendar 絶対 URL に書換える。Score frontend は相対だと Score origin に取りに行き 404 になるため。
        Calendar 配信は無認証ゆえ <img> から直接取得可。"""
        if not url or not isinstance(url, str):
            return url
        if url.startswith("http://") or url.startswith("https://"):
            return url
        if url.startswith("/"):
            return self.base_url.rstrip("/") + url
        return url

    def get_me(self, actor_user_id: str | None = None) -> CalendarUser:
        """GET /api/me — 自分のプロフィール取得 (calender_api_complete_list.md §8)"""
        resp = httpx.get(f"{self.base_url}/api/me", headers=self._headers(actor_user_id))
        resp.raise_for_status()
        d = resp.json()
        # 殿御命 2026-06-08: email から推測 fallback 追加 (Ryoji 等 name 欠落 user 対応)
        _email_local = (d.get("email") or "").split("@")[0]
        name = d.get("name") or d.get("full_name") or d.get("username") or _email_local or "ユーザ"
        return CalendarUser(
            user_id=d["id"],
            email=d["email"],
            role=d["role"],
            name=name,
            icon_url=self._abs_avatar(d.get("avatar_url") or d.get("iconUrl")) or None,
        )

    def get_shots(self, project_id: int, actor_user_id: str | None = None) -> list[CalendarShot]:
        """GET /api/shots?project_id=N — ショット一覧取得 (calender_api_complete_list.md §4)"""
        resp = httpx.get(
            f"{self.base_url}/api/shots",
            params={"project_id": project_id},
            headers=self._headers(actor_user_id),
        )
        resp.raise_for_status()
        return [
            CalendarShot(
                shot_id=item["id"],
                project_id=item["project_id"],
                name=item.get("shot_code") or f'{item.get("seq_code", "")}/{item.get("shot_code", "")}',
                status=item["status"],
                shot_code=item.get("shot_code"),
                seq_code=item.get("seq_code"),
            )
            for item in resp.json()
        ]

    def get_shot(self, shot_id: int, actor_user_id: str | None = None) -> CalendarShot | None:
        """GET /api/shots/{shot_id} — shot 単体取得。未存在/エラー時は None。"""
        try:
            r = httpx.get(
                f"{self.base_url}/api/shots/{shot_id}",
                headers=self._headers(actor_user_id),
            )
        except httpx.ConnectError:
            return None
        if r.status_code != 200:
            return None
        d = r.json()
        return CalendarShot(
            shot_id=d.get("id", shot_id),
            project_id=d.get("project_id", 0),
            name=d.get("shot_code") or d.get("seq_code") or str(shot_id),
            status=d.get("status", ""),
            shot_code=d.get("shot_code"),
            seq_code=d.get("seq_code"),
        )

    def get_shot_detail(self, shot_id: int, actor_user_id: str | None = None) -> dict:
        """GET /api/me/shots/{shot_id} — actor スコープの shot 詳細 (Phase A relink A-2)"""
        resp = httpx.get(
            f"{self.base_url}/api/me/shots/{shot_id}",
            headers=self._headers(actor_user_id),
        )
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
        return resp.json()

    def get_tasks(self, shot_id: int, actor_user_id: str | None = None) -> list[CalendarTask]:
        """GET /api/shots/{id}/tasks — ショット配下タスク一覧取得 (calender_api_complete_list.md §4).

        404 (shot 不在) → []・fail-gracefully (cmd_454 advisory 修正 2026-05-21)。
        """
        resp = httpx.get(
            f"{self.base_url}/api/shots/{shot_id}/tasks",
            headers=self._headers(actor_user_id),
        )
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        return [
            CalendarTask(
                task_id=item["id"],
                shot_id=item["shot_id"],
                type=item["type"],
                assignee_id=item.get("assigned_to"),
                status=item["status"],
            )
            for item in resp.json()
        ]

    def get_tasks_by_project(self, project_id: int, actor_user_id: str | None = None) -> list:
        """GET /api/tasks?project_id=N — project 配下全 task 一括取得 (N+1 排除用)"""
        resp = httpx.get(
            f"{self.base_url}/api/tasks",
            params={"project_id": project_id},
            headers=self._headers(actor_user_id),
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("tasks", [])

    def get_task(self, task_id: int, actor_user_id: str | None = None) -> dict:
        """GET /api/tasks/{id} — task 単独取得 (id 経由)"""
        resp = httpx.get(
            f"{self.base_url}/api/tasks/{task_id}",
            headers=self._headers(actor_user_id),
        )
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
        return resp.json()

    # ─── 書込メソッド (calender_api_complete_list.md §8) ────────────────────

    def post_retakes(self, body: dict, actor_user_id: str) -> dict:
        """POST /api/retakes — タイムコード付きリテイク指示の発行"""
        resp = httpx.post(
            f"{self.base_url}/api/retakes",
            json=body,
            headers=self._headers(actor_user_id),
        )
        resp.raise_for_status()
        return resp.json()

    def post_shot_approve(self, shot_id: int, body: dict, actor_user_id: str) -> dict:
        """POST /api/shots/{id}/approve — ショットの最終承認・ステータス更新"""
        resp = httpx.post(
            f"{self.base_url}/api/shots/{shot_id}/approve",
            json=body,
            headers=self._headers(actor_user_id),
        )
        resp.raise_for_status()
        return resp.json()

    def post_look_distributions(self, body: dict, actor_user_id: str) -> dict:
        """POST /api/look_distributions — 制作素材（Look）の担当者への配布・指示"""
        resp = httpx.post(
            f"{self.base_url}/api/look_distributions",
            json=body,
            headers=self._headers(actor_user_id),
        )
        resp.raise_for_status()
        return resp.json()

    def get_assets_by_shot(self, shot_id: int, actor_user_id: str | None = None) -> list:
        """GET /api/assets?shot_id=N — shot 紐付き asset 一覧 (next_version 採番用)"""
        resp = httpx.get(
            f"{self.base_url}/api/assets",
            params={"shot_id": shot_id},
            headers=self._headers(actor_user_id),
        )
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("assets", [])

    def next_version(self, shot_id: int, task_id: int | None = None, actor_user_id: str | None = None) -> str:
        """δ version 採番: 既存 assets から max base_version 抽出→次連番/衝突時 a/b/c サフィックス。
        殿御命 2026-06-03 hotfix: GET /api/assets は Calendar 側 405 未実装ゆえ
        /api/me/shots/{id}.asset_list 経由 (get_shot_detail) で取得。"""
        try:
            shot_dict = self.get_shot_detail(shot_id, actor_user_id=actor_user_id) or {}
            assets = list(shot_dict.get("asset_list", []) or [])
        except Exception:
            assets = []
        if task_id is not None:
            assets = [a for a in assets if a.get("task_id") == task_id]
        versions = set()
        max_base = 0
        for a in assets:
            v = a.get("version", "v000")
            if v and len(v) >= 4 and v[0] == 'v':
                base = v[:4]
                try:
                    num = int(base[1:])
                    max_base = max(max_base, num)
                except ValueError:
                    pass
                versions.add(v)
        next_num = max_base + 1
        candidate = f"v{next_num:03d}"
        if candidate not in versions:
            return candidate
        for c in 'abcdefghijklmnopqrstuvwxyz':
            cand = f"{candidate}{c}"
            if cand not in versions:
                return cand
        return candidate

    def post_asset(self, file_data: bytes, filename: str, content_type: str,
                   actor_user_id: str,
                   task_id: int | None = None,
                   shot_id: int | None = None,
                   version: str | None = None) -> dict:
        """POST /api/assets — QC/review asset upload (multipart)
        nibu 殿仕様確認済 (2026-06-02)。"""
        files = {"file": (filename, file_data, content_type)}
        data: dict[str, str] = {}
        if task_id is not None: data["task_id"] = str(task_id)
        if shot_id is not None: data["shot_id"] = str(shot_id)
        if version: data["version"] = version
        headers = self._headers(actor_user_id)
        resp = httpx.post(
            f"{self.base_url}/api/assets",
            files=files,
            data=data,
            headers=headers,
            timeout=60.0,
        )
        # 殿御命 2026-06-03 debug: 422 等 error 時 body 詳細出力
        if resp.status_code >= 400:
            import sys
            print(f"[post_asset DEBUG] HTTP {resp.status_code} url=/api/assets actor_uid={actor_user_id} X-Actor={headers.get('X-Actor-User-Id')} data={data} filename={filename} ct={content_type} body={resp.text[:600]}", file=sys.stderr, flush=True)
        resp.raise_for_status()
        return resp.json()

    def post_my_avatar(self, file_data: bytes, filename: str, content_type: str,
                       actor_user_id: str) -> dict:
        """POST /api/me/avatar — avatar 画像 upload (nibu 殿仕様 2026-06-02・multipart)"""
        files = {"file": (filename, file_data, content_type)}
        resp = httpx.post(
            f"{self.base_url}/api/me/avatar",
            files=files,
            headers=self._headers(actor_user_id),
            timeout=60.0,
        )
        resp.raise_for_status()
        _r = resp.json()
        # 殿御命 2026-06-10: 返り avatar_url を Calendar 絶対 URL に書換え
        if isinstance(_r, dict) and _r.get("avatar_url"):
            _r["avatar_url"] = self._abs_avatar(_r["avatar_url"])
        return _r

    def patch_task(self, task_id: int, body: dict, actor_user_id: str | None = None) -> dict:
        """殿御命 2026-06-03: task status/progress 更新 pass-through
        2026-06-03 真因解消: Calendar 側は PATCH 未実装 → PUT /api/tasks/{id} が正解 (実機確証 200)"""
        resp = httpx.put(
            f"{self.base_url}/api/tasks/{task_id}",
            json=body,
            headers={**self._headers(actor_user_id), "Content-Type": "application/json"},
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()

    def patch_task_status(self, task_id: int, status: str, actor_user_id: str | None = None) -> dict:
        return self.patch_task(task_id, {"status": status}, actor_user_id=actor_user_id)

    def post_dm_thread(self, participant_ids: list[int], task_id: int | None = None, actor_user_id: str | None = None) -> dict:
        """殿御命 2026-06-04: nibu 殿実装 POST /api/dm/threads — 多人数 thread 作成"""
        body = {"participant_ids": participant_ids}
        if task_id is not None:
            body["task_id"] = task_id
        resp = httpx.post(
            f"{self.base_url}/api/dm/threads",
            json=body,
            headers={**self._headers(actor_user_id), "Content-Type": "application/json"},
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()

    def post_dm(self, thread_id: int, body_text: str, actor_user_id: str | None = None) -> dict:
        """殿御命 2026-06-04: POST /api/dm — 既存 thread にメッセージ送信"""
        resp = httpx.post(
            f"{self.base_url}/api/dm",
            json={"thread_id": thread_id, "body": body_text},
            headers={**self._headers(actor_user_id), "Content-Type": "application/json"},
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()

    def get_my_dm_threads(self, actor_user_id: str | None = None) -> list:
        """殿御命 2026-06-04: GET /api/me/dm/threads — 自分参加全 thread (多人数含)"""
        resp = httpx.get(
            f"{self.base_url}/api/me/dm/threads",
            headers=self._headers(actor_user_id),
            timeout=10.0,
        )
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []

    def delete_asset(self, asset_id: int, actor_user_id: str | None = None) -> dict:
        """殿御命 2026-06-03: DELETE /api/assets/{id} (nibu 殿 6/3 実装完了)
        本人 or admin のみ可・物理削除 + LookDistribution リンク自動 NULL 化"""
        resp = httpx.delete(
            f"{self.base_url}/api/assets/{asset_id}",
            headers=self._headers(actor_user_id),
            timeout=10.0,
        )
        if resp.status_code == 204:
            return {"ok": True, "asset_id": asset_id}
        resp.raise_for_status()
        return {"ok": True, "asset_id": asset_id, "response": resp.text}

    def get_project_roles(self, project_id: int, actor_user_id: str | None = None) -> dict:
        """殿御命 2026-06-05 (nibu Phase 2 EP): GET /api/projects/{id}/roles
        Response: {role_name: user_id, ...} (role 毎 first-wins・例 {"director": 53, "pm": 52})
        404 (project 不在) の場合 {} 返却。"""
        try:
            resp = httpx.get(
                f"{self.base_url}/api/projects/{project_id}/roles",
                headers=self._headers(actor_user_id),
                timeout=10.0,
            )
            if resp.status_code == 404:
                return {}
            resp.raise_for_status()
            return resp.json() or {}
        except Exception:
            return {}

    def get_project_directors(self, project_id: int, actor_user_id: str | None = None) -> list:
        """get_project_roles から director 抽出 (互換 wrapper)"""
        roles = self.get_project_roles(project_id, actor_user_id=actor_user_id) or {}
        d = roles.get("director")
        return [int(d)] if d is not None else []

    def get_project_pms(self, project_id: int, actor_user_id: str | None = None) -> list:
        """get_project_roles から pm 抽出 (互換 wrapper)"""
        roles = self.get_project_roles(project_id, actor_user_id=actor_user_id) or {}
        p = roles.get("pm")
        return [int(p)] if p is not None else []

    def post_notification(self, recipient_id: int, title: str, body: str,
                          notif_type: str = "unread", meta: dict | None = None,
                          actor_user_id: str | None = None) -> dict:
        """殿御命 2026-06-05 (nibu Phase 2 EP): POST /api/notifications
        notif_type: 'mention' | 'notice' | 'unread'"""
        payload = {"recipient_id": recipient_id, "title": title, "body": body, "type": notif_type, "meta": meta or {}}
        resp = httpx.post(
            f"{self.base_url}/api/notifications",
            json=payload,
            headers={**self._headers(actor_user_id), "Content-Type": "application/json"},
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()

    def send_notification_to_users(self, user_ids: list, title: str, body: str,
                                   actor_user_id: str | None = None) -> dict:
        """殿御命 2026-06-05: post_notification を ループ実行 (旧 API 互換 wrapper)"""
        created = []
        for uid in user_ids:
            try:
                uid_int = int(uid)
                r = self.post_notification(uid_int, title, body, "unread", None, actor_user_id=actor_user_id)
                created.append({"recipient_id": uid_int, "id": r.get("id")})
            except Exception as e:
                created.append({"recipient_id": uid, "error": str(e)[:120]})
        return {"ok": True, "created": created}

    def get_dm_thread_messages(self, thread_id: int, actor_user_id: str | None = None) -> list:
        """殿御命 2026-06-05 (nibu Phase 2 EP): GET /api/dm/threads/{id}/messages
        Returns: [{id, thread_id, sender_id, body, created_at, ...}] (created_at 昇順)"""
        resp = httpx.get(
            f"{self.base_url}/api/dm/threads/{thread_id}/messages",
            headers=self._headers(actor_user_id),
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json() or []

    def post_dm_thread_read(self, thread_id: int, actor_user_id: str | None = None) -> dict:
        """殿御命 2026-06-05 (nibu Phase 2 EP): POST /api/dm/threads/{id}/read
        Returns: {thread_id, read_count} (冪等)"""
        resp = httpx.post(
            f"{self.base_url}/api/dm/threads/{thread_id}/read",
            headers=self._headers(actor_user_id),
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()

    def get_event(self, event_id: int, actor_user_id: str | None = None) -> dict:
        """殿御命 2026-06-05 (nibu 御回答): 正規パス /api/calendar/events/{id}"""
        resp = httpx.get(
            f"{self.base_url}/api/calendar/events/{event_id}",
            headers=self._headers(actor_user_id),
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()

    def patch_look_distribution_accept(self, distribution_id: int, actor_user_id: str) -> dict:
        """PATCH /api/look_distributions/{id}/accept — Look 配布の受諾 (nibu 殿御回答 2026-06-01 F 高)"""
        resp = httpx.patch(
            f"{self.base_url}/api/look_distributions/{distribution_id}/accept",
            headers=self._headers(actor_user_id),
        )
        resp.raise_for_status()
        return resp.json()

    def patch_look_distribution_complete(self, distribution_id: int, actor_user_id: str) -> dict:
        """PATCH /api/look_distributions/{id}/complete — Look 配布の完了通知 (nibu 殿御回答 2026-06-01 F 高)"""
        resp = httpx.patch(
            f"{self.base_url}/api/look_distributions/{distribution_id}/complete",
            headers=self._headers(actor_user_id),
        )
        resp.raise_for_status()
        return resp.json()

    def post_timecard_clock_out(self, body: dict, actor_user_id: str) -> dict:
        """POST /api/timecards/clock_out — 制作現場用・簡易退勤打刻
        Score body (mode/clock_out_time/blocker/handover/next_priority) を
        Calendar TimecardCreate (date/clock_out_at/memo/worked_minutes/break_minutes) に変換する。"""
        import re
        jst = timezone(timedelta(hours=9))
        now_jst = datetime.now(jst)
        # date: submitted_at から day part 抽出 (or 今日)
        submitted = body.get("submitted_at") or now_jst.isoformat()
        try:
            sub_dt = datetime.fromisoformat(submitted.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            sub_dt = now_jst
        date_iso = sub_dt.isoformat()
        # clock_out_at: clock_out_time(HH:MM) を date と組合せ
        clock_out_at = None
        cot = body.get("clock_out_time")
        if cot and re.match(r"^\d{2}:\d{2}$", cot):
            hh, mm = cot.split(":")
            clock_out_dt = sub_dt.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
            clock_out_at = clock_out_dt.isoformat()
        # memo: blocker / handover / next_priority を集約
        memo_parts = []
        if body.get("mode"):
            memo_parts.append(f"[mode] {body['mode']}")
        if body.get("blocker"):
            memo_parts.append(f"[作業報告/blocker] {body['blocker']}")
        if body.get("handover"):
            memo_parts.append(f"[申送り] {body['handover']}")
        if body.get("next_priority"):
            memo_parts.append(f"[翌日優先] {body['next_priority']}")
        if body.get("next_priority_memo"):
            memo_parts.append(f"[翌日メモ] {body['next_priority_memo']}")
        calendar_body = {
            "date": date_iso,
            "clock_out_at": clock_out_at,
            "worked_minutes": int(body.get("worked_minutes", 0)),
            "break_minutes": int(body.get("break_minutes", 0)),
            "memo": "\n".join(memo_parts) if memo_parts else None,
        }
        # user_id は Calendar 側で X-Actor-User-Id から自動補完される想定
        resp = httpx.post(
            f"{self.base_url}/api/timecards/clock_out",
            json=calendar_body,
            headers=self._headers(actor_user_id),
        )
        resp.raise_for_status()
        return resp.json()

    def post_routines(self, body: dict, actor_user_id: str) -> dict:
        """POST /api/routines — 朝のルーティン・コンディション報告"""
        resp = httpx.post(
            f"{self.base_url}/api/routines",
            json=body,
            headers=self._headers(actor_user_id),
        )
        resp.raise_for_status()
        return resp.json()

    def post_change_requests(self, body: dict, actor_user_id: str) -> dict:
        """POST /api/change_requests — 締切延長などの各種変更申請の発行"""
        resp = httpx.post(
            f"{self.base_url}/api/change_requests",
            json=body,
            headers=self._headers(actor_user_id),
        )
        resp.raise_for_status()
        return resp.json()

    def post_troubles(self, body: dict, actor_user_id: str) -> dict:
        """POST /api/troubles — 現場で発生したトラブルの報告"""
        resp = httpx.post(
            f"{self.base_url}/api/troubles",
            json=body,
            headers=self._headers(actor_user_id),
        )
        resp.raise_for_status()
        return resp.json()

    def patch_trouble_resolve(self, trouble_id: int, body: dict, actor_user_id: str) -> dict:
        """PATCH /api/troubles/{id}/resolve — 報告済みトラブルの解決済みフラグ立て"""
        resp = httpx.patch(
            f"{self.base_url}/api/troubles/{trouble_id}/resolve",
            json=body,
            headers=self._headers(actor_user_id),
        )
        resp.raise_for_status()
        return resp.json()

    def post_messages(self, body: dict, actor_user_id: str) -> dict:
        """POST /api/messages — 制作チャンネルへのメッセージ投稿"""
        resp = httpx.post(
            f"{self.base_url}/api/messages",
            json=body,
            headers=self._headers(actor_user_id),
        )
        resp.raise_for_status()
        return resp.json()

    def get_messages(self, actor_user_id: str | None = None) -> list:
        """GET /api/me/messages — 自分宛 message 一覧 (api_complete_list_v3 §8)"""
        resp = httpx.get(
            f"{self.base_url}/api/me/messages",
            headers=self._headers(actor_user_id),
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("messages", [])

    def get_dm_threads(self, actor_user_id: str | None = None) -> list:
        """GET /api/me/dm/threads — DM スレッド一覧 (nibu 殿納品確認 2026-05-29)"""
        resp = httpx.get(
            f"{self.base_url}/api/me/dm/threads",
            headers=self._headers(actor_user_id),
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("threads", [])

    def get_users(self, actor_user_id: str | None = None) -> list:
        """GET /api/users — Calendar 全 user 一覧 (id→name 解決用)"""
        resp = httpx.get(
            f"{self.base_url}/api/users",
            headers=self._headers(actor_user_id),
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("users", [])

    def get_timecards(self, actor_user_id: str | None = None,
                       from_date: str | None = None, to_date: str | None = None,
                       limit: int | None = None) -> list:
        """GET /api/me/timecards — actor の打刻履歴 (nibu 殿納品 2026-06-01 最小版)
        response: [{id, user_id, date, clock_out_at, worked_minutes, break_minutes, memo}]
        フル仕様 (type/mode/submitted_at/for_date/fields) は段階実装予定。"""
        params = {}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        if limit:
            params["limit"] = limit
        resp = httpx.get(
            f"{self.base_url}/api/me/timecards",
            params=params,
            headers=self._headers(actor_user_id),
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("timecards", [])

    def get_user_messages(self, shot_id: int | None = None, project_id: int | None = None,
                          author_id: int | None = None, actor_user_id: str | None = None) -> list:
        """GET /api/user_messages — shot/project/author で filter したメッセージ一覧"""
        params = {}
        if shot_id is not None:
            params["shot_id"] = shot_id
        if project_id is not None:
            params["project_id"] = project_id
        if author_id is not None:
            params["author_id"] = author_id
        resp = httpx.get(
            f"{self.base_url}/api/user_messages",
            params=params,
            headers=self._headers(actor_user_id),
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("messages", [])

    def patch_notification_read(self, notification_id: int, body: dict, actor_user_id: str) -> dict:
        """PATCH /api/notifications/{id}/read — 自分宛通知の既読化"""
        resp = httpx.patch(
            f"{self.base_url}/api/notifications/{notification_id}/read",
            json=body,
            headers=self._headers(actor_user_id),
        )
        resp.raise_for_status()
        return resp.json()

    def get_my_projects(self, actor_user_id: str) -> dict:
        """GET /api/me/projects — 自分のプロジェクト一覧取得 (calender_api_complete_list.md §8)"""
        resp = httpx.get(
            f"{self.base_url}/api/me/projects",
            headers=self._headers(actor_user_id),
        )
        resp.raise_for_status()
        return resp.json()

    def get_project(self, project_id: int, actor_user_id: str | None = None) -> dict:
        """殿御命 2026-06-05: GET /api/projects/{id} — admin scope で任意 project 詳細取得
        sender が project 未参加でも proj_name 解決可能にする fallback"""
        try:
            resp = httpx.get(
                f"{self.base_url}/api/projects/{project_id}",
                headers=self._headers(actor_user_id),
                timeout=10.0,
            )
            if resp.status_code == 404:
                return {}
            resp.raise_for_status()
            return resp.json() or {}
        except Exception:
            return {}

    def get_my_project_detail(self, project_id: int, actor_user_id: str | None = None) -> dict:
        """GET /api/me/projects/{project_id} — 単一 project 詳細取得 (Phase A relink A-3)"""
        resp = httpx.get(
            f"{self.base_url}/api/me/projects/{project_id}",
            headers=self._headers(actor_user_id),
        )
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
        return resp.json()

    def get_my_shots(self, actor_user_id: str) -> dict:
        """GET /api/me/shots — 自分のショット一覧取得 (calender_api_complete_list.md §8)"""
        resp = httpx.get(
            f"{self.base_url}/api/me/shots",
            headers=self._headers(actor_user_id),
        )
        resp.raise_for_status()
        return resp.json()

    def get_meetings(self, project_id: int, actor_user_id: str) -> list:
        """GET /api/projects/{id}/meetings — プロジェクト紐付き会議一覧取得 (api_complete_list_v3 §1.1)
        各 record は transcript / decisions / tasks / discussion_points / deadlines を含む。
        AI 提案 + 朝会議事録 表示用。
        """
        resp = httpx.get(
            f"{self.base_url}/api/projects/{project_id}/meetings",
            headers=self._headers(actor_user_id),
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("meetings", [])

    def get_holidays(self, year: int, actor_user_id: str | None = None) -> list:
        """GET /api/holidays?year={year} — 日本の祝日(振替・国民の休日含む) (api_complete_list_v3 §1.2)
        カレンダー表示用。"""
        resp = httpx.get(
            f"{self.base_url}/api/holidays",
            params={"year": year},
            headers=self._headers(actor_user_id) if actor_user_id else {},
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("holidays", [])

    def get_events(self, actor_user_id: str | None = None) -> list:
        """GET /api/me/events — actor の event 一覧 (発注書 v2.1 MOCK-005 / nibu 殿納品 2026-05-29)
        filter: (A) actor が user_ids 含む OR (B) user_ids 空かつ actor がプロジェクトメンバー。"""
        resp = httpx.get(
            f"{self.base_url}/api/me/events",
            headers=self._headers(actor_user_id),
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("events", [])

    def get_meetings_by_event(self, event_id: int, actor_user_id: str) -> list:
        """GET /api/events/{event_id}/meetings — event 紐付き議事録一覧 (api_complete_list_v3 §1.2)
        event modal「📋 議事録を見る」link 用。"""
        resp = httpx.get(
            f"{self.base_url}/api/events/{event_id}/meetings",
            headers=self._headers(actor_user_id),
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("meetings", [])

    def get_meeting(self, meeting_id: int, actor_user_id: str) -> dict:
        """GET /api/meetings/{meeting_id} — 議事録単体詳細 (api_complete_list_v3 §1.2)
        議事録 detail page 用。"""
        resp = httpx.get(
            f"{self.base_url}/api/meetings/{meeting_id}",
            headers=self._headers(actor_user_id),
        )
        resp.raise_for_status()
        return resp.json()

    def post_meeting(self, project_id: int, body: dict, actor_user_id: str) -> dict:
        """POST /api/projects/{id}/meetings — 手動議事録作成・登録 (api_complete_list_v3 §1.2)
        AI 自動抽出以外の経路で議事録を手動投稿する用。"""
        resp = httpx.post(
            f"{self.base_url}/api/projects/{project_id}/meetings",
            json=body,
            headers=self._headers(actor_user_id),
        )
        resp.raise_for_status()
        return resp.json()

    def patch_meeting(self, meeting_id: int, body: dict, actor_user_id: str) -> dict:
        """PATCH /api/meetings/{meeting_id} — 議事録の手動編集 (api_complete_list_v3 §1.2)
        AI 生成後の補正・修正用。"""
        resp = httpx.patch(
            f"{self.base_url}/api/meetings/{meeting_id}",
            json=body,
            headers=self._headers(actor_user_id),
        )
        resp.raise_for_status()
        return resp.json()

    def get_production_tracker(self, project_id: str, actor_user_id: str) -> dict:
        """GET /api/projects/{id}/production-tracker (calender_api_complete_list.md §8)"""
        resp = httpx.get(
            f"{self.base_url}/api/projects/{project_id}/production-tracker",
            headers=self._headers(actor_user_id),
        )
        resp.raise_for_status()
        return resp.json()

    # ── §5-bis profile API (2026-05-25 配備済) ──
    def get_my_profile(self, actor_user_id: str) -> dict:
        """GET /api/me/profile — 自身の全 profile 取得 (settings_json + google 連携 含)"""
        resp = httpx.get(
            f"{self.base_url}/api/me/profile",
            headers=self._headers(actor_user_id),
        )
        resp.raise_for_status()
        _p = resp.json()
        # 殿御命 2026-06-10: avatar_url を Calendar 絶対 URL に書換え (frontend が Calendar から直接取得)
        if isinstance(_p, dict) and _p.get("avatar_url"):
            _p["avatar_url"] = self._abs_avatar(_p["avatar_url"])
        return _p

    def patch_my_profile(self, body: dict, actor_user_id: str) -> dict:
        """PATCH /api/me/profile — 自身の profile 更新
        body: full_name / birthday / bio / phone / line_id / work_start_time / work_end_time / skills / settings_json 等"""
        resp = httpx.patch(
            f"{self.base_url}/api/me/profile",
            json=body,
            headers=self._headers(actor_user_id),
        )
        resp.raise_for_status()
        return resp.json()

    def get_user_profile(self, user_id: int, actor_user_id: str) -> dict:
        """GET /api/users/{id}/profile — 他者 profile (アクセス制御 4 段階: 全員/同 project/admin/本人限定)"""
        resp = httpx.get(
            f"{self.base_url}/api/users/{user_id}/profile",
            headers=self._headers(actor_user_id),
        )
        resp.raise_for_status()
        return resp.json()

    def get_my_tasks(self, actor_user_id: str) -> list:
        """GET /api/me/tasks — 自分が担当しているタスク一覧 (api_complete_list_v3 §8)"""
        resp = httpx.get(
            f"{self.base_url}/api/me/tasks",
            headers=self._headers(actor_user_id),
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("tasks", [])

    def get_my_notifications(self, actor_user_id: str) -> list:
        """GET /api/me/notifications — 自分宛の未読通知一覧(最新 50 件)(api_complete_list_v3 §8)"""
        resp = httpx.get(
            f"{self.base_url}/api/me/notifications",
            headers=self._headers(actor_user_id),
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("notifications", [])

    def get_my_retakes(self, actor_user_id: str) -> list:
        """GET /api/me/retakes — 自分が発行 or 自分担当 SHOT のリテイク一覧 (api_complete_list_v3 §8)"""
        resp = httpx.get(
            f"{self.base_url}/api/me/retakes",
            headers=self._headers(actor_user_id),
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("retakes", [])

    def get_my_troubles(self, actor_user_id: str) -> list:
        """GET /api/me/troubles — 自分が報告 or 担当のトラブル一覧 (api_complete_list_v3 §8)"""
        resp = httpx.get(
            f"{self.base_url}/api/me/troubles",
            headers=self._headers(actor_user_id),
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("troubles", [])

    def get_birthdays_today(self, project_id: int, actor_user_id: str) -> list:
        """GET /api/users/birthdays_today?project_id={id} — 本日誕生日メンバー(生年除外)"""
        resp = httpx.get(
            f"{self.base_url}/api/users/birthdays_today",
            params={"project_id": project_id},
            headers=self._headers(actor_user_id),
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("users", [])
