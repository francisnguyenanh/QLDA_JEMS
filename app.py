import os
import shutil
import sqlite3
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash
import pandas as pd
import openpyxl
from pandas import isna

app = Flask(__name__)
app.secret_key = 'your_secret_key'

# SQLite database setup
DB_FILE = 'projects.db'
DISPLAY_COLUMNS = ['SE', '案件名', 'PH', '開発工数（h）', '設計工数（h）', '要件引継', '設計開始',
                   '設計完了', '設計書送付', '開発開始', '開発完了', 'SE納品', 'BSE', '案件番号', 'PJNo.', '備考']
DATE_COLUMNS_DB = ['要件引継', '設計開始', '設計完了', '設計書送付', '開発開始', '開発完了', 'SE納品']
DATE_COLUMNS_DISPLAY = ['要件引継', '設計開始', '設計完了', '設計書送付', '開発開始', '開発完了', 'SE納品']


def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # Create table with only the specified columns
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
            備考 TEXT
        )
    ''')
    conn.commit()
    conn.close()


# Read users from txt file
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


# Check if a project exists in SQLite based on 4 key columns
def project_exists(cursor, project):
    cursor.execute('''
        SELECT COUNT(*) FROM projects
        WHERE 案件名 = ? AND PH = ? AND "PJNo." = ? AND 案件番号 = ?
    ''', (project.get('案件名', ''), project.get('PH', ''), project.get('PJNo.', ''), project.get('案件番号', '')))
    return cursor.fetchone()[0] > 0


# Import data from Excel to SQLite
def import_excel_to_sqlite():
    if not os.path.exists('projects.xlsx'):
        return

    # Read Excel file and select only the required columns
    df = pd.read_excel('projects.xlsx', engine='openpyxl')
    # Keep only the columns that exist in DISPLAY_COLUMNS (adjust for naming)
    available_columns = [col for col in DISPLAY_COLUMNS if col in df.columns]
    df = df[available_columns].copy()

    # Convert Timestamp columns to string format (YYYY-MM-DD) for database storage
    for col in DATE_COLUMNS_DB:
        if col in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[col]):
                df[col] = df[col].dt.strftime('%Y-%m-%d').fillna('')
            else:
                df[col] = df[col].astype(str).fillna('')

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Insert data into SQLite if the project doesn't exist
    for _, row in df.iterrows():
        project = row.to_dict()
        if not project_exists(cursor, project):
            cursor.execute('''
                INSERT INTO projects (
                    SE, 案件名, PH, "開発工数（h）", "設計工数（h）", 要件引継, 設計開始,
                    設計完了, 設計書送付, 開発開始, 開発完了, SE納品, BSE, 案件番号, "PJNo.", 備考
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                project.get('備考', '')
            ))

    conn.commit()
    conn.close()

    # Move the file to 'old' directory (overwrite if exists)
    if not os.path.exists('old'):
        os.makedirs('old')
    shutil.move('projects.xlsx', os.path.join('old', 'projects.xlsx'))


# Read projects from SQLite
def read_projects():
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query('SELECT * FROM projects', conn)
    conn.close()
    return df


# Update project in SQLite
def update_project(project_id, updates):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

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

    print("SQL Query:", sql)
    print("Values:", values)

    cursor.execute(sql, values)

    conn.commit()
    conn.close()


# Convert date string (YYYY-MM-DD) from DB to datetime object
def parse_date_from_db(date_str):
    if isna(date_str) or date_str is None or date_str == '':
        return None
    try:
        return datetime.strptime(date_str, '%Y-%m-%d')
    except (ValueError, TypeError):
        return None


# Convert date string or datetime to datetime
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


# Convert datetime to Japanese format<ctrl3348>/mm/dd(aaa)
def format_date_jp(date):
    if date is None:
        return ''
    # Japanese weekday names
    weekdays = ['月', '火', '水', '木', '金', '土', '日']
    weekday = weekdays[date.weekday()]
    return date.strftime('%Y/%m/%d') + f'({weekday})'


# Convert NaT/NaN values in a dictionary to empty string, and ensure float values are displayed
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

    # Initialize database and import Excel if it exists
    init_db()
    import_excel_to_sqlite()

    df = read_projects()
    current_date = datetime.now()
    filtered_projects = []

    for _, row in df.iterrows():
        se_delivery_date = parse_date_from_db(row['SE納品'])
        # Display rows where SE納品 is either missing (None) or not in the past
        if se_delivery_date is None or se_delivery_date.date() >= current_date.date():
            project = row.to_dict()

            closest_date = None
            min_diff = float('inf')

            for col in DATE_COLUMNS_DISPLAY:
                date_str_db = row[col]
                date_obj = parse_date_from_db(date_str_db)
                project[f'{col}_past'] = False  # Default value
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