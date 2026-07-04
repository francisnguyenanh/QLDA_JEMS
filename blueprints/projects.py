"""
Projects blueprint — dashboard, upload, sort, duplicate handling, copy, etc. (B1).

P1: sort_projects now supports pagination via `page` and `per_page` params.
P2: /upload runs Excel import in a background thread; polls via /api/upload_progress/<task_id>.
"""
import os
import sqlite3
import logging
import threading
import uuid
from datetime import datetime, timedelta

import pandas as pd
from pandas import isna
from flask import Blueprint, render_template, redirect, url_for, flash, request, session, jsonify

from config import (
    DB_FILE, PROJECT_DIR, OLD_DIR, MAIL_DIR, DISPLAY_COLUMNS,
    DATE_COLUMNS_DB, DATE_COLUMNS_DISPLAY, VALID_STATUSES, STATUS_PRIORITY,
    DEFAULT_PAGE_SIZE,
)
from database import (
    init_db, read_projects, update_project, add_project_history,
    project_exists, compare_projects, get_mail_templates, sync_design_todo,
    invalidate_projects_cache,
)
from services.date_calc import (
    calculate_status, parse_date_from_db, parse_date_for_comparison,
    format_date_jp, convert_nat_to_none, enrich_project_for_display,
    calculate_test_completion_date, calculate_fb_completion_date, read_pages_ranges, read_working_days,
)

projects_bp = Blueprint('projects', __name__)

# ── P2: upload task tracking ──────────────────────────────────────────────────
upload_tasks: dict = {}   # task_id → status dict


def _require_login():
    return 'username' not in session


# ── Routes ────────────────────────────────────────────────────────────────────

@projects_bp.route('/')
def index():
    if 'username' in session:
        return redirect(url_for('projects.dashboard'))
    return redirect(url_for('auth.login'))


@projects_bp.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    if _require_login():
        return redirect(url_for('auth.login'))

    init_db()
    for d in [PROJECT_DIR, OLD_DIR, 'temp_compare']:
        os.makedirs(d, exist_ok=True)

    show_all        = request.form.get('show_all') == 'on'
    show_unnecessary = request.form.get('show_unnecessary') == 'on'

    df           = read_projects()
    current_date = datetime.now()

    filtered_projects = []
    for _, row in df.iterrows():
        if not show_unnecessary and row.get('不要', 0) == 1:
            continue
        project = enrich_project_for_display(row.to_dict(), current_date)
        filtered_projects.append(project)

    def _sort_key(p):
        return (
            STATUS_PRIORITY.get(p.get('ステータス', '要件引継待ち'), 8),
            parse_date_for_comparison(p.get('設計開始', '')) or datetime.max,
            parse_date_for_comparison(p.get('設計完了', '')) or datetime.max,
            -(float(p.get('設計工数（h）') or 0) if p.get('設計工数（h）') else 0),
        )
    filtered_projects.sort(key=_sort_key)

    # POST: inline edit
    if request.method == 'POST' and 'index' in request.form:
        try:
            project_id = int(request.form['index'])
        except ValueError:
            flash('エラー: 無効なプロジェクトIDです', 'danger')
            return redirect(url_for('projects.dashboard'))

        updates = {}
        for col in df.columns:
            if col in request.form and col != 'id':
                updates[col] = ','.join(request.form.getlist(col)) if col == 'タスク' else request.form[col]
        for field in ['不要', '注文設計', '注文テスト', '注文FB', '注文BrSE', '並行テスト']:
            updates.setdefault(field, 0)

        update_project(project_id, updates)

        pj_name = updates.get('案件名', '')
        ph      = updates.get('PH', '')
        new_start = updates.get('設計開始', '')
        new_end   = updates.get('設計完了', '')
        pjno      = str(updates.get('PJNo.') or updates.get('案件番号') or '').strip()
        if pj_name and new_start and new_end:
            sync_design_todo(project_id, pj_name, ph, new_start, new_end, pjno)

        flash('プロジェクトが正常に更新されました', 'success')
        return redirect(url_for('projects.dashboard'))

    return render_template(
        'dashboard.html',
        projects=filtered_projects,
        display_columns=DISPLAY_COLUMNS,
        date_columns=DATE_COLUMNS_DISPLAY,
        show_all=show_all,
        show_unnecessary=show_unnecessary,
        mail_templates=get_mail_templates(),
        ranges=read_pages_ranges(),
        valid_statuses=VALID_STATUSES,
        working_days=read_working_days(),
    )


# ── Upload (P2: async background) ─────────────────────────────────────────────

def _import_excel_background(task_id: str, file_path: str):
    """Run Excel import in background thread (P2)."""
    from app import import_excel_to_sqlite   # kept in app.py — avoid moving 600-line function
    try:
        upload_tasks[task_id]['status'] = 'processing'
        success, duplicated, total, imported = import_excel_to_sqlite(file_path)
        if success:
            has_dup = bool(duplicated)
            upload_tasks[task_id].update({
                'completed': True,
                'success': True,
                'has_duplicates': has_dup,
                'duplicated_projects': duplicated if has_dup else [],
                'total_projects': total,
                'imported_count': imported,
                'message': f'{imported}件のプロジェクトをインポートしました',
                'status': 'done',
            })
            invalidate_projects_cache()
        else:
            upload_tasks[task_id].update({'completed': True, 'success': False, 'error': 'インポートに失敗しました', 'status': 'error'})
    except Exception as e:
        logging.error(f'[upload bg] {e}')
        upload_tasks[task_id].update({'completed': True, 'success': False, 'error': str(e), 'status': 'error'})


@projects_bp.route('/upload', methods=['POST'])
def upload():
    if _require_login():
        return jsonify({'error': 'Unauthorized'}), 401

    if 'file' not in request.files:
        return jsonify({'error': 'ファイルが選択されていません'}), 400
    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'ファイルが選択されていません'}), 400
    if not (file.filename.endswith('.xlsx') or file.filename.endswith('.xls')):
        return jsonify({'error': '許可されていないファイル形式です'}), 400

    os.makedirs(PROJECT_DIR, exist_ok=True)
    os.makedirs(OLD_DIR, exist_ok=True)

    file_path = os.path.join(PROJECT_DIR, file.filename)
    file.save(file_path)

    # Move other files to OLD_DIR
    for f in os.listdir(PROJECT_DIR):
        if f != file.filename:
            fp = os.path.join(PROJECT_DIR, f)
            if os.path.isfile(fp):
                import shutil
                shutil.move(fp, os.path.join(OLD_DIR, f))

    task_id = str(uuid.uuid4())
    upload_tasks[task_id] = {'completed': False, 'status': 'queued'}

    thread = threading.Thread(target=_import_excel_background, args=(task_id, file_path), daemon=True)
    thread.start()

    return jsonify({'success': True, 'task_id': task_id, 'async': True})


@projects_bp.route('/api/upload_progress/<task_id>')
def get_upload_progress(task_id: str):
    """Poll upload background task status (P2)."""
    if _require_login():
        return jsonify({'error': 'Unauthorized'}), 401
    if task_id not in upload_tasks:
        return jsonify({'error': 'Task not found'}), 404
    return jsonify(upload_tasks[task_id])


@projects_bp.route('/handle_duplicate_project', methods=['POST'])
def handle_duplicate_project():
    if _require_login():
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        data         = request.get_json()
        action       = data.get('action')
        project_data = data.get('project_data', {})
        existing_id  = data.get('existing_id')
        differences  = data.get('differences', {})

        if not action or not project_data:
            return jsonify({'error': 'Missing action or project_data'}), 400

        conn   = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()

        if action == 'skip':
            conn.close()
            return jsonify({'success': True, 'action': 'skipped'})

        if action == 'update':
            if not existing_id:
                return jsonify({'error': 'Missing existing_id'}), 400
            fields = list(differences.keys()) if differences else [k for k in project_data if k != 'id']
            set_parts = [f'"{f}"=?' for f in fields if f in project_data]
            vals      = [project_data[f] for f in fields if f in project_data]
            if set_parts:
                cursor.execute(f'UPDATE projects SET {",".join(set_parts)} WHERE id=?', vals + [existing_id])
            if differences:
                display  = list(differences.keys())[:5]
                details  = ', '.join(display) + (f' 他{len(differences)-5}項目' if len(differences) > 5 else '')
                cursor.execute('SELECT id FROM project_history WHERE project_id=?', (existing_id,))
                if cursor.fetchone():
                    cursor.execute("UPDATE project_history SET action_type='excel_updated', action_details=?, created_at=datetime('now','localtime') WHERE project_id=?", (details, existing_id))
                else:
                    cursor.execute("INSERT INTO project_history (project_id,action_type,action_details,created_at) VALUES (?,'excel_updated',?,datetime('now','localtime'))", (existing_id, details))
            conn.commit(); conn.close()
            invalidate_projects_cache()
            return jsonify({'success': True, 'action': 'updated', 'project_id': existing_id})

        if action == 'import_new':
            g = project_data.get
            cursor.execute('''
                INSERT INTO projects (SE,"SE(sub)",案件名,PH,"開発工数（h）","設計工数（h）",要件引継,設計開始,設計完了,設計書送付,開発開始,開発完了,SE納品,BSE,案件番号,"PJNo.",備考,テスト開始日,テスト完了日,FB完了予定日,ページ数,タスク,ステータス,不要,注文設計,注文テスト,注文FB,注文BrSE,user_edited_status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (g('SE',''),g('SE(sub)',''),g('案件名',''),g('PH',''),g('開発工数（h）'),g('設計工数（h）'),
                 g('要件引継',''),g('設計開始',''),g('設計完了',''),g('設計書送付',''),g('開発開始',''),g('開発完了',''),
                 g('SE納品',''),g('BSE',''),g('案件番号',''),g('PJNo.',''),g('備考',''),
                 g('テスト開始日',''),g('テスト完了日',''),g('FB完了予定日',''),
                 g('ページ数'),g('タスク',''),g('ステータス','要件引継待ち'),
                 g('不要',0),g('注文設計',0),g('注文テスト',0),g('注文FB',0),g('注文BrSE',0),g('user_edited_status',0)))
            new_id = cursor.lastrowid
            cursor.execute("INSERT INTO project_history (project_id,action_type,action_details,created_at) VALUES (?,'created','Excelから新規作成 (重複対応)',datetime('now','localtime'))", (new_id,))
            conn.commit(); conn.close()
            invalidate_projects_cache()
            return jsonify({'success': True, 'action': 'imported_new', 'project_id': new_id})

        conn.close()
        return jsonify({'error': 'Invalid action'}), 400

    except Exception as e:
        logging.error(f'[dup] {e}')
        return jsonify({'error': str(e)}), 500


# ── Sort / filter with pagination (P1) ───────────────────────────────────────

@projects_bp.route('/sort_projects', methods=['POST'])
def sort_projects():
    if _require_login():
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        data         = request.get_json()
        column       = data.get('column')
        direction    = data.get('direction', 'asc').lower()
        show_unnecessary = data.get('show_unnecessary', False)
        search       = data.get('search_project_name', '').strip()
        page         = int(data.get('page', 1))          # P1
        per_page     = int(data.get('per_page', 0))      # P1 — 0 means all

        valid_cols = set(DISPLAY_COLUMNS + ['id', '設計実績', 'テスト実績', 'FB実績', 'BrSE実績'])
        if column not in valid_cols:
            return jsonify({'error': 'Invalid column'}), 400
        if direction not in ('asc', 'desc'):
            return jsonify({'error': 'Invalid direction'}), 400

        df           = read_projects()
        current_date = datetime.now()
        results      = []

        for _, row in df.iterrows():
            if not show_unnecessary and row.get('不要', 0) == 1:
                continue
            if search and search.lower() not in str(row.get('案件名', '')).lower():
                continue
            project = enrich_project_for_display(row.to_dict(), current_date)
            results.append(project)

        def _sort_val(p):
            v = p.get(column, 0 if column in {'設計実績', 'テスト実績', 'FB実績', 'BrSE実績'} else '')
            if column in DATE_COLUMNS_DB:
                dt = parse_date_for_comparison(v)
                return dt if dt else (datetime.max if direction == 'asc' else datetime.min)
            if isinstance(v, str) and v == '':
                return '' if direction == 'asc' else '￿'
            return v

        results.sort(key=_sort_val, reverse=(direction == 'desc'))

        total = len(results)

        # P1: apply pagination if per_page > 0
        if per_page > 0:
            start   = (page - 1) * per_page
            results = results[start:start + per_page]

        return jsonify({
            'projects':   results,
            'total':      total,
            'page':       page,
            'per_page':   per_page,
            'total_pages': (total + per_page - 1) // per_page if per_page > 0 else 1,
        })

    except Exception as e:
        logging.error(f'[sort] {e}')
        return jsonify({'error': str(e)}), 500


@projects_bp.route('/calculate_test_dates', methods=['POST'])
def calculate_test_dates():
    try:
        data            = request.get_json()
        page_count      = data.get('page_count')
        test_start_date = data.get('test_start_date')
        test_completion_date = data.get('test_completion_date')

        if test_start_date and page_count:
            tc = calculate_test_completion_date(page_count, test_start_date)
            fb = calculate_fb_completion_date(tc)
            return jsonify({'test_completion_date': tc, 'fb_completion_date': fb})
        elif test_completion_date:
            fb = calculate_fb_completion_date(test_completion_date)
            return jsonify({'fb_completion_date': fb})
        return jsonify({'error': 'Missing parameters'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@projects_bp.route('/get_schedule_data', methods=['GET'])
def get_schedule_data():
    if _require_login():
        return jsonify({'error': 'Unauthorized'}), 401
    week_start = request.args.get('week_start')
    if not week_start:
        return jsonify({'error': 'Missing week_start'}), 400
    try:
        from database import get_mail_templates  # noqa – just checking import works
        conn   = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('SELECT id,案件名,"PJNo.",PH,SE,テスト開始日,テスト完了日,FB完了予定日,SE納品 FROM projects WHERE 不要=0 ORDER BY テスト開始日')
        cols = [d[0] for d in cursor.description]
        projects = [dict(zip(cols, r)) for r in cursor.fetchall()]

        week_dt = datetime.strptime(week_start, '%Y-%m-%d')
        week_dates = [(week_dt + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(7)]

        cursor.execute('SELECT project_id,date_column,done FROM schedule_done_status WHERE date_column IN ({})'.format(','.join('?'*7)), week_dates)
        done_status = {(r[0], r[1]): r[2] for r in cursor.fetchall()}
        conn.close()

        return jsonify({'projects': projects, 'week_dates': week_dates, 'done_status': [{'project_id': k[0], 'date_column': k[1], 'done': v} for k, v in done_status.items()]})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@projects_bp.route('/save_schedule_done', methods=['POST'])
def save_schedule_done():
    if _require_login():
        return jsonify({'error': 'Unauthorized'}), 401
    data       = request.get_json()
    project_id = data.get('project_id')
    date_col   = data.get('date_column')
    done       = 1 if data.get('done') else 0
    try:
        conn   = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM schedule_done_status WHERE project_id=? AND date_column=?', (project_id, date_col))
        if cursor.fetchone():
            cursor.execute('UPDATE schedule_done_status SET done=? WHERE project_id=? AND date_column=?', (done, project_id, date_col))
        else:
            cursor.execute('INSERT INTO schedule_done_status (project_id,date_column,done) VALUES (?,?,?)', (project_id, date_col, done))
        conn.commit(); conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@projects_bp.route('/copy_project', methods=['POST'])
def copy_project():
    if _require_login():
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        project_name = request.form.get('project_name', '').strip()
        if not project_name:
            return jsonify({'error': 'プロジェクト名は必須です'}), 400

        conn   = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM projects WHERE LOWER(案件名)=LOWER(?)', (project_name,))
        if cursor.fetchone()[0] > 0:
            conn.close()
            return jsonify({'error': 'このプロジェクト名は既に存在します'}), 400

        data = {}
        for key in request.form.keys():
            if key in ('project_name', '案件名'):
                continue
            if key == 'タスク':
                data[key] = request.form.get(key, '')
            elif key in ['注文設計', '注文テスト', '注文FB', '注文BrSE', '並行テスト']:
                data[key] = 1 if request.form.get(key) == '1' else 0
            elif key in ['開発工数（h）', '設計工数（h）']:
                v = request.form.get(key, '')
                data[key] = float(v) if v else None
            elif key == 'ページ数':
                v = request.form.get(key, '')
                data[key] = int(v) if v else None
            else:
                data[key] = request.form.get(key, '')

        data['案件名']  = project_name
        data.setdefault('ステータス', '要件引継待ち')

        columns = []
        values  = []
        for col in DISPLAY_COLUMNS + ['注文設計', '注文テスト', '注文FB', '注文BrSE', '並行テスト']:
            if col in data and data[col] not in (None, ''):
                columns.append(f'"{col}"')
                values.append(data[col])

        if columns:
            cursor.execute(f'INSERT INTO projects ({",".join(columns)}) VALUES ({",".join("?"*len(columns))})', values)
        new_id = cursor.lastrowid
        cursor.execute("INSERT INTO project_history (project_id,action_type,action_details,created_at) VALUES (?,'copied','プロジェクトコピーから作成',datetime('now','localtime'))", (new_id,))
        conn.commit(); conn.close()
        invalidate_projects_cache()
        return jsonify({'success': True, 'project_id': new_id})

    except Exception as e:
        logging.error(f'[copy] {e}')
        return jsonify({'error': str(e)}), 500


@projects_bp.route('/delete_all_data', methods=['POST'])
def delete_all_data():
    if _require_login():
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        conn   = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        for table in ['project_history', 'daily_hours', 'todos', 'schedule_done_status', 'copied_templates', 'projects']:
            cursor.execute(f'DELETE FROM {table}')
        conn.commit(); conn.close()
        invalidate_projects_cache()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@projects_bp.route('/get_daily_report_data', methods=['GET'])
def get_daily_report_data():
    if _require_login():
        return jsonify({'error': 'Unauthorized'}), 401
    week_start = request.args.get('week_start')
    if not week_start:
        return jsonify({'error': 'Missing week_start'}), 400
    try:
        datetime.strptime(week_start, '%Y-%m-%d')
        from database import get_mail_templates  # noqa
        conn   = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('SELECT id,案件名,"PJNo.",PH FROM projects WHERE 不要=0')
        projects = [{'id': r[0], '案件名': r[1], 'PJNo.': r[2], 'PH': r[3]} for r in cursor.fetchall()]

        week_dt    = datetime.strptime(week_start, '%Y-%m-%d')
        week_dates = [{'date': (week_dt + timedelta(days=i)).strftime('%Y-%m-%d')} for i in range(7)]
        dates      = [d['date'] for d in week_dates]

        cursor.execute(
            f'SELECT project_id,date,task_type,hours FROM daily_hours WHERE date IN ({",".join("?"*len(dates))})',
            dates,
        )
        hours = [{'project_id': r[0], 'date': r[1], 'task_type': r[2], 'hours': r[3]} for r in cursor.fetchall()]
        conn.close()
        return jsonify({'projects': projects, 'week_dates': week_dates, 'hours': hours})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@projects_bp.route('/save_daily_hours', methods=['POST'])
def save_daily_hours():
    if _require_login():
        return jsonify({'error': 'Unauthorized'}), 401
    entries = request.get_json().get('hours', [])
    try:
        conn   = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        for e in entries:
            cursor.execute('DELETE FROM daily_hours WHERE project_id=? AND date=? AND task_type=?', (e['project_id'], e['date'], e['task_type']))
        for e in entries:
            cursor.execute('INSERT INTO daily_hours (project_id,date,task_type,hours) VALUES (?,?,?,?)', (e['project_id'], e['date'], e['task_type'], e['hours']))
        conn.commit(); conn.close()
        invalidate_projects_cache()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
