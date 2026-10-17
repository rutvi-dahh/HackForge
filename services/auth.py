from functools import wraps
from flask import session, redirect, url_for, flash, abort, g
from werkzeug.security import generate_password_hash, check_password_hash
from database.db import get_db

def hash_password(password: str) -> str:
    return generate_password_hash(password, method="pbkdf2:sha256")

def verify_password(stored_hash: str, password: str) -> bool:
    return check_password_hash(stored_hash, password)

def get_current_user():
    if "user_id" not in session:
        return None
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()
    db.close()
    return user

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login"))
        # Guard against stale sessions (e.g. after DB re-seed)
        db = get_db()
        user = db.execute("SELECT id FROM users WHERE id = ?", (session["user_id"],)).fetchone()
        db.close()
        if not user:
            session.clear()
            flash("Your session has expired. Please log in again.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in as an administrator.", "warning")
            return redirect(url_for("login"))
        if session.get("role") != "admin":
            abort(403)
        return f(*args, **kwargs)
    return decorated_function
