"""cmd_172⑦ (軍師QC172A-4): 自動化印(Calendar送信ヘッダ)の判定材料を、
CalendarClient._headers()(全送信が通る単一の choke point・61箇所)へ、個々の
書込エンドポイントを一つずつ触ることなく伝える。ASGI ミドルウェア
(app.main.ServiceCallContextMiddleware)が各リクエストの先頭でこの値を設定し、
_headers() 側がそれを読む。★観測用の印にすぎず、認可判断はこの値に一切
依存しない(app.deps.get_actor_id が独立に typ="service" を再検証して判断する)
ため、ここでの判定ミスが認可をすり抜けさせることはない。"""
import contextvars

_service_call_var: "contextvars.ContextVar[bool]" = contextvars.ContextVar(
    "score_service_call", default=False
)


def is_service_call() -> bool:
    return _service_call_var.get()


def set_service_call(value: bool) -> None:
    _service_call_var.set(value)
