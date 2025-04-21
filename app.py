import os
import shutil
import sqlite3
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, flash
import pandas as pd
import openpyxl
from pandas import isna

app = Flask(__name__)
app.secret_key = 'your_secret_key'

# SQLite database setup
DB_FILE = 'projects.db'
DISPLAY_COLUMNS = [
    'タスク', '案件名', 'PH', '開発工数（h）', '設計工数（h）', '要件引継', '設計開始',
    '設計完了', '設計書送付', '開発開始', '開発完了', 'テスト開始日', 'テスト完了日',
    'FB完了予定日', 'SE納品', 'SE', 'BSE', '案件番号', 'PJNo.', 'ページ数', '備考'
]
DATE_COLUMNS_DB = [
    '要件引継', '設計開始', '設計完了', '設計書送付', '開発開始', '開発完了',
    'テスト開始日', 'テスト完了日', 'FB完了予定日', 'SE納品'
]
DATE_COLUMNS_DISPLAY = DATE_COLUMNS_DB.copy()

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            SE TEXT,
            案件名 TEXT,
            PH TEXT,
            "開発工数（h）" REAL,
            "設計工数（h）" REAL,
            要件引継 TEXT,
            設計開始 TEXT,
            設計完了 TEXT,
            設計書送付 TEXT,
            開発開始 TEXT,
            開発完了 TEXT,
            SE納品 TEXT,
            BSE TEXT,
            案件番号 TEXT,
            "PJNo." TEXT,
            備考 TEXT,
            テスト開始日 TEXT,
            テスト完了日 TEXT,
            FB完了予定日 TEXT,
            ページ数 INTEGER,
            タスク TEXT
        )
    ''')
    conn.commit()
    conn.close()

def read_users():
    users = {}
    try:
        with open('users.txt', 'r', encoding='utf-8') as f:
            for line in f:
                username, password = line.strip().split(':')
                users[username] = password
    except FileNotFoundError:
        with open('users.txt', 'w', encoding='utf-8') as f:
            f.write('admin:admin123\n')
        users['admin'] = 'admin123'
    return users

def project_exists(cursor, project):
    cursor.execute('''
        SELECT COUNT(*) FROM projects
        WHERE 案件名 = ? AND PH = ? AND "PJNo." = ? AND 案件番号 = ?
    ''', (project.get('案件名', ''), project.get('PH', ''), project.get('PJNo.', ''), project.get('案件番号', '')))
    return cursor.fetchone()[0] > 0

def import_excel_to_sqlite():
    if not os.path.exists('projects.xlsx'):
        return

    df = pd.read_excel('projects.xlsx', engine='openpyxl')
    available_columns = [col for col in DISPLAY_COLUMNS if col in df.columns]
    df = df[available_columns].copy()

    # Convert date columns to YYYY-MM-DD format
    for col in DATE_COLUMNS_DB:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors='coerce').dt.strftime('%Y-%m-%d').fillna('')
        else:
            df[col] = ''

    # Set テスト開始日 based on 開発完了
    if '開発完了' in df.columns:
        df['テスト開始日'] = df['開発完了'].apply(
            lambda x: (pd.to_datetime(x, errors='coerce') + timedelta(days=1)).strftime('%Y-%m-%d')
            if pd.notna(x) and x != '' else ''
        )

    # Initialize other columns if missing
    for col in ['ページ数', 'タスク']:
        if col not in df.columns:
            df[col] = ''

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    for _, row in df.iterrows():
        project = row.to_dict()
        if not project_exists(cursor, project):
            cursor.execute('''
                INSERT INTO projects (
                    SE, 案件名, PH, "開発工数（h）", "設計工数（h）", 要件引継, 設計開始,
                    設計完了, 設計書送付, 開発開始, 開発完了, SE納品, BSE, 案件番号, "PJNo.",
                    備考, テスト開始日, テスト完了日, FB完了予定日, ページ数, タスク
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                project.get('SE', ''),
                project.get('案件名', ''),
                project.get('PH', ''),
                project.get('開発工数（h）', None),
                project.get('設計工数（h）', None),
                project.get('要件引継', ''),
                project.get('設計開始', ''),
                project.get('設計完了', ''),
                project.get('設計書送付', ''),
                project.get('開発開始', ''),
                project.get('開発完了', ''),
                project.get('SE納品', ''),
                project.get('BSE', ''),
                project.get('案件番号', ''),
                project.get('PJNo.', ''),
                project.get('備考', ''),
                project.get('テスト開始日', ''),
                project.get('テスト完了日', ''),
                project.get('FB完了予定日', ''),
                project.get('ページ数', None),
                project.get('タスク', '')
            ))

    conn.commit()
    conn.close()

    if not os.path.exists('old'):
        os.makedirs('old')
    shutil.move('projects.xlsx', os.path.join('old', 'projects.xlsx'))

def read_projects():
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query('SELECT * FROM projects', conn)
    conn.close()
    return df

def update_project(project_id, updates):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    if 'ページ数' in updates:
        if updates['ページ数'] == '':
            updates['ページ数'] = None
        else:
            try:
                page_count = int(updates['ページ数'])
                if page_count <= 0:
                    raise ValueError
                updates['ページ数'] = page_count
            except ValueError:
                updates['ページ数'] = None

    if 'タスク' in updates:
        valid_tasks = ['設計', 'Brse', 'テスト', 'FB']
        tasks = updates['タスク'].split(',') if updates['タスク'] else []
        tasks = [task.strip() for task in tasks if task.strip() in valid_tasks]
        updates['タスク'] = ','.join(tasks) if tasks else ''
    else:
        updates['タスク'] = ''  # Handle case where no tasks are selected

    # Handle テスト開始日 logic: set to 開発完了 + 1 day if 開発完了 is set, else blank
    if '開発完了' in updates and updates['開発完了']:
        try:
            dev_complete_date = datetime.strptime(updates['開発完了'], '%Y-%m-%d')
            updates['テスト開始日'] = (dev_complete_date + timedelta(days=1)).strftime('%Y-%m-%d')
        except ValueError:
            updates['テスト開始日'] = ''
    elif '開発完了' in updates and not updates['開発完了']:
        updates['テスト開始日'] = ''

    set_clause_parts = []
    values = []
    for key, value in updates.items():
        set_clause_parts.append(f'"{key}" = ?')
        values.append(value)
    set_clause = ', '.join(set_clause_parts)
    values.append(project_id)

    sql = f'''
        UPDATE projects
        SET {set_clause}
        WHERE id = ?
    '''

    cursor.execute(sql, values)
    conn.commit()
    conn.close()

def parse_date_from_db(date_str):
    if isna(date_str) or date_str is None or date_str == '':
        return None
    try:
        return datetime.strptime(date_str, '%Y-%m-%d')
    except (ValueError, TypeError):
        return None

def parse_date_for_comparison(date_str):
    if isna(date_str) or date_str is None or date_str == '':
        return None
    try:
        if isinstance(date_str, datetime):
            return date_str
        return datetime.strptime(date_str, '%Y/%m/%d(%a)')
    except ValueError:
        try:
            return datetime.strptime(date_str, '%Y-%m-%d')
        except (ValueError, TypeError):
            return None

def format_date_jp(date):
    if date is None:
        return ''
    weekdays = ['月', '火', '水', '木', '金', '土', '日']
    weekday = weekdays[date.weekday()]
    return date.strftime('%Y/%m/%d') + f'({weekday})'

def convert_nat_to_none(project_dict):
    for key, value in project_dict.items():
        if isna(value) or value is None:
            project_dict[key] = ''
        elif isinstance(value, (float, int)):
            project_dict[key] = str(value)
    return project_dict

@app.route('/')
def index():
    if 'username' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        users = read_users()

        if username in users and users[username] == password:
            session['username'] = username
            return redirect(url_for('dashboard'))
        else:
            flash('無効な認証情報', 'danger')

    return render_template('login.html')

@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    if 'username' not in session:
        return redirect(url_for('login'))

    init_db()
    import_excel_to_sqlite()

    df = read_projects()
    current_date = datetime.now()
    filtered_projects = []

    for _, row in df.iterrows():
        se_delivery_date = parse_date_from_db(row['SE納品'])
        if se_delivery_date is None or se_delivery_date.date() >= current_date.date():
            project = row.to_dict()

            closest_date = None
            min_diff = float('inf')

            for col in DATE_COLUMNS_DISPLAY:
                date_str_db = row[col]
                date_obj = parse_date_from_db(date_str_db)
                project[f'{col}_past'] = False
                if date_obj is not None:
                    diff = abs((current_date.date() - date_obj.date()).days)
                    if diff < min_diff:
                        min_diff = diff
                        closest_date = col
                    if date_obj.date() < current_date.date():
                        project[f'{col}_past'] = True
                project[col] = format_date_jp(date_obj)

            project['highlight_column'] = closest_date if closest_date else None
            project = convert_nat_to_none(project)
            filtered_projects.append(project)

    if request.method == 'POST':
        project_id = int(request.form['index'])
        updates = {}
        for col in df.columns:
            if col in request.form and col != 'id':
                if col == 'タスク':
                    # Handle multiple checkbox values
                    updates[col] = ','.join(request.form.getlist(col))
                else:
                    updates[col] = request.form[col]
        update_project(project_id, updates)
        flash('プロジェクトが正常に更新されました', 'success')
        return redirect(url_for('dashboard'))

    return render_template('dashboard.html',
                           projects=filtered_projects,
                           display_columns=DISPLAY_COLUMNS,
                           date_columns=DATE_COLUMNS_DISPLAY)

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True)