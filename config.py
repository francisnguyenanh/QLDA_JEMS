"""
Centralized configuration — all constants and environment-aware settings live here.
Import this module instead of scattering magic strings / numbers through the codebase.
"""
import os
from pathlib import Path

# ── Load .env if present (optional dependency: python-dotenv) ────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / '.env')
except ImportError:
    pass  # python-dotenv is optional; falls back to OS env / defaults

# ── Flask ─────────────────────────────────────────────────────────────────────
SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
DEBUG       = os.getenv('FLASK_DEBUG', 'true').lower() == 'true'
PORT        = int(os.getenv('PORT', '5020'))

# ── Database ──────────────────────────────────────────────────────────────────
DB_FILE = os.getenv('DB_FILE', 'projects.db')

# ── Directories ───────────────────────────────────────────────────────────────
MAIL_DIR        = os.getenv('MAIL_DIR',        'mail')
PROJECT_DIR     = os.getenv('PROJECT_DIR',     'project')
OLD_DIR         = os.getenv('OLD_DIR',         'old')
FILEUPLOAD_DIR  = os.getenv('FILEUPLOAD_DIR',  'fileupload')
TEMP_DIR        = os.getenv('TEMP_DIR',        'temp')
REPLACED_DIR    = os.getenv('REPLACED_DIR',    'replaced')
GEN_SCRIPT_DIR  = os.getenv('GEN_SCRIPT_DIR',  'gen_script')
TEMP_COMPARE    = os.getenv('TEMP_COMPARE',    'temp_compare')

# ── Business constants ────────────────────────────────────────────────────────
FB_WORKING_DAYS_DEFAULT = 9   # fallback when config.txt is missing / invalid

# Column sets
DISPLAY_COLUMNS = [
    'ステータス', '案件名', 'PH', '要件引継', '設計開始',
    '設計完了', '設計書送付', '開発開始', '開発完了', 'テスト開始日', 'テスト完了日',
    'FB完了予定日', 'SE納品', 'タスク', 'SE', 'SE(sub)', 'BSE', '案件番号', 'PJNo.',
    '開発工数（h）', '設計工数（h）', 'ページ数', '注文設計', '注文テスト', '注文FB', '注文BrSE', '並行テスト', '備考', '履歴',
]

DATE_COLUMNS_DB = [
    '要件引継', '設計開始', '設計完了', '設計書送付', '開発開始', '開発完了',
    'テスト開始日', 'テスト完了日', 'FB完了予定日', 'SE納品',
]

DATE_COLUMNS_DISPLAY = DATE_COLUMNS_DB.copy()

VALID_STATUSES = [
    '要件引継待ち', '設計中', 'SE送付済', '開発中', 'テスト中', 'FB対応中', 'SE納品済',
]

STATUS_PRIORITY = {
    '設計中':     1,
    'SE送付済':   2,
    '開発中':     3,
    'テスト中':   4,
    'FB対応中':   5,
    '要件引継待ち': 6,
    'SE納品済':   7,
}

# ── Pagination ────────────────────────────────────────────────────────────────
DEFAULT_PAGE_SIZE = 50

# ── Cache TTL (seconds) ───────────────────────────────────────────────────────
PROJECTS_CACHE_TTL = 30   # invalidate after 30 s of inactivity
