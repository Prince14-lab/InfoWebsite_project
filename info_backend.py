from flask import Flask, render_template, request, redirect, url_for, flash
from datetime import timedelta
import mysql.connector
import os

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
except ImportError:
    Limiter = None
    get_remote_address = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")


def load_env_file_fallback(path=ENV_PATH):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


if load_dotenv:
    load_dotenv(ENV_PATH)
else:
    load_env_file_fallback(ENV_PATH)

from auth import auth
from customer import customer
from owner import owner
from admin import admin
from driver import driver
from email_utils import send_email
from security_utils import init_security_schema, migrate_plaintext_passwords

app = Flask(__name__)

app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-this-dev-secret")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = (
    os.getenv("SESSION_COOKIE_SECURE", "").lower() in ("true", "1", "yes")
    or os.getenv("FLASK_ENV") == "production"
)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=30)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
app.config["DEBUG"] = os.getenv("FLASK_DEBUG", "0") == "1"

DB_CONFIG = {
    "host": os.getenv("MYSQL_HOST") or os.getenv("MYSQLHOST") or "localhost",
    "port": int(os.getenv("MYSQL_PORT") or os.getenv("MYSQLPORT") or 3306),
    "user": os.getenv("MYSQL_USER") or os.getenv("MYSQLUSER") or "root",
    "password": os.getenv("MYSQL_PASSWORD") or os.getenv("MYSQLPASSWORD") or "",
    "database": os.getenv("MYSQL_DATABASE") or os.getenv("MYSQLDATABASE") or "infomanagement_db",
}


def get_db_connection():
    try:
        return mysql.connector.connect(**DB_CONFIG)
    except mysql.connector.Error as exc:
        print("Database connection failed. Check MYSQL_USER, MYSQL_PASSWORD, and MYSQL_DATABASE in .env.")
        print(f"MySQL error: {exc}")
        raise


app.config["DB_CONFIG"] = DB_CONFIG
app.config["PAYMONGO_SECRET_KEY"] = os.getenv("PAYMONGO_SECRET_KEY")
app.config["PAYMONGO_PUBLIC_KEY"] = os.getenv("PAYMONGO_PUBLIC_KEY")
app.config["SMTP_HOST"] = os.getenv("SMTP_HOST") or "smtp.gmail.com"
app.config["SMTP_PORT"] = int(os.getenv("SMTP_PORT") or "587")
app.config["SMTP_USER"] = os.getenv("SMTP_USER")
app.config["SMTP_PASSWORD"] = os.getenv("SMTP_PASSWORD")
app.config["SMTP_FROM_EMAIL"] = os.getenv("SMTP_FROM_EMAIL") or app.config["SMTP_USER"]

if app.config["DEBUG"]:
    print("ENV PATH:", ENV_PATH)
    print("DB USER:", DB_CONFIG["user"])
    print("DB NAME:", DB_CONFIG["database"])
    print("DB PASSWORD SET:", bool(DB_CONFIG["password"]))
    print("SMTP CONFIGURED:", bool(app.config["SMTP_USER"] and app.config["SMTP_PASSWORD"]))

if Limiter:
    limiter = Limiter(get_remote_address, app=app, default_limits=["200 per day", "60 per hour"])
else:
    limiter = None


@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


@app.errorhandler(403)
def forbidden(error):
    return "You are not allowed to access this page.", 403


@app.errorhandler(413)
def file_too_large(error):
    return "Uploaded file is too large. Maximum file size is 5MB.", 413


@app.errorhandler(500)
def server_error(error):
    return "Something went wrong. Please try again.", 500


app.register_blueprint(auth)
app.register_blueprint(customer)
app.register_blueprint(owner)
app.register_blueprint(admin)
app.register_blueprint(driver)


with app.app_context():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        init_security_schema(cursor)
        migrate_plaintext_passwords(cursor)
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as exc:
        print("Startup database setup failed. Check your .env database settings.")
        print(f"Startup error: {exc}")


@app.route("/PlantNursery")
def index():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE account_type = 'owner' ORDER BY id ASC LIMIT 1")
    owner_info = cursor.fetchone() or {}
    cursor.close()
    conn.close()
    return render_template("index.html", owner_info=owner_info)


@app.route("/contact-owner", methods=["POST"])
def contact_owner():
    customer_name = (request.form.get("name") or "").strip()
    customer_email = (request.form.get("email") or "").strip()
    customer_message = (request.form.get("message") or "").strip()

    if not customer_name or not customer_email or not customer_message:
        flash("Please complete all contact fields before sending.", "error")
        return redirect(url_for("index") + "#contact")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT fullname, email FROM users WHERE account_type = 'owner' ORDER BY id ASC LIMIT 1")
    owner_info = cursor.fetchone() or {}
    cursor.close()
    conn.close()

    owner_email = owner_info.get("shop_email") or owner_info.get("email")
    if not owner_email:
        flash("The shop email is not available right now. Please try again later.", "error")
        return redirect(url_for("index") + "#contact")

    email_sent = send_email(
        owner_email,
        f"Plant inquiry from {customer_name}",
        "A visitor sent a plant inquiry from the Green Nursery website.\n\n"
        f"Name: {customer_name}\n"
        f"Email: {customer_email}\n\n"
        f"Message:\n{customer_message}\n",
        reply_to=customer_email,
    )

    if not email_sent:
        flash("Unable to send your message right now. Please try again later.", "error")
        return redirect(url_for("index") + "#contact")

    flash("Your message was sent to the nursery owner.", "success")
    return redirect(url_for("index") + "#contact")


if __name__ == "__main__":
    app.run(debug=app.config["DEBUG"])
