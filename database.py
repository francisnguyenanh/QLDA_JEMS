"""
database.py — all SQLite operations in one place (B1 extract from app.py).

Includes a simple TTL cache for read_projects() (P3).
"""
import sqlite3
import logging
import time
import threading
from datetime import datetime, timedelta

import pandas as pd
from pandas import isna

from config import (
    DB_FILE, DISPLAY_COLUMNS, DATE_COLUMNS_DB, VALID_STATUSES, DEFAULT_PAGE_SIZE,
    PROJECTS_CACHE_TTL,
)
from services.date_calc import (
    calculate_status, calculate_test_completion_date, calculate_fb_completion_date,
)


# ── Schema / init ─────────────────────────────────────────────────────────────

def init_db():
    """Create all tables and add missing columns (idempotent)."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            SE TEXT, 案件名 TEXT, PH TEXT,
            "開発工数（h）" REAL, "設計工数（h）" REAL,
            要件引継 TEXT, 設計開始 TEXT, 設計完了 TEXT, 設計書送付 TEXT,
            開発開始 TEXT, 開発完了 TEXT, SE納品 TEXT, BSE TEXT,
            案件番号 TEXT, "PJNo." TEXT, 備考 TEXT,
            テスト開始日 TEXT, テスト完了日 TEXT, FB完了予定日 TEXT,
            ページ数 INTEGER, タスク TEXT, ステータス TEXT,
            不要 INTEGER DEFAULT 0, 注文設計 INTEGER DEFAULT 0,
            注文テスト INTEGER DEFAULT 0, 注文FB INTEGER DEFAULT 0,
            注文BrSE INTEGER DEFAULT 0, user_edited_status INTEGER DEFAULT 0
        )
    ''')

    for table_sql in [
        '''CREATE TABLE IF NOT EXISTS copied_templates (
               id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER,
               filename TEXT, copied_at TEXT,
               FOREIGN KEY (project_id) REFERENCES projects(id))''',
        '''CREATE TABLE IF NOT EXISTS daily_hours (
               id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER,
               date TEXT, task_type TEXT, hours REAL,
               FOREIGN KEY (project_id) REFERENCES projects(id))''',
        '''CREATE TABLE IF NOT EXISTS todos (
               id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
               date TEXT NOT NULL, priority TEXT DEFAULT 'medium',
               completed INTEGER DEFAULT 0, repeat_type TEXT DEFAULT 'none',
               repeat_interval INTEGER DEFAULT 1, repeat_unit TEXT DEFAULT 'days',
               end_date TEXT, parent_id INTEGER,
               created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
               FOREIGN KEY (parent_id) REFERENCES todos(id))''',
        '''CREATE TABLE IF NOT EXISTS schedule_done_status (
               id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER,
               date_column TEXT, done INTEGER DEFAULT 0,
               FOREIGN KEY (project_id) REFERENCES projects(id))''',
        '''CREATE TABLE IF NOT EXISTS editor_document (
               id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
               content TEXT NOT NULL,
               created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
               updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''',
        '''CREATE TABLE IF NOT EXISTS memo (
               id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
               content TEXT NOT NULL,
               created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
               updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''',
        '''CREATE TABLE IF NOT EXISTS memo_files (
               id INTEGER PRIMARY KEY AUTOINCREMENT, memo_id INTEGER,
               filename TEXT NOT NULL, original_filename TEXT NOT NULL,
               file_type TEXT NOT NULL, file_size INTEGER,
               uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
               FOREIGN KEY (memo_id) REFERENCES memo(id))''',
        '''CREATE TABLE IF NOT EXISTS project_history (
               id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
               action_type TEXT NOT NULL, action_details TEXT,
               created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
               FOREIGN KEY (project_id) REFERENCES projects(id))''',
    ]:
        cursor.execute(table_sql)

    # Add columns if missing
    def _add_col(table, col, col_type='TEXT'):
        cursor.execute(f'PRAGMA table_info({table})')
        if col not in [r[1] for r in cursor.fetchall()]:
            cursor.execute(f'ALTER TABLE {table} ADD COLUMN "{col}" {col_type}')

    _add_col('projects', 'ステータス')
    cursor.execute("UPDATE projects SET ステータス='要件引継待ち' WHERE ステータス IS NULL")
    _add_col('projects', '不要', 'INTEGER DEFAULT 0')
    _add_col('projects', '注文設計', 'INTEGER DEFAULT 0')
    _add_col('projects', '注文テスト', 'INTEGER DEFAULT 0')
    _add_col('projects', '注文FB', 'INTEGER DEFAULT 0')
    _add_col('projects', '注文BrSE', 'INTEGER DEFAULT 0')
    _add_col('projects', 'user_edited_status', 'INTEGER DEFAULT 0')
    _add_col('projects', 'SE(sub)')
    _add_col('projects', '並行テスト', 'INTEGER DEFAULT 0')
    _add_col('todos', 'project_id')

    conn.commit()
    conn.close()


# ── Auth ──────────────────────────────────────────────────────────────────────

def read_users() -> dict:
    """Load username→password map from users.txt; create default if missing."""
    try:
        with open('users.txt', 'r', encoding='utf-8') as f:
            return {u: p for u, p in (line.strip().split(':', 1) for line in f if ':' in line)}
    except FileNotFoundError:
        with open('users.txt', 'w', encoding='utf-8') as f:
            f.write('admin:admin123\n')
        return {'admin': 'admin123'}


# ── History ───────────────────────────────────────────────────────────────────

def add_project_history(project_id: int, action_type: str, action_details: str = ''):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM project_history WHERE project_id = ?', (project_id,))
        if cursor.fetchone():
            cursor.execute(
                "UPDATE project_history SET action_type=?, action_details=?, created_at=datetime('now','localtime') WHERE project_id=?",
                (action_type, action_details, project_id),
            )
        else:
            cursor.execute(
                "INSERT INTO project_history (project_id, action_type, action_details, created_at) VALUES (?, ?, ?, datetime('now','localtime'))",
                (project_id, action_type, action_details),
            )
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f'[history] {e}')


# ── Duplicate detection ───────────────────────────────────────────────────────

def project_exists(cursor, project: dict):
    """Return (existing_project_dict, match_level) or (None, 0)."""
    exclude = " AND (不要 IS NULL OR 不要=0 OR 不要='0')"

    key_groups = [
        (['案件名', 'PH', 'PJNo.', '案件番号'], 4),
        (['案件名', 'PH', 'PJNo.'], 3),
        (['案件名', 'PH'], 2),
    ]
    for keys, level in key_groups:
        conds, vals = [], []
        for k in keys:
            v = project.get(k, '')
            if v is None or (isinstance(v, str) and not v.strip()) or isna(v):
                break
            conds.append(f'"{k}"=?')
            vals.append(str(v))
        else:
            if len(conds) == len(keys):
                cursor.execute(f'SELECT * FROM projects WHERE {" AND ".join(conds)}{exclude}', vals)
                row = cursor.fetchone()
                if row:
                    cols = [d[0] for d in cursor.description]
                    return dict(zip(cols, row)), level

    for k in ['案件名', 'PH', 'PJNo.', '案件番号']:
        v = project.get(k, '')
        if v and not isna(v):
            cursor.execute(f'SELECT * FROM projects WHERE "{k}"=?{exclude}', [str(v)])
            row = cursor.fetchone()
            if row:
                cols = [d[0] for d in cursor.description]
                return dict(zip(cols, row)), 1

    return None, 0


def compare_projects(existing: dict, new_proj: dict) -> dict:
    """Return dict of {field: {old, new}} for changed fields."""
    excluded = {'id', 'user_edited_status', 'ステータス'}
    diffs = {}
    for field in DISPLAY_COLUMNS:
        if field in excluded or field not in new_proj:
            continue
        old = existing.get(field, '') or ''
        new = new_proj.get(field, '') or ''
        old = '' if str(old).lower() == 'nan' else str(old).strip()
        new = '' if str(new).lower() == 'nan' else str(new).strip()
        if old != new:
            diffs[field] = {'old': old, 'new': new}
    return diffs


# ── TTL cache for read_projects (P3) ─────────────────────────────────────────

_cache_lock = threading.Lock()
_projects_cache: dict | None = None          # cached DataFrame
_projects_cache_time: float = 0.0            # epoch seconds


def invalidate_projects_cache():
    global _projects_cache, _projects_cache_time
    with _cache_lock:
        _projects_cache = None
        _projects_cache_time = 0.0


def read_projects(use_cache: bool = True):
    """Return DataFrame of all projects with hours + history.
    Results are cached for PROJECTS_CACHE_TTL seconds (P3).
    """
    global _projects_cache, _projects_cache_time
    if use_cache:
        with _cache_lock:
            if _projects_cache is not None and (time.time() - _projects_cache_time) < PROJECTS_CACHE_TTL:
                return _projects_cache.copy()

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM projects')
    rows = cursor.fetchall()
    cols = [d[0] for d in cursor.description]

    projects = []
    for row in rows:
        p = dict(zip(cols, row))

        cursor.execute(
            'SELECT task_type, SUM(hours) FROM daily_hours WHERE project_id=? GROUP BY task_type',
            (p['id'],),
        )
        hours = {r[0]: r[1] for r in cursor.fetchall()}
        p['設計実績']  = hours.get('設計', 0)
        p['テスト実績'] = hours.get('テスト', 0)
        p['FB実績']    = hours.get('FB', 0)
        p['BrSE実績']  = hours.get('BrSE', 0)

        cursor.execute(
            'SELECT action_type, action_details FROM project_history WHERE project_id=? ORDER BY created_at DESC LIMIT 1',
            (p['id'],),
        )
        hist = cursor.fetchone()
        if hist:
            atype, adetails = hist
            p['履歴'] = {
                'created':      '新規作成',
                'updated':      f'{adetails}を更新' if adetails else '更新',
                'excel_updated': f'{adetails}を更新 (Excel)',
                'mail_sent':    f'メール送信: {adetails}',
                'copied':       'プロジェクトコピー',
            }.get(atype, adetails or atype)
        else:
            p['履歴'] = ''

        projects.append(p)

    conn.close()
    df = pd.DataFrame(projects) if projects else pd.DataFrame(columns=cols)

    with _cache_lock:
        _projects_cache = df
        _projects_cache_time = time.time()

    return df.copy()


# ── Update project ────────────────────────────────────────────────────────────

def update_project(project_id: int, updates: dict):
    """Validate, compute derived fields, and persist project updates."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    current_date = datetime.now()

    # Normalise SE(sub)
    if 'SE(sub)' in updates and updates['SE(sub)']:
        updates['SE(sub)'] = updates['SE(sub)'].split()[0]

    # Normalise page count
    if 'ページ数' in updates:
        try:
            v = int(updates['ページ数'])
            updates['ページ数'] = v if v > 0 else None
        except (ValueError, TypeError):
            updates['ページ数'] = None

    # Normalise task list
    valid_tasks = {'設計', 'Brse', 'テスト', 'FB'}
    tasks = [t.strip() for t in (updates.get('タスク', '') or '').split(',') if t.strip() in valid_tasks]
    updates['タスク'] = ','.join(tasks)

    # Normalise checkboxes
    for f in ['不要', '注文設計', '注文テスト', '注文FB', '注文BrSE', '並行テスト']:
        if f in updates:
            updates[f] = 1 if updates[f] == 'on' else 0

    # Delete todos if project marked 不要
    if updates.get('不要') == 1:
        cursor.execute('DELETE FROM todos WHERE project_id=?', (project_id,))

    # Status / user_edited_status
    if 'ステータス' in updates and updates['ステータス'] in VALID_STATUSES:
        updates['user_edited_status'] = 1
    else:
        updates['user_edited_status'] = 0

    # Auto-compute test dates from 開発完了
    if '開発完了' in updates:
        dev = updates['開発完了']
        if dev:
            try:
                dev_dt = datetime.strptime(dev, '%Y-%m-%d')
                ts = dev_dt + timedelta(days=1)
                if ts.weekday() == 5:
                    ts += timedelta(days=2)
                elif ts.weekday() == 6:
                    ts += timedelta(days=1)
                updates['テスト開始日'] = ts.strftime('%Y-%m-%d')
            except ValueError:
                updates['テスト開始日'] = ''
        else:
            updates['テスト開始日'] = ''

    page_count     = updates.get('ページ数')
    test_start     = updates.get('テスト開始日', '')
    test_end_form  = updates.get('テスト完了日', '')
    fb_end_form    = updates.get('FB完了予定日', '')

    if test_end_form:
        try:
            datetime.strptime(test_end_form, '%Y-%m-%d')
        except ValueError:
            test_end_form = ''
    updates['テスト完了日'] = test_end_form or (
        calculate_test_completion_date(page_count, test_start) if page_count and test_start else ''
    )

    if fb_end_form:
        try:
            datetime.strptime(fb_end_form, '%Y-%m-%d')
        except ValueError:
            fb_end_form = ''
    updates['FB完了予定日'] = fb_end_form or (
        calculate_fb_completion_date(updates['テスト完了日']) if updates['テスト完了日'] else ''
    )

    if updates.get('user_edited_status', 0) == 0:
        updates['ステータス'] = calculate_status(updates, current_date)

    # Detect changed fields for history
    cursor.execute('SELECT * FROM projects WHERE id=?', (project_id,))
    row = cursor.fetchone()
    old = dict(zip([d[0] for d in cursor.description], row)) if row else {}

    numeric_fields  = {'開発工数（h）', '設計工数（h）', 'ページ数'}
    checkbox_fields = {'不要', '注文設計', '注文テスト', '注文FB', '注文BrSE', '並行テスト'}
    excluded_fields = {'user_edited_status', 'ステータス', 'id'}
    changed = []

    for k, new_v in updates.items():
        if k in excluded_fields:
            continue
        old_v = old.get(k, '')
        if k in numeric_fields:
            old_n = float(old_v) if old_v not in (None, '', 'None') else 0
            new_n = float(new_v) if new_v not in (None, '', 'None') else 0
            if old_n != new_n:
                changed.append(k)
        elif k in checkbox_fields:
            if int(old_v or 0) != int(new_v or 0):
                changed.append(k)
        else:
            old_s = '' if old_v is None or (isinstance(old_v, float) and isna(old_v)) else str(old_v).strip()
            new_s = '' if new_v is None or (isinstance(new_v, float) and isna(new_v)) else str(new_v).strip()
            if old_s != new_s:
                changed.append(k)

    # Execute UPDATE
    set_parts = [f'"{k}"=?' for k in updates]
    vals = list(updates.values()) + [project_id]
    cursor.execute(f'UPDATE projects SET {", ".join(set_parts)} WHERE id=?', vals)

    if changed:
        display = changed[:5]
        details = ', '.join(display) + (f' 他{len(changed)-5}項目' if len(changed) > 5 else '')
        cursor.execute('SELECT id FROM project_history WHERE project_id=?', (project_id,))
        if cursor.fetchone():
            cursor.execute(
                "UPDATE project_history SET action_type='updated', action_details=?, created_at=datetime('now','localtime') WHERE project_id=?",
                (details, project_id),
            )
        else:
            cursor.execute(
                "INSERT INTO project_history (project_id, action_type, action_details, created_at) VALUES (?, 'updated', ?, datetime('now','localtime'))",
                (project_id, details),
            )

    conn.commit()
    conn.close()
    invalidate_projects_cache()  # P3: clear cache after mutation


# ── Mail templates ────────────────────────────────────────────────────────────

def get_mail_templates(mail_dir: str = 'mail') -> list:
    import os
    if not os.path.exists(mail_dir):
        os.makedirs(mail_dir)
    templates = sorted(f for f in os.listdir(mail_dir) if f.endswith('.txt'))
    return [(f, f[:-4]) for f in templates]


# ── Sync design todo ──────────────────────────────────────────────────────────

def sync_design_todo(project_id: int, project_name: str, ph: str, new_start: str, new_end: str, pjno: str = None):
    if not new_start or not new_end:
        return
    ph_text    = f' PH{ph}' if ph else ''
    pjno_text  = f' (PJ {pjno})' if pjno else ''
    title      = f'[設計中] {project_name}{pjno_text}{ph_text}'

    start = datetime.strptime(new_start, '%Y-%m-%d')
    end   = datetime.strptime(new_end,   '%Y-%m-%d')
    new_dates = {(start + timedelta(days=i)).strftime('%Y-%m-%d') for i in range((end - start).days + 1)}

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT id, date FROM todos WHERE project_id=? AND title=?', (project_id, title))
    existing = cursor.fetchall()
    old_dates = {r[1] for r in existing}

    for todo_id, date in existing:
        if date not in new_dates:
            cursor.execute('DELETE FROM todos WHERE id=?', (todo_id,))

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for date in new_dates - old_dates:
        cursor.execute(
            'INSERT INTO todos (project_id, title, date, priority, created_at) VALUES (?,?,?,?,?)',
            (project_id, title, date, 'low', now),
        )

    conn.commit()
    conn.close()
