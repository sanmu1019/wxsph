from wxsph_api import Handler


def _handler(headers: dict[str, str] | None = None) -> Handler:
    instance = object.__new__(Handler)
    instance.headers = headers or {}
    return instance


def test_missing_api_key_denies_business_request(monkeypatch) -> None:
    monkeypatch.setattr("wxsph_api.API_KEYS", set())
    assert _handler().authorized() is False


def test_valid_api_key_is_accepted(monkeypatch) -> None:
    monkeypatch.setattr("wxsph_api.API_KEYS", {"test-key"})
    assert _handler({"X-API-Key": "test-key"}).authorized() is True
    assert _handler({"Authorization": "Bearer test-key"}).authorized() is True


def test_query_api_key_is_accepted_for_legacy_clients(monkeypatch) -> None:
    monkeypatch.setattr("wxsph_api.API_KEYS", {"test-key"})
    assert _handler().authorized({"key": ["test-key"]}) is True
