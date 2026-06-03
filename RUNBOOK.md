# Score-BE 起動・運用手引書

**対象**: `score_be/`（Score バックエンド）  
**作成日**: 2026-05-18  
**作成者**: 足軽1号 (subtask_446a)  
**ステータス**: 初版  
**一次資料**: main.py / requirements.txt / alembic.ini / alembic/versions/ / database.py / auth.py / .env.example / score_system_architecture.md §5

> **本書はScore-BE(score_be/)の運用手引書。Score-FEは対象外。**  
> Score-FE（port 8080）は現時点では未実装（cmd_443 で作成した動作確認用モックのみ）。本格実装はPhase2予定。  
> **制約**: CalendarAPIへの実疎通はPhase6まではMock主導。Score-BEは動作するが、CalendarAPIが返すデータはモックとなる。

---

## ① 前提

### OS / Python バージョン要件

- OS: Linux / macOS / Windows (WSL2 推奨)
- Python: **3.10 以上**（requirements.txt の各パッケージが 3.10+ を前提とする）

### 依存インストール

```bash
cd /mnt/h/multi-agent-shogun-main/score_be
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

依存パッケージ（requirements.txt より確認済）:

| パッケージ | バージョン要件 |
|-----------|-------------|
| fastapi | >=0.109.0 |
| uvicorn[standard] | >=0.27.0 |
| sqlalchemy | >=2.0.0 |
| alembic | >=1.13.0 |
| PyJWT | >=2.8.0 |
| python-dotenv | >=1.0.0 |
| httpx | >=0.25.0 |
| pytest | >=7.0.0 |
| pytest-mock | >=3.0.0 |
| jinja2 | >=3.0.0 |

### 必須環境変数

`.env.example` を参考に `.env` ファイルを作成し、以下の変数を設定する（**実値をこの手引書に書くな**）:

| 変数名 | 説明 | デフォルト |
|--------|------|----------|
| `JWT_SECRET` | JWT署名シークレット。**本番は32バイト以上の乱数文字列必須。** | なし（必須） |
| `CALENDAR_BASE_URL` | CalendarAPI のベースURL | `http://192.168.44.253:8001` |
| `CALENDAR_M2M_TOKEN` | CalendarAPIへのM2Mトークン（本番用） | なし（各自設定） |
| `DATABASE_URL` | SQLAlchemyのDB URL | `sqlite:///./score.db` |

> **セキュリティ注記**: `JWT_SECRET` が未設定だと起動時にエラーではなくリクエスト処理時に `RuntimeError: JWT_SECRET is not set` で落ちる（auth.py 確認済）。**必ず設定すること。** 本番環境では `openssl rand -hex 32` 等で生成した32バイト以上の値を使用すること。

`.env` ファイルの作成例:

```bash
cp score_be/.env.example score_be/.env
# .env を編集して実値を設定する
```

---

## ② 起動順序

起動順序は **殿決裁D-5** (score_system_architecture.md §5) に準拠:

```
① Calendar API  (port 8001)
  ↓
② Score-BE      (port 8100)   ← 本書の対象
  ↓
③ Score-FE      (port 8080)   ← 未実装（モックのみ）
```

> CalendarAPI が未起動でも Score-BE は起動する。各画面でフォールバック動作する（④ヘルスチェック参照）。

### Score-BE 起動コマンド（.venv使用・main.py確認済）

```bash
cd /mnt/h/multi-agent-shogun-main/score_be

# 環境変数を読み込む
source .env  # または export JWT_SECRET=... 等を手動で設定

# uvicorn で起動（main.py で `host=0.0.0.0, port=8100` と定義済）
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8100 --reload
```

### scripts/start_score.sh を使った起動

```bash
cd /mnt/h/multi-agent-shogun-main
bash scripts/start_score.sh
```

start_score.sh の実装（確認済）:

```bash
#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/../score_be"
exec .venv/bin/uvicorn app.main:app \
  --host 0.0.0.0 --port 8100 \
  --log-level info \
  --reload
```

> `start_score.sh` は環境変数を自動読み込みしない。事前に `source .env` または `export` で環境変数を設定しておくこと。

---

## ③ DB初期化 / マイグレーション

### Alembic の設定（alembic.ini 確認済）

- `script_location = alembic`（alembic/ ディレクトリ）
- `sqlalchemy.url = sqlite:///./score.db`（デフォルト。環境変数 `DATABASE_URL` で上書き可）

### DB初期化（初回）

```bash
cd /mnt/h/multi-agent-shogun-main/score_be
source .env  # DATABASE_URL を設定する場合
.venv/bin/alembic upgrade head
```

### 現在のリビジョン確認

```bash
.venv/bin/alembic current
```

### マイグレーションバージョン（alembic/versions/ 実ファイルより確認済）

| リビジョンID | 説明 | 作成日 |
|------------|------|-------|
| `0001` | `score_user_roles` テーブル作成 | 2026-05-16 |

`score_user_roles` テーブル構造（0001_create_score_user_roles.py 確認済）:

| カラム | 型 | 備考 |
|--------|------|------|
| id | INTEGER | PRIMARY KEY |
| user_id | STRING | NOT NULL |
| project_id | STRING | NOT NULL |
| role | STRING | NOT NULL |
| created_at | DATETIME | nullable |

制約: `UNIQUE(user_id, project_id)` として `uq_user_project` が定義されている。

### downgrade（ロールバック）

```bash
# 1つ前のリビジョンへ
.venv/bin/alembic downgrade -1

# 特定リビジョンへ
.venv/bin/alembic downgrade <revision_id>

# 例: 0001 を取り消す（base = 初期状態）
.venv/bin/alembic downgrade base
```

---

## ④ ヘルスチェック

### `/api/health` エンドポイント（main.py 確認済）

```python
@app.get("/api/health")
def health_check():
    return {"status": "ok", "service": "score-be"}
```

### curl によるヘルスチェック

```bash
# Score-BE のヘルスチェック（認証不要）
curl http://localhost:8100/api/health
# → {"status":"ok","service":"score-be"}
```

### JWT トークン生成（ヘルスチェック等で認証付きEPを叩く場合）

auth.py の `create_score_token` を使った Python コマンド:

```bash
cd /mnt/h/multi-agent-shogun-main/score_be
source .env
.venv/bin/python -c "
from app.auth import create_score_token
token = create_score_token('user@example.com')
print(token)
"
```

生成したトークンを使ったリクエスト例:

```bash
TOKEN=<上記で生成したトークン>
curl -H "Authorization: Bearer $TOKEN" http://localhost:8100/dashboard
```

### scripts/health_check.sh を使ったヘルスチェック

```bash
cd /mnt/h/multi-agent-shogun-main
bash scripts/health_check.sh
```

health_check.sh の動作（確認済）:

- `http://localhost:8100/login` で Score-BE を確認
- `http://localhost:8001/api/me` で CalendarAPI を確認
- CalendarAPI が NG でも Score-BE が OK なら終了コード 0（CalendarAPI未起動は既知の状態）

### CalendarAPI 未起動時の動作

Score-BE は CalendarAPI 未起動でも **起動・動作する**。ただし動作は EP 種別により異なる:
- **Calendar 依存 BFF GET**（`/api/bff/me`、`/api/bff/shots` 等）: CalendarAPI 未起動時は HTTP 500（フォールバック無し）
- **ページ動線**（`/dashboard`、`/shot/{id}`、`/qc/{id}`、`/reference/{id}`、`/cross/projects` 等）: CalendarAPI 未起動でも HTTP 200（フォールバック設計・Phase6まではMock主導）

---

## ⑤ 動作確認（物語動線）

### JWT 生成コマンド

```bash
cd /mnt/h/multi-agent-shogun-main/score_be
source .env

# auth.py の create_score_token を使用（確認済）
# トークン有効期限: 次の JST 朝 05:00
.venv/bin/python -c "
from app.auth import create_score_token
print(create_score_token('sato@example.com'))
"
```

または PyJWT を直接使用:

```bash
.venv/bin/python -c "
import jwt, os, datetime, zoneinfo
secret = os.environ['JWT_SECRET']
jst = zoneinfo.ZoneInfo('Asia/Tokyo')
now_jst = datetime.datetime.now(jst)
exp = now_jst.replace(hour=5, minute=0, second=0, microsecond=0)
if now_jst >= exp:
    exp += datetime.timedelta(days=1)
token = jwt.encode({'sub': 'sato@example.com', 'exp': exp}, secret, algorithm='HS256')
print(token)
"
```

### 物語動線（主要EP・main.py + 各ルーター確認済）

ベースURL: `http://localhost:8100`

| 動線ステップ | エンドポイント | 認証 |
|------------|-------------|------|
| ログイン画面 | `GET /login` または `GET /` | 不要 |
| ダッシュボード | `GET /dashboard` | Bearer JWT |
| SHOT詳細 | `GET /shot/{id}` | Bearer JWT |
| QCビューア | `GET /qc/{id}` | Bearer JWT |
| 参考資料 | `GET /reference/{id}` | Bearer JWT |
| メッセージ | `GET /messages` | 不要（ページ表示） |
| 退勤報告 | `GET /exit_report` | 不要（ページ表示） |
| さようなら | `GET /goodbye` | 不要（ページ表示） |

横断画面:

| 動線 | エンドポイント | 認証 |
|------|-------------|------|
| プロジェクト一覧 | `GET /cross/projects` | Bearer JWT |
| 制作トラッカー | `GET /cross/production-tracker/{project_id}` | Bearer JWT |

BFF (API) エンドポイント（bff.py / bff_write.py 確認済）:

| メソッド | エンドポイント | 説明 |
|---------|-------------|------|
| GET | `/api/bff/me` | 自分の情報 |
| GET | `/api/bff/shots` | ショット一覧 |
| GET | `/api/bff/shots/{id}/tasks` | SHOTのタスク一覧 |
| POST | `/api/bff/retakes` | Retake発行 |
| POST | `/api/bff/shots/{id}/approve` | SHOT承認 |
| POST | `/api/bff/look_distributions` | Look配布 |
| POST | `/api/bff/timecards/clock_out` | 退勤 |
| POST | `/api/bff/routines` | ルーティン登録 |
| POST | `/api/bff/change_requests` | 変更申請 |
| POST | `/api/bff/troubles` | トラブル報告 |
| PATCH | `/api/bff/troubles/{id}/resolve` | トラブル解決 |
| POST | `/api/bff/messages` | メッセージ送信 |
| PATCH | `/api/bff/notifications/{id}/read` | 通知既読 |

### curl による動作確認例

```bash
TOKEN=<生成したJWTトークン>

# ヘルスチェック
curl http://localhost:8100/api/health

# ダッシュボード（HTML返却・CalendarAPI未起動でも200）
curl -H "Authorization: Bearer $TOKEN" http://localhost:8100/dashboard

# 自分の情報（JSON）※ Calendar依存EP: CalendarAPI起動時のみ200 / 未起動時500=既知・Phase6まではMock主導
curl -H "Authorization: Bearer $TOKEN" http://localhost:8100/api/bff/me

# ショット一覧（JSON）※ project_id は必須クエリパラメータ / Calendar依存EP: CalendarAPI起動時のみ200 / 未起動時500=既知・Phase6まではMock主導
curl -H "Authorization: Bearer $TOKEN" "http://localhost:8100/api/bff/shots?project_id=1"
```

---

## ⑥ バックアップ / リストア

### scripts/backup_score_db.sh を使ったバックアップ

```bash
cd /mnt/h/multi-agent-shogun-main
bash scripts/backup_score_db.sh
```

backup_score_db.sh の動作（確認済）:

1. `sqlite3 score.db "PRAGMA wal_checkpoint(FULL);"` でWALをチェックポイント
2. `cp score.db backups/score_db_YYYYMMDD_HHMMSS.db` でコピー
3. 7日以上古いバックアップを `find -mtime +7 -delete` で削除

バックアップ格納先: `score_be/../backups/` （= `multi-agent-shogun-main/backups/`）

### リストア手順

```bash
# Score-BE を停止する（⑦ 停止参照）

# バックアップファイルを score.db として配置
cp backups/score_db_YYYYMMDD_HHMMSS.db score_be/score.db

# マイグレーション状態の確認
cd score_be && .venv/bin/alembic current

# Score-BE を再起動
```

### Alembic downgrade を使ったDB状態のロールバック

```bash
cd score_be
# スキーマをロールバック（データも戻る操作のため注意）
.venv/bin/alembic downgrade <revision_id>
```

### WALチェックポイントの意義

SQLite WAL（Write-Ahead Logging）モードでは、コミット済みのデータがWALファイルに蓄積する。チェックポイントを実行することでWALの内容をメインDBファイルに書き込み、バックアップとしてコピーしたファイルが完全なスナップショットになることを保証する。バックアップ前に必ずチェックポイントを実施すること。

---

## ⑦ 停止

### uvicorn プロセスの停止

ターミナルで起動している場合:

```
Ctrl+C
```

バックグラウンドプロセスを停止する場合:

```bash
# PIDを確認
ps aux | grep "uvicorn app.main:app"

# PIDを指定して終了
kill <PID>
```

### 停止前の DB 整合性確保

SQLite WAL モードでは通常のシャットダウン（Ctrl+C）でも整合性は保たれる。ただし重要なデータ変更後はバックアップを推奨:

```bash
bash scripts/backup_score_db.sh
```

その後プロセスを停止する。

---

## ⑧ トラブルシュート

### JWT_SECRET 未設定時のエラー

**症状**: リクエスト処理時に `RuntimeError: JWT_SECRET is not set` が発生する

**原因**: 環境変数 `JWT_SECRET` が設定されていない（auth.py の `_get_secret()` が検出）

**対処**:

```bash
# .env を確認
cat score_be/.env | grep JWT_SECRET

# 手動で設定して再起動
export JWT_SECRET=<32バイト以上の乱数文字列>
bash scripts/start_score.sh
```

乱数文字列の生成例:

```bash
openssl rand -hex 32
```

---

### CalendarAPI 未起動時の動作

**症状**: CalendarAPI に依存する画面でデータが空になる / エラーが表示される

**原因**: CalendarAPI (port 8001) が未起動

**動作**: Score-BE は起動し続ける。CalendarAPI へのリクエストが必要な画面では空レスポンスまたはフォールバックデータを返す（Phase6まではMock主導の設計）。

**確認方法**:

```bash
bash scripts/health_check.sh
# → CalendarAPI (port 8001): NG (CalendarAPI未起動は既知の状態)
```

**対処**: CalendarAPI を先に起動する（起動順序 ①→②→③ を守ること）。

---

### DB マイグレーション失敗時の対処

**症状**: `alembic upgrade head` がエラーで終了する

**確認コマンド**:

```bash
cd score_be
.venv/bin/alembic current   # 現在のリビジョン確認
.venv/bin/alembic history   # マイグレーション履歴確認
```

**対処**:

1. DBファイルが存在するか確認: `ls -la score_be/score.db`
2. DBファイルが壊れている場合はバックアップからリストア（⑥ リストア手順参照）
3. clean な状態で再試行: `rm score_be/score.db && .venv/bin/alembic upgrade head`（データ消去注意）

---

### ポート競合時の対処

**症状**: `ERROR: [Errno 98] Address already in use` が表示されて起動できない

**確認コマンド**:

```bash
# port 8100 を使っているプロセスを確認
ss -tlnp | grep 8100
# または
lsof -i :8100
```

**対処**:

```bash
# 使用中のプロセスを確認して停止
kill <PID>

# または別ポートで起動（一時的な確認用）
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8101 --reload
```

---

## 付録: エンドポイント一覧（実装確認済）

`GET /api/health` — ヘルスチェック（認証不要）  
`GET /` — ログイン画面（alias）  
`GET /login` — ログイン画面  
`GET /routine` — ルーティン画面  
`GET /exit_report` — 退勤報告画面  
`GET /index` — インデックス画面  
`GET /dashboard` — ダッシュボード（JWT必須）  
`GET /shot/{id}` — SHOT詳細（JWT必須）  
`GET /qc/{id}` — QCビューア（JWT必須）  
`GET /reference/{id}` — 参考資料（JWT必須）  
`GET /messages` — メッセージ画面  
`GET /goodbye` — 退勤完了画面  
`GET /cross/projects` — 横断プロジェクト一覧（JWT必須）  
`GET /cross/production-tracker/{project_id}` — 制作トラッカー（JWT必須）  
`GET /api/bff/me` — 自分の情報（JWT必須）  
`GET /api/bff/shots` — ショット一覧（JWT必須）  
`GET /api/bff/shots/{id}/tasks` — SHOTタスク一覧（JWT必須）  
`POST /api/bff/retakes` — Retake発行  
`POST /api/bff/shots/{id}/approve` — SHOT承認  
`POST /api/bff/look_distributions` — Look配布  
`POST /api/bff/timecards/clock_out` — 退勤打刻  
`POST /api/bff/routines` — ルーティン登録  
`POST /api/bff/change_requests` — 変更申請  
`POST /api/bff/troubles` — トラブル報告  
`PATCH /api/bff/troubles/{id}/resolve` — トラブル解決  
`POST /api/bff/messages` — メッセージ送信  
`PATCH /api/bff/notifications/{id}/read` — 通知既読  
