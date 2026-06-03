import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

_DIR = Path(__file__).parent
JST = timezone(timedelta(hours=9))


def get_translator(lang: str = "ja") -> dict:
    lang = lang if lang in ("ja", "en") else "ja"
    path = _DIR / f"{lang}.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def t(key: str, translations: dict, **kwargs) -> str:
    val = translations.get(key, key)
    return val.format(**kwargs) if kwargs else val


def get_time_greeting_key(now: datetime | None = None) -> tuple[str, str]:
    """JST 時刻で挨拶 i18n key 接尾辞と emoji を返す。
    5:00-10:59 → ('morning', '☀️')
    11:00-17:59 → ('afternoon', '🌤')
    18:00-4:59 → ('evening', '🌙')
    """
    h = (now or datetime.now(JST)).astimezone(JST).hour
    if 5 <= h < 11:
        return ("morning", "☀️")
    if 11 <= h < 18:
        return ("afternoon", "🌤")
    return ("evening", "🌙")
