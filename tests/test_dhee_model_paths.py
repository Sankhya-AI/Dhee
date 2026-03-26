from dhee_shared.model_paths import resolve_model_path


def test_resolve_model_path_prefers_explicit_path(tmp_path):
    explicit = tmp_path / "custom.gguf"
    assert resolve_model_path(str(explicit), model_dir=str(tmp_path)) == str(explicit)


def test_resolve_model_path_respects_env(monkeypatch, tmp_path):
    env_model = tmp_path / "env.gguf"
    monkeypatch.setenv("DHEE_MODEL_PATH", str(env_model))
    assert resolve_model_path(model_dir=str(tmp_path)) == str(env_model)


def test_resolve_model_path_prefers_2b_over_legacy_08b(monkeypatch, tmp_path):
    monkeypatch.delenv("DHEE_MODEL_PATH", raising=False)
    legacy = tmp_path / "dhee-qwen3.5-0.8b-q4_k_m.gguf"
    preferred = tmp_path / "dhee-qwen3.5-2b-q4_k_m.gguf"
    legacy.write_text("legacy")
    preferred.write_text("preferred")

    assert resolve_model_path(model_dir=str(tmp_path)) == str(preferred)


def test_resolve_model_path_falls_back_to_latest_custom_artifact(monkeypatch, tmp_path):
    monkeypatch.delenv("DHEE_MODEL_PATH", raising=False)
    older = tmp_path / "dhee-custom-old.gguf"
    newer = tmp_path / "dhee-custom-new.gguf"
    older.write_text("old")
    newer.write_text("new")

    older.touch()
    newer.touch()

    assert resolve_model_path(model_dir=str(tmp_path)) == str(newer)


def test_resolve_model_path_treats_auto_as_auto(monkeypatch, tmp_path):
    monkeypatch.delenv("DHEE_MODEL_PATH", raising=False)
    preferred = tmp_path / "dhee-qwen3.5-2b-q4_k_m.gguf"
    preferred.write_text("preferred")

    assert resolve_model_path("auto", model_dir=str(tmp_path)) == str(preferred)
