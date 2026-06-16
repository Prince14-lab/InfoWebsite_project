from datetime import datetime
from uuid import uuid4
import os

from flask import current_app
from werkzeug.utils import secure_filename


ALLOWED_MESSAGE_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}


def allowed_message_image(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_MESSAGE_IMAGE_EXTENSIONS


def save_message_photos(files):
    saved_files = []
    upload_folder = os.path.join(current_app.root_path, "static", "message_photos")
    os.makedirs(upload_folder, exist_ok=True)

    for file_storage in files:
        if not file_storage or not file_storage.filename or not allowed_message_image(file_storage.filename):
            continue
        filename = secure_filename(file_storage.filename)
        extension = filename.rsplit(".", 1)[1].lower()
        saved_name = f"message_{uuid4().hex}.{extension}"
        file_storage.save(os.path.join(upload_folder, saved_name))
        saved_files.append({
            "file_url": f"/static/message_photos/{saved_name}",
            "original_name": filename,
        })

    return saved_files


def ensure_message_schema(cursor):
    cursor.execute("SHOW COLUMNS FROM users")
    user_columns = {column["Field"] for column in cursor.fetchall()}
    if "profile_photo" not in user_columns:
        if "address" in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN profile_photo VARCHAR(255) NULL AFTER address")
        else:
            cursor.execute("ALTER TABLE users ADD COLUMN profile_photo VARCHAR(255) NULL")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS message_threads (
            id INT AUTO_INCREMENT PRIMARY KEY,
            customer_id INT NOT NULL,
            owner_id INT NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY unique_customer_owner_thread (customer_id, owner_id),
            FOREIGN KEY (customer_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INT AUTO_INCREMENT PRIMARY KEY,
            thread_id INT NOT NULL,
            sender_id INT NOT NULL,
            receiver_id INT NOT NULL,
            body TEXT NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (thread_id) REFERENCES message_threads(id) ON DELETE CASCADE,
            FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (receiver_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS message_attachments (
            id INT AUTO_INCREMENT PRIMARY KEY,
            message_id INT NOT NULL,
            file_url VARCHAR(255) NOT NULL,
            original_name VARCHAR(255),
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
        )
    """)


def get_owner_user(cursor):
    cursor.execute("""
        SELECT id, fullname, profile_photo
        FROM users
        WHERE account_type = 'owner'
        ORDER BY id ASC
        LIMIT 1
    """)
    return cursor.fetchone()


def get_admin_user(cursor):
    cursor.execute("""
        SELECT id, fullname, profile_photo
        FROM users
        WHERE account_type = 'admin'
        ORDER BY id ASC
        LIMIT 1
    """)
    return cursor.fetchone()


def get_or_create_thread(cursor, customer_id, owner_id):
    cursor.execute("""
        SELECT id
        FROM message_threads
        WHERE customer_id = %s AND owner_id = %s
        LIMIT 1
    """, (customer_id, owner_id))
    thread = cursor.fetchone()
    if thread:
        return thread["id"]

    cursor.execute("""
        INSERT INTO message_threads (customer_id, owner_id, created_at, updated_at)
        VALUES (%s, %s, %s, %s)
    """, (customer_id, owner_id, datetime.now(), datetime.now()))
    return cursor.lastrowid


def fetch_thread_messages(cursor, thread_id):
    cursor.execute("""
        SELECT m.id, m.sender_id, m.receiver_id, m.body, m.created_at,
               u.fullname AS sender_name, u.account_type AS sender_type
        FROM messages m
        JOIN users u ON u.id = m.sender_id
        WHERE m.thread_id = %s
        ORDER BY m.created_at ASC, m.id ASC
    """, (thread_id,))
    messages = cursor.fetchall()
    if not messages:
        return messages

    message_ids = [message["id"] for message in messages]
    placeholders = ", ".join(["%s"] * len(message_ids))
    cursor.execute(f"""
        SELECT message_id, file_url, original_name
        FROM message_attachments
        WHERE message_id IN ({placeholders})
        ORDER BY id ASC
    """, tuple(message_ids))
    attachments = cursor.fetchall()
    attachments_by_message = {}
    for attachment in attachments:
        attachments_by_message.setdefault(attachment["message_id"], []).append({
            "file_url": attachment["file_url"],
            "original_name": attachment.get("original_name") or "Photo",
        })

    for message in messages:
        message["attachments"] = attachments_by_message.get(message["id"], [])
    return messages


def serialize_message(message, current_user_id):
    return {
        "id": message["id"],
        "body": message["body"],
        "created_at": message["created_at"].strftime("%Y-%m-%d %H:%M") if message.get("created_at") else "",
        "sender_name": message.get("sender_name") or "User",
        "sender_type": message.get("sender_type") or "",
        "is_mine": message["sender_id"] == current_user_id,
        "attachments": message.get("attachments") or [],
    }


def insert_message(cursor, thread_id, sender_id, receiver_id, body, attachments=None):
    now = datetime.now()
    cursor.execute("""
        INSERT INTO messages (thread_id, sender_id, receiver_id, body, created_at)
        VALUES (%s, %s, %s, %s, %s)
    """, (thread_id, sender_id, receiver_id, body or "", now))
    message_id = cursor.lastrowid
    for attachment in attachments or []:
        cursor.execute("""
            INSERT INTO message_attachments (message_id, file_url, original_name, created_at)
            VALUES (%s, %s, %s, %s)
        """, (
            message_id,
            attachment["file_url"],
            attachment.get("original_name"),
            now,
        ))
    cursor.execute("""
        UPDATE message_threads
        SET updated_at = %s
        WHERE id = %s
    """, (now, thread_id))
    return message_id
