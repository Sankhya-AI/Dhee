from dhee import secret_store


def test_secret_store_can_store_and_rotate_provider_keys(monkeypatch, tmp_path):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path))

    first = "sk-test-openai-1234"
    second = "sk-test-openai-5678"

    stored = secret_store.store_api_key("openai", first)
    key, source, env_var = secret_store.get_api_key("openai")

    assert stored["hasStoredKey"] is True
    assert key == first
    assert source == "stored"
    assert env_var is None
    assert stored["activePreview"] == "****1234"

    rotated = secret_store.rotate_api_key("openai", second)
    key, source, _ = secret_store.get_api_key("openai")

    assert rotated["storedVersionsCount"] == 2
    assert rotated["activePreview"] == "****5678"
    assert key == second
    assert source == "stored"

    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-9999")
    env_key, env_source, env_var = secret_store.get_api_key("openai")
    status = secret_store.get_provider_status("openai")

    assert env_key == "sk-env-9999"
    assert env_source == "env"
    assert env_var == "OPENAI_API_KEY"
    assert status["activeSource"] == "env"
