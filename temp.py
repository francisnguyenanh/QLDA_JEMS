import os
import sqlite3
import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
import hashlib
import re

app = Flask(__name__)
app.secret_key = 'your_secret_key'  # Thay bằng khóa bí mật thực tế
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Định nghĩa hằng số
DB_FILE = 'projects.db'
PROJECT_DIR = 'projects'
OLD_DIR = os.path.join(PROJECT_DIR, 'old')
ALLOWED_EXTENSIONS = {'xlsx', 'xls', 'txt'}
DATE_COLUMNS_DB = [
    '要件引継', '設計開始', '設計完了', '設計書送付', '開発開始', '開発完了',
    'テスト開始日', 'テスト完了日', 'FB完了予定日', 'SE納品'
]
DATE_COLUMNS_DISPLAY = [
    '要件引継', '設計開始', '設計完了', '設計書送付', '開発開始', '開発完了',
    'テスト開始日', 'テスト完了日', 'FB完了予定日', 'SE納品'
]
DISPLAY_COLUMNS = [
    'ステータス', '案件名', '要件引継', '設計開始', '設計完了', '設計書送付', '開発開始',
    '開発完了', 'テスト開始日', 'テスト完了日', 'FB完了予定日', 'SE納品', 'タスク', 'SE',
    'BSE', '案件番号', 'PJNo.', 'PH', '開発工数（h）', '設計工数（h）', 'ページ数',
    '注文設計', '注文テスト', '注文FB', '注文BrSE', '備考'
]
VALID_STATUSES = [
    '要件引継待ち', '設計中', '設計完了', '開発中', 'テスト中', 'FB待ち', '納品済み'
]
PAGE_RANGE = {
    (1, 5): {'test': 2, 'fb': 2},
    (6, 10): {'test': 3, 'fb': 3},
    (11, 20): {'test': 4, 'fb': 4},
    (21, float('inf')): {'test': 5, 'fb': 5}
}
WORKING_DAYS = set([
    '2024-01-01', '2024-12-25'  # Ví dụ, thay bằng danh sách ngày làm việc thực tế
])

def allowed_file(filename):
    """Kiểm tra xem file có phần mở rộng hợp lệ không."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def init_db():
    """Khởi tạo cơ sở dữ liệu SQLite với các bảng projects, copied_templates và daily_hours."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # Bảng projects
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
    # Bảng copied_templates
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS copied_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            filename TEXT,
            copied_at TEXT,
            FOREIGN KEY (project_id) REFERENCES projects(id)
        )
    ''')
    # Bảng daily_hours
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
    # Kiểm tra và thêm cột nếu chưa tồn tại
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
    """Đọc danh sách người dùng từ file users.txt."""
    users = {}
    try:
        with open('users.txt', 'r', encoding='utf-8') as f:
            for line in f:
                if ':' in line:
                    username, password = line.strip().split(':', 1)
                    users[username] = password
    except FileNotFoundError:
        pass
    return users

def hash_password(password):
    """Tạo hash SHA-256 cho mật khẩu."""
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

def parse_date_from_db(date_str):
    """Chuyển đổi chuỗi ngày từ DB sang đối tượng datetime."""
    if not date_str or pd.isna(date_str):
        return None
    try:
        if isinstance(date_str, str):
            if re.match(r'^\d{4}/\d{2}/\d{2}$', date_str):
                return datetime.strptime(date_str, '%Y/%m/%d')
            elif re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
                return datetime.strptime(date_str, '%Y-%m-%d')
            elif re.match(r'^\d{4}/\d{2}/\d{2}\(\w+\)$', date_str):
                date_str = date_str.split('(')[0]
                return datetime.strptime(date_str, '%Y/%m/%d')
        return None
    except (ValueError, TypeError):
        return None

def parse_date_for_comparison(date_str):
    """Chuyển đổi chuỗi ngày để so sánh."""
    date_obj = parse_date_from_db(date_str)
    return date_obj if date_obj else datetime.max

def format_date_jp(date_obj):
    """Định dạng ngày theo kiểu Nhật Bản."""
    if date_obj is None or pd.isna(date_obj):
        return ''
    if isinstance(date_obj, str):
        date_obj = parse_date_from_db(date_obj)
    if date_obj:
        weekday = ['月', '火', '水', '木', '金', '土', '日'][date_obj.weekday()]
        return date_obj.strftime('%Y/%m/%d') + f'({weekday})'
    return ''

def convert_nat_to_none(project):
    """Chuyển đổi NaT và None thành chuỗi rỗng."""
    for key in project:
        if pd.isna(project[key]) or project[key] is None:
            project[key] = ''
    return project

def get_mail_templates():
    """Lấy danh sách template email."""
    templates = []
    if os.path.exists(PROJECT_DIR):
        for filename in os.listdir(PROJECT_DIR):
            if filename.endswith('.txt'):
                display_name = filename.replace('.txt', '')
                templates.append((filename, display_name))
    return sorted(templates, key=lambda x: x[1])

def read_projects():
    """Đọc tất cả dự án từ cơ sở dữ liệu với tổng số giờ làm việc."""
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

def update_project(project_id, updates):
    """Cập nhật thông tin dự án trong cơ sở dữ liệu."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    query = 'UPDATE projects SET '
    params = []
    for key, value in updates.items():
        if key in ['不要', '注文設計', '注文テスト', '注文FB', '注文BrSE']:
            value = 1 if value in ['on', '1', 1, True] else 0
        elif key in ['開発工数（h）', '設計工数（h）', 'ページ数']:
            value = float(value) if value else 0
        query += f'"{key}" = ?, '
        params.append(value)
    query = query.rstrip(', ') + ' WHERE id = ?'
    params.append(project_id)
    cursor.execute(query, params)
    if 'ステータス' in updates:
        cursor.execute('UPDATE projects SET user_edited_status = 1 WHERE id = ?', (project_id,))
    conn.commit()
    conn.close()

def calculate_status(project, current_date):
    """Tính toán trạng thái dự án dựa trên ngày hiện tại."""
    if project.get('user_edited_status', 0) == 1:
        return project.get('ステータス', '要件引継待ち')

    for date_col in DATE_COLUMNS_DB:
        date_str = project.get(date_col)
        date_obj = parse_date_from_db(date_str)
        if date_obj and date_obj.date() <= current_date.date():
            if date_col == 'SE納品':
                return '納品済み'
            elif date_col == 'FB完了予定日':
                return 'FB待ち'
            elif date_col == 'テスト開始日':
                return 'テスト中'
            elif date_col == '開発開始':
                return '開発中'
            elif date_col == '設計開始':
                return '設計中'
    return '要件引継待ち'

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

@app.route('/')
def index():
    """Chuyển hướng đến trang dashboard."""
    return redirect(url_for('dashboard'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Xử lý đăng nhập người dùng."""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        users = read_users()
        stored_password = users.get(username)
        if stored_password and stored_password == hash_password(password):
            session['username'] = username
            flash('ログイン成功', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('ユーザー名またはパスワードが正しくありません', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    """Xử lý đăng xuất người dùng."""
    session.pop('username', None)
    flash('ログアウトしました', 'success')
    return redirect(url_for('login'))

@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    """Hiển thị dashboard với dữ liệu dự án và xử lý cập nhật."""
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
        if not show_unnecessary and row.get('不要', 0) == 1:
            continue

        se_delivery_date = parse_date_from_db(row['SE納品'])
        if not show_all and se_delivery_date and se_delivery_date.date() < current_date.date():
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
    return render_template('dashboard.html',
                           projects=filtered_projects,
                           display_columns=DISPLAY_COLUMNS,
                           date_columns=DATE_COLUMNS_DISPLAY,
                           show_all=show_all,
                           show_unnecessary=show_unnecessary,
                           mail_templates=mail_templates,
                           valid_statuses=VALID_STATUSES)

@app.route('/upload', methods=['POST'])
def upload_file():
    """Xử lý tải lên file Excel."""
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    if 'file' not in request.files:
        flash('ファイルが選択されていません', 'danger')
        return redirect(url_for('dashboard'))

    file = request.files['file']
    if file.filename == '':
        flash('ファイルが選択されていません', 'danger')
        return redirect(url_for('dashboard'))

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        new_filename = f"{timestamp}_{filename}"
        file_path = os.path.join(PROJECT_DIR, new_filename)
        file.save(file_path)

        try:
            df = pd.read_excel(file_path)
            print(df)
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            for _, row in df.iterrows():
                row = row.where(pd.notnull(row), None)
                tasks = ','.join([t for t in ['設計', 'Brse', 'テスト', 'FB'] if row.get(t) == '○'])
                cursor.execute('''
                    INSERT INTO projects (
                        SE, 案件名, PH, "開発工数（h）", "設計工数（h）", 要件引継, 設計開始,
                        設計完了, 設計書送付, 開発開始, 開発完了, SE納品, BSE, 案件番号,
                        "PJNo.", 備考, テスト開始日, テスト完了日, FB完了予定日, ページ数,
                        タスク, ステータス, 不要, 注文設計, 注文テスト, 注文FB, 注文BrSE
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    row.get('SE'), row.get('案件名'), row.get('PH'), row.get('開発工数（h）'),
                    row.get('設計工数（h）'), row.get('要件引継'), row.get('設計開始'),
                    row.get('設計完了'), row.get('設計書送付'), row.get('開発開始'),
                    row.get('開発完了'), row.get('SE納品'), row.get('BSE'), row.get('案件番号'),
                    row.get('PJNo.'), row.get('備考'), row.get('テスト開始日'),
                    row.get('テスト完了日'), row.get('FB完了予定日'), row.get('ページ数'),
                    tasks, '要件引継待ち', 0,
                    1 if row.get('注文設計') == '○' else 0,
                    1 if row.get('注文テスト') == '○' else 0,
                    1 if row.get('注文FB') == '○' else 0,
                    1 if row.get('注文BrSE') == '○' else 0
                ))
            conn.commit()
            conn.close()
            print("OK")
            flash('ファイルが正常にアップロードされました', 'success')
        except Exception as e:
            flash(f'エラー: ファイルの処理中にエラーが発生しました - {str(e)}', 'danger')
            print(e)
        return redirect(url_for('dashboard'))
    else:
        flash('許可されていないファイル形式です', 'danger')
        print("NG2")
        return redirect(url_for('dashboard'))

@app.route('/upload_mail_template', methods=['POST'])
def upload_mail_template():
    """Xử lý tải lên template email."""
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    if 'file' not in request.files:
        return jsonify({'error': 'ファイルが選択されていません'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'ファイルが選択されていません'}), 400

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file_path = os.path.join(PROJECT_DIR, filename)
        file.save(file_path)
        return jsonify({'success': True})
    else:
        return jsonify({'error': '許可されていないファイル形式です'}), 400

@app.route('/get_mail_content/<int:project_id>/<filename>')
def get_mail_content(project_id, filename):
    """Lấy nội dung template email và thay thế placeholder."""
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    file_path = os.path.join(PROJECT_DIR, secure_filename(filename))
    if not os.path.exists(file_path):
        return jsonify({'error': 'テンプレートファイルが見つかりません'}), 404

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM projects WHERE id = ?', (project_id,))
    project = cursor.fetchone()
    conn.close()

    if not project:
        return jsonify({'error': 'プロジェクトが見つかりません'}), 404

    columns = [description[0] for description in cursor.description]
    project_dict = dict(zip(columns, project))

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        placeholders = {
            '{案件名}': project_dict.get('案件名', ''),
            '{se名}': project_dict.get('SE', ''),
            '{pj}': project_dict.get('PJNo.', ''),
            '{開発完了}': format_date_jp(project_dict.get('開発完了')),
            '{SE納品}': format_date_jp(project_dict.get('SE納品'))
        }
        for placeholder, value in placeholders.items():
            content = content.replace(placeholder, str(value))
        return jsonify({'content': content})
    except Exception as e:
        return jsonify({'error': f'ファイルの読み込みに失敗しました: {str(e)}'}), 500

@app.route('/save_copied_template', methods=['POST'])
def save_copied_template():
    """Lưu thông tin template đã được sao chép."""
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    project_id = data.get('project_id')
    filename = data.get('filename')
    copied_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO copied_templates (project_id, filename, copied_at)
        VALUES (?, ?, ?)
    ''', (project_id, filename, copied_at))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/get_copied_templates/<int:project_id>')
def get_copied_templates(project_id):
    """Lấy danh sách template đã sao chép cho dự án."""
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT filename FROM copied_templates WHERE project_id = ?', (project_id,))
    templates = [row[0] for row in cursor.fetchall()]
    conn.close()
    return jsonify({'templates': templates})

@app.route('/sort_projects', methods=['POST'])
def sort_projects():
    """Sắp xếp dự án theo cột và hướng được chỉ định."""
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        data = request.get_json()
        column = data.get('column')
        direction = data.get('direction', 'asc').lower()
        show_all = data.get('show_all', False)
        show_unnecessary = data.get('show_unnecessary', False)

        valid_columns = DISPLAY_COLUMNS + ['id', '設計実績', 'テスト実績', 'FB実績', 'BrSE実績']
        if column not in valid_columns:
            return jsonify({'error': 'Invalid column'}), 400
        if direction not in ['asc', 'desc']:
            return jsonify({'error': 'Invalid direction'}), 400

        df = read_projects()
        current_date = datetime.now()
        filtered_projects = []

        for _, row in df.iterrows():
            if not show_unnecessary and row.get('不要', 0) == 1:
                continue

            se_delivery_date = parse_date_from_db(row['SE納品'])
            if not show_all and se_delivery_date and se_delivery_date.date() < current_date.date():
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
        return jsonify({'error': str(e)}), 500

@app.route('/calculate_test_dates', methods=['POST'])
def calculate_test_dates():
    """Tính toán ngày hoàn thành kiểm thử và FB."""
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    page_count = data.get('page_count')
    test_start_date = data.get('test_start_date')

    if not page_count or not test_start_date:
        return jsonify({'error': 'Missing parameters'}), 400

    try:
        page_count = int(page_count)
        test_start = datetime.strptime(test_start_date, '%Y-%m-%d')
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid parameters'}), 400

    for (min_pages, max_pages), durations in PAGE_RANGE.items():
        if min_pages <= page_count <= max_pages:
            test_days = durations['test']
            fb_days = durations['fb']
            current_date = test_start
            test_days_counted = 0
            while test_days_counted < test_days:
                current_date += timedelta(days=1)
                if current_date.strftime('%Y-%m-%d') in WORKING_DAYS:
                    test_days_counted += 1
            test_completion = current_date
            current_date += timedelta(days=1)
            fb_days_counted = 0
            while fb_days_counted < fb_days:
                current_date += timedelta(days=1)
                if current_date.strftime('%Y-%m-%d') in WORKING_DAYS:
                    fb_days_counted += 1
            fb_completion = current_date
            return jsonify({
                'test_completion_date': test_completion.strftime('%Y-%m-%d'),
                'fb_completion_date': fb_completion.strftime('%Y-%m-%d')
            })
    return jsonify({'error': 'Invalid page count'}), 400

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

        cursor.execute('SELECT id, 案件名 FROM projects WHERE 不要 = 0')
        projects = [{'id': row[0], '案件名': row[1]} for row in cursor.fetchall()]

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
    """Xóa tất cả dữ liệu từ các bảng projects, copied_templates và daily_hours."""
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    password = data.get('password')
    users = read_users()
    username = session['username']

    if users.get(username) != hash_password(password):
        return jsonify({'error': 'パスワードが正しくありません'}), 400

    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM projects')
        cursor.execute('DELETE FROM copied_templates')
        cursor.execute('DELETE FROM daily_hours')
        cursor.execute('DELETE FROM sqlite_sequence')
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except sqlite3.Error as e:
        return jsonify({'error': 'Database error'}), 500

if __name__ == '__main__':
    app.run(debug=True)