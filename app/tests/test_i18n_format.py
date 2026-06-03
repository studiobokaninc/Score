from app.i18n import t


def test_format_with_name():
    trans = {"dashboard.greeting": "おはよう、{name} さん"}
    assert t("dashboard.greeting", trans, name="ryoji") == "おはよう、ryoji さん"


def test_format_without_kwargs_backward_compat():
    trans = {"key": "hello"}
    assert t("key", trans) == "hello"


def test_format_missing_key_returns_key():
    assert t("missing.key", {}) == "missing.key"


def test_format_name_empty_string():
    trans = {"dashboard.greeting": "おはよう、{name} さん"}
    assert t("dashboard.greeting", trans, name="") == "おはよう、 さん"
