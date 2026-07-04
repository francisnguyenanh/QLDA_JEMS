"""
Date calculation helpers — extracted from app.py (B1).
All pure functions, no Flask dependency.
"""
import logging
from datetime import datetime, timedelta

import pandas as pd
from pandas import isna

from config import (
    DATE_COLUMNS_DB, DATE_COLUMNS_DISPLAY, VALID_STATUSES, STATUS_PRIORITY,
    FB_WORKING_DAYS_DEFAULT,
)


# ── Date parsing ──────────────────────────────────────────────────────────────

def parse_date_from_db(date_str) -> datetime | None:
    """Parse YYYY-MM-DD or YYYY/MM/DD string from DB to datetime."""
    if isna(date_str) or date_str is None or date_str == '':
        return None
    for fmt in ('%Y-%m-%d', '%Y/%m/%d'):
        try:
            return datetime.strptime(date_str, fmt)
        except (ValueError, TypeError):
            continue
    return None


def parse_date_for_comparison(date_str) -> datetime | None:
    """Parse date supporting YYYY/MM/DD(曜日) and YYYY-MM-DD."""
    if isna(date_str) or date_str is None or date_str == '':
        return None
    if isinstance(date_str, datetime):
        return date_str
    if '(' in date_str:
        date_str = date_str.split('(')[0]
    for fmt in ('%Y/%m/%d', '%Y-%m-%d'):
        try:
            return datetime.strptime(date_str, fmt)
        except (ValueError, TypeError):
            continue
    return None


def format_date_jp(date: datetime | None) -> str:
    """Format datetime to YYYY/MM/DD(曜日)."""
    if date is None:
        return ''
    weekdays = ['月', '火', '水', '木', '金', '土', '日']
    return date.strftime('%Y/%m/%d') + f'({weekdays[date.weekday()]})'


# ── Data normalization ────────────────────────────────────────────────────────

CHECKBOX_FIELDS = ['不要', '注文設計', '注文テスト', '注文FB', '注文BrSE', '並行テスト', 'user_edited_status']


def convert_nat_to_none(project_dict: dict) -> dict:
    """Normalize NaT/NaN/None values; convert checkbox fields to ○/blank."""
    for key, value in project_dict.items():
        if isna(value) or value is None:
            project_dict[key] = 0 if key in CHECKBOX_FIELDS else ''
        elif key == 'PJNo.':
            project_dict[key] = str(int(value)) if isinstance(value, (float, int)) else str(value)
        elif isinstance(value, (float, int)) and key not in CHECKBOX_FIELDS:
            project_dict[key] = str(value)
        elif key in ['注文設計', '注文テスト', '注文FB', '注文BrSE', '並行テスト']:
            project_dict[key] = '○' if value == 1 else ''
        if key == 'fb_late':
            project_dict[key] = bool(value)
    return project_dict


# ── Status calculation ────────────────────────────────────────────────────────

def calculate_status(project: dict, current_date: datetime = None) -> str:
    """Derive project status from milestone dates unless user_edited_status=1."""
    if project.get('user_edited_status', 0) == 1:
        return project.get('ステータス', '要件引継待ち')

    if current_date is None:
        current_date = datetime.now()
    today = current_date.date()

    se_delivery = parse_date_from_db(project.get('SE納品', ''))
    if se_delivery and se_delivery.date() < today:
        return 'SE納品済'

    date_fields = [
        ('要件引継',   '要件引継待ち'),
        ('設計完了',   '設計中'),
        ('設計書送付', 'SE送付済'),
        ('開発完了',   '開発中'),
        ('テスト完了日', 'テスト中'),
        ('SE納品',    'FB対応中'),
    ]
    for field, status in date_fields:
        dt = parse_date_from_db(project.get(field, ''))
        if dt and dt.date() >= today:
            return status

    return '要件引継待ち'


# ── Page-range / working-day helpers ─────────────────────────────────────────

def read_pages_ranges(file_path='pages.txt') -> list:
    ranges = []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    range_part, days = line.split(':')
                    from_page, to_page = map(int, range_part.split('-'))
                    ranges.append((from_page, to_page, int(days.strip())))
                except ValueError:
                    continue
    except FileNotFoundError:
        pass
    return ranges


def read_working_days(file_path='config.txt') -> int:
    """Read FB working-days from config.txt; returns default if missing."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith('fix FB days =') or line.startswith('const workingDays ='):
                    try:
                        return int(line.split('=')[1].strip())
                    except (IndexError, ValueError):
                        return FB_WORKING_DAYS_DEFAULT
    except FileNotFoundError:
        pass
    return FB_WORKING_DAYS_DEFAULT


def add_working_days(start_date: datetime, working_days: int) -> str:
    if not start_date or working_days <= 0:
        return ''
    current = start_date
    added = 0
    while added < working_days:
        current += timedelta(days=1)
        if current.weekday() < 5:
            added += 1
    return current.strftime('%Y-%m-%d')


def calculate_test_completion_date(page_count, test_start_date: str) -> str:
    if not page_count or not test_start_date:
        return ''
    try:
        page_count = int(page_count)
        start = datetime.strptime(test_start_date, '%Y-%m-%d')
    except (ValueError, TypeError):
        return ''
    for from_p, to_p, days in read_pages_ranges():
        if from_p <= page_count <= to_p:
            return add_working_days(start, days)
    return ''


def calculate_fb_completion_date(test_completion_date) -> str:
    if not test_completion_date:
        return ''
    try:
        if isinstance(test_completion_date, str):
            test_dt = datetime.strptime(test_completion_date, '%Y-%m-%d')
        elif isinstance(test_completion_date, datetime):
            test_dt = test_completion_date
        else:
            return ''
        return add_working_days(test_dt, read_working_days())
    except (ValueError, TypeError):
        return ''


# ── Project display enrichment ────────────────────────────────────────────────

def enrich_project_for_display(project: dict, current_date: datetime = None) -> dict:
    """Add date formatting, past flags, highlight_column, fb_late, status."""
    if current_date is None:
        current_date = datetime.now()

    project['ステータス'] = calculate_status(project, current_date)

    closest_date = None
    min_diff = float('inf')
    for col in DATE_COLUMNS_DISPLAY:
        date_obj = parse_date_from_db(project.get(col, ''))
        project[f'{col}_past'] = False
        if date_obj is not None:
            diff = (date_obj.date() - current_date.date()).days
            if 0 <= diff < min_diff:
                min_diff = diff
                closest_date = col
            if date_obj.date() < current_date.date():
                project[f'{col}_past'] = True
        project[col] = format_date_jp(date_obj)

    project['highlight_column'] = closest_date

    fb_date = parse_date_from_db(project.get('FB完了予定日', ''))
    se_date = parse_date_from_db(project.get('SE納品', ''))
    project['fb_late'] = bool(fb_date and se_date and fb_date.date() > se_date.date())

    return convert_nat_to_none(project)
