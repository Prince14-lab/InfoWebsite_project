from flask import Blueprint, render_template, request, redirect, url_for, session, flash, current_app
from werkzeug.security import generate_password_hash
from datetime import datetime, timedelta
import hashlib
import mysql.connector
import secrets

from email_utils import send_email
from security_utils import (
    get_table_columns,
    init_security_schema,
    log_activity,
    valid_email,
    valid_username,
    verify_password_with_migration,
)

auth = Blueprint("auth", __name__)


def get_db_connection():
    return mysql.connector.connect(**current_app.config["DB_CONFIG"])


def ensure_password_reset_schema(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            token_hash VARCHAR(255) NOT NULL,
            expires_at DATETIME NOT NULL,
            used_at DATETIME NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)


def hash_reset_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@auth.route("/login", methods=["GET"])
def login():
    return render_template("login.html")


@auth.route("/login", methods=["POST"])
def login_post():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""

    if not username or not password:
        flash("Invalid username or password.", "error")
        log_activity(None, "guest", "login failure", f"Missing username or password for {username}")
        return redirect(url_for("auth.login"))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    init_security_schema(cursor)
    conn.commit()

    cursor.execute("SELECT * FROM users WHERE username = %s LIMIT 1", (username,))
    user = cursor.fetchone()

    if not user or not verify_password_with_migration(cursor, user, password):
        conn.commit()
        cursor.close()
        conn.close()
        flash("Invalid username or password.", "error")
        log_activity(None, "guest", "login failure", f"Failed login for username {username}")
        return redirect(url_for("auth.login"))

    if (user.get("account_status") or "active").lower() == "blocked":
        conn.commit()
        cursor.close()
        conn.close()
        flash("Your account has been blocked. Please contact support.", "error")
        log_activity(user["id"], user.get("account_type"), "blocked login attempt", "Blocked user tried to log in.")
        return redirect(url_for("auth.login"))

    conn.commit()
    cursor.close()
    conn.close()

    session.clear()
    session.permanent = True
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["account_type"] = user["account_type"]
    log_activity(user["id"], user["account_type"], "login success", "User logged in.")

    if user["account_type"] == "admin":
        return redirect(url_for("admin.admin_dashboard"))
    if user["account_type"] == "owner":
        return redirect(url_for("owner.owner_dashboard"))
    if user["account_type"] == "driver":
        return redirect(url_for("driver.driver_dashboard"))
    return redirect(url_for("customer.customer_home"))


@auth.route("/signup", methods=["POST"])
def signup():
    fullname = (request.form.get("fullname") or "").strip()
    email = (request.form.get("email") or "").strip()
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    confirm_password = request.form.get("confirm_password") or ""

    if not fullname or len(fullname) > 120:
        flash("Enter a valid full name.", "error")
        return redirect(url_for("auth.login"))
    if not valid_email(email):
        flash("Enter a valid email address.", "error")
        return redirect(url_for("auth.login"))
    if not valid_username(username):
        flash("Username must be 3-30 characters and use only letters, numbers, or underscore.", "error")
        return redirect(url_for("auth.login"))
    if len(password) < 8:
        flash("Password must be at least 8 characters.", "error")
        return redirect(url_for("auth.login"))
    if password != confirm_password:
        flash("Passwords do not match.", "error")
        return redirect(url_for("auth.login"))

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        init_security_schema(cursor)
        conn.commit()

        cursor.execute("SELECT id FROM users WHERE email = %s LIMIT 1", (email,))
        if cursor.fetchone():
            flash("Email already exists.", "error")
            return redirect(url_for("auth.login"))

        cursor.execute("SELECT id FROM users WHERE username = %s LIMIT 1", (username,))
        if cursor.fetchone():
            flash("Username already exists.", "error")
            return redirect(url_for("auth.login"))

        user_columns = get_table_columns(cursor, "users")
        user_data = {
            "fullname": fullname,
            "email": email,
            "username": username,
            "password": generate_password_hash(password),
            "account_type": "customer",
            "account_status": "active",
        }
        insert_columns = [column for column in user_data if column in user_columns]
        placeholders = ", ".join(["%s"] * len(insert_columns))
        cursor.execute(f"""
            INSERT INTO users ({", ".join(insert_columns)})
            VALUES ({placeholders})
        """, tuple(user_data[column] for column in insert_columns))
        conn.commit()
        new_user_id = cursor.lastrowid
    except mysql.connector.Error as exc:
        if conn:
            conn.rollback()
        current_app.logger.exception("Signup database error: %s", exc)
        flash("Unable to create account right now. Please check the server terminal for the database error.", "error")
        return redirect(url_for("auth.login"))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    log_activity(new_user_id, "customer", "signup", "Customer account created.")
    send_email(
        email,
        "Welcome to Green Nursery",
        f"Hello {fullname},\n\n"
        "Welcome to Green Nursery! Your customer account has been created successfully.\n\n"
        "You can now browse plants, add items to your cart, place orders, and track your purchases.\n\n"
        "Thank you,\n"
        "Green Nursery",
    )
    flash("Account created successfully. Please log in.", "success")
    return redirect(url_for("auth.login"))


@auth.route("/forgot-password", methods=["GET"])
def forgot_password():
    return render_template("forgot_password.html")


@auth.route("/forgot-password", methods=["POST"])
def forgot_password_post():
    email = (request.form.get("email") or "").strip()
    generic_message = "If an account with that email exists, a password reset link has been sent."

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        ensure_password_reset_schema(cursor)
        conn.commit()

        cursor.execute("SELECT id, fullname, email FROM users WHERE email = %s LIMIT 1", (email,))
        user = cursor.fetchone()

        if user:
            raw_token = secrets.token_urlsafe(32)
            token_hash = hash_reset_token(raw_token)
            expires_at = datetime.now() + timedelta(minutes=30)
            cursor.execute("""
                INSERT INTO password_reset_tokens (user_id, token_hash, expires_at)
                VALUES (%s, %s, %s)
            """, (user["id"], token_hash, expires_at))
            conn.commit()

            reset_link = url_for("auth.reset_password", token=raw_token, _external=True)
            send_email(
                user["email"],
                "Reset Your Green Nursery Password",
                f"Hello {user.get('fullname') or 'Green Nursery user'},\n\n"
                "We received a request to reset your Green Nursery account password.\n\n"
                "Click the link below to reset your password:\n"
                f"{reset_link}\n\n"
                "This link will expire in 30 minutes.\n\n"
                "If you did not request this, you can ignore this email.\n\n"
                "Thank you,\n"
                "Green Nursery",
            )
    except Exception as exc:
        if conn:
            conn.rollback()
        current_app.logger.exception("Forgot password request failed: %s", exc)
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    flash(generic_message, "success")
    return redirect(url_for("auth.forgot_password"))


@auth.route("/reset-password/<token>", methods=["GET"])
def reset_password(token):
    token_hash = hash_reset_token(token)
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_password_reset_schema(cursor)
    conn.commit()
    cursor.execute("""
        SELECT id, user_id
        FROM password_reset_tokens
        WHERE token_hash = %s
          AND used_at IS NULL
          AND expires_at > NOW()
        LIMIT 1
    """, (token_hash,))
    token_row = cursor.fetchone()
    cursor.close()
    conn.close()

    if not token_row:
        flash("This password reset link is invalid or expired.", "error")
        return redirect(url_for("auth.forgot_password"))

    return render_template("reset_password.html", token=token)


@auth.route("/reset-password/<token>", methods=["POST"])
def reset_password_post(token):
    password = request.form.get("password") or ""
    confirm_password = request.form.get("confirm_password") or ""

    if len(password) < 8:
        flash("Password must be at least 8 characters.", "error")
        return redirect(url_for("auth.reset_password", token=token))
    if password != confirm_password:
        flash("Passwords do not match.", "error")
        return redirect(url_for("auth.reset_password", token=token))

    token_hash = hash_reset_token(token)
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        ensure_password_reset_schema(cursor)
        conn.commit()
        cursor.execute("""
            SELECT id, user_id
            FROM password_reset_tokens
            WHERE token_hash = %s
              AND used_at IS NULL
              AND expires_at > NOW()
            LIMIT 1
        """, (token_hash,))
        token_row = cursor.fetchone()

        if not token_row:
            flash("This password reset link is invalid or expired.", "error")
            return redirect(url_for("auth.forgot_password"))

        user_id = token_row["user_id"]
        cursor.execute(
            "UPDATE users SET password = %s WHERE id = %s",
            (generate_password_hash(password), user_id),
        )
        cursor.execute(
            "UPDATE password_reset_tokens SET used_at = NOW() WHERE id = %s",
            (token_row["id"],),
        )
        cursor.execute("""
            UPDATE password_reset_tokens
            SET used_at = NOW()
            WHERE user_id = %s AND used_at IS NULL
        """, (user_id,))
        conn.commit()
        log_activity(user_id, "user", "password reset", "User reset password using email reset link.")
    except Exception as exc:
        if conn:
            conn.rollback()
        current_app.logger.exception("Password reset failed: %s", exc)
        flash("Unable to reset password right now. Please try again.", "error")
        return redirect(url_for("auth.forgot_password"))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    flash("Your password has been reset successfully. Please log in.", "success")
    return redirect(url_for("auth.login"))


@auth.route("/logout")
def logout():
    log_activity(session.get("user_id"), session.get("account_type"), "logout", "User logged out.")
    session.clear()
    return redirect(url_for("auth.login"))
