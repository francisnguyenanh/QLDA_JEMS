"""
Lightweight schema migration runner.
Each migration is a (version, description, sql) tuple.
The runner tracks the applied version in a `schema_version` table.
"""
import sqlite3
import logging

# ── Migration definitions — append only, never edit existing entries ──────────
MIGRATIONS = [
    (1, "Initial schema", None),   # placeholder: original tables created by init_db()

    (2, "Add performance indexes", """
        CREATE INDEX IF NOT EXISTS idx_projects_status    ON projects(ステータス);
        CREATE INDEX IF NOT EXISTS idx_projects_dev_done  ON projects(開発完了);
        CREATE INDEX IF NOT EXISTS idx_projects_test_start ON projects(テスト開始日);
        CREATE INDEX IF NOT EXISTS idx_projects_unnecessary ON projects(不要);
        CREATE INDEX IF NOT EXISTS idx_todos_date         ON todos(date);
        CREATE INDEX IF NOT EXISTS idx_todos_parent       ON todos(parent_id);
        CREATE INDEX IF NOT EXISTS idx_daily_hours_proj   ON daily_hours(project_id, date);
        CREATE INDEX IF NOT EXISTS idx_proj_history_proj  ON project_history(project_id);
        CREATE INDEX IF NOT EXISTS idx_memo_files_memo    ON memo_files(memo_id);
    """),

    (3, "Add 並行テスト column (if missing)", """
        -- no-op guard: column already added via PRAGMA in init_db
        SELECT 1;
    """),
]


def _ensure_version_table(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version     INTEGER PRIMARY KEY,
            description TEXT,
            applied_at  TEXT DEFAULT (datetime('now','localtime'))
        )
    """)


def get_current_version(conn):
    cursor = conn.cursor()
    _ensure_version_table(cursor)
    cursor.execute("SELECT MAX(version) FROM schema_version")
    row = cursor.fetchone()
    return row[0] or 0


def run_migrations(db_file: str):
    """Apply any pending migrations and return the final schema version."""
    conn = sqlite3.connect(db_file)
    try:
        cursor = conn.cursor()
        _ensure_version_table(cursor)
        conn.commit()

        current = get_current_version(conn)
        applied = 0

        for version, description, sql in MIGRATIONS:
            if version <= current:
                continue
            logging.info(f"[migration] Applying v{version}: {description}")
            if sql:
                # SQLite doesn't support multiple statements in execute(); split on ';'
                for statement in sql.split(';'):
                    stmt = statement.strip()
                    if stmt:
                        try:
                            cursor.execute(stmt)
                        except sqlite3.OperationalError as e:
                            # INDEX already exists or similar harmless errors
                            logging.warning(f"[migration] v{version} statement warning: {e}")
            cursor.execute(
                "INSERT OR IGNORE INTO schema_version (version, description) VALUES (?, ?)",
                (version, description),
            )
            conn.commit()
            applied += 1
            logging.info(f"[migration] v{version} applied.")

        if applied == 0:
            logging.debug("[migration] Schema up-to-date.")
        return get_current_version(conn)
    finally:
        conn.close()
