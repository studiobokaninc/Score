# Score (score_be) — 制作現場向け BFF / SSR フロントエンド

最終更新: 2026-06-10

Studio Bokan の制作現場（VFX/CG）向け業務アプリ。**Calendar（別サービス）を唯一の真実源(source of truth)とする BFF + サーバサイドレンダリング(SSR) フロントエンド**。
ユーザーの daily フロー（出勤→タスク→QC/Review→退勤）を一枚で回す。

---

## 1. アーキテクチャ概要

```
ブラウザ ── HTTP ──▶ Score (FastAPI + Jinja2 SSR)  ──▶ Calendar API (本番 source of truth)
                         │
                         ├─ score.db (SQLite)  … Score 固有データのみ
                         └─ /tmp/* (mock state, push subs, prefs 等)
```

- **Framework**: FastAPI + Jinja2（`cache_size=0` で常に最新テンプレ配信）+ Tailwind(CDN)
- **データ方針**: 業務データ（project/shot/task/asset/timecard/routine 等）は **Calendar が保持**。Score は Calendar API を叩いて表示・操作する BFF。
- **Score 固有 DB(score.db)** が持つのは Score 内部データのみ（後述）。

---

## 2. 起動方法

```bash
# real mode (本番 Calendar 接続) — .env の CALENDAR_MOCK=0 が効く
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8201

# 注: app/main.py が load_dotenv(override=True) するため .env が shell 環境変数より優先される
```

> 運用メモ: `--reload` は過去に多数の漏洩プロセスで inotify を枯渇させた事故あり。安定運用では `--reload` を外し、コード変更時に手動再起動する。

### 主な環境変数 (.env)

| 変数 | 用途 |
|------|------|
| `CALENDAR_MOCK` | `1`=MockCalendarClient(/tmp state) / `0`=実 Calendar 接続 |
| `CALENDAR_BASE_URL` | 実 Calendar の base URL (例 http://192.168.44.253:8001) |
| `SCORE_CALENDAR_ADMIN_EMAIL` / `_PASSWORD` | real mode の admin token 取得用 |
| `JWT_SECRET` | Score の session JWT(`score_token` cookie) 署名鍵 |
| `DATABASE_URL` | score.db (既定 `sqlite:///./score.db`) |
| `SCORE_PUBLIC_URL` | push 通知リンク等の絶対 URL 生成元 |
| `CALENDAR_WEBHOOK_SECRET` | Calendar→Score webhook の HMAC 検証鍵 |
| `VAPID_PUBLIC_KEY` / `_PRIVATE_KEY` / `VAPID_CLAIM_SUB` | Web Push (VAPID) |
| `CALENDAR_MOCK_STATE_FILE` / `CALENDAR_MOCK_RESET` | mock の state file 制御 |

---

## 3. 認証 / 認可

- **session**: ログイン(`POST /api/auth/login`, form `username`=email) → `score_token` (JWT, httponly cookie)。
- **`get_actor_id`** (`app/deps.py`): cookie の JWT sub(email) → `resolve_email_to_user_id` で **Calendar uid** に解決して返す。
- **`get_actor_role`**: actor の role(admin/director/pm/lead/user 等) を返す。
- **Calendar 呼び出し** (real mode): **admin token + ヘッダ `X-Actor-User-Id`(操作者の Calendar uid)**。Calendar 側は role を enforce せず、`X-Actor-User-Id` は attribution(操作者記録)に使う。
- **role guard**: 一部 director 専用ページ(`/director_*`)に guard。`admin`/`pm`/委任 user は通す（後述 QC 代理権限）。

---

## 4. 主要ページ (SSR / GET)

| Path | 内容 |
|------|------|
| `/login` `/routine` `/exit_report` | ログイン / 出勤(体調) / 退勤(申し送り) |
| `/dashboard` | 本日の action item・タスク・受信 QC/Review 依頼 |
| `/projects` `/project_detail/{id}` | プロジェクト一覧(自分アサインの進行中のみ表示) / 詳細 |
| `/shot/{id}` `/task/{id}` | SHOT / Task 詳細 |
| `/qc/{id}` | QC ビューア(asset 比較・Approve/Retake・上申) |
| `/director_retake_input` `/retake_view/{shot}/{task}` | Retake 発行 / Retake 内容 view |
| `/messages` | SHOT thread + DM(メッセージ) |
| `/notification_center` `/settings/notifications` | 通知センター / 通知設定 |
| `/calendar` | カレンダー |
| `/profile` | プロフィール(アバター編集・各種設定) |
| `/bug_report` | バグ報告(試運転フェーズの FB 回収) |
| `/meeting_minutes/{id}` | 議事録 |
| role別: `/director_dashboard` `/pm_dashboard` `/pm_delivery` `/lead_dashboard` `/kato_troubleshoot` | |

---

## 5. BFF エンドポイント (`/api/bff/*`) — Calendar への pass-through 中心

- 読取: `/api/bff/me` `/shots` `/shots/{id}/tasks` `/dm/threads_meta` `/dm/threads/{id}/messages` 等
- 書込: `/assets`(QC/Review 提出) `/retakes` `/shots/{id}/approve` `/qc/approve` `/dm` `/dm/threads` `/me/avatar` `/timecards/clock_out` `/routines` `/tasks/{id}` 等
- 通知: `/push/subscribe`(VAPID) `/notifications/stream`(SSE) `/notifications/{id}/read`
- Score 独自: `/api/bug_reports`(POST) `/api/bug_reports/export.csv`(admin DL)

> リアルタイム性方針: events/tasks/assets の主要 read EP は **cache 無し pass-through** を維持（隠れ async/cache 層の無断追加禁止）。

---

## 6. Score 固有 DB (score.db / SQLAlchemy)

業務データは Calendar 持ち。score.db は **Score 内部データのみ**:

| テーブル | 用途 |
|----------|------|
| `score_user_roles` | Score 側 role 補完 |
| `bug_reports` | バグ報告(件名/詳細/severity/操作ログ/UA/status)。admin が CSV export |
| `qc_delegations` | QC/Review 代理権限(案A)。依頼で mention された user に、その依頼1件限定で Approve/Retake を委任(依頼単位・resolved で失効) |
| `routine_logs` | 出勤時刻 + 体調(condition) の保存(取りこぼし防止) |
| `timecard_logs` | 退勤 + 申し送り(handover)/課題(blocker)/翌日優先 の保存 |

起動時 `Base.metadata.create_all` + 既存テーブルへの idempotent ALTER(`app/main.py`)。

---

## 7. 主要機能

- **通知 三重立て**: Web Push(VAPID/Service Worker root scope) + SSE(`/api/bff/notifications/stream`) + 画面 badge。user 別 ON/OFF。
- **QC/Review/Retake フロー**: asset 提出(`submission_type=qc|review`+mentions) → SHOT thread に「🔍 QC 依頼/📌 Review 依頼」投稿 + 通知。判定者が Approve/Retake。
- **QC 代理権限(案A)**: Dir 不在時、依頼で mention された user がその依頼に限り Approve/Retake 可(`qc_delegations`)。実承認は admin token + X-Actor-User-Id で Calendar が受理・代理 user 名義で記録。
- **アバター**: Calendar が `/static/uploads/avatars/{uuid}.ext`(無認証)で配信。Score は相対 URL を **Calendar 絶対 URL に書換え**て `<img>` 表示。
- **出退勤/体調/申し送り**: Calendar(`/api/timecards/clock_out`, `/api/routines`)へ送信 + score.db にも保存。
- **多言語(i18n)**: `t()` + translate="no" 制御。

### ステータス・モデル（Calendar が真実源・2026-06-10 実データ確認）

- **SHOT status** = ライフサイクル。既定 **`planning`** → `in_progress` …（QC/Review/Retake は **付かない**）。
- **TASK status** = 作業状態。`in-progress`(ハイフン) / `reviewing` / `review` / `retake` / `approved` / `completed` / `delayed` / `open` / `todo` 等。**QC/Review/Retake/Approved は TASK 単位**。
- **PROJECT status** = `in-progress` / `completed` / `cancelled` 等。
- ※ 正式な enum は Calendar schema が真実源（Calendar README §3 で明文化依頼中）。

---

## 8. Calendar 連携 / アダプタ

- `app/adapters/calendar_client.py` … 実 `CalendarClient`(admin JWT + X-Actor-User-Id)
- `app/adapters/mock_calendar_client.py` … `MockCalendarClient`(`/tmp/score_mock_state.yaml`)
- `app/adapters/calendar_factory.py` … `CALENDAR_MOCK` で切替
- Calendar→Score **webhook**: `POST /api/bff/webhook/calendar`(HMAC-SHA256, RAW hex, ヘッダ `X-Calendar-Signature`) → SSE 配信。

---

## 9. 既知の制約 / 保留

- **ダークテーマ**: 設定肢はあるが dark mode CSS 未実装(機能せず)。
- **体調/routine の表示**: Calendar に永続化+GET あり。Score 側のビューは未結線(データは score.db に保存中)。
- **アバター切り抜き**: 編集ズーム/位置は localStorage のみ(サイドメニュー未適用)。
- 一部画面に旧デモ/seed データ由来の表示が残る場合あり(Calendar seed の整理は Calendar 側案件)。

---

## 10. ディレクトリ

```
app/
  main.py            … FastAPI app, static/uploads mount, DB init, router include
  deps.py            … get_actor_id / get_actor_role / get_db
  auth.py            … JWT(score_token)
  database.py models.py … SQLAlchemy
  qc_delegation.py   … QC 代理権限ヘルパー
  adapters/          … Calendar client (real/mock/factory)
  routers/           … pages_*(SSR) / bff*(BFF) / auth_login / sse_notifications
  templates/         … Jinja2 (+ _sidemenu.html 共通)
  static/            … sw.js, push-register.js, notif-client.js, css 等
tutorial/            … 機能チュートリアル(静的)
```
