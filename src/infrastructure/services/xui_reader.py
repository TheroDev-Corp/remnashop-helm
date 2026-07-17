import sqlite3
from pathlib import Path


class XuiDbReaderImpl:
    async def read_inbounds(self, db_path: Path) -> list[tuple[int, str]]:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, settings FROM inbounds")
            return cursor.fetchall()
