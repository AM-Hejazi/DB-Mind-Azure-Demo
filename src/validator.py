"""Compatibility execution entrypoint. All active queries use Database transport."""
from pathlib import Path
import re

from src.database import Database, DatabaseError
from src.settings import SettingsError


def validate_sql(sql):
    """Preserve (rows, columns, feedback); every caller uses the same AST gate."""
    try:
        rows, columns = Database().query(sql)
        return rows, columns, None
    except (DatabaseError, SettingsError) as error:
        return None, None, f"[DATABASE ERROR:{getattr(error, 'code', 'configuration')}] {error}"


def log_feedback(tag, content, timestamp):
    """Legacy explicit feedback writer; importing this module creates no files."""
    if not all(re.fullmatch(r"[A-Za-z0-9_-]+", value) for value in (tag, timestamp)):
        raise ValueError("Invalid feedback filename")
    folder = Path("logs")
    folder.mkdir(exist_ok=True)
    (folder / f"{tag}_{timestamp}.txt").write_text(content, encoding="utf-8")
