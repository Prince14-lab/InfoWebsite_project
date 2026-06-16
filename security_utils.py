from functools import wraps
from flask import current_app, redirect, request, session, abort
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from uuid import uuid4
import os
import re
import mysql.connector


ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}


def get_db_connection():
    return mysql.connector.connect(**current_app.config["DB_CONFIG"])


def table_exists(cursor, table_name):
    cursor.execute("SHOW TABLES LIKE %s", (table_name,))
    return cursor.fetchone() is not None


def get_table_columns(cursor, table_name):
    cursor.execute(f"SHOW COLUMNS FROM {table_name}")
    return {column["Field"] for column in cursor.fetchall()}


def init_security_schema(cursor):
    if table_exists(cursor, "users"):
        user_columns = get_table_columns(cursor, "users")
        if "account_status" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN account_status VARCHAR(20) NOT NULL DEFAULT 'active'")
        cursor.execute("SHOW COLUMNS FROM users LIKE 'account_type'")
        account_type_column = cursor.fetchone()
        column_type = ""
        if account_type_column:
            column_type = account_type_column.get("Type") if isinstance(account_type_column, dict) else account_type_column[1]
        if "enum" in (column_type or "").lower() and "driver" not in column_type.lower():
            cursor.execute("""
                ALTER TABLE users
                MODIFY account_type ENUM('admin','owner','customer','driver') NOT NULL DEFAULT 'customer'
            """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS activity_logs (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NULL,
            role VARCHAR(30),
            action VARCHAR(255) NOT NULL,
            details TEXT,
            ip_address VARCHAR(45),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payment_events (
            id INT AUTO_INCREMENT PRIMARY KEY,
            event_id VARCHAR(100) UNIQUE,
            event_type VARCHAR(100),
            checkout_id VARCHAR(100),
            order_id INT NULL,
            raw_payload LONGTEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


def looks_hashed_password(password):
    password = password or ""
    return password.startswith(("scrypt:", "pbkdf2:", "argon2:", "bcrypt:", "$2a$", "$2b$", "$2y$"))


def migrate_plaintext_passwords(cursor):
    if not table_exists(cursor, "users"):
        return 0

    cursor.execute("SELECT id, password FROM users")
    users = cursor.fetchall()
    migrated = 0
    for user in users:
        password = user.get("password") if isinstance(user, dict) else user[1]
        user_id = user.get("id") if isinstance(user, dict) else user[0]
        if password and not looks_hashed_password(password):
            cursor.execute("UPDATE users SET password = %s WHERE id = %s", (generate_password_hash(password), user_id))
            migrated += 1
    return migrated


def log_activity(user_id, role, action, details=None):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        init_security_schema(cursor)
        cursor.execute("""
            INSERT INTO activity_logs (user_id, role, action, details, ip_address)
            VALUES (%s, %s, %s, %s, %s)
        """, (user_id, role, action, details, request.headers.get("X-Forwarded-For", request.remote_addr)))
        conn.commit()
        cursor.close()
        conn.close()
    except Exception:
        pass


def password_matches(stored_password, submitted_password):
    stored_password = stored_password or ""
    submitted_password = submitted_password or ""
    try:
        return check_password_hash(stored_password, submitted_password)
    except Exception:
        return False


def verify_password_with_migration(cursor, user, submitted_password):
    stored_password = user.get("password") or ""
    if password_matches(stored_password, submitted_password):
        return True

    if stored_password == submitted_password:
        cursor.execute(
            "UPDATE users SET password = %s WHERE id = %s",
            (generate_password_hash(submitted_password), user["id"]),
        )
        return True

    return False


def valid_email(email):
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email or ""))


def valid_username(username):
    return bool(re.match(r"^[A-Za-z0-9_]{3,30}$", username or ""))


def role_required(role):
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not session.get("user_id") or session.get("account_type") != role:
                if request.method != "GET" or request.accept_mimetypes.best == "application/json":
                    abort(403)
                return redirect("/login")
            return view(*args, **kwargs)
        return wrapper
    return decorator


customer_required = role_required("customer")
owner_required = role_required("owner")
admin_required = role_required("admin")


def save_safe_image(file_storage, folder_name, prefix):
    if not file_storage or not file_storage.filename:
        return None

    original_name = secure_filename(file_storage.filename)
    if "." not in original_name:
        return None

    extension = original_name.rsplit(".", 1)[1].lower()
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        return None

    upload_folder = os.path.join(current_app.root_path, "static", folder_name)
    os.makedirs(upload_folder, exist_ok=True)
    saved_name = f"{prefix}_{uuid4().hex}.{extension}"
    file_storage.save(os.path.join(upload_folder, saved_name))
    return f"/static/{folder_name}/{saved_name}"
