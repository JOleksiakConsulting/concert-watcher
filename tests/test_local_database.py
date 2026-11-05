from pathlib import Path

from app.db.paths import ensure_sqlite_directory


def test_sqlite_directory_created_for_relative_path_with_query(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ensure_sqlite_directory("sqlite+aiosqlite:///./data/nested/concert.db?timeout=30")
    assert Path("data/nested").is_dir()
    assert not Path("data/nested/concert.db").exists()


def test_memory_database_needs_no_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ensure_sqlite_directory("sqlite+aiosqlite:///:memory:")
    assert list(tmp_path.iterdir()) == []
