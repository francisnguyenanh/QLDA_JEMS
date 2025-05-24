import sqlite3
from datetime import datetime, timedelta
from flask import render_template, redirect, url_for, flash
import pandas as pd
from pandas import isna
import logging
import zipfile
import io
from charset_normalizer import detect
import re
from flask import Flask, jsonify, request, session
import os
import shutil
import chardet
import csv

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)
app.secret_key = 'your_secret_key'

# SQLite database setup
DB_FILE = 'projects.db'
MAIL_DIR = 'mail'
PROJECT_DIR = 'project'
OLD_DIR = 'old'
DISPLAY_COLUMNS = [
    'ステータス', '案件名', '要件引継', '設計開始',
    '設計完了', '設計書送付', '開発開始', '開発完了', 'テスト開始日', 'テスト完了日',
    'FB完了予定日', 'SE納品', 'タスク', 'SE', 'BSE', '案件番号', 'PJNo.', 'PH',
    '開発工数（h）', '設計工数（h）', 'ページ数', '注文設計', '注文テスト', '注文FB', '注文BrSE', '備考'
]
DATE_COLUMNS_DB = [
    '要件引継', '設計開始', '設計完了', '設計書送付', '開発開始', '開発完了',
    'テスト開始日', 'テスト完了日', 'FB完了予定日', 'SE納品'
]
DATE_COLUMNS_DISPLAY = DATE_COLUMNS_DB.copy()
VALID_STATUSES = [
    '要件引継待ち', '設計中', 'SE送付済', '開発中', 'テスト中', 'FB対応中', 'SE納品済'
]

MAIL_DIR = 'mail'

def init_db():
    """Initialize SQLite database with projects, copied_templates, and daily_hours tables."""
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
            タスク TEXT,
            ステータス TEXT,
            不要 INTEGER DEFAULT 0,
            注文設計 INTEGER DEFAULT 0,
            注文テスト INTEGER DEFAULT 0,
            注文FB INTEGER DEFAULT 0,
            注文BrSE INTEGER DEFAULT 0,
            user_edited_status INTEGER DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS copied_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            filename TEXT,
            copied_at TEXT,
            FOREIGN KEY (project_id) REFERENCES projects(id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_hours (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            date TEXT,
            task_type TEXT,
            hours REAL,
            FOREIGN KEY (project_id) REFERENCES projects(id)
        )
    ''')
    cursor.execute("PRAGMA table_info(projects)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'ステータス' not in columns:
        cursor.execute('ALTER TABLE projects ADD COLUMN ステータス TEXT')
        cursor.execute("UPDATE projects SET ステータス = '要件引継待ち' WHERE ステータス IS NULL")
    if '不要' not in columns:
        cursor.execute('ALTER TABLE projects ADD COLUMN 不要 INTEGER DEFAULT 0')
    if '注文設計' not in columns:
        cursor.execute('ALTER TABLE projects ADD COLUMN 注文設計 INTEGER DEFAULT 0')
    if '注文テスト' not in columns:
        cursor.execute('ALTER TABLE projects ADD COLUMN 注文テスト INTEGER DEFAULT 0')
    if '注文FB' not in columns:
        cursor.execute('ALTER TABLE projects ADD COLUMN 注文FB INTEGER DEFAULT 0')
    if '注文BrSE' not in columns:
        cursor.execute('ALTER TABLE projects ADD COLUMN 注文BrSE INTEGER DEFAULT 0')
    if 'user_edited_status' not in columns:
        cursor.execute('ALTER TABLE projects ADD COLUMN user_edited_status INTEGER DEFAULT 0')
    conn.commit()
    conn.close()

def read_users():
    """Read users from users.txt, create default admin if not exists."""
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
    """Check if a project already exists based on 案件名, PH, PJNo."""
    keys = ['案件名', 'PH', 'PJNo.']
    conditions = []
    values = []

    for key in keys:
        value = project.get(key, '')
        if value is None or (isinstance(value, str) and value.strip() == '') or isna(value):
            continue
        conditions.append(f'"{key}" = ?')
        values.append(str(value))

    if not conditions:
        logging.debug("All keys (案件名, PH, PJNo.) are empty, treating as duplicate")
        return True

    query = f'''
        SELECT COUNT(*) FROM projects
        WHERE {' AND '.join(conditions)}
    '''
    logging.debug(f"Executing query: {query} with values: {values}")
    cursor.execute(query, values)
    count = cursor.fetchone()[0]
    logging.debug(f"Found {count} matching projects")
    return count > 0

def parse_date_from_db(date_str):
    """Parse date string from database (YYYY-MM-DD or YYYY/MM/DD) to datetime object."""
    if isna(date_str) or date_str is None or date_str == '':
        logging.debug(f"Date string is empty or None: {date_str}")
        return None
    try:
        parsed_date = datetime.strptime(date_str, '%Y-%m-%d')
        logging.debug(f"Successfully parsed date: {date_str} -> {parsed_date}")
        return parsed_date
    except ValueError:
        try:
            parsed_date = datetime.strptime(date_str, '%Y/%m/%d')
            logging.debug(f"Successfully parsed date: {date_str} -> {parsed_date}")
            return parsed_date
        except (ValueError, TypeError) as e:
            #logging.error(f"Failed to parse date: {date_str}, error: {e}")
            return None

def parse_date_for_comparison(date_str):
    """Parse date string for comparison, supports YYYY/MM/DD(曜日) and YYYY-MM-DD."""
    if isna(date_str) or date_str is None or date_str == '':
        logging.debug(f"Date string for comparison is empty or None: {date_str}")
        return None
    try:
        if isinstance(date_str, datetime):
            return date_str
        if '(' in date_str:
            date_str = date_str.split('(')[0]
        parsed_date = datetime.strptime(date_str, '%Y/%m/%d')
        logging.debug(f"Successfully parsed date for comparison: {date_str} -> {parsed_date}")
        return parsed_date
    except ValueError:
        try:
            parsed_date = datetime.strptime(date_str, '%Y-%m-%d')
            logging.debug(f"Successfully parsed date for comparison: {date_str} -> {parsed_date}")
            return parsed_date
        except (ValueError, TypeError) as e:
            #logging.error(f"Failed to parse date for comparison: {date_str}, error: {e}")
            return None

def format_date_jp(date):
    """Format datetime object to YYYY/MM/DD(曜日)."""
    if date is None:
        return ''
    weekdays = ['月', '火', '水', '木', '金', '土', '日']
    weekday = weekdays[date.weekday()]
    return date.strftime('%Y/%m/%d') + f'({weekday})'

def convert_nat_to_none(project_dict):
    """Convert NaT/NaN/None values to empty strings and handle specific data types."""
    for key, value in project_dict.items():
        if isna(value) or value is None:
            project_dict[key] = '' if key not in ['不要', '注文設計', '注文テスト', '注文FB', '注文BrSE',
                                                  'user_edited_status'] else 0
        elif key == 'PJNo.':
            if isinstance(value, (float, int)):
                project_dict[key] = str(int(value))
            else:
                project_dict[key] = str(value)
        elif isinstance(value, (float, int)) and key not in ['不要', '注文設計', '注文テスト', '注文FB', '注文BrSE',
                                                             'user_edited_status']:
            project_dict[key] = str(value)
        elif key in ['注文設計', '注文テスト', '注文FB', '注文BrSE']:
            project_dict[key] = '○' if value == 1 else ''
        if key == 'fb_late':
            project_dict[key] = bool(value)
    return project_dict

def calculate_status(project, current_date=None):
    """Calculate project status based on milestone dates and current date, unless user_edited_status is 1."""
    if project.get('user_edited_status', 0) == 1:
        return project.get('ステータス', '要件引継待ち')

    if current_date is None:
        current_date = datetime.now()

    date_fields = [
        ('要件引継', '要件引継待ち'),
        ('設計完了', '設計中'),
        ('設計書送付', 'SE送付済'),
        ('開発完了', '開発中'),
        ('テスト完了日', 'テスト中'),
        ('SE納品', 'FB対応中'),
    ]

    current_date = current_date.date()

    se_delivery_date = parse_date_from_db(project.get('SE納品', ''))
    if se_delivery_date and se_delivery_date.date() < current_date:
        return 'SE納品済'

    for date_field, status in date_fields:
        date_value = parse_date_from_db(project.get(date_field, ''))
        if date_value and date_value.date() >= current_date:
            return status

    return '要件引継待ち'

def read_pages_ranges():
    """Read page ranges and corresponding days from pages.txt."""
    ranges = []
    try:
        with open('pages.txt', 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    range_part, days = line.split(':')
                    days = int(days.strip())
                    from_page, to_page = map(int, range_part.split('-'))
                    ranges.append((from_page, to_page, days))
                except ValueError:
                    continue
    except FileNotFoundError:
        pass
    return ranges

def read_working_days(file_path='config.txt'):
    """Read working days from config.txt, default to 9 if not found."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith('fix FB days =') or line.startswith('const workingDays ='):
                    try:
                        value = int(line.split('=')[1].strip())
                        return value
                    except (IndexError, ValueError):
                        #logging.error(f"Invalid workingDays format in {file_path}: {line}")
                        return 9
        logging.warning(f"workingDays not found in {file_path}")
        return 9
    except FileNotFoundError:
        logging.warning(f"File {file_path} not found")
        return 9
    except Exception as e:
        #logging.error(f"Error reading {file_path}: {e}")
        return 9

def add_working_days(start_date, working_days):
    """Add working days to a start date, skipping weekends."""
    if not start_date or working_days <= 0:
        return ''
    current_date = start_date
    days_added = 0
    while days_added < working_days:
        current_date += timedelta(days=1)
        if current_date.weekday() < 5:
            days_added += 1
    return current_date.strftime('%Y-%m-%d')

def calculate_test_completion_date(page_count, test_start_date):
    logging.debug(f"Calculating test completion date: page_count={page_count}, test_start_date={test_start_date}")
    if not page_count or not test_start_date:
        #logging.error("Invalid inputs: page_count or test_start_date is empty")
        return ''
    try:
        page_count = int(page_count)
        test_start_date = datetime.strptime(test_start_date, '%Y-%m-%d')
    except (ValueError, TypeError) as e:
        #logging.error(f"Input parsing error: {str(e)}")
        return ''

    ranges = read_pages_ranges()
    logging.debug(f"Page ranges from pages.txt: {ranges}")
    for from_page, to_page, days in ranges:
        if from_page <= page_count <= to_page:
            result = add_working_days(test_start_date, days)
            logging.debug(f"Test completion date calculated: {result}")
            return result
    logging.warning(f"No matching range found for page_count={page_count}")
    return ''

def calculate_fb_completion_date(test_completion_date):
    logging.debug(f"Calculating FB completion date: test_completion_date={test_completion_date}")
    if not test_completion_date:
        #logging.error("Test completion date is empty")
        return ''
    try:
        test_completion_date = datetime.strptime(test_completion_date, '%Y-%m-%d')
        working_days = read_working_days()
        result = add_working_days(test_completion_date, working_days)
        logging.debug(f"FB completion date calculated: {result}")
        return result
    except (ValueError, TypeError) as e:
        #logging.error(f"Error parsing test_completion_date: {str(e)}")
        return ''

def import_excel_to_sqlite(file_path):
    """Import projects from Excel file to SQLite database."""
    if not os.path.exists(file_path):
        return False

    try:
        df = pd.read_excel(file_path, engine='openpyxl', dtype={'PJNo.': str})
        available_columns = [col for col in DISPLAY_COLUMNS if col in df.columns]
        df = df[available_columns].copy()

        current_date = datetime.now()

        # Process date columns
        for col in DATE_COLUMNS_DB:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors='coerce').dt.strftime('%Y-%m-%d').fillna('')
            else:
                df[col] = ''

        # Set test start date based on development completion
        if '開発完了' in df.columns:
            def calculate_test_start_date(dev_complete):
                if pd.notna(dev_complete) and dev_complete != '':
                    try:
                        dev_complete_date = pd.to_datetime(dev_complete)
                        test_start_date = dev_complete_date + timedelta(days=1)
                        # Kiểm tra nếu là cuối tuần
                        if test_start_date.weekday() == 5:  # Thứ Bảy
                            test_start_date += timedelta(days=2)
                        elif test_start_date.weekday() == 6:  # Chủ Nhật
                            test_start_date += timedelta(days=1)
                        return test_start_date.strftime('%Y-%m-%d')
                    except (ValueError, TypeError):
                        return ''
                return ''

            df['テスト開始日'] = df['開発完了'].apply(calculate_test_start_date)

        # Handle PJNo. formatting
        if 'PJNo.' in df.columns:
            df['PJNo.'] = df['PJNo.'].apply(
                lambda x: str(int(float(x))) if pd.notna(x) and x != '' and isinstance(x, (int, float)) else str(
                    x) if pd.notna(x) else ''
            )

        # Handle PH formatting
        if 'PH' in df.columns:
            df['PH'] = df['PH'].apply(
                lambda x: str(int(float(x))) if pd.notna(x) and x != '' and isinstance(x, (int, float)) else str(
                    x) if pd.notna(x) else ''
            )

        # Process 案件名: Remove "株式会社"
        if '案件名' in df.columns:
            df['案件名'] = df['案件名'].apply(
                lambda x: str(x).replace('株式会社', '') if pd.notna(x) and isinstance(x, str) else str(x)
            )

        # Process SE: Keep only the part before the first space (1-byte or 2-byte)
        if 'SE' in df.columns:
            df['SE'] = df['SE'].apply(
                lambda x: str(x).split()[0] if pd.notna(x) and isinstance(x, str) and str(x).strip() else str(x)
            )

        # Initialize missing columns
        for col in ['ページ数', 'タスク', 'ステータス', '不要', '注文設計', '注文テスト', '注文FB', '注文BrSE',
                    'user_edited_status']:
            if col not in df.columns:
                df[col] = '' if col != 'ステータス' else '要件引継待ち'
                if col in ['不要', '注文設計', '注文テスト', '注文FB', '注文BrSE', 'user_edited_status']:
                    df[col] = 0

        # Calculate status for each project
        for index, row in df.iterrows():
            project = row.to_dict()
            df.at[index, 'ステータス'] = calculate_status(project, current_date)

        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()

        imported_count = 0
        for _, row in df.iterrows():
            project = row.to_dict()
            if not project_exists(cursor, project):
                cursor.execute('''
                    INSERT INTO projects (
                        SE, 案件名, PH, "開発工数（h）", "設計工数（h）", 要件引継, 設計開始,
                        設計完了, 設計書送付, 開発開始, 開発完了, SE納品, BSE, 案件番号, "PJNo.",
                        備考, テスト開始日, テスト完了日, FB完了予定日, ページ数, タスク, ステータス,
                        不要, 注文設計, 注文テスト, 注文FB, 注文BrSE, user_edited_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    project.get('タスク', ''),
                    project.get('ステータス', '要件引継待ち'),
                    project.get('不要', 0),
                    project.get('注文設計', 0),
                    project.get('注文テスト', 0),
                    project.get('注文FB', 0),
                    project.get('注文BrSE', 0),
                    project.get('user_edited_status', 0)
                ))
                imported_count += 1

        conn.commit()
        conn.close()
        logging.info(f"Imported {imported_count} new projects from {file_path}")
        return True
    except Exception as e:
        #logging.error(f"Failed to import Excel file {file_path}: {e}")
        return False

def read_projects():
    """Read all projects from SQLite database with total hours worked."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM projects')
    rows = cursor.fetchall()
    columns = [description[0] for description in cursor.description]

    projects = []
    for row in rows:
        project = dict(zip(columns, row))
        cursor.execute('''
            SELECT task_type, SUM(hours) as total_hours
            FROM daily_hours
            WHERE project_id = ?
            GROUP BY task_type
        ''', (project['id'],))
        hours = {row[0]: row[1] for row in cursor.fetchall()}
        project['設計実績'] = hours.get('設計', 0)
        project['テスト実績'] = hours.get('テスト', 0)
        project['FB実績'] = hours.get('FB', 0)
        project['BrSE実績'] = hours.get('BrSE', 0)
        projects.append(project)

    conn.close()
    df = pd.DataFrame(projects)
    return df

def get_mail_templates():
    """Get list of mail templates from mail directory."""
    if not os.path.exists(MAIL_DIR):
        os.makedirs(MAIL_DIR)
    templates = [f for f in os.listdir(MAIL_DIR) if f.endswith('.txt')]
    templates = [(f, f[:-4]) for f in sorted(templates)]
    #logging.debug(f"Mail templates: {templates}")
    return templates

def get_week_dates(week_start):
    """Tạo danh sách ngày trong tuần bắt đầu từ Monday."""
    dates = []
    current_date = datetime.strptime(week_start, '%Y-%m-%d')
    for i in range(7):
        date = current_date + timedelta(days=i)
        weekdays = ['月', '火', '水', '木', '金', '土', '日']
        display = date.strftime('%Y/%m/%d') + f'({weekdays[date.weekday()]})'
        dates.append({'date': date.strftime('%Y-%m-%d'), 'display': display})
    return dates

def update_project(project_id, updates):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    current_date = datetime.now()

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
        updates['タスク'] = ''

    for field in ['不要', '注文設計', '注文テスト', '注文FB', '注文BrSE']:
        if field in updates:
            updates[field] = 1 if updates[field] == 'on' else 0

    # Handle ステータス and user_edited_status
    if 'ステータス' in updates and updates['ステータス'] in VALID_STATUSES:
        updates['user_edited_status'] = 1
    else:
        updates['user_edited_status'] = 0

    if '開発完了' in updates and updates['開発完了']:
        try:
            dev_complete_date = datetime.strptime(updates['開発完了'], '%Y-%m-%d')
            test_start_date = dev_complete_date + timedelta(days=1)
            # Kiểm tra nếu là cuối tuần
            if test_start_date.weekday() == 5:  # Thứ Bảy
                test_start_date += timedelta(days=2)
            elif test_start_date.weekday() == 6:  # Chủ Nhật
                test_start_date += timedelta(days=1)
            updates['テスト開始日'] = test_start_date.strftime('%Y-%m-%d')
        except ValueError:
            updates['テスト開始日'] = ''
    elif '開発完了' in updates and not updates['開発完了']:
        updates['テスト開始日'] = ''

    # Calculate テスト完了日 and FB完了予定日
    page_count = updates.get('ページ数')
    test_start_date = updates.get('テスト開始日')
    #logging.debug(f"Calculating test dates: page_count={page_count}, test_start_date={test_start_date}")

    # Use values from form if available and valid
    if 'テスト完了日' in updates and updates['テスト完了日']:
        try:
            datetime.strptime(updates['テスト完了日'], '%Y-%m-%d')
            #logging.debug(f"Using テスト完了日 from form: {updates['テスト完了日']}")
        except ValueError:
            updates['テスト完了日'] = ''
    else:
        updates['テスト完了日'] = calculate_test_completion_date(page_count, test_start_date) if page_count and test_start_date else ''

    if 'FB完了予定日' in updates and updates['FB完了予定日']:
        try:
            datetime.strptime(updates['FB完了予定日'], '%Y-%m-%d')
            #logging.debug(f"Using FB完了予定日 from form: {updates['FB完了予定日']}")
        except ValueError:
            updates['FB完了予定日'] = ''
    else:
        updates['FB完了予定日'] = calculate_fb_completion_date(updates['テスト完了日']) if updates['テスト完了日'] else ''

    # Only calculate status if not user-edited
    if updates.get('user_edited_status', 0) == 0:
        updates['ステータス'] = calculate_status(updates, current_date)

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
    #logging.debug(f"Executing SQL: {sql} with values: {values}")
    cursor.execute(sql, values)
    conn.commit()
    conn.close()

@app.route('/')
def index():
    """Redirect to dashboard if logged in, else to login."""
    if 'username' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Handle user login."""
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        users = read_users()
        if username in users and users[username] == password:
            session['username'] = username
            flash('ログイン成功', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('ユーザー名またはパスワードが正しくありません', 'danger')
    return render_template('login.html')

# Thêm từ điển ánh xạ trạng thái với mức độ ưu tiên
STATUS_PRIORITY = {
    '要件引継待ち': 6,
    '設計中': 1,
    'SE送付済': 2,
    '開発中': 3,
    'テスト中': 4,
    'FB対応中': 5,
    'SE納品済': 7
}

@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    """Render dashboard with project data and handle project updates."""
    if 'username' not in session:
        return redirect(url_for('login'))

    init_db()

    if not os.path.exists(PROJECT_DIR):
        os.makedirs(PROJECT_DIR)
    if not os.path.exists(OLD_DIR):
        os.makedirs(OLD_DIR)

    show_all = request.form.get('show_all') == 'on'
    show_unnecessary = request.form.get('show_unnecessary') == 'on'

    df = read_projects()
    current_date = datetime.now()
    filtered_projects = []

    for _, row in df.iterrows():
        # Apply filtering: include project if (不要 = 0 OR show_unnecessary) AND (SE納品 not past OR show_all)
        if not show_unnecessary and row.get('不要', 0) == 1:
            continue

        # se_delivery_date = parse_date_from_db(row['SE納品'])
        # if not show_all and se_delivery_date and se_delivery_date.date() < current_date.date():
        #     continue

        # if not show_all:
        #     continue

        project = row.to_dict()
        project['ステータス'] = calculate_status(project, current_date)

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
        fb_completion_date = parse_date_from_db(row['FB完了予定日'])
        project['fb_late'] = False

        se_delivery_date = parse_date_from_db(row['SE納品'])
        if fb_completion_date and se_delivery_date:
            project['fb_late'] = fb_completion_date.date() > se_delivery_date.date()

        project = convert_nat_to_none(project)
        filtered_projects.append(project)

    # Sắp xếp mặc định theo yêu cầu
    def safe_get(project, key):
        if key == 'ステータス':
            # Trả về mức độ ưu tiên của trạng thái
            return STATUS_PRIORITY.get(project.get(key, '要件引継待ち'), 8)
        elif key in ['設計開始', '設計完了']:
            # Xử lý ngày, trả về datetime.max nếu trống để xếp cuối khi tăng dần
            date_obj = parse_date_for_comparison(project.get(key, ''))
            return date_obj if date_obj else datetime.max
        elif key == '設計工数（h）':
            # Xử lý số thực, trả về 0 nếu trống để xếp cuối khi giảm dần
            try:
                return float(project.get(key, 0)) if project.get(key, '') != '' else 0
            except ValueError:
                return 0
        return project.get(key, '')

    filtered_projects.sort(
        key=lambda x: (
            safe_get(x, 'ステータス'),           # 1. ステータス (theo thứ tự ưu tiên)
            safe_get(x, '設計開始'),            # 2. 設計開始 (tăng dần)
            safe_get(x, '設計完了'),            # 3. 設計完了 (tăng dần)
            -safe_get(x, '設計工数（h）')       # 4. 設計工数（h） (giảm dần, dùng dấu - để đảo ngược)
        )
    )

    if request.method == 'POST' and 'index' in request.form:
        try:
            project_id = int(request.form['index'])
        except ValueError:
            flash('エラー: 無効なプロジェクトIDです', 'danger')
            return redirect(url_for('dashboard'))

        updates = {}
        for col in df.columns:
            if col in request.form and col != 'id':
                if col == 'タスク':
                    updates[col] = ','.join(request.form.getlist(col))
                else:
                    updates[col] = request.form[col]
        update_project(project_id, updates)
        flash('プロジェクトが正常に更新されました', 'success')
        return redirect(url_for('dashboard'))

    mail_templates = get_mail_templates()
    ranges = read_pages_ranges()
    working_days = read_working_days()

    return render_template('dashboard.html',
                           projects=filtered_projects,
                           display_columns=DISPLAY_COLUMNS,
                           date_columns=DATE_COLUMNS_DISPLAY,
                           show_all=show_all,
                           show_unnecessary=show_unnecessary,
                           mail_templates=mail_templates,
                           ranges=ranges,
                           valid_statuses=VALID_STATUSES,
                           working_days=working_days)

@app.route('/upload', methods=['POST'])
def upload():
    """Handle Excel file upload."""
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    if 'file' not in request.files:
        flash('ファイルが選択されていません', 'danger')
        return redirect(url_for('dashboard'))

    file = request.files['file']
    if file.filename == '':
        flash('ファイルが選択されていません', 'danger')
        return redirect(url_for('dashboard'))

    if not (file.filename.endswith('.xlsx') or file.filename.endswith('.xls')):
        flash('許可されていないファイル形式です', 'danger')
        return redirect(url_for('dashboard'))

    try:
        if not os.path.exists(PROJECT_DIR):
            os.makedirs(PROJECT_DIR)
        if not os.path.exists(OLD_DIR):
            os.makedirs(OLD_DIR)

        file_path = os.path.join(PROJECT_DIR, file.filename)
        file.save(file_path)

        for existing_file in os.listdir(PROJECT_DIR):
            if existing_file != file.filename:
                existing_file_path = os.path.join(PROJECT_DIR, existing_file)
                if os.path.isfile(existing_file_path):
                    os.remove(existing_file_path)

        if import_excel_to_sqlite(file_path):
            os.remove(file_path)
            flash('ファイルが正常にアップロードされました', 'success')
        else:
            os.remove(file_path)
            flash('エラー: ファイルのインポートに失敗しました', 'danger')

    except Exception as e:
        #logging.error(f"Error uploading file: {e}")
        if os.path.exists(file_path):
            os.remove(file_path)
        flash(f'エラー: ファイルのアップロードに失敗しました: {str(e)}', 'danger')

    return redirect(url_for('dashboard'))

@app.route('/upload_mail_template', methods=['POST'])
def upload_mail_template():
    """Handle mail template file upload."""
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    if 'file' not in request.files:
        return jsonify({'error': 'ファイルが選択されていません'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'ファイルが選択されていません'}), 400

    if not file.filename.endswith('.txt'):
        return jsonify({'error': 'テキストファイル（.txt）のみアップロード可能です'}), 400

    try:
        if not os.path.exists(MAIL_DIR):
            os.makedirs(MAIL_DIR)
        if not os.path.exists(OLD_DIR):
            os.makedirs(OLD_DIR)

        file_path = os.path.join(MAIL_DIR, file.filename)
        if os.path.exists(file_path):
            old_file_path = os.path.join(
                OLD_DIR,
                f"{os.path.splitext(file.filename)[0]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{os.path.splitext(file.filename)[1]}"
            )
            shutil.move(file_path, old_file_path)
            #logging.info(f"Moved existing file to: {old_file_path}")

        file.save(file_path)
        #logging.info(f"Uploaded new mail template: {file_path}")
        return jsonify({'success': True})

    except Exception as e:
        #logging.error(f"Error uploading mail template: {e}")
        return jsonify({'error': f'ファイルのアップロードに失敗しました: {str(e)}'}), 500




@app.route('/calculate_test_dates', methods=['POST'])
def calculate_test_dates():
    try:
        data = request.get_json()
        page_count = data.get('page_count')
        test_start_date = data.get('test_start_date')

        #logging.debug(f"Received calculate_test_dates request: page_count={page_count}, test_start_date={test_start_date}")

        if not page_count or not test_start_date:
            #logging.error("Missing page_count or test_start_date")
            return jsonify({'error': 'Missing page_count or test_start_date'}), 400

        page_count = int(page_count)
        test_start_date = datetime.strptime(test_start_date, '%Y-%m-%d')

        #logging.debug(f"Parsed inputs: page_count={page_count}, test_start_date={test_start_date}")

        test_completion_date = calculate_test_completion_date(page_count, test_start_date.strftime('%Y-%m-%d'))
        fb_completion_date = calculate_fb_completion_date(test_completion_date)

        #logging.debug(f"Calculated dates: test_completion_date={test_completion_date}, fb_completion_date={fb_completion_date}")

        return jsonify({
            'test_completion_date': test_completion_date,
            'fb_completion_date': fb_completion_date
        })
    except Exception as e:
        #logging.error(f"Error calculating test dates: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/sort_projects', methods=['POST'])
def sort_projects():
    """Sort and filter projects based on column, direction, and search criteria."""
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        data = request.get_json()
        column = data.get('column')
        direction = data.get('direction', 'asc').lower()
        show_all = data.get('show_all', False)
        show_unnecessary = data.get('show_unnecessary', False)
        search_project_name = data.get('search_project_name', '').strip()  # Lấy giá trị tìm kiếm 案件名

        valid_columns = DISPLAY_COLUMNS + ['id', '設計実績', 'テスト実績', 'FB実績', 'BrSE実績']
        if column not in valid_columns:
            return jsonify({'error': 'Invalid column'}), 400
        if direction not in ['asc', 'desc']:
            return jsonify({'error': 'Invalid direction'}), 400

        df = read_projects()
        current_date = datetime.now()
        filtered_projects = []

        for _, row in df.iterrows():
            # Áp dụng bộ lọc: include project if (不要 = 0 OR show_unnecessary) AND (SE納品 not past OR show_all)
            if not show_unnecessary and row.get('不要', 0) == 1:
                continue

            # se_delivery_date = parse_date_from_db(row['SE納品'])
            # if not show_all and se_delivery_date and se_delivery_date.date() < current_date.date():
            #     continue

            # Lọc theo 案件名
            if search_project_name and search_project_name.lower() not in str(row['案件名']).lower():
                continue

            project = row.to_dict()
            project['ステータス'] = calculate_status(project, current_date)

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
            fb_completion_date = parse_date_from_db(row['FB完了予定日'])
            project['fb_late'] = False

            se_delivery_date = parse_date_from_db(row['SE納品'])
            if fb_completion_date and se_delivery_date:
                project['fb_late'] = fb_completion_date.date() > se_delivery_date.date()

            project = convert_nat_to_none(project)
            filtered_projects.append(project)

        def safe_get(project, key):
            value = project.get(key, 0 if key in ['設計実績', 'テスト実績', 'FB実績', 'BrSE実績'] else '')
            if key in DATE_COLUMNS_DB:
                date_obj = parse_date_for_comparison(value)
                return date_obj if date_obj else datetime.max if direction == 'asc' else datetime.min
            if isinstance(value, str) and value == '':
                return '' if direction == 'asc' else '\uffff'
            return value

        filtered_projects.sort(
            key=lambda x: safe_get(x, column),
            reverse=(direction == 'desc')
        )

        return jsonify({'projects': filtered_projects})
    except Exception as e:
        #logging.error(f"Error sorting projects: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/get_daily_report_data', methods=['GET'])
def get_daily_report_data():
    """Lấy dữ liệu cho modal báo cáo hàng ngày."""
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    week_start = request.args.get('week_start')
    if not week_start:
        return jsonify({'error': 'Missing week_start parameter'}), 400

    try:
        datetime.strptime(week_start, '%Y-%m-%d')
    except ValueError:
        return jsonify({'error': 'Invalid week_start format'}), 400

    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()

        cursor.execute('SELECT id, 案件名, "PJNo.", PH FROM projects WHERE 不要 = 0')
        projects = [{'id': row[0], '案件名': row[1], 'PJNo.': row[2], 'PH': row[3]} for row in cursor.fetchall()]

        week_dates = get_week_dates(week_start)
        date_range = [d['date'] for d in week_dates]
        cursor.execute('''
            SELECT project_id, date, task_type, hours
            FROM daily_hours
            WHERE date IN ({})
        '''.format(','.join('?' * len(date_range))), date_range)
        hours = [{'project_id': row[0], 'date': row[1], 'task_type': row[2], 'hours': row[3]} for row in cursor.fetchall()]

        conn.close()
        return jsonify({
            'projects': projects,
            'week_dates': week_dates,
            'hours': hours
        })
    except sqlite3.Error as e:
        return jsonify({'error': f'Database error: {str(e)}'}), 500

@app.route('/save_daily_hours', methods=['POST'])
def save_daily_hours():
    """Lưu số giờ làm việc hàng ngày vào cơ sở dữ liệu."""
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    hours_data = data.get('hours', [])

    if not hours_data:
        return jsonify({'success': True})

    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()

        for entry in hours_data:
            cursor.execute('''
                DELETE FROM daily_hours
                WHERE project_id = ? AND date = ? AND task_type = ?
            ''', (entry['project_id'], entry['date'], entry['task_type']))

        for entry in hours_data:
            cursor.execute('''
                INSERT INTO daily_hours (project_id, date, task_type, hours)
                VALUES (?, ?, ?, ?)
            ''', (entry['project_id'], entry['date'], entry['task_type'], entry['hours']))

        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except sqlite3.Error as e:
        return jsonify({'error': f'Database error: {str(e)}'}), 500

@app.route('/delete_all_data', methods=['POST'])
def delete_all_data():
    """Delete all data from projects, copied_templates, and daily_hours tables."""
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    password = data.get('password')
    users = read_users()
    username = session['username']

    if users.get(username) != password:
        return jsonify({'error': 'パスワードが正しくありません'}), 400

    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM projects')
        cursor.execute('DELETE FROM copied_templates')
        cursor.execute('DELETE FROM daily_hours')
        cursor.execute('DELETE FROM sqlite_sequence')  # Reset auto-increment
        conn.commit()
        conn.close()
        #logging.info("All data deleted successfully")
        return jsonify({'success': True})
    except sqlite3.Error as e:
        #logging.error(f"Database error while deleting all data: {e}")
        return jsonify({'error': 'Database error'}), 500

@app.route('/logout')
def logout():
    """Handle user logout."""
    session.pop('username', None)
    flash('ログアウトしました', 'success')
    return redirect(url_for('login'))

def get_temp_dir(project_id):
    """Get the temporary directory path for a project."""
    return os.path.join('temp', str(project_id))

def get_replaced_dir(project_id):
    """Get the replaced files directory path for a project."""
    return os.path.join('replaced', str(project_id))

def read_placeholders(file_path='placeholders.txt'):
    """Read placeholder mappings from a text file, supporting context-dependent rules."""
    placeholders = {'simple': {}, 'context': []}
    try:
        encoding = detect_file_encoding(file_path)
        logging.debug(f"Detected encoding for {file_path}: {encoding}")
        with open(file_path, 'r', encoding=encoding) as f:
            for line_number, line in enumerate(f, 1):
                line = line.strip()
                if not line or '=' not in line:
                    logging.debug(f"Skipping empty or invalid line {line_number}: {line}")
                    continue
                try:
                    # Split on the first '=' to handle values containing '='
                    key, value = map(str.strip, line.split('=', 1))
                    if not key or not value:
                        logging.warning(f"Empty key or value at line {line_number}: {line}. Skipping.")
                        continue
                    # Check for context-dependent rule: [context|target]
                    if key.startswith('[') and key.endswith(']') and '|' in key[1:-1]:
                        try:
                            context, target = key[1:-1].split('|', 1)
                            if not context.strip() or not target.strip():
                                logging.warning(f"Empty context or target at line {line_number}: {line}. Skipping.")
                                continue
                            placeholders['context'].append({
                                'context': context.strip(),
                                'target': target.strip(),
                                'replacement': value.strip()
                            })
                        except ValueError:
                            logging.warning(f"Malformed context-dependent rule at line {line_number}: {line}. Skipping.")
                            continue
                    else:
                        # Handle as simple replacement, removing brackets if present
                        clean_key = key[1:-1].strip() if key.startswith('[') and key.endswith(']') else key.strip()
                        placeholders['simple'][clean_key] = value.strip()
                except Exception as e:
                    logging.warning(f"Error parsing line {line_number}: {line}. Skipping. Error: {str(e)}")
                    continue
        if not placeholders['simple'] and not placeholders['context']:
            logging.error(f"No valid rules found in {file_path}")
            return None
        logging.debug(f"Parsed placeholders: {placeholders}")
        return placeholders
    except UnicodeDecodeError as e:
        logging.error(f"Failed to decode {file_path} with {encoding}: {str(e)}")
        return None
    except FileNotFoundError:
        logging.error(f"Placeholder file {file_path} not found")
        return None
    except Exception as e:
        logging.error(f"Error reading placeholder file {file_path}: {str(e)}")
        return None

@app.route('/upload_mail_edit_files', methods=['POST'])
def upload_mail_edit_files():
    """Handle multiple text file uploads for mail editing."""
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    if 'files' not in request.files:
        return jsonify({'error': 'ファイルが選択されていません'}), 400

    files = request.files.getlist('files')
    if not files or all(file.filename == '' for file in files):
        return jsonify({'error': 'ファイルが選択されていません'}), 400

    temp_dir = 'temp'
    if not os.path.exists(temp_dir):
        os.makedirs(temp_dir)

    filenames = []
    try:
        for file in files:
            if not file.filename.endswith('.txt'):
                return jsonify({'error': 'テキストファイル（.txt）のみアップロード可能です'}), 400
            file_path = os.path.join(temp_dir, file.filename)
            file.save(file_path)
            filenames.append(file.filename)
        return jsonify({'success': True, 'filenames': filenames})
    except Exception as e:
        return jsonify({'error': f'ファイルのアップロードに失敗しました: {str(e)}'}), 500

def detect_file_encoding(file_path):
    """Detect the encoding of a file, prioritizing UTF-8 or Shift-JIS."""
    try:
        with open(file_path, 'rb') as f:
            raw_data = f.read()
        result = detect(raw_data)
        encoding = result.get('encoding', 'shift-jis')  # Default to Shift-JIS
        confidence = result.get('confidence', 0)
        # Restrict to UTF-8 or Shift-JIS, fallback to Shift-JIS if uncertain
        if encoding not in ['utf-8', 'shift-jis'] or confidence < 0.8:
            return 'shift-jis'
        return encoding
    except Exception as e:
        logging.error(f"Error detecting encoding for {file_path}: {str(e)}")
        return 'shift-jis'

@app.route('/replace_mail_content', methods=['POST'])
def replace_mail_content():
    """Replace strings in a specific uploaded text file (UTF-8 or Shift-JIS) and output in Shift-JIS, falling back to UTF-8 if needed."""
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    filename = data.get('filename')
    if not filename:
        return jsonify({'error': 'Missing filename'}), 400

    temp_dir = 'temp'
    replaced_dir = 'replaced'

    # Check if temp directory and file exist
    file_path = os.path.join(temp_dir, filename)
    if not os.path.exists(temp_dir) or not os.path.exists(file_path) or not filename.endswith('.txt'):
        return jsonify({'error': f'ファイル {filename} が見つかりません'}), 404

    # Create or clear replaced directory
    if os.path.exists(replaced_dir):
        shutil.rmtree(replaced_dir)
    os.makedirs(replaced_dir)

    # Read placeholders from file
    placeholders = read_placeholders()
    if not placeholders:
        return jsonify({'error': 'プレースホルダーファイルの読み込みに失敗しました。ファイルのエンコーディングを確認してください。'}), 500

    results = []
    failed_files = []
    try:
        # Detect file encoding
        encoding = detect_file_encoding(file_path)
        logging.debug(f"Detected encoding for {filename}: {encoding}")
        with open(file_path, 'r', encoding=encoding) as f:
            content = f.read()

        # Apply context-dependent replacements
        for rule in placeholders['context']:
            try:
                if not isinstance(rule, dict) or 'context' not in rule or 'target' not in rule or 'replacement' not in rule:
                    logging.error(f"Invalid context-dependent rule: {rule}. Skipping.")
                    failed_files.append(f"{filename}: 無効なコンテキスト依存ルールが見つかりました。placeholders.txtを確認してください。")
                    continue
                context = re.escape(rule['context'])
                target = re.escape(rule['target'])
                pattern = f'{context}\n{target}'
                replacement = f"{rule['context']}\n{rule['replacement']}"
                content = re.sub(pattern, replacement, content)
            except Exception as e:
                logging.error(f"Error applying context-dependent rule {rule}: {str(e)}")
                failed_files.append(f"{filename}: コンテキスト依存ルールの適用中にエラーが発生しました: {str(e)}")
                continue

        # Apply simple replacements
        for key, value in placeholders['simple'].items():
            content = content.replace(key, value)

        replaced_file_path = os.path.join(replaced_dir, filename)
        # Try writing in Shift-JIS first
        try:
            with open(replaced_file_path, 'w', encoding='shift-jis') as f:
                f.write(content)
            output_encoding = 'shift-jis'
        except UnicodeEncodeError as e:
            # Fallback to UTF-8 if Shift-JIS fails
            logging.warning(f"Failed to encode {filename} in Shift-JIS: {str(e)}. Falling back to UTF-8.")
            with open(replaced_file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            output_encoding = 'utf-8'
            failed_files.append(
                f"{filename}: Shift-JISでサポートされていない文字（例: ①）が含まれています。UTF-8で出力されました。"
            )

        # Convert content to UTF-8 for JSON response (for UI display)
        results.append(f"ファイル: {filename} (出力エンコーディング: {output_encoding})\n{content.encode('utf-8', errors='replace').decode('utf-8', errors='replace')}")

        if failed_files:
            error_message = "ファイルで問題が発生しました:\n" + "\n".join(failed_files)
            return jsonify({'success': True, 'results': results, 'warnings': error_message})

        return jsonify({'success': True, 'results': results})
    except UnicodeDecodeError as e:
        logging.error(f"Failed to decode {filename} with {encoding}: {str(e)}")
        return jsonify({'error': f"{filename}: エンコーディングが無効です（UTF-8またはShift-JISを期待）"}), 500
    except Exception as e:
        logging.error(f"Error processing {filename}: {str(e)}")
        return jsonify({'error': f"{filename}: 処理中にエラーが発生しました: {str(e)}"}), 500

@app.route('/download_replaced_files', methods=['GET'])
def download_replaced_files():
    """Download replaced files (Shift-JIS) as a ZIP archive."""
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    replaced_dir = 'replaced'
    if not os.path.exists(replaced_dir):
        return jsonify({'error': 'リプレースされたファイルが見つかりません'}), 404

    try:
        memory_file = io.BytesIO()
        with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
            for filename in os.listdir(replaced_dir):
                if filename.endswith('.txt'):
                    file_path = os.path.join(replaced_dir, filename)
                    zf.write(file_path, filename)
        memory_file.seek(0)

        # Clean up temp and replaced directories
        temp_dir = 'temp'
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        if os.path.exists(replaced_dir):
            shutil.rmtree(replaced_dir)

        return app.response_class(
            memory_file,
            mimetype='application/zip',
            headers={'Content-Disposition': 'attachment; filename=replaced_mail_templates.zip'}
        )
    except Exception as e:
        return jsonify({'error': f'ダウンロードに失敗しました: {str(e)}'}), 500

@app.route('/get_mail_template_list', methods=['GET'])
def get_mail_template_list():
    """Lấy danh sách các file txt trong thư mục mail."""
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        templates = get_mail_templates()
        return jsonify({'templates': [full_name for full_name, _ in templates]})
    except Exception as e:
        logging.error(f"Error fetching mail template list: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/save_replaced_file', methods=['POST'])
def save_replaced_file():
    """Lưu nội dung đã replace vào thư mục mail với tên file được chỉ định."""
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    filename = data.get('filename')
    content = data.get('content')
    overwrite = data.get('overwrite', False)

    if not filename or not content:
        return jsonify({'error': 'Missing filename or content'}), 400

    if not filename.endswith('.txt'):
        filename += '.txt'

    file_path = os.path.join(MAIL_DIR, filename)
    try:
        if os.path.exists(file_path) and not overwrite:
            return jsonify({'error': f'File {filename} already exists. Use overwrite option.'}), 400

        if not os.path.exists(MAIL_DIR):
            os.makedirs(MAIL_DIR)

        # Viết file với encoding Shift-JIS, fallback sang UTF-8 nếu cần
        try:
            with open(file_path, 'w', encoding='shift-jis') as f:
                f.write(content)
            encoding = 'shift-jis'
        except UnicodeEncodeError:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            encoding = 'utf-8'

        return jsonify({'success': True, 'encoding': encoding})
    except Exception as e:
        logging.error(f"Error saving replaced file {filename}: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/clear_temp_files', methods=['POST'])
def clear_temp_files():
    """Xóa tất cả file trong thư mục temp và replaced."""
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    temp_dir = 'temp'
    replaced_dir = 'replaced'
    try:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        if os.path.exists(replaced_dir):
            shutil.rmtree(replaced_dir)
        return jsonify({'success': True})
    except Exception as e:
        logging.error(f"Error clearing temp files: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/delete_mail_template', methods=['POST'])
def delete_mail_template():
    """Delete a mail template file from the mail directory."""
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    filename = data.get('filename')

    print(f"file name: {filename}")

    if not filename:
        logging.error("Missing filename in delete_mail_template request")
        return jsonify({'error': 'Missing filename'}), 400

    if not filename.endswith('.txt'):
        logging.error(f"Invalid file extension for {filename}: Only .txt files are allowed")
        return jsonify({'error': 'Only .txt files are allowed'}), 400

    # Prevent path traversal attacks
    if '..' in filename or filename.startswith('/') or filename.startswith('\\'):
        logging.error(f"Invalid filename detected: {filename}")
        return jsonify({'error': 'Invalid filename'}), 400

    file_path = os.path.join(MAIL_DIR, filename)
    print(f"file path: {file_path}")

    if not os.path.exists(file_path):
        print("go 4")
        logging.error(f"File not found: {file_path}")
        return jsonify({'error': 'File not found'}), 404

    try:
        print("go 1")
        os.remove(file_path)
        print("go 2")
        logging.info(f"Successfully deleted mail template: {file_path}")
        return jsonify({'success': True})
    except Exception as e:
        print("go 3")
        logging.error(f"Error deleting mail template {filename}: {str(e)}")
        return jsonify({'error': f'Failed to delete file: {str(e)}'}), 500

@app.route('/rename_mail_template', methods=['POST'])
def rename_mail_template():
    """Rename a mail template file in the mail directory."""
    if 'username' not in session:
        logging.error("Unauthorized access to /rename_mail_template")
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    old_filename = data.get('old_filename')
    new_filename = data.get('new_filename')

    logging.info(f"Received rename request: old_filename={old_filename}, new_filename={new_filename}")

    if not old_filename or not new_filename:
        logging.error("Missing old_filename or new_filename")
        return jsonify({'error': 'Old and new filenames are required'}), 400

    old_filepath = os.path.join(MAIL_DIR, old_filename)
    new_filepath = os.path.join(MAIL_DIR, new_filename)

    logging.info(f"Attempting to rename {old_filepath} to {new_filepath}")

    try:
        if not os.path.exists(MAIL_DIR):
            logging.error(f"Mail directory does not exist: {MAIL_DIR}")
            return jsonify({'error': f'Mail directory does not exist'}), 500

        if not os.path.exists(old_filepath):
            logging.error(f"Source file does not exist: {old_filepath}")
            return jsonify({'error': f'File {old_filename} does not exist'}), 404

        if os.path.exists(new_filepath):
            logging.error(f"Destination file already exists: {new_filepath}")
            return jsonify({'error': f'File {new_filename} already exists'}), 400

        os.rename(old_filepath, new_filepath)
        logging.info(f"Successfully renamed {old_filename} to {new_filename}")
        return jsonify({'success': True})
    except Exception as e:
        logging.error(f"Error renaming mail template: {str(e)}")
        return jsonify({'error': str(e)}), 500

def validate_email(email):
    """Validate email format."""
    if not email:
        return True  # Allow empty emails
    email_regex = r'^[^\s@]+@[^\s@]+\.[^\s@]+$'
    return bool(re.match(email_regex, email))

@app.route('/get_email_data', methods=['GET'])
def get_email_data():
    """Fetch SE email addresses and manager info."""
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        # Fetch unique SE names from projects table
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('SELECT DISTINCT SE FROM projects WHERE SE IS NOT NULL AND SE != ""')
        se_names = [row[0] for row in cursor.fetchall()]
        conn.close()

        # Read SE_email.csv
        se_email_file = os.path.join(MAIL_DIR, 'SE_email.csv')
        se_emails = []
        if os.path.exists(se_email_file):
            df = pd.read_csv(se_email_file, encoding='utf-8')
            df = df.fillna({'email': ''})
            # Include all SE names from DB, even if not in CSV
            existing_se = set(df['SE'].tolist())
            for se in se_names:
                if se in existing_se:
                    email = df[df['SE'] == se]['email'].iloc[0]
                else:
                    email = ''
                se_emails.append({'SE': se, 'email': email})
        else:
            # If CSV doesn't exist, create entries with empty emails
            se_emails = [{'SE': se, 'email': ''} for se in se_names]

        # Read kanrisha.csv
        kanrisha_file = os.path.join(MAIL_DIR, 'kanrisha.csv')
        manager = None
        if os.path.exists(kanrisha_file):
            df = pd.read_csv(kanrisha_file, encoding='utf-8')
            if not df.empty:
                manager = {'name': df['name'].iloc[0], 'email': df['email'].iloc[0]}

        return jsonify({'se_emails': se_emails, 'manager': manager})
    except Exception as e:
        logging.error(f"Error fetching email data: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/save_email_data', methods=['POST'])
def save_email_data():
    """Save SE email addresses and manager info to CSV files."""
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        data = request.get_json()
        se_emails = data.get('se_emails', [])
        manager = data.get('manager', {})

        # Validate SE emails
        for se in se_emails:
            if not validate_email(se.get('email')):
                return jsonify({'error': f"無効なメールアドレスです: {se.get('SE')} のメールアドレスを確認してください"}), 400

        # Validate manager email
        if manager.get('email') and not validate_email(manager.get('email')):
            return jsonify({'error': '無効な管理者メールアドレスです。メールアドレスを確認してください'}), 400

        # Ensure MAIL_DIR exists
        if not os.path.exists(MAIL_DIR):
            os.makedirs(MAIL_DIR)

        # Save SE_email.csv
        se_email_file = os.path.join(MAIL_DIR, 'SE_email.csv')
        df_se = pd.DataFrame(se_emails)
        df_se.to_csv(se_email_file, index=False, encoding='utf-8')

        # Save kanrisha.csv
        kanrisha_file = os.path.join(MAIL_DIR, 'kanrisha.csv')
        df_manager = pd.DataFrame([manager] if manager else [], columns=['name', 'email'])
        df_manager.to_csv(kanrisha_file, index=False, encoding='utf-8')

        return jsonify({'success': True})
    except Exception as e:
        logging.error(f"Error saving email data: {str(e)}")
        return jsonify({'error': str(e)}), 500

# Hàm hỗ trợ
def detect_file_encoding(file_path):
    """Detect file encoding using chardet."""
    with open(file_path, 'rb') as f:
        raw_data = f.read()
        result = chardet.detect(raw_data)
        return result['encoding'] or 'utf-8'

def parse_date_from_db(date_str):
    """Parse date string from database."""
    if not date_str or date_str == '':
        return None
    try:
        return datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError:
        return None

def format_date_jp(date_obj):
    """Format date to Japanese format (YYYY/MM/DD)."""
    if date_obj is None:
        return ''
    return date_obj.strftime('%Y/%m/%d')

def convert_nat_to_none(project_dict):
    """Convert NaT or None-like values to None."""
    for key, value in project_dict.items():
        if value is None or value != value:  # NaN/NaT check
            project_dict[key] = ''
    return project_dict

def read_se_emails():
    """Read SE emails from mail/SE_email.csv."""
    se_emails = {}
    file_path = os.path.join(MAIL_DIR, 'SE_email.csv')
    if not os.path.exists(file_path):
        return se_emails

    encoding = detect_file_encoding(file_path)
    try:
        with open(file_path, 'r', encoding=encoding) as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:  # Kiểm tra nếu file rỗng
                return se_emails
            for row in reader:
                if row['email']:  # Chỉ thêm nếu email không rỗng
                    se_emails[row['SE']] = row['email']
        return se_emails
    except Exception as e:
        #logging.error(f"Failed to read SE_email.csv: {e}")
        return se_emails

def read_manager_email():
    """Read manager email from mail/kanrisha.csv."""
    file_path = os.path.join(MAIL_DIR, 'kanrisha.csv')
    if not os.path.exists(file_path):
        return ''

    encoding = detect_file_encoding(file_path)
    try:
        with open(file_path, 'r', encoding=encoding) as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:  # Kiểm tra nếu file rỗng
                return ''
            for row in reader:
                return row['email'] if row['email'] else ''  # Trả về email đầu tiên, nếu có
        return ''
    except Exception as e:
        #logging.error(f"Failed to read kanrisha.csv: {e}")
        return ''

@app.route('/get_copied_templates/<int:project_id>', methods=['GET'])
def get_copied_templates(project_id):
    """Get list of copied templates for a project."""
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT filename FROM copied_templates
            WHERE project_id = ?
        ''', (project_id,))
        templates = [row[0] for row in cursor.fetchall()]
        conn.close()
        #logging.debug(f"Copied templates for project_id={project_id}: {templates}")
        return jsonify({'templates': templates})
    except sqlite3.Error as e:
        #logging.error(f"Database error while fetching copied templates: {e}")
        return jsonify({'error': 'Database error'}), 500

@app.route('/get_mail_content/<int:project_id>/<filename>', methods=['GET'])
def get_mail_content(project_id, filename):
    """Get mail template content and replace placeholders including {mail}."""
    if '..' in filename or filename.startswith('/') or filename.startswith('\\'):
        #logging.error(f"Invalid filename detected: {filename}")
        return jsonify({'error': 'Invalid filename'}), 400
    file_path = os.path.join(MAIL_DIR, filename)
    if not os.path.exists(file_path) or not filename.endswith('.txt'):
        #logging.error(f"File not found or invalid: {file_path}")
        return jsonify({'error': 'File not found or not a .txt file'}), 404

    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM projects WHERE id = ?', (project_id,))
        project = cursor.fetchone()
        columns = [description[0] for description in cursor.description]
        conn.close()
    except sqlite3.Error as e:
        #logging.error(f"Database error: {e}")
        return jsonify({'error': 'Database error'}), 500

    if not project:
        #logging.error(f"Project not found for ID: {project_id}")
        return jsonify({'error': 'プロジェクトが見つかりません'}), 404

    project_dict = {col: project[i] for i, col in enumerate(columns)}
    project_dict = convert_nat_to_none(project_dict)

    encoding = detect_file_encoding(file_path)
    try:
        with open(file_path, 'r', encoding=encoding) as f:
            content = f.read()
    except Exception as e:
        #logging.error(f"Failed to read file {file_path}: {e}")
        return jsonify({'error': f'ファイルの読み込みに失敗しました: {str(e)}'}), 500

    # Đọc email của SE và quản lý
    se_emails = read_se_emails()
    manager_email = read_manager_email()
    se_name = project_dict.get('SE', '')

    # Chuẩn bị placeholders
    pjno_value = project_dict.get('PJNo.', '')
    if isinstance(pjno_value, (float, int)):
        pjno_value = str(int(pjno_value))
    else:
        pjno_value = str(pjno_value)

    ph = project_dict.get('PH', '')
    if ph != '':
        ph = ' PH' + ph

    placeholders = {
        '{anken_name}': project_dict.get('案件名', ''),
        '{se_name}': se_name,
        '{pj}': pjno_value,
        '{開発工数}': project_dict.get('開発工数（h）', ''),
        '{PH}': ph
    }

    # Chỉ thêm {mail} vào placeholders nếu cả se_emails và manager_email đều hợp lệ
    if se_emails and manager_email and se_name in se_emails and se_emails[se_name]:
        mail_value = f"{se_emails[se_name]}, {manager_email}"
        placeholders['{mail}'] = mail_value

    for date_col in DATE_COLUMNS_DB:
        date_str = project_dict.get(date_col, '')
        date_obj = parse_date_from_db(date_str)
        placeholders[f'{{{date_col}}}'] = format_date_jp(date_obj)

    for key, value in placeholders.items():
        content = content.replace(key, str(value))
    return jsonify({'content': content})

@app.route('/save_copied_template', methods=['POST'])
def save_copied_template():
    """Save copied mail template to database."""
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json()
    project_id = data.get('project_id')
    filename = data.get('filename')
    if not project_id or not filename:
        #logging.error(f"Missing project_id or filename: project_id={project_id}, filename={filename}")
        return jsonify({'error': 'Missing project_id or filename'}), 400
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO copied_templates (project_id, filename, copied_at)
            VALUES (?, ?, ?)
        ''', (project_id, filename, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()
        conn.close()
        #logging.debug(f"Saved copied template: project_id={project_id}, filename={filename}")
        return jsonify({'success': True})
    except sqlite3.Error as e:
        #logging.error(f"Database error while saving copied template: {e}")
        return jsonify({'error': 'Database error'}), 500

@app.route('/remove_copied_template', methods=['POST'])
def remove_copied_template():
    """Remove a copied mail template from the database."""
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json()
    project_id = data.get('project_id')
    filename = data.get('filename')
    if not project_id or not filename:
        #logging.error(f"Missing project_id or filename: project_id={project_id}, filename={filename}")
        return jsonify({'error': 'Missing project_id or filename'}), 400
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('''
            DELETE FROM copied_templates
            WHERE project_id = ? AND filename = ?
        ''', (project_id, filename))
        if cursor.rowcount == 0:
            conn.close()
            #logging.debug(f"No copied template found: project_id={project_id}, filename={filename}")
            return jsonify({'error': 'Template not found'}), 404
        conn.commit()
        conn.close()
        #logging.debug(f"Removed copied template: project_id={project_id}, filename={filename}")
        return jsonify({'success': True})
    except sqlite3.Error as e:
        conn.close()
        #logging.error(f"Database error while removing copied template: {e}")
        return jsonify({'error': 'Database error'}), 500


if __name__ == '__main__':
    app.run(debug=True)