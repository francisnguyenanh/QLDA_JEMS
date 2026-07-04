"""Editor blueprint (B1)."""
import sqlite3
import logging
from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, jsonify, request, session
from config import DB_FILE

editor_bp = Blueprint('editor', __name__)


def _require_login():
    return 'username' not in session


@editor_bp.route('/editor_list')
def editor_list():
    if _require_login():
        return redirect(url_for('auth.login'))
    return render_template('editor_list.html')


@editor_bp.route('/editor')
def editor():
    if _require_login():
        return redirect(url_for('auth.login'))
    document_id = request.args.get('id')
    document = None
    if document_id:
        try:
            conn = sqlite3.connect(DB_FILE)
            cur = conn.cursor()
            cur.execute('SELECT id,title,content,created_at,updated_at FROM editor_document WHERE id=?', (document_id,))
            row = cur.fetchone()
            conn.close()
            if row:
                document = dict(zip(['id', 'title', 'content', 'created_at', 'updated_at'], row))
        except Exception as e:
            logging.error(f'[editor] {e}')
    return render_template('editor.html', document=document)


@editor_bp.route('/api/editor_documents', methods=['GET'])
def get_editor_documents():
    if _require_login():
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        cur.execute('SELECT id,title,content,created_at,updated_at FROM editor_document ORDER BY updated_at DESC')
        docs = [dict(zip(['id', 'title', 'content', 'created_at', 'updated_at'], r)) for r in cur.fetchall()]
        conn.close()
        return jsonify({'documents': docs})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@editor_bp.route('/api/editor_documents', methods=['POST'])
def create_editor_document():
    if _require_login():
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json()
    title = (data.get('title') or '').strip()
    content = (data.get('content') or '').strip()
    if not title:
        return jsonify({'error': 'Title is required'}), 400
    try:
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        cur.execute('INSERT INTO editor_document (title,content,created_at,updated_at) VALUES (?,?,?,?)', (title, content, now, now))
        doc_id = cur.lastrowid
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'id': doc_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@editor_bp.route('/api/editor_documents/<int:document_id>', methods=['GET'])
def get_editor_document(document_id):
    if _require_login():
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        cur.execute('SELECT id,title,content,created_at,updated_at FROM editor_document WHERE id=?', (document_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return jsonify({'error': 'Document not found'}), 404
        return jsonify({'document': dict(zip(['id', 'title', 'content', 'created_at', 'updated_at'], row))})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@editor_bp.route('/api/editor_documents/<int:document_id>', methods=['PUT'])
def update_editor_document(document_id):
    if _require_login():
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json()
    title = (data.get('title') or '').strip()
    if not title:
        return jsonify({'error': 'Title is required'}), 400
    try:
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        cur.execute('UPDATE editor_document SET title=?, content=?, updated_at=? WHERE id=?', (title, data.get('content', ''), now, document_id))
        if cur.rowcount == 0:
            conn.close()
            return jsonify({'error': 'Document not found'}), 404
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@editor_bp.route('/api/editor_documents/<int:document_id>', methods=['DELETE'])
def delete_editor_document(document_id):
    if _require_login():
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        cur.execute('DELETE FROM editor_document WHERE id=?', (document_id,))
        if cur.rowcount == 0:
            conn.close()
            return jsonify({'error': 'Document not found'}), 404
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
