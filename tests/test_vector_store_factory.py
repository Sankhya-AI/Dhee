from dhee.utils.factory import _normalize_sqlite_vec_config


def test_normalize_sqlite_vec_config_keeps_file_paths():
    cfg = {"path": "/tmp/dhee/sqlite_vec.db", "collection_name": "x"}
    normalized = _normalize_sqlite_vec_config(cfg)
    assert normalized["path"] == "/tmp/dhee/sqlite_vec.db"
    assert normalized["collection_name"] == "x"


def test_normalize_sqlite_vec_config_converts_directory_paths():
    cfg = {"path": "/tmp/dhee/zvec"}
    normalized = _normalize_sqlite_vec_config(cfg)
    assert normalized["path"] == "/tmp/dhee/zvec/sqlite_vec.db"
