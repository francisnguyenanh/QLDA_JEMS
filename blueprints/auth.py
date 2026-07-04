"""Auth blueprint — login / logout (B1)."""
from flask import Blueprint, render_template, redirect, url_for, flash, request, session
from database import read_users

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        users = read_users()
        if username in users and users[username] == password:
            session['username'] = username
            flash('ログイン成功', 'success')
            return redirect(url_for('projects.dashboard'))
        flash('ユーザー名またはパスワードが正しくありません', 'danger')
    return render_template('login.html')


@auth_bp.route('/logout')
def logout():
    session.pop('username', None)
    flash('ログアウトしました', 'success')
    return redirect(url_for('auth.login'))
