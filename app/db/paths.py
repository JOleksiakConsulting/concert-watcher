from pathlib import Path

from sqlalchemy.engine import make_url


def ensure_sqlite_directory(database_url: str) -> None:
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite" and url.database not in {None, "", ":memory:"}:
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)
