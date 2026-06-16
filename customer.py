from flask import Blueprint, abort, jsonify, render_template, redirect, session, current_app, request, Response
from datetime import datetime
from uuid import uuid4
from werkzeug.utils import secure_filename
import base64
import json
import os
import random
import re
import requests
import mysql.connector
from message_utils import (
    ensure_message_schema,
    fetch_thread_messages,
    get_or_create_thread,
    get_owner_user,
    insert_message,
    save_message_photos,
    serialize_message,
)
from email_utils import send_email
from notification_utils import (
    check_and_send_low_stock_notifications,
    get_owner_emails,
    notify_customer_payment_confirmed,
    notify_owner_new_order,
)
from security_utils import log_activity
from tracking_utils import ensure_order_tracking_schema

customer = Blueprint("customer", __name__)


CUSTOMER_PUBLIC_ENDPOINTS = {"customer.plant_details", "customer.customer_chatbot", "customer.paymongo_webhook"}


@customer.before_request
def require_customer_for_sensitive_routes():
    if request.endpoint in CUSTOMER_PUBLIC_ENDPOINTS:
        return None
    if session.get("account_type") != "customer":
        if request.method == "GET":
            return redirect("/login")
        return jsonify({"success": False, "message": "Customer access is required."}), 403


def get_db_connection():
    return mysql.connector.connect(**current_app.config["DB_CONFIG"])


ALLOWED_PROFILE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
ALLOWED_PROOF_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}


# ── HELPERS ───────────────────────────────────────────────────────────────────

def format_category(category):
    labels = {
        "indoor":    "Indoor Plant",
        "outdoor":   "Outdoor Plant",
        "fruit":     "Fruit Bearing Plant",
        "flowering": "Flowering Plant",
    }
    return labels.get((category or "").lower(), category or "Plant")


def plant_description(plant):
    category = format_category(plant.get("category")).lower()
    return (
        f"{plant['name']} is a healthy {category} selected for home gardens, "
        "balconies, and indoor spaces. Choose your preferred size and quantity, "
        "then add it to your cart or continue straight to checkout."
    )


def build_plant_gallery(plant):
    sample_columns = (
        "sample_photo",
        "sample_image",
        "sample_image_url",
        "plant_sample",
        "plant_sample_photo",
    )
    placeholder_images = {
        "/static/snakeplant.jpg",
        "/static/calamasiplant.jpg",
        "/static/rosalplant.jpg",
    }
    gallery = []

    image_url = plant.get("image_url")
    if image_url:
        gallery.append(image_url)

    for column in sample_columns:
        image = plant.get(column)
        if image and image not in placeholder_images and image not in gallery:
            gallery.append(image)

    sample_photos = plant.get("sample_photos") or ""
    for image in sample_photos.replace(",", "\n").splitlines():
        image = image.strip()
        if image and image not in placeholder_images and image not in gallery:
            gallery.append(image)

    return gallery


def order_status_label(status):
    labels = {
        "to_pay":           "To Pay",
        "to_ship":          "To Ship",
        "to_receive":       "To Receive",
        "completed":        "Completed",
        "return_refund":    "Return / Refund",
        "cancelled":        "Cancelled",
        "Preparing":        "Preparing",
        "Packed":           "Packed",
        "Out for Delivery": "Out for Delivery",
        "Delivered":        "Delivered",
        "Cancelled":        "Cancelled",
    }
    return labels.get(status, status)


def payment_method_label(payment_method):
    return {
        "cash_on_delivery": "Cash on Delivery",
        "gcash": "GCash",
        "bank": "Bank",
        "GCash": "GCash",
        "Bank Transfer": "Bank Transfer",
        "PayMongo GCash": "GCash",
        "PayMongo Card": "Visa / Mastercard",
        "Cash on Delivery": "Cash on Delivery",
    }.get(payment_method, payment_method or "Pending")


def return_request_label(request_status):
    return {
        "approved": "Approved",
        "disapproved": "Rejected",
        "pending": "Pending",
    }.get(request_status, (request_status or "").replace("_", " ").title())


def get_table_columns(cursor, table_name):
    cursor.execute(f"SHOW COLUMNS FROM {table_name}")
    return {column["Field"] for column in cursor.fetchall()}


def table_exists(cursor, table_name):
    cursor.execute("SHOW TABLES LIKE %s", (table_name,))
    return cursor.fetchone() is not None


def ensure_customer_schema(cursor):
    user_columns = get_table_columns(cursor, "users")
    if "profile_photo" not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN profile_photo VARCHAR(255) NULL AFTER address")

    plant_columns = get_table_columns(cursor, "plants")
    if "sample_photo" not in plant_columns:
        cursor.execute("ALTER TABLE plants ADD COLUMN sample_photo VARCHAR(255) NULL AFTER image_url")
    if "sample_photos" not in plant_columns:
        cursor.execute("ALTER TABLE plants ADD COLUMN sample_photos TEXT NULL AFTER sample_photo")

    order_columns = get_table_columns(cursor, "orders")
    paymongo_columns = {
        "paymongo_checkout_id": "VARCHAR(100) NULL",
        "paymongo_payment_id": "VARCHAR(100) NULL",
        "payment_reference": "VARCHAR(100) NULL",
        "receipt_no": "VARCHAR(50) UNIQUE NULL",
        "paid_at": "DATETIME NULL",
    }
    for column, definition in paymongo_columns.items():
        if column not in order_columns:
            cursor.execute(f"ALTER TABLE orders ADD COLUMN {column} {definition}")

    ensure_enum_values(cursor, "orders", "order_status", [
        "To Pay", "Preparing", "Packed", "Out for Delivery", "Delivered", "Cancelled",
    ])
    ensure_enum_values(cursor, "orders", "status", [
        "to_pay", "to_ship", "to_receive", "completed", "cancelled",
        "To Pay", "Preparing", "Packed", "Out for Delivery", "Delivered", "Cancelled",
    ])
    ensure_enum_values(cursor, "orders", "payment_method", [
        "Cash on Delivery", "PayMongo GCash", "PayMongo Card", "GCash", "Bank Transfer",
    ])
    ensure_enum_values(cursor, "orders", "payment_status", ["Pending", "Paid", "Failed"])

    order_items_table = get_order_items_table(cursor)
    if table_exists(cursor, order_items_table):
        order_item_columns = get_table_columns(cursor, order_items_table)
        item_columns = {
            "plant_name": "VARCHAR(255) NULL",
            "species": "VARCHAR(255) NULL",
            "subtotal": "DECIMAL(10,2) NOT NULL DEFAULT 0",
        }
        for column, definition in item_columns.items():
            if column not in order_item_columns:
                cursor.execute(f"ALTER TABLE {order_items_table} ADD COLUMN {column} {definition}")

    if not table_exists(cursor, "plant_reviews"):
        cursor.execute("""
            CREATE TABLE plant_reviews (
                id INT AUTO_INCREMENT PRIMARY KEY,
                plant_id INT NOT NULL,
                user_id INT NOT NULL,
                order_id INT NULL,
                order_item_id INT NULL,
                rating INT NOT NULL,
                comment TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (plant_id) REFERENCES plants(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

    if not table_exists(cursor, "return_refund_requests"):
        cursor.execute("""
            CREATE TABLE return_refund_requests (
                id INT AUTO_INCREMENT PRIMARY KEY,
                order_id INT NOT NULL,
                user_id INT NOT NULL,
                reason TEXT NOT NULL,
                proof_photo VARCHAR(255),
                request_status VARCHAR(30) NOT NULL DEFAULT 'pending',
                owner_response TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reviewed_at DATETIME NULL,
                FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

    if not table_exists(cursor, "reports"):
        cursor.execute("""
            CREATE TABLE reports (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NULL,
                reporter_type VARCHAR(30) DEFAULT 'customer',
                issue_type VARCHAR(100) DEFAULT 'Platform Concern',
                description TEXT NOT NULL,
                proof_photo VARCHAR(255),
                status VARCHAR(30) NOT NULL DEFAULT 'pending',
                admin_response TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reviewed_at DATETIME NULL
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


def allowed_profile_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_PROFILE_EXTENSIONS


def allowed_proof_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_PROOF_EXTENSIONS


def get_table_column_types(cursor, table_name):
    cursor.execute(f"SHOW COLUMNS FROM {table_name}")
    return {column["Field"]: column["Type"] for column in cursor.fetchall()}


def enum_values(column_type):
    column_type = column_type or ""
    if not column_type.lower().startswith("enum("):
        return []
    return re.findall(r"'((?:[^'\\]|\\.)*)'", column_type)


def ensure_enum_values(cursor, table_name, column_name, required_values):
    column_types = get_table_column_types(cursor, table_name)
    column_type = column_types.get(column_name, "")
    values = enum_values(column_type)
    if not values:
        return

    missing = [value for value in required_values if value not in values]
    if not missing:
        return

    updated_values = values + missing
    enum_sql = ", ".join("'" + value.replace("'", "''") + "'" for value in updated_values)
    cursor.execute(f"ALTER TABLE {table_name} MODIFY COLUMN {column_name} ENUM({enum_sql}) NULL")


def db_status_value(column_type, ui_status):
    legacy_statuses = {
        "to_pay": "Preparing",
        "to_ship": "Packed",
        "to_receive": "Out for Delivery",
        "completed": "Delivered",
        "return_refund": "Cancelled",
        "cancelled": "Cancelled",
    }
    column_type = (column_type or "").lower()
    legacy_status = legacy_statuses.get(ui_status)
    if "enum" in column_type and legacy_status and legacy_status.lower() in column_type:
        return legacy_status
    if "enum" in column_type and ui_status not in column_type:
        return legacy_status or ui_status
    return ui_status


def get_order_items_table(cursor):
    cursor.execute("SHOW TABLES LIKE 'order_items'")
    if cursor.fetchone():
        return "order_items"

    cursor.execute("SHOW TABLES LIKE 'order_item'")
    if cursor.fetchone():
        return "order_item"

    return "order_items"


def generate_order_code(cursor):
    cursor.execute("SELECT COALESCE(MAX(id), 0) + 1 AS next_id FROM orders")
    row = cursor.fetchone() or {}
    return f"ORD-{int(row.get('next_id') or 1):05d}"


def generate_receipt_no(order_id):
    return f"RCPT-{int(order_id):05d}"


def money_to_centavos(value):
    return int(round(float(value or 0) * 100))


def peso_text(value):
    return f"PHP {float(value or 0):,.2f}"


def order_reference(order):
    return order.get("order_code") or order.get("checkout_code") or order.get("id")


def send_order_created_email(customer_user, order_summary):
    if not customer_user or not customer_user.get("email"):
        return False

    is_paymongo = order_summary["payment_method"] in ["PayMongo GCash", "PayMongo Card"]
    if is_paymongo:
        subject = "Green Nursery Order Created - Payment Required"
        body = (
            f"Hello {customer_user.get('fullname') or 'Customer'},\n\n"
            f"Your Green Nursery order #{order_summary['reference']} has been created.\n\n"
            f"Payment Method: {order_summary['payment_method']}\n"
            "Payment Status: Pending\n"
            "Order Status: To Pay\n"
            f"Total Amount: {peso_text(order_summary['total'])}\n\n"
            "Please go to My Purchases > To Pay and click Pay Now to continue your payment.\n"
            "Your e-receipt will be available after PayMongo confirms your payment.\n\n"
            "Thank you,\n"
            "Green Nursery"
        )
    else:
        subject = "Green Nursery Order Confirmation"
        body = (
            f"Hello {customer_user.get('fullname') or 'Customer'},\n\n"
            f"Thank you for shopping with Green Nursery. Your order #{order_summary['reference']} was placed successfully.\n\n"
            f"Payment Method: {order_summary['payment_method']}\n"
            f"Payment Status: {order_summary['payment_status']}\n"
            f"Order Status: {order_summary['order_status']}\n"
            f"Subtotal: {peso_text(order_summary['subtotal'])}\n"
            f"Delivery Fee: {peso_text(order_summary['delivery_fee'])}\n"
            f"Total Amount: {peso_text(order_summary['total'])}\n"
            f"Delivery Address: {order_summary['delivery_address'] or 'Not provided'}\n"
            f"Contact Number: {order_summary['contact_number'] or 'Not provided'}\n\n"
            "Please check your My Purchases page for more details.\n\n"
            "Thank you,\n"
            "Green Nursery"
        )
    return send_email(customer_user["email"], subject, body)


def send_payment_confirmed_email(cursor, order_id):
    order_columns = get_table_columns(cursor, "orders")
    total_expr = "o.total_amount" if "total_amount" in order_columns else ("o.total" if "total" in order_columns else "o.subtotal")
    code_expr = "o.order_code" if "order_code" in order_columns else "o.id"
    receipt_expr = "receipt_no" if "receipt_no" in order_columns else "NULL"
    paid_at_expr = "paid_at" if "paid_at" in order_columns else "NULL"
    cursor.execute(f"""
        SELECT o.id, o.user_id, o.payment_method, o.payment_status,
               {total_expr} AS total_amount,
               {code_expr} AS order_code,
               {receipt_expr} AS receipt_no,
               {paid_at_expr} AS paid_at,
               u.fullname, u.email
        FROM orders o
        JOIN users u ON u.id = o.user_id
        WHERE o.id = %s
        LIMIT 1
    """, (order_id,))
    order = cursor.fetchone()
    if not order or not order.get("email"):
        return False

    paid_at = order.get("paid_at")
    if hasattr(paid_at, "strftime"):
        paid_at = paid_at.strftime("%Y-%m-%d %H:%M")

    return notify_customer_payment_confirmed(
        order["email"],
        order.get("fullname"),
        order.get("order_code") or order["id"],
        order.get("receipt_no") or generate_receipt_no(order["id"]),
        order.get("total_amount"),
        cursor=cursor,
        order_id=order["id"],
    )


def deduct_stock_from_items(cursor, items):
    plant_columns = get_table_columns(cursor, "plants")
    has_sold = "sold" in plant_columns

    for item in items:
        quantity = int(item.get("quantity") or 0)
        plant_id = item.get("plant_id")
        if not plant_id or quantity <= 0:
            continue

        cursor.execute("""
            UPDATE plants
            SET stock = GREATEST(stock - %s, 0)
            WHERE id = %s
        """, (quantity, plant_id))

        if has_sold:
            cursor.execute("""
                UPDATE plants
                SET sold = COALESCE(sold, 0) + %s
                WHERE id = %s
            """, (quantity, plant_id))

        check_and_send_low_stock_notifications(cursor, plant_id)


def create_paymongo_checkout_session(order, items, base_url):
    secret_key = current_app.config.get("PAYMONGO_SECRET_KEY")
    if not secret_key:
        raise RuntimeError("PAYMONGO_SECRET_KEY is not configured.")

    payment_method = order.get("payment_method")
    payment_method_types = ["gcash"] if payment_method == "PayMongo GCash" else ["card"]
    line_items = []

    for item in items:
        item_subtotal = item.get("subtotal")
        if item_subtotal is None:
            item_subtotal = float(item.get("unit_price") or item.get("price") or 0) * int(item.get("quantity") or 1)

        line_items.append({
            "currency": "PHP",
            "amount": money_to_centavos(item_subtotal),
            "description": item.get("species") or item.get("plant_name") or "Plant order item",
            "name": item.get("plant_name") or "Plant",
            "quantity": 1,
        })

    delivery_fee = float(order.get("delivery_fee") or 0)
    if delivery_fee > 0:
        line_items.append({
            "currency": "PHP",
            "amount": money_to_centavos(delivery_fee),
            "description": "Delivery fee",
            "name": "Delivery Fee",
            "quantity": 1,
        })

    auth_token = base64.b64encode(f"{secret_key}:".encode("utf-8")).decode("utf-8")
    payload = {
        "data": {
            "attributes": {
                "send_email_receipt": False,
                "show_description": True,
                "show_line_items": True,
                "line_items": line_items,
                "payment_method_types": payment_method_types,
                "success_url": f"{base_url}/payment-success/{order['id']}",
                "cancel_url": f"{base_url}/payment-cancel/{order['id']}",
                "description": f"Green Nursery order #{order.get('order_code') or order['id']}",
            }
        }
    }
    response = requests.post(
        "https://api.paymongo.com/v1/checkout_sessions",
        headers={
            "Authorization": f"Basic {auth_token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    if response.status_code >= 400:
        print("PAYMONGO CHECKOUT ERROR", response.status_code, response.text)
    response.raise_for_status()
    data = response.json()
    checkout = data.get("data", {})
    return checkout.get("id"), checkout.get("attributes", {}).get("checkout_url")


def paymongo_headers():
    secret_key = current_app.config.get("PAYMONGO_SECRET_KEY")
    if not secret_key:
        raise RuntimeError("PAYMONGO_SECRET_KEY is not configured.")
    auth_token = base64.b64encode(f"{secret_key}:".encode("utf-8")).decode("utf-8")
    return {
        "Authorization": f"Basic {auth_token}",
        "Content-Type": "application/json",
    }


def fetch_paymongo_checkout_session(checkout_id):
    response = requests.get(
        f"https://api.paymongo.com/v1/checkout_sessions/{checkout_id}",
        headers=paymongo_headers(),
        timeout=30,
    )
    if response.status_code >= 400:
        print("PAYMONGO CHECKOUT FETCH ERROR", response.status_code, response.text)
    response.raise_for_status()
    return response.json().get("data", {})


def checkout_session_paid(checkout):
    attributes = checkout.get("attributes", {}) if isinstance(checkout, dict) else {}
    payments = attributes.get("payments") or []
    status = str(attributes.get("status") or "").lower()
    payment_intent = attributes.get("payment_intent") or {}
    payment_intent_status = str(payment_intent.get("attributes", {}).get("status") or "").lower()
    return bool(payments) or status in {"paid", "succeeded"} or payment_intent_status in {"succeeded", "paid"}


def checkout_payment_info(checkout):
    attributes = checkout.get("attributes", {}) if isinstance(checkout, dict) else {}
    payments = attributes.get("payments") or []
    payment = payments[0] if payments and isinstance(payments[0], dict) else {}
    payment_id = payment.get("id") or attributes.get("payment_intent_id")
    reference = attributes.get("reference_number") or payment.get("attributes", {}).get("reference_number") or checkout.get("id")
    return payment_id, reference


def finalize_paid_paymongo_order(cursor, order, payment_id=None, payment_reference=None):
    if not order or (order.get("payment_status") or "").lower() == "paid":
        return False

    order_columns = get_table_columns(cursor, "orders")
    order_items_table = get_order_items_table(cursor)
    cursor.execute(f"""
        SELECT plant_id, quantity
        FROM {order_items_table}
        WHERE order_id = %s
    """, (order["id"],))
    items = cursor.fetchall()

    update_data = {
        "payment_status": "Paid",
        "order_status": "Preparing",
        "status": "Preparing",
        "paid_at": datetime.now(),
        "receipt_no": order.get("receipt_no") or generate_receipt_no(order["id"]),
        "paymongo_payment_id": payment_id,
        "payment_reference": payment_reference,
    }
    update_columns = [column for column in update_data if column in order_columns]
    assignments = ", ".join(f"{column} = %s" for column in update_columns)
    cursor.execute(f"""
        UPDATE orders
        SET {assignments}
        WHERE id = %s
    """, tuple(update_data[column] for column in update_columns) + (order["id"],))

    deduct_stock_from_items(cursor, items)
    if "sold_recorded" in order_columns:
        cursor.execute("UPDATE orders SET sold_recorded = 1 WHERE id = %s", (order["id"],))

    cursor.execute("DELETE FROM cart WHERE user_id = %s", (order["user_id"],))
    return True


def status_tab_key(status):
    return {
        "To Pay": "to_pay",
        "to_pay": "to_pay",
        "pending": "to_pay",
        "Pending": "to_pay",
        "Preparing": "to_ship",
        "to_ship": "to_ship",
        "Packed": "to_ship",
        "Out for Delivery": "to_receive",
        "to_receive": "to_receive",
        "Delivered": "completed",
        "completed": "completed",
        "Cancelled": "cancelled",
        "cancelled": "cancelled",
    }.get(status, status or "to_pay")


def fetch_customer_orders(user_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_customer_schema(cursor)
    conn.commit()
    order_columns = get_table_columns(cursor, "orders")
    order_items_table = get_order_items_table(cursor)
    item_columns = get_table_columns(cursor, order_items_table)
    ensure_order_tracking_schema(cursor)

    total_expr = "o.total_amount" if "total_amount" in order_columns else (
        "o.total" if "total" in order_columns else "0"
    )
    status_expr = "o.order_status" if "order_status" in order_columns else (
        "o.status" if "status" in order_columns else "'to_pay'"
    )
    ordered_expr = "o.ordered_at" if "ordered_at" in order_columns else (
        "o.order_at" if "order_at" in order_columns else (
            "o.created_at" if "created_at" in order_columns else "NULL"
        )
    )
    delivery_fee_expr = "o.delivery_fee" if "delivery_fee" in order_columns else "0"
    payment_method_expr = "o.payment_method" if "payment_method" in order_columns else "'Cash on Delivery'"
    payment_status_expr = "o.payment_status" if "payment_status" in order_columns else "'Pending'"
    delivery_address_expr = "o.delivery_address" if "delivery_address" in order_columns else "''"
    contact_number_expr = "o.contact_number" if "contact_number" in order_columns else "''"
    order_code_expr = "o.order_code" if "order_code" in order_columns else "o.id"

    plant_name_expr = "oi.plant_name" if "plant_name" in item_columns else "p.name"
    species_expr = "oi.species" if "species" in item_columns else (
        "p.species" if "species" in get_table_columns(cursor, "plants") else "p.category"
    )
    price_expr = "oi.unit_price" if "unit_price" in item_columns else (
        "oi.price" if "price" in item_columns else "p.price"
    )
    subtotal_expr = "oi.subtotal" if "subtotal" in item_columns else f"({price_expr} * oi.quantity)"
    size_expr = "oi.size" if "size" in item_columns else "'Small'"

    cursor.execute(f"""
        SELECT
            o.id,
            {order_code_expr} AS order_code,
            {total_expr} AS total_amount,
            {delivery_fee_expr} AS delivery_fee,
            {payment_method_expr} AS payment_method,
            {payment_status_expr} AS payment_status,
            {status_expr} AS order_status,
            {delivery_address_expr} AS delivery_address,
            {contact_number_expr} AS contact_number,
            {ordered_expr} AS ordered_at,
            oi.id AS order_item_id,
            oi.plant_id,
            {plant_name_expr} AS plant_name,
            {species_expr} AS species,
            {price_expr} AS unit_price,
            oi.quantity,
            {size_expr} AS size,
            {subtotal_expr} AS item_subtotal,
            p.image_url,
            rr.request_status AS return_request_status,
            rr.reason AS return_reason,
            rr.proof_photo AS return_proof_photo,
            rr.owner_response AS return_owner_response,
            pr.rating AS feedback_rating,
            pr.comment AS feedback_comment,
            pr.created_at AS feedback_at
        FROM orders o
        JOIN {order_items_table} oi ON oi.order_id = o.id
        JOIN plants p ON p.id = oi.plant_id
        LEFT JOIN return_refund_requests rr ON rr.order_id = o.id
        LEFT JOIN plant_reviews pr
            ON pr.order_item_id = oi.id
            AND pr.user_id = o.user_id
        WHERE o.user_id = %s
        ORDER BY ordered_at DESC, o.id DESC
    """, (user_id,))
    rows = cursor.fetchall()

    latest_tracking = {}
    order_ids_for_tracking = sorted({row["id"] for row in rows})
    if order_ids_for_tracking:
        placeholders = ", ".join(["%s"] * len(order_ids_for_tracking))
        cursor.execute(f"""
            SELECT order_id, tracking_status, location, note, created_at
            FROM order_tracking
            WHERE order_id IN ({placeholders})
            ORDER BY created_at DESC, id DESC
        """, tuple(order_ids_for_tracking))
        for tracking in cursor.fetchall():
            latest_tracking.setdefault(tracking["order_id"], tracking)

    cursor.close()
    conn.close()

    # Group rows into orders dict
    orders = {}
    for row in rows:
        order_id = row["id"]
        status_key = status_tab_key(row["order_status"])
        payment_status_key = (row["payment_status"] or "").lower()
        if order_id not in orders:
            orders[order_id] = {
                "id":               order_id,
                "order_code":       row["order_code"],
                "status":           status_key,
                "total":            row["total_amount"],
                "total_amount":     row["total_amount"],
                "delivery_fee":     row["delivery_fee"],
                "payment_method":   row["payment_method"],
                "payment_method_label": payment_method_label(row["payment_method"]),
                "payment_status":   row["payment_status"],
                "order_status":     row["order_status"],
                "status_label":     order_status_label(status_key),
                "can_view_receipt": payment_status_key == "paid" or status_key == "completed",
                "delivery_address": row["delivery_address"],
                "contact_number":   row["contact_number"],
                "ordered_at":       row["ordered_at"],
                "return_request_status": row["return_request_status"],
                "return_request_label": return_request_label(row["return_request_status"]),
                "return_reason": row["return_reason"],
                "return_proof_photo": row["return_proof_photo"],
                "return_owner_response": row["return_owner_response"],
                "latest_tracking_status": (latest_tracking.get(order_id) or {}).get("tracking_status"),
                "latest_tracking_location": (latest_tracking.get(order_id) or {}).get("location"),
                "latest_tracking_at": (latest_tracking.get(order_id) or {}).get("created_at"),
                "items":            [],
            }
        orders[order_id]["items"].append({
            "plant_name": row["plant_name"],
            "plant_id":   row["plant_id"],
            "order_item_id": row["order_item_id"],
            "species":    row["species"],
            "image_url":  row["image_url"],
            "quantity":   row["quantity"],
            "size":       row["size"],
            "unit_price": row["unit_price"],
            "subtotal":   row["item_subtotal"],
            "feedback_rating": row["feedback_rating"],
            "feedback_comment": row["feedback_comment"],
            "feedback_at": row["feedback_at"],
        })

    # Group by order_status for the tabs in my_purchase.html
    grouped = {
        "to_pay":        [],
        "to_ship":       [],
        "to_receive":    [],
        "completed":     [],
        "return_refund": [],
        "cancelled":     [],
    }
    for order in orders.values():
        status = status_tab_key(order["order_status"])
        if order.get("return_request_status"):
            status = "return_refund"
            order["status_label"] = order["return_request_label"]
        order["status"] = status
        grouped.setdefault(status, []).append(order)

    return grouped


# ── HOME ──────────────────────────────────────────────────────────────────────

@customer.route("/customer")
def customer_home():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    plant_columns = get_table_columns(cursor, "plants")
    sold_fallback = "COALESCE(p.sold, 0) AS sold" if "sold" in plant_columns else "0 AS sold"

    order_columns = get_table_columns(cursor, "orders")
    status_column = "order_status" if "order_status" in order_columns else (
        "status" if "status" in order_columns else None
    )
    order_items_table = get_order_items_table(cursor)

    if status_column and table_exists(cursor, order_items_table):
        order_column_types = get_table_column_types(cursor, "orders")
        completed_db = db_status_value(order_column_types.get(status_column), "completed")
        item_columns = get_table_columns(cursor, order_items_table)
        quantity_expr = "oi.quantity" if "quantity" in item_columns else "1"

        cursor.execute(f"""
            SELECT p.*,
                   COALESCE(SUM(CASE
                       WHEN o.{status_column} = %s THEN {quantity_expr}
                       ELSE 0
                   END), 0) AS sold
            FROM plants p
            LEFT JOIN {order_items_table} oi ON oi.plant_id = p.id
            LEFT JOIN orders o ON o.id = oi.order_id
            GROUP BY p.id
            ORDER BY p.id DESC
        """, (completed_db,))
    else:
        cursor.execute(f"""
            SELECT p.*, {sold_fallback}
            FROM plants p
            ORDER BY p.id DESC
        """)
    plants = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template("customer.html", plants=plants)


# ── PROFILE ───────────────────────────────────────────────────────────────────

@customer.route("/profile")
@customer.route("/customer_profile.html")
def customer_profile():
    user_id = session.get("user_id")
    if not user_id:
        return redirect("/login")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_customer_schema(cursor)
    conn.commit()
    cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
    user = cursor.fetchone()
    cursor.execute("""
        SELECT id, issue_type, description, proof_photo, status, admin_response, created_at, reviewed_at
        FROM reports
        WHERE user_id = %s
        ORDER BY created_at DESC, id DESC
    """, (user_id,))
    customer_reports = cursor.fetchall()
    cursor.close()
    conn.close()

    if not user:
        return redirect("/login")

    return render_template("customer_profile.html", user=user, customer_reports=customer_reports)


@customer.route("/update-profile-photo", methods=["POST"])
def update_profile_photo():
    user_id = session.get("user_id")
    if not user_id:
        return redirect("/login")

    photo = request.files.get("profile_photo")
    if not photo or not photo.filename or not allowed_profile_file(photo.filename):
        return redirect("/profile")

    upload_folder = os.path.join(current_app.root_path, "static", "profile")
    os.makedirs(upload_folder, exist_ok=True)

    filename = secure_filename(photo.filename)
    extension = filename.rsplit(".", 1)[1].lower()
    saved_name = f"user_{user_id}_{uuid4().hex}.{extension}"
    saved_path = os.path.join(upload_folder, saved_name)
    photo.save(saved_path)

    profile_photo = f"/static/profile/{saved_name}"

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_customer_schema(cursor)
    cursor.execute("UPDATE users SET profile_photo = %s WHERE id = %s", (profile_photo, user_id))
    conn.commit()
    cursor.close()
    conn.close()

    return redirect("/profile")


@customer.route("/update-customer-profile", methods=["POST"])
def update_customer_profile():
    user_id = session.get("user_id")
    if not user_id:
        return {"success": False, "message": "Please log in first."}, 401

    fullname = request.form.get("fullname")
    email    = request.form.get("email")
    phone    = request.form.get("phone")
    address  = request.form.get("address")

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE users
        SET fullname=%s, email=%s, phone=%s, address=%s
        WHERE id=%s
    """, (fullname, email, phone, address, user_id))
    conn.commit()
    cursor.close()
    conn.close()

    return {"success": True, "message": "Profile updated successfully!"}


@customer.route("/customer/report", methods=["POST"])
def submit_customer_report():
    user_id = session.get("user_id")
    if not user_id:
        return redirect("/login")

    issue_type = (request.form.get("issue_type") or "Platform Concern").strip()
    description = (request.form.get("description") or "").strip()
    proof_photo = None
    proof = request.files.get("proof_photo")

    if proof and proof.filename and allowed_proof_file(proof.filename):
        upload_folder = os.path.join(current_app.root_path, "static", "report_proofs")
        os.makedirs(upload_folder, exist_ok=True)
        filename = secure_filename(proof.filename)
        extension = filename.rsplit(".", 1)[1].lower()
        saved_name = f"report_{user_id}_{uuid4().hex}.{extension}"
        proof.save(os.path.join(upload_folder, saved_name))
        proof_photo = f"/static/report_proofs/{saved_name}"

    if not description:
        return redirect("/profile")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_customer_schema(cursor)
    cursor.execute("""
        INSERT INTO reports (user_id, reporter_type, issue_type, description, proof_photo, status)
        VALUES (%s, 'customer', %s, %s, %s, 'pending')
    """, (user_id, issue_type, description, proof_photo))
    conn.commit()
    cursor.close()
    conn.close()

    return redirect("/profile")


# ── PLANT DETAILS ─────────────────────────────────────────────────────────────

@customer.route("/plant/<int:plant_id>")
def plant_details(plant_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_customer_schema(cursor)
    conn.commit()
    cursor.execute("SELECT * FROM plants WHERE id = %s", (plant_id,))
    plant = cursor.fetchone()

    if not plant:
        cursor.close()
        conn.close()
        abort(404)

    order_columns = get_table_columns(cursor, "orders")
    status_column = "order_status" if "order_status" in order_columns else (
        "status" if "status" in order_columns else None
    )
    order_items_table = get_order_items_table(cursor)

    if status_column and table_exists(cursor, order_items_table):
        order_column_types = get_table_column_types(cursor, "orders")
        completed_db = db_status_value(order_column_types.get(status_column), "completed")
        item_columns = get_table_columns(cursor, order_items_table)
        quantity_expr = "oi.quantity" if "quantity" in item_columns else "1"

        cursor.execute(f"""
            SELECT COALESCE(SUM({quantity_expr}), 0) AS sold
            FROM {order_items_table} oi
            JOIN orders o ON o.id = oi.order_id
            WHERE oi.plant_id = %s AND o.{status_column} = %s
        """, (plant_id, completed_db))
        sold_row = cursor.fetchone()
        plant["sold"] = sold_row["sold"] if sold_row else 0

    cursor.execute("""
        SELECT pr.rating, pr.comment, pr.created_at, u.fullname AS name
        FROM plant_reviews pr
        JOIN users u ON u.id = pr.user_id
        WHERE pr.plant_id = %s
        ORDER BY pr.created_at DESC
    """, (plant_id,))
    reviews = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template(
        "cplantdetails.html",
        plant=plant,
        category_label=format_category(plant.get("category")),
        description=plant.get("description") or plant_description(plant),
        gallery=build_plant_gallery(plant),
        reviews=reviews,
    )


# ── CART ──────────────────────────────────────────────────────────────────────

@customer.route("/add_to_cart/<int:plant_id>", methods=["GET", "POST"])
def add_to_cart(plant_id):
    user_id = session.get("user_id")
    if not user_id:
        return redirect("/login")

    session.pop("buy_now_item", None)

    quantity  = request.form.get("quantity", 1, type=int)
    size      = request.form.get("size", "Small")
    next_page = request.form.get("next", "/cart")

    if quantity < 1:
        quantity = 1

    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT id, quantity FROM cart
        WHERE user_id = %s AND plant_id = %s AND size = %s
    """, (user_id, plant_id, size))
    existing = cursor.fetchone()

    if existing:
        cursor.execute("""
            UPDATE cart SET quantity = quantity + %s WHERE id = %s
        """, (quantity, existing["id"]))
    else:
        cursor.execute("""
            INSERT INTO cart (user_id, plant_id, quantity, size)
            VALUES (%s, %s, %s, %s)
        """, (user_id, plant_id, quantity, size))

    conn.commit()
    cursor.close()
    conn.close()

    return redirect(next_page)


@customer.route("/cart")
def customer_cart():
    user_id = session.get("user_id")
    if not user_id:
        return redirect("/login")

    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT c.id, p.name, p.price, p.image_url, c.quantity, c.size
        FROM cart c
        JOIN plants p ON c.plant_id = p.id
        WHERE c.user_id = %s
        ORDER BY c.added_at DESC
    """, (user_id,))
    cart_items = cursor.fetchall()
    cursor.close()
    conn.close()

    subtotal     = sum(item["price"] * item["quantity"] for item in cart_items)
    delivery_fee = 50 if cart_items else 0
    total        = subtotal + delivery_fee

    return render_template(
        "cart.html",
        items=cart_items,
        subtotal=subtotal,
        delivery_fee=delivery_fee,
        total=total,
    )


@customer.route("/update_cart", methods=["POST"])
def update_cart():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"status": "error", "message": "Not logged in"}), 401

    data     = request.json
    item_id  = data.get("item_id")
    quantity = data.get("quantity")

    if not item_id or quantity is None or quantity < 1:
        return jsonify({"status": "error", "message": "Invalid data"}), 400

    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        UPDATE cart SET quantity = %s WHERE id = %s AND user_id = %s
    """, (quantity, item_id, user_id))
    conn.commit()

    cursor.execute("""
        SELECT c.quantity, p.price FROM cart c
        JOIN plants p ON c.plant_id = p.id
        WHERE c.user_id = %s
    """, (user_id,))
    all_items    = cursor.fetchall()
    subtotal     = sum(i["price"] * i["quantity"] for i in all_items)
    delivery_fee = 50 if all_items else 0
    total        = subtotal + delivery_fee

    cursor.close()
    conn.close()

    return jsonify({"status": "success", "subtotal": float(subtotal),
                    "delivery_fee": delivery_fee, "total": float(total)})


@customer.route("/update_size", methods=["POST"])
def update_size():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"status": "error", "message": "Not logged in"}), 401

    data    = request.json
    item_id = data.get("item_id")
    size    = data.get("size")

    if not item_id or not size:
        return jsonify({"status": "error", "message": "Invalid data"}), 400

    conn   = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE cart SET size = %s WHERE id = %s AND user_id = %s
    """, (size, item_id, user_id))
    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({"status": "success"})


@customer.route("/remove_item", methods=["POST"])
def remove_item():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"status": "error", "message": "Not logged in"}), 401

    item_id = request.json.get("item_id")
    if not item_id:
        return jsonify({"status": "error", "message": "Invalid data"}), 400

    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        DELETE FROM cart WHERE id = %s AND user_id = %s
    """, (item_id, user_id))
    conn.commit()

    cursor.execute("""
        SELECT c.quantity, p.price FROM cart c
        JOIN plants p ON c.plant_id = p.id
        WHERE c.user_id = %s
    """, (user_id,))
    remaining    = cursor.fetchall()
    subtotal     = sum(i["price"] * i["quantity"] for i in remaining)
    delivery_fee = 50 if remaining else 0
    total        = subtotal + delivery_fee

    cursor.close()
    conn.close()

    return jsonify({"status": "removed", "subtotal": float(subtotal),
                    "delivery_fee": delivery_fee, "total": float(total)})


# ── BUY NOW ───────────────────────────────────────────────────────────────────

@customer.route("/buy_now/<int:plant_id>", methods=["POST"])
def buy_now(plant_id):
    if not session.get("user_id"):
        return redirect("/login")

    quantity = request.form.get("quantity", 1, type=int)
    size     = request.form.get("size", "Small")

    if quantity < 1:
        quantity = 1

    session["buy_now_item"] = {
        "plant_id": plant_id,
        "quantity": quantity,
        "size":     size,
    }

    return redirect("/checkout")


# ── CHECKOUT ──────────────────────────────────────────────────────────────────

@customer.route("/checkout")
def checkout():
    user_id = session.get("user_id")
    if not user_id:
        return redirect("/login")

    if request.args.get("source") == "cart":
        session.pop("buy_now_item", None)
        selected_ids = [
            int(item_id)
            for item_id in request.args.get("items", "").split(",")
            if item_id.strip().isdigit()
        ]
        session["checkout_cart_ids"] = selected_ids

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Get saved customer contact info
    cursor.execute("""
        SELECT phone, address
        FROM users
        WHERE id = %s
    """, (user_id,))
    user_info = cursor.fetchone() or {}

    contact_number = user_info.get("phone") or ""
    delivery_address = user_info.get("address") or ""

    buy_now_item = session.get("buy_now_item")

    if buy_now_item:
        cursor.execute("""
            SELECT id, name, price, image_url 
            FROM plants 
            WHERE id = %s
        """, (buy_now_item["plant_id"],))
        plant = cursor.fetchone()

        if not plant:
            session.pop("buy_now_item", None)
            cursor.close()
            conn.close()
            abort(404)

        checkout_items = [{
            "id": plant["id"],
            "plant_id": plant["id"],
            "name": plant["name"],
            "price": plant["price"],
            "image_url": plant["image_url"],
            "quantity": buy_now_item["quantity"],
            "size": buy_now_item["size"],
        }]
    else:
        selected_ids = session.get("checkout_cart_ids") or []
        if not selected_ids:
            cursor.close()
            conn.close()
            return redirect("/cart")

        placeholders = ", ".join(["%s"] * len(selected_ids))
        cursor.execute(f"""
            SELECT 
                c.id,
                p.id AS plant_id,
                p.name,
                p.price,
                p.image_url,
                c.quantity,
                c.size
            FROM cart c
            JOIN plants p ON c.plant_id = p.id
            WHERE c.user_id = %s AND c.id IN ({placeholders})
            ORDER BY c.added_at DESC
        """, (user_id, *selected_ids))
        checkout_items = cursor.fetchall()

    cursor.close()
    conn.close()

    subtotal = sum(float(item["price"]) * item["quantity"] for item in checkout_items)
    delivery_fee = 50 if checkout_items else 0
    total = subtotal + delivery_fee
    item_count = sum(item["quantity"] for item in checkout_items)

    payment_methods = {
        "Cash on Delivery": "Cash on Delivery",
        "PayMongo GCash": "GCash",
        "PayMongo Card": "Visa / Mastercard",
    }

    return render_template(
        "checkout.html",
        items=checkout_items,
        subtotal=subtotal,
        delivery_fee=delivery_fee,
        total=total,
        item_count=item_count,
        checkout_source="buy_now" if buy_now_item else "cart",
        checkout_cart_ids=",".join(str(item["id"]) for item in checkout_items),
        payment_method=payment_methods,
        contact_number=contact_number,
        delivery_address=delivery_address
    )


# ── PLACE ORDER ───────────────────────────────────────────────────────────────

@customer.route("/place-order", methods=["POST"])
def place_order():
    user_id = session.get("user_id")
    if not user_id:
        return redirect("/login")

    source = request.form.get("source", "cart")
    payment_method = request.form.get("payment_method", "Cash on Delivery")
    delivery_address = request.form.get("delivery_address")
    contact_number = request.form.get("contact_number")
    selected_cart_ids = [
        int(item_id)
        for item_id in request.form.get("cart_item_ids", "").split(",")
        if item_id.strip().isdigit()
    ] or session.get("checkout_cart_ids", [])

    legacy_payment_map = {
        "GCash": "PayMongo GCash",
        "Bank": "PayMongo Card",
        "Bank Transfer": "PayMongo Card",
        "Card": "PayMongo Card",
        "Visa / Mastercard": "PayMongo Card",
    }
    payment_method = legacy_payment_map.get(payment_method, payment_method)
    valid_payments = ["Cash on Delivery", "PayMongo GCash", "PayMongo Card"]
    if payment_method not in valid_payments:
        payment_method = "Cash on Delivery"
    is_paymongo_order = payment_method in ["PayMongo GCash", "PayMongo Card"]

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_customer_schema(cursor)
    conn.commit()
    plant_columns = get_table_columns(cursor, "plants")
    order_columns = get_table_columns(cursor, "orders")
    order_column_types = get_table_column_types(cursor, "orders")
    order_items_table = get_order_items_table(cursor)
    order_item_columns = get_table_columns(cursor, order_items_table)
    plant_species_expr = "species" if "species" in plant_columns else "category"

    cursor.execute("SELECT fullname, email, phone, address FROM users WHERE id = %s LIMIT 1", (user_id,))
    customer_user = cursor.fetchone() or {}
    delivery_address = (delivery_address or customer_user.get("address") or "").strip()
    contact_number = (contact_number or customer_user.get("phone") or "").strip()

    buy_now_item = session.get("buy_now_item") if source == "buy_now" else None

    if buy_now_item:
        cursor.execute("""
            SELECT 
                id AS plant_id,
                name AS plant_name,
                price AS unit_price,
                stock,
                {plant_species_expr} AS species
            FROM plants 
            WHERE id = %s
        """.format(plant_species_expr=plant_species_expr), (buy_now_item["plant_id"],))
        plant = cursor.fetchone()

        if not plant:
            cursor.close()
            conn.close()
            abort(404)

        order_items = [{
            "plant_id": plant["plant_id"],
            "plant_name": plant["plant_name"],
            "quantity": buy_now_item["quantity"],
            "size": buy_now_item["size"],
            "species": plant["species"],
            "unit_price": float(plant["unit_price"]),
            "stock": plant["stock"],
        }]
    else:
        if not selected_cart_ids:
            cursor.close()
            conn.close()
            return redirect("/cart")

        placeholders = ", ".join(["%s"] * len(selected_cart_ids))
        cursor.execute(f"""
            SELECT 
                c.id AS cart_id,
                c.plant_id,
                p.name AS plant_name,
                p.price AS unit_price,
                p.stock,
                p.{plant_species_expr} AS species,
                c.quantity,
                c.size
            FROM cart c
            JOIN plants p ON p.id = c.plant_id
            WHERE c.user_id = %s AND c.id IN ({placeholders})
        """, (user_id, *selected_cart_ids))
        order_items = cursor.fetchall()

    if not order_items:
        cursor.close()
        conn.close()
        return redirect("/checkout")

    for item in order_items:
        if int(item.get("stock") or 0) < int(item.get("quantity") or 0):
            cursor.close()
            conn.close()
            return redirect("/checkout")

    subtotal = sum(float(item["unit_price"]) * item["quantity"] for item in order_items)
    delivery_fee = 50.00
    total_amount = subtotal + delivery_fee

    payment_status = "Pending"
    order_status = "To Pay" if is_paymongo_order else "Preparing"
    db_order_status = db_status_value(order_column_types.get("order_status"), order_status)
    db_status = db_status_value(order_column_types.get("status"), order_status)
    now = datetime.now()

    order_data = {
        "user_id": user_id,
        "total_amount": total_amount,
        "total": total_amount,
        "subtotal": subtotal,
        "delivery_fee": delivery_fee,
        "payment_method": payment_method,
        "payment_status": payment_status,
        "order_status": db_order_status,
        "status": db_status,
        "delivery_address": delivery_address,
        "contact_number": contact_number,
        "ordered_at": now,
        "order_at": now,
        "order_code": generate_order_code(cursor),
    }
    order_insert_columns = [column for column in order_data if column in order_columns]
    order_placeholders = ", ".join(["%s"] * len(order_insert_columns))
    cursor.execute(f"""
        INSERT INTO orders ({", ".join(order_insert_columns)})
        VALUES ({order_placeholders})
    """, tuple(order_data[column] for column in order_insert_columns))

    order_id = cursor.lastrowid
    created_order_reference = order_data["order_code"] if "order_code" in order_insert_columns else order_id

    for item in order_items:
        item_subtotal = float(item["unit_price"]) * item["quantity"]

        item_data = {
            "order_id": order_id,
            "plant_id": item["plant_id"],
            "plant_name": item["plant_name"],
            "species": item.get("species"),
            "unit_price": item["unit_price"],
            "price": item["unit_price"],
            "quantity": item["quantity"],
            "size": item["size"],
            "subtotal": item_subtotal,
        }
        item_columns = [column for column in item_data if column in order_item_columns]
        item_placeholders = ", ".join(["%s"] * len(item_columns))
        cursor.execute(f"""
            INSERT INTO {order_items_table} ({", ".join(item_columns)})
            VALUES ({item_placeholders})
        """, tuple(item_data[column] for column in item_columns))

    if not is_paymongo_order:
        deduct_stock_from_items(cursor, order_items)
        if "sold_recorded" in order_columns:
            cursor.execute("UPDATE orders SET sold_recorded = 1 WHERE id = %s", (order_id,))

    if not buy_now_item and not is_paymongo_order:
        placeholders = ", ".join(["%s"] * len(selected_cart_ids))
        cursor.execute(f"""
            DELETE FROM cart
            WHERE user_id = %s AND id IN ({placeholders})
        """, (user_id, *selected_cart_ids))

    owner_emails = get_owner_emails(cursor)
    conn.commit()
    cursor.close()
    conn.close()

    session.pop("buy_now_item", None)
    session.pop("checkout_cart_ids", None)
    log_activity(user_id, "customer", "order placed", f"Order {order_id} placed with {payment_method}.")
    send_order_created_email(
        customer_user,
        {
            "reference": created_order_reference,
            "payment_method": payment_method,
            "payment_status": payment_status,
            "order_status": order_status,
            "subtotal": subtotal,
            "delivery_fee": delivery_fee,
            "total": total_amount,
            "delivery_address": delivery_address,
            "contact_number": contact_number,
        },
    )
    if not is_paymongo_order:
        notify_owner_new_order(
            owner_emails,
            created_order_reference,
            customer_user.get("fullname"),
            total_amount,
            payment_method=payment_method,
        )

    if is_paymongo_order:
        return redirect("/my-purchases?tab=to_pay")

    return redirect("/my-purchases")


@customer.route("/pay-order/<int:order_id>", methods=["POST"])
def pay_order(order_id):
    user_id = session.get("user_id")
    if not user_id:
        return redirect("/login")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_customer_schema(cursor)
    conn.commit()

    order_columns = get_table_columns(cursor, "orders")
    total_expr = "total_amount" if "total_amount" in order_columns else ("total" if "total" in order_columns else "subtotal")
    code_expr = "order_code" if "order_code" in order_columns else "id"

    cursor.execute(f"""
        SELECT *, {total_expr} AS checkout_total, {code_expr} AS checkout_code
        FROM orders
        WHERE id = %s AND user_id = %s
        LIMIT 1
    """, (order_id, user_id))
    order = cursor.fetchone()

    if (
        not order
        or (order.get("payment_status") or "").lower() != "pending"
        or order.get("payment_method") not in ["PayMongo GCash", "PayMongo Card", "GCash", "Bank Transfer", "Bank"]
    ):
        cursor.close()
        conn.close()
        return redirect("/my-purchases?tab=to_pay")

    if order.get("payment_method") in ["GCash"]:
        order["payment_method"] = "PayMongo GCash"
    elif order.get("payment_method") in ["Bank Transfer", "Bank"]:
        order["payment_method"] = "PayMongo Card"

    order_items_table = get_order_items_table(cursor)
    item_columns = get_table_columns(cursor, order_items_table)
    plant_columns = get_table_columns(cursor, "plants")
    name_expr = "oi.plant_name" if "plant_name" in item_columns else "p.name"
    species_expr = "oi.species" if "species" in item_columns else ("p.species" if "species" in plant_columns else "p.category")
    price_expr = "oi.unit_price" if "unit_price" in item_columns else ("oi.price" if "price" in item_columns else "p.price")
    subtotal_expr = "oi.subtotal" if "subtotal" in item_columns else f"({price_expr} * oi.quantity)"
    size_expr = "oi.size" if "size" in item_columns else "''"

    cursor.execute(f"""
        SELECT oi.plant_id, {name_expr} AS plant_name, {species_expr} AS species,
               {price_expr} AS unit_price, oi.quantity, {size_expr} AS size,
               {subtotal_expr} AS subtotal
        FROM {order_items_table} oi
        JOIN plants p ON p.id = oi.plant_id
        WHERE oi.order_id = %s
    """, (order_id,))
    items = cursor.fetchall()

    if not items:
        cursor.close()
        conn.close()
        return redirect("/my-purchases?tab=to_pay")

    try:
        base_url = request.host_url.rstrip("/")
        checkout_id, checkout_url = create_paymongo_checkout_session(order, items, base_url)
    except (requests.RequestException, RuntimeError):
        cursor.close()
        conn.close()
        return redirect("/my-purchases?tab=to_pay")

    if checkout_id and "paymongo_checkout_id" in order_columns:
        cursor.execute("""
            UPDATE orders
            SET paymongo_checkout_id = %s
            WHERE id = %s AND user_id = %s
        """, (checkout_id, order_id, user_id))
        conn.commit()

    cursor.close()
    conn.close()

    if checkout_url:
        log_activity(user_id, "customer", "pay now clicked", f"Customer started PayMongo checkout for order {order_id}.")
        return redirect(checkout_url)
    return redirect("/my-purchases?tab=to_pay")


@customer.route("/payment-success/<int:order_id>")
def payment_success(order_id):
    if not session.get("user_id"):
        return redirect("/login")
    return redirect("/my-purchases")


@customer.route("/payment-cancel/<int:order_id>")
def payment_cancel(order_id):
    if not session.get("user_id"):
        return redirect("/login")
    return redirect("/my-purchases?tab=to_pay")


@customer.route("/paymongo-webhook", methods=["POST"])
def paymongo_webhook():
    print("PAYMONGO WEBHOOK RECEIVED")
    event = request.get_json(silent=True) or {}
    print(event)

    attributes = event.get("data", {}).get("attributes", {})
    event_type = attributes.get("type") or event.get("type")
    event_id = event.get("data", {}).get("id") or event.get("id")

    checkout_session = attributes.get("data") or {}
    checkout_id = checkout_session.get("id") if isinstance(checkout_session, dict) else None
    checkout_attributes = checkout_session.get("attributes", {}) if isinstance(checkout_session, dict) else {}
    payments = checkout_attributes.get("payments") or []
    payment = payments[0] if payments and isinstance(payments[0], dict) else {}
    payment_id = payment.get("id") or checkout_attributes.get("payment_intent_id")
    payment_reference = (
        checkout_attributes.get("reference_number")
        or payment.get("attributes", {}).get("reference_number")
        or checkout_id
    )

    if not checkout_id:
        return jsonify({"received": True})

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_customer_schema(cursor)
    conn.commit()

    if event_id:
        cursor.execute("SELECT id FROM payment_events WHERE event_id = %s LIMIT 1", (event_id,))
        if cursor.fetchone():
            cursor.close()
            conn.close()
            return jsonify({"received": True})

    order_columns = get_table_columns(cursor, "orders")
    if "paymongo_checkout_id" not in order_columns:
        cursor.execute("""
            INSERT IGNORE INTO payment_events (event_id, event_type, checkout_id, raw_payload)
            VALUES (%s, %s, %s, %s)
        """, (event_id, event_type, checkout_id, json.dumps(event)))
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({"received": True})

    cursor.execute("""
        SELECT *
        FROM orders
        WHERE paymongo_checkout_id = %s
        LIMIT 1
    """, (checkout_id,))
    order = cursor.fetchone()

    cursor.execute("""
        INSERT IGNORE INTO payment_events (event_id, event_type, checkout_id, order_id, raw_payload)
        VALUES (%s, %s, %s, %s, %s)
    """, (event_id, event_type, checkout_id, order["id"] if order else None, json.dumps(event)))

    if event_type != "checkout_session.payment.paid":
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({"received": True})

    if not order or (order.get("payment_status") or "").lower() == "paid":
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({"received": True})

    finalized = finalize_paid_paymongo_order(cursor, order, payment_id, payment_reference)
    if finalized:
        log_activity(order["user_id"], "customer", "paymongo payment paid", f"Order {order['id']} was marked paid by PayMongo webhook.")
        send_payment_confirmed_email(cursor, order["id"])
        owner_emails = get_owner_emails(cursor)
        order_columns = get_table_columns(cursor, "orders")
        total_expr = "o.total_amount" if "total_amount" in order_columns else ("o.total" if "total" in order_columns else "o.subtotal")
        code_expr = "o.order_code" if "order_code" in order_columns else "o.id"
        cursor.execute(f"""
            SELECT {code_expr} AS order_code, {total_expr} AS total_amount, u.fullname
            FROM orders o
            JOIN users u ON u.id = o.user_id
            WHERE o.id = %s
            LIMIT 1
        """, (order["id"],))
        order_notice = cursor.fetchone() or {}
        notify_owner_new_order(
            owner_emails,
            order_notice.get("order_code") or order["id"],
            order_notice.get("fullname"),
            order_notice.get("total_amount"),
            payment_method=order.get("payment_method"),
        )
    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({"received": True})


# ── MY PURCHASES ──────────────────────────────────────────────────────────────

@customer.route("/cancel-order/<int:order_id>", methods=["POST"])
def cancel_order(order_id):
    user_id = session.get("user_id")
    if not user_id:
        return redirect("/login")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    order_columns = get_table_columns(cursor, "orders")
    order_column_types = get_table_column_types(cursor, "orders")
    status_columns = [column for column in ("order_status", "status") if column in order_columns]

    if status_columns:
        assignments = []
        values = []
        for column in status_columns:
            assignments.append(f"{column} = %s")
            values.append(db_status_value(order_column_types.get(column), "cancelled"))
        cursor.execute(f"""
            UPDATE orders
            SET {", ".join(assignments)}
            WHERE id = %s AND user_id = %s
        """, tuple(values + [order_id, user_id]))
        conn.commit()

    cursor.close()
    conn.close()

    return redirect("/my-purchases")


@customer.route("/rate-product", methods=["POST"])
def rate_product():
    user_id = session.get("user_id")
    if not user_id:
        return redirect("/login")

    plant_id = request.form.get("plant_id", type=int)
    order_id = request.form.get("order_id", type=int)
    order_item_id = request.form.get("order_item_id", type=int)
    rating = request.form.get("rating", type=int)
    comment = (request.form.get("comment") or "").strip()

    if not plant_id or not order_id or not rating or rating < 1 or rating > 5:
        return redirect("/my-purchases")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_customer_schema(cursor)

    plant_columns = get_table_columns(cursor, "plants")
    if "average_rating" not in plant_columns:
        cursor.execute("ALTER TABLE plants ADD COLUMN average_rating DECIMAL(3,2) DEFAULT 0")
    if "rating_count" not in plant_columns:
        cursor.execute("ALTER TABLE plants ADD COLUMN rating_count INT DEFAULT 0")

    cursor.execute("""
        SELECT o.id
        FROM orders o
        WHERE o.id = %s AND o.user_id = %s
    """, (order_id, user_id))
    owns_order = cursor.fetchone()

    if owns_order:
        cursor.execute("""
            SELECT id
            FROM plant_reviews
            WHERE user_id = %s AND order_item_id = %s
            LIMIT 1
        """, (user_id, order_item_id))
        existing_feedback = cursor.fetchone()

        if existing_feedback:
            cursor.close()
            conn.close()
            return redirect("/my-purchases")

        cursor.execute("""
            INSERT INTO plant_reviews
                (plant_id, user_id, order_id, order_item_id, rating, comment)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (plant_id, user_id, order_id, order_item_id, rating, comment))

        cursor.execute("""
            UPDATE plants p
            SET average_rating = (
                    SELECT COALESCE(AVG(rating), 0)
                    FROM plant_reviews
                    WHERE plant_id = %s
                ),
                rating_count = (
                    SELECT COUNT(*)
                    FROM plant_reviews
                    WHERE plant_id = %s
                )
            WHERE p.id = %s
        """, (plant_id, plant_id, plant_id))

        conn.commit()

    cursor.close()
    conn.close()

    return redirect("/my-purchases?tab=completed")


@customer.route("/request-return-refund", methods=["POST"])
def request_return_refund():
    user_id = session.get("user_id")
    if not user_id:
        return redirect("/login")

    order_id = request.form.get("order_id", type=int)
    reason = (request.form.get("reason") or "").strip()
    proof = request.files.get("proof_photo")

    if not order_id or not reason or not proof or not proof.filename or not allowed_proof_file(proof.filename):
        return redirect("/my-purchases?tab=completed")

    upload_folder = os.path.join(current_app.root_path, "static", "refund_proofs")
    os.makedirs(upload_folder, exist_ok=True)

    filename = secure_filename(proof.filename)
    extension = filename.rsplit(".", 1)[1].lower()
    saved_name = f"return_{user_id}_{order_id}_{uuid4().hex}.{extension}"
    proof.save(os.path.join(upload_folder, saved_name))
    proof_path = f"/static/refund_proofs/{saved_name}"

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_customer_schema(cursor)

    cursor.execute("SELECT id FROM orders WHERE id = %s AND user_id = %s", (order_id, user_id))
    owns_order = cursor.fetchone()
    if owns_order:
        cursor.execute("""
            SELECT id
            FROM return_refund_requests
            WHERE order_id = %s AND user_id = %s
            LIMIT 1
        """, (order_id, user_id))
        existing = cursor.fetchone()

        if not existing:
            cursor.execute("""
                INSERT INTO return_refund_requests
                    (order_id, user_id, reason, proof_photo, request_status)
                VALUES (%s, %s, %s, %s, 'pending')
            """, (order_id, user_id, reason, proof_path))
            conn.commit()

    cursor.close()
    conn.close()

    return redirect("/my-purchases?tab=return_refund")


def fetch_receipt(order_id, user_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_customer_schema(cursor)
    conn.commit()

    order_columns = get_table_columns(cursor, "orders")
    total_expr = "o.total_amount" if "total_amount" in order_columns else ("o.total" if "total" in order_columns else "0")
    date_expr = "o.ordered_at" if "ordered_at" in order_columns else ("o.order_at" if "order_at" in order_columns else ("o.created_at" if "created_at" in order_columns else "NULL"))
    code_expr = "COALESCE(o.receipt_no, o.order_code)" if {"receipt_no", "order_code"}.issubset(order_columns) else (
        "o.receipt_no" if "receipt_no" in order_columns else ("o.order_code" if "order_code" in order_columns else "o.id")
    )
    status_expr = "o.order_status" if "order_status" in order_columns else ("o.status" if "status" in order_columns else "''")

    cursor.execute(f"""
        SELECT o.*, {total_expr} AS receipt_total, {date_expr} AS receipt_date,
               {code_expr} AS receipt_code, {status_expr} AS receipt_status,
               u.fullname, u.email, u.phone, u.address
        FROM orders o
        JOIN users u ON u.id = o.user_id
        WHERE o.id = %s AND o.user_id = %s
    """, (order_id, user_id))
    order = cursor.fetchone()

    receipt_status_key = status_tab_key(order.get("receipt_status"))
    can_view_receipt = (order.get("payment_status") or "").lower() == "paid" or receipt_status_key == "completed"
    if not order or not can_view_receipt:
        cursor.close()
        conn.close()
        return None, []

    order_items_table = get_order_items_table(cursor)
    item_columns = get_table_columns(cursor, order_items_table)
    name_expr = "oi.plant_name" if "plant_name" in item_columns else "p.name"
    price_expr = "oi.unit_price" if "unit_price" in item_columns else ("oi.price" if "price" in item_columns else "p.price")
    subtotal_expr = "oi.subtotal" if "subtotal" in item_columns else f"({price_expr} * oi.quantity)"
    size_expr = "oi.size" if "size" in item_columns else "''"

    cursor.execute(f"""
        SELECT {name_expr} AS plant_name, {price_expr} AS unit_price,
               oi.quantity, {size_expr} AS size, {subtotal_expr} AS subtotal
        FROM {order_items_table} oi
        JOIN plants p ON p.id = oi.plant_id
        WHERE oi.order_id = %s
    """, (order_id,))
    items = cursor.fetchall()

    cursor.close()
    conn.close()
    return order, items


@customer.route("/receipt/<int:order_id>")
def view_receipt(order_id):
    user_id = session.get("user_id")
    if not user_id:
        return redirect("/login")

    order, items = fetch_receipt(order_id, user_id)
    if not order:
        abort(404)

    return render_template("receipt.html", order=order, items=items)


def pdf_text(value):
    text = str(value or "").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return text.encode("latin-1", "ignore").decode("latin-1")


def money_text(value):
    return f"PHP {value or 0}"


def truncate_text(value, limit):
    text = str(value or "")
    return text if len(text) <= limit else f"{text[:limit - 3]}..."


def designed_receipt_pdf(order, items):
    pages = []
    commands = []

    def color(hex_color):
        hex_color = hex_color.lstrip("#")
        r = int(hex_color[0:2], 16) / 255
        g = int(hex_color[2:4], 16) / 255
        b = int(hex_color[4:6], 16) / 255
        return f"{r:.3f} {g:.3f} {b:.3f}"

    def rect(x, y, w, h, fill):
        commands.append(f"{color(fill)} rg {x} {y} {w} {h} re f")

    def stroke_rect(x, y, w, h, stroke):
        commands.append(f"{color(stroke)} RG {x} {y} {w} {h} re S")

    def text(value, x, y, size=10, font="F1", fill="#1f2d22"):
        commands.append(f"{color(fill)} rg BT /{font} {size} Tf {x} {y} Td ({pdf_text(value)}) Tj ET")

    def header():
        rect(0, 680, 612, 112, "#2e7d32")
        rect(42, 716, 46, 46, "#ffffff")
        text("G", 57, 730, 24, "F2", "#2e7d32")
        text("Green Nursery", 104, 744, 24, "F2", "#ffffff")
        text("Official customer e-receipt", 104, 724, 11, "F1", "#e8f5e9")
        text(f"Receipt #{order['receipt_code']}", 410, 744, 13, "F2", "#ffffff")
        text(order["receipt_date"], 410, 724, 10, "F1", "#e8f5e9")

    def new_page(include_table_header=False):
        nonlocal commands
        if commands:
            pages.append(commands)
        commands = []
        rect(0, 0, 612, 792, "#f4faf3")
        rect(28, 28, 556, 736, "#ffffff")
        stroke_rect(28, 28, 556, 736, "#dcebdc")
        header()
        if include_table_header:
            draw_table_header(42, 612)
            return 588
        return 0

    def draw_info_box(x, y, w, h, label, value):
        rect(x, y, w, h, "#f7fbf7")
        stroke_rect(x, y, w, h, "#e2f0e2")
        text(label, x + 12, y + h - 18, 9, "F2", "#2e7d32")
        text(truncate_text(value, 38), x + 12, y + h - 38, 10, "F1", "#1f2d22")

    def draw_table_header(x, y):
        rect(x, y, 528, 26, "#f0f8f0")
        stroke_rect(x, y, 528, 26, "#e2f0e2")
        text("Plant", x + 10, y + 9, 9, "F2", "#2e7d32")
        text("Size", x + 225, y + 9, 9, "F2", "#2e7d32")
        text("Qty", x + 310, y + 9, 9, "F2", "#2e7d32")
        text("Unit Price", x + 365, y + 9, 9, "F2", "#2e7d32")
        text("Subtotal", x + 455, y + 9, 9, "F2", "#2e7d32")

    new_page()
    text("Customer & Order Details", 42, 645, 12, "F2", "#2e7d32")
    draw_info_box(42, 592, 250, 42, "Customer", order["fullname"])
    draw_info_box(320, 592, 250, 42, "Contact", order.get("phone") or order.get("contact_number"))
    draw_info_box(42, 536, 250, 42, "Email", order.get("email") or "N/A")
    draw_info_box(320, 536, 250, 42, "Payment Method", order.get("payment_method"))
    draw_info_box(42, 480, 250, 42, "Delivery Address", order.get("delivery_address") or order.get("address"))
    draw_info_box(320, 480, 250, 42, "Payment Status", order.get("payment_status"))
    draw_info_box(42, 424, 250, 42, "Paid At", order.get("paid_at") or "N/A")
    draw_info_box(320, 424, 250, 42, "Order Status", order.get("receipt_status"))

    text("Purchased Plants", 42, 384, 12, "F2", "#2e7d32")
    draw_table_header(42, 348)
    y = 322
    for item in items:
        if y < 92:
            y = new_page(include_table_header=True)
        rect(42, y - 4, 528, 26, "#ffffff")
        stroke_rect(42, y - 4, 528, 26, "#e7efe7")
        text(truncate_text(item["plant_name"], 32), 52, y + 5, 9, "F1", "#1f2d22")
        text(item.get("size") or "", 267, y + 5, 9, "F1", "#1f2d22")
        text(item["quantity"], 352, y + 5, 9, "F1", "#1f2d22")
        text(money_text(item["unit_price"]), 407, y + 5, 9, "F1", "#1f2d22")
        text(money_text(item["subtotal"]), 497, y + 5, 9, "F1", "#1f2d22")
        y -= 30

    if y < 118:
        y = new_page()
    rect(390, y - 12, 180, 48, "#2e7d32")
    text("Amount Paid", 412, y + 15, 9, "F1", "#e8f5e9")
    text(money_text(order["receipt_total"]), 412, y - 4, 17, "F2", "#ffffff")
    text(
        "Thank you for shopping with Green Nursery. Keep this receipt for order tracking and return or refund review.",
        42,
        66,
        9,
        "F1",
        "#5e6d60",
    )
    pages.append(commands)

    objects = [
        "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj",
        "3 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj",
        "4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >> endobj",
    ]
    page_refs = []
    next_id = 5
    for page_commands in pages:
        stream = "\n".join(page_commands)
        stream_bytes = stream.encode("latin-1", "ignore")
        page_id = next_id
        content_id = next_id + 1
        next_id += 2
        page_refs.append(f"{page_id} 0 R")
        objects.append(
            f"{page_id} 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {content_id} 0 R >> endobj"
        )
        objects.append(
            f"{content_id} 0 obj << /Length {len(stream_bytes)} >> stream\n{stream}\nendstream endobj"
        )

    objects.insert(1, f"2 0 obj << /Type /Pages /Kids [{' '.join(page_refs)}] /Count {len(page_refs)} >> endobj")

    pdf = "%PDF-1.4\n"
    offsets = [0]
    for obj in objects:
        offsets.append(len(pdf.encode("latin-1")))
        pdf += obj + "\n"
    xref = len(pdf.encode("latin-1"))
    pdf += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    for offset in offsets[1:]:
        pdf += f"{offset:010d} 00000 n \n"
    pdf += f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF"
    return pdf.encode("latin-1", "ignore")


@customer.route("/receipt/<int:order_id>/pdf")
def download_receipt_pdf(order_id):
    user_id = session.get("user_id")
    if not user_id:
        return redirect("/login")

    order, items = fetch_receipt(order_id, user_id)
    if not order:
        abort(404)

    return Response(
        designed_receipt_pdf(order, items),
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=receipt-{order_id}.pdf"},
    )


def format_chatbot_price(value):
    try:
        return f"PHP {float(value):,.2f}"
    except (TypeError, ValueError):
        return "price unavailable"


def plantpal_plant_line(plant):
    stock = plant.get("stock") or 0
    stock_text = f"{stock} in stock" if stock else "currently out of stock"
    return f"{plant.get('name')} ({plant.get('category') or 'Plant'}) - {format_chatbot_price(plant.get('price'))}, {stock_text}"


def get_latest_customer_order(cursor, user_id):
    order_columns = get_table_columns(cursor, "orders")
    total_expr = "total_amount" if "total_amount" in order_columns else (
        "total" if "total" in order_columns else (
            "subtotal" if "subtotal" in order_columns else "0"
        )
    )
    status_expr = "order_status" if "order_status" in order_columns else (
        "status" if "status" in order_columns else "''"
    )
    payment_method_expr = "payment_method" if "payment_method" in order_columns else "''"
    payment_status_expr = "payment_status" if "payment_status" in order_columns else "''"
    order_code_expr = "order_code" if "order_code" in order_columns else "id"
    sort_column = "ordered_at" if "ordered_at" in order_columns else (
        "order_at" if "order_at" in order_columns else (
            "created_at" if "created_at" in order_columns else "id"
        )
    )

    cursor.execute(f"""
        SELECT {order_code_expr} AS order_code,
               {status_expr} AS order_status,
               {payment_method_expr} AS payment_method,
               {payment_status_expr} AS payment_status,
               {total_expr} AS total_amount
        FROM orders
        WHERE user_id = %s
        ORDER BY {sort_column} DESC, id DESC
        LIMIT 1
    """, (user_id,))
    return cursor.fetchone()


def forward_plantpal_question_to_owner(cursor, user_id, question):
    if not user_id:
        return "login_required"

    ensure_message_schema(cursor)
    owner_user = get_owner_user(cursor)
    if not owner_user:
        return {"status": "no_owner"}

    thread_id = get_or_create_thread(cursor, user_id, owner_user["id"])
    insert_message(
        cursor,
        thread_id,
        user_id,
        owner_user["id"],
        "PlantPal forwarded this customer question:\n\n" + question,
        [],
    )
    return {
        "status": "forwarded",
        "owner_name": owner_user.get("fullname") or "Green Owner",
    }


@customer.route("/customer-chatbot", methods=["POST"])
def customer_chatbot():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    lower_message = message.lower()
    user_id = session.get("user_id")

    if not message:
        return jsonify({
            "reply": "I am here whenever you need help. You can ask me about plants, checkout, payment, delivery, or your purchases."
        })

    greetings = ("hello", "hi", "hey", "good morning", "good afternoon", "good evening")
    care_words = ("care", "water", "watering", "sunlight", "soil", "fertilizer", "pest", "overwater")
    order_words = ("where is my order", "order status", "track", "tracking", "my order", "my purchases")
    cart_words = ("cart", "basket")
    checkout_words = ("checkout", "check out", "place order", "buy")
    payment_words = ("payment", "pay", "gcash", "bank", "cod", "cash on delivery")
    delivery_words = ("delivery", "shipping", "deliver", "fee")
    profile_words = ("profile", "address", "contact", "phone", "number")
    refund_words = ("cancel", "cancellation", "refund", "return", "damaged", "wrong plant")
    guide_words = ("guide", "how to use", "website", "navigate")
    category_map = {
        "indoor": "indoor",
        "outdoor": "outdoor",
        "fruit-bearing": "fruit",
        "fruit bearing": "fruit",
        "fruit": "fruit",
        "flowering": "flowering",
        "flower": "flowering",
    }

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        if any(word in lower_message for word in greetings):
            reply = random.choice([
                "Hello! I am PlantPal. I can help you find plants, understand care tips, or guide you through checkout and order tracking.",
                "Hi there! Welcome to Green Plant Nursery. Tell me what kind of plant you are looking for and I will help you choose.",
                "Hello, plant friend! I can help with recommendations, care tips, payment, delivery, cart, checkout, and purchases.",
            ])

        elif any(word in lower_message for word in order_words):
            if not user_id:
                reply = "You can track orders in My Purchases after logging in. Once you are logged in, I can also check your latest order for you."
            else:
                order = get_latest_customer_order(cursor, user_id)
                if not order:
                    reply = "I checked your account, and you do not have any purchases yet. You can browse plants on the Home page and add your favorites to the cart."
                else:
                    reply = (
                        f"Sure! Your latest order is #{order['order_code']}. "
                        f"Status: {order.get('order_status') or 'Pending'}. "
                        f"Payment method: {payment_method_label(order.get('payment_method'))}. "
                        f"Payment status: {(order.get('payment_status') or 'Pending')}. "
                        f"Total: {format_chatbot_price(order.get('total_amount'))}. "
                        "You can open My Purchases to see the full details and updates."
                    )

        elif any(word in lower_message for word in cart_words):
            if not user_id:
                reply = "Your cart keeps the plants you want to buy before checkout. Please log in first so your cart items can be saved properly."
            else:
                cursor.execute("SELECT COALESCE(SUM(quantity), 0) AS cart_count FROM cart WHERE user_id = %s", (user_id,))
                cart_count = cursor.fetchone()["cart_count"]
                reply = (
                    f"You currently have {cart_count} plant item{'s' if cart_count != 1 else ''} in your cart. "
                    "Open Cart to review quantities, sizes, and selected plants before checkout."
                )

        elif "recommend" in lower_message or "suggest" in lower_message or "best plant" in lower_message:
            plant_columns = get_table_columns(cursor, "plants")
            sold_expr = "COALESCE(sold, 0)" if "sold" in plant_columns else "0"
            cursor.execute("""
                SELECT name, category, price, stock
                FROM plants
                WHERE stock > 0
                ORDER BY """ + sold_expr + """ DESC, name ASC
                LIMIT 5
            """)
            plants = cursor.fetchall()
            if plants:
                recommendations = "; ".join(plantpal_plant_line(plant) for plant in plants)
                reply = random.choice([
                    f"Of course! These are good available choices right now: {recommendations}. If you want something easy, indoor plants are usually beginner-friendly.",
                    f"I would be happy to help you choose. Here are some available plants: {recommendations}. You can click a plant card to see details before adding it to cart.",
                ])
            else:
                reply = "I do not see available plants in stock right now. Please check again later or contact the shop for restocking updates."

        elif any(category in lower_message for category in category_map):
            selected_category = next(value for key, value in category_map.items() if key in lower_message)
            cursor.execute("""
                SELECT name, category, price, stock
                FROM plants
                WHERE LOWER(category) = %s
                ORDER BY stock DESC, name ASC
                LIMIT 5
            """, (selected_category,))
            plants = cursor.fetchall()
            if plants:
                reply = f"Here are {selected_category} plants you can check: " + "; ".join(
                    plantpal_plant_line(plant) for plant in plants
                ) + "."
            else:
                reply = f"I could not find {selected_category} plants right now. You may still browse the Home page to see all available plants."

        elif any(word in lower_message for word in care_words):
            reply = random.choice([
                "For most plants, check the soil first. If the top soil feels dry, that is usually the right time to water. Give bright indirect light when possible, use well-draining soil, and avoid letting roots sit in water.",
                "PlantPal care tip: do not water on a strict schedule only. Feel the soil, give enough light based on the plant type, remove yellow leaves, and check under leaves for pests.",
                "A simple care routine is watering when the top soil is dry, placing the plant where it gets suitable sunlight, using loose soil, and adding fertilizer lightly during active growth.",
            ])

        elif any(word in lower_message for word in checkout_words):
            extra = ""
            if user_id:
                cursor.execute("SELECT phone, address FROM users WHERE id = %s", (user_id,))
                user = cursor.fetchone() or {}
                if not user.get("phone") or not user.get("address"):
                    extra = " Before placing your order, please update your Profile with your contact number and delivery address so delivery will be smoother."
            else:
                extra = " Please log in first so your cart, contact number, and delivery address can be used during checkout."
            reply = (
                "To checkout, open your Cart, select the plants you want to buy, review the quantity and size, then proceed to Checkout. "
                "After that, choose your payment method and place the order." + extra
            )

        elif any(word in lower_message for word in payment_words):
            reply = (
                "Green Plant Nursery supports Cash on Delivery, GCash, and Bank Transfer. "
                "For Cash on Delivery, payment is received after delivery. For GCash or Bank Transfer, follow the payment instructions during checkout."
            )

        elif any(word in lower_message for word in delivery_words):
            reply = (
                "The delivery fee is shown during checkout before you place the order. "
                "After ordering, you can track the delivery status in My Purchases, such as Preparing, Packed, Out for Delivery, Delivered, or Cancelled."
            )

        elif any(word in lower_message for word in profile_words):
            if not user_id:
                reply = "Your Profile page stores your contact number and delivery address. Please log in first so you can update and save those details."
            else:
                cursor.execute("SELECT phone, address FROM users WHERE id = %s", (user_id,))
                user = cursor.fetchone() or {}
                phone_status = "saved" if user.get("phone") else "not saved yet"
                address_status = "saved" if user.get("address") else "not saved yet"
                reply = (
                    f"I checked your profile. Contact number is {phone_status}, and delivery address is {address_status}. "
                    "Keeping both updated helps the shop deliver your plants smoothly."
                )

        elif any(word in lower_message for word in refund_words):
            reply = (
                "For cancellation, return, or refund concerns, open My Purchases and check the order status. "
                "If the plant was damaged or not what you ordered, use the return/refund request option and provide a photo proof and a short statement."
            )

        elif any(word in lower_message for word in guide_words):
            reply = (
                "Here is a quick guide: Home lets you browse plants. Plant Details shows care and product info. "
                "Cart lets you review selected plants. Checkout is where you confirm address, payment, and delivery. "
                "My Purchases tracks orders, receipts, feedback, and return/refund requests. Profile stores your contact and address."
            )

        else:
            plant_columns = get_table_columns(cursor, "plants")
            species_expr = "species" if "species" in plant_columns else "category"
            cursor.execute(f"""
                SELECT name, category, {species_expr} AS species, price, stock
                FROM plants
                WHERE %s LIKE CONCAT('%%', LOWER(name), '%%')
                ORDER BY CHAR_LENGTH(name) DESC
                LIMIT 1
            """, (lower_message,))
            plant = cursor.fetchone()
            if plant:
                stock_text = "available" if plant.get("stock", 0) > 0 else "currently out of stock"
                reply = (
                    f"{plant['name']} is a {plant.get('category') or 'plant'} plant"
                    f"{' with species ' + plant['species'] if plant.get('species') else ''}. "
                    f"It costs {format_chatbot_price(plant.get('price'))} and is {stock_text}. "
                    "You can open its plant details page to choose a size and add it to your cart."
                )
            else:
                forward_result = forward_plantpal_question_to_owner(cursor, user_id, message)
                forward_status = forward_result.get("status") if isinstance(forward_result, dict) else forward_result
                if forward_status == "forwarded":
                    conn.commit()
                    owner_name = forward_result.get("owner_name") or "Green Owner"
                    reply = (
                        "I am not fully sure about that yet, so I forwarded your question to the nursery owner. "
                        f"You can open the {owner_name} conversation in Messages to continue there."
                    )
                elif forward_status == "login_required":
                    reply = (
                        "I am not fully sure about that yet. Please log in first, then I can forward your question to the nursery owner through Messages."
                    )
                else:
                    reply = (
                        "I am not fully sure about that yet, and I could not find an owner account to forward it to right now. "
                        "You may try asking about plants, checkout, payments, delivery, or My Purchases."
                    )

    except mysql.connector.Error:
        reply = "PlantPal had trouble checking the shop database just now, but I can still help with general plant care, checkout, payment, and delivery questions."
    finally:
        cursor.close()
        conn.close()

    return jsonify({"reply": reply})


@customer.route("/customer/messages", methods=["GET"])
def customer_messages():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "message": "Please log in first."}), 401

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_message_schema(cursor)
    target = "owner"
    owner_user = get_owner_user(cursor)
    contact_user = owner_user
    if not contact_user:
        cursor.close()
        conn.close()
        return jsonify({"success": False, "message": "No contact account found."}), 404

    thread_id = get_or_create_thread(cursor, user_id, contact_user["id"])
    conn.commit()
    messages = fetch_thread_messages(cursor, thread_id)
    cursor.close()
    conn.close()

    return jsonify({
        "success": True,
        "thread_id": thread_id,
        "target": target,
        "contact": {
            "id": contact_user["id"],
            "name": contact_user.get("fullname") or "Green Owner",
            "photo": contact_user.get("profile_photo") or "/static/default-profile.jpg",
        },
        "owner_contact": {
            "id": owner_user["id"] if owner_user else "",
            "name": owner_user.get("fullname") if owner_user else "Green Owner",
            "photo": owner_user.get("profile_photo") if owner_user and owner_user.get("profile_photo") else "/static/default-profile.jpg",
        },
        "messages": [serialize_message(message, user_id) for message in messages],
    })


@customer.route("/customer/messages", methods=["POST"])
def send_customer_message():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "message": "Please log in first."}), 401

    body = (request.form.get("message") or "").strip()
    photos = save_message_photos(request.files.getlist("photos"))
    if not body and not photos:
        return jsonify({"success": False, "message": "Message cannot be empty."}), 400

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_message_schema(cursor)
    target = "owner"
    owner_user = get_owner_user(cursor)
    contact_user = owner_user
    if not contact_user:
        cursor.close()
        conn.close()
        return jsonify({"success": False, "message": "No contact account found."}), 404

    thread_id = get_or_create_thread(cursor, user_id, contact_user["id"])
    insert_message(cursor, thread_id, user_id, contact_user["id"], body, photos)
    conn.commit()
    messages = fetch_thread_messages(cursor, thread_id)
    cursor.close()
    conn.close()

    return jsonify({
        "success": True,
        "messages": [serialize_message(message, user_id) for message in messages],
    })


@customer.route("/my-purchases")
@customer.route("/my_purchase.html")
def my_purchases():
    user_id = session.get("user_id")
    if not user_id:
        return redirect("/login")

    return render_template(
        "my_purchase.html",
        orders_by_status=fetch_customer_orders(user_id),
    )


@customer.route("/track-order/<int:order_id>")
def track_order(order_id):
    user_id = session.get("user_id")
    if not user_id:
        return redirect("/login")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_order_tracking_schema(cursor)
    order_columns = get_table_columns(cursor, "orders")
    code_expr = "order_code" if "order_code" in order_columns else "id"
    status_expr = "order_status" if "order_status" in order_columns else ("status" if "status" in order_columns else "''")
    cursor.execute(f"""
        SELECT *, {code_expr} AS order_code, {status_expr} AS tracking_order_status
        FROM orders
        WHERE id = %s AND user_id = %s
        LIMIT 1
    """, (order_id, user_id))
    order = cursor.fetchone()

    if not order:
        cursor.close()
        conn.close()
        return redirect("/my-purchases")

    cursor.execute("""
        SELECT *
        FROM order_tracking
        WHERE order_id = %s
        ORDER BY created_at DESC, id DESC
    """, (order_id,))
    tracking_updates = cursor.fetchall()
    driver_name = None
    cursor.execute("""
        SELECT u.fullname AS driver_name
        FROM order_driver_assignments oda
        LEFT JOIN users u ON u.id = oda.driver_id
        WHERE oda.order_id = %s AND oda.is_active = 1
        ORDER BY
            CASE WHEN oda.assignment_type = 'delivery' THEN 0 ELSE 1 END,
            oda.assigned_at DESC
        LIMIT 1
    """, (order_id,))
    assigned_driver = cursor.fetchone()
    if assigned_driver:
        driver_name = assigned_driver.get("driver_name")

    cursor.execute("""
        SELECT oll.latitude, oll.longitude, oll.updated_at, u.fullname AS driver_name
        FROM order_live_locations oll
        LEFT JOIN users u ON u.id = oll.driver_id
        WHERE oll.order_id = %s AND oll.is_active = 1
        LIMIT 1
    """, (order_id,))
    live_location = cursor.fetchone()
    cursor.close()
    conn.close()

    initial_location = None
    live_driver_name = (live_location or {}).get("driver_name") if live_location else driver_name
    if live_location:
        initial_location = {
            "latitude": float(live_location["latitude"]),
            "longitude": float(live_location["longitude"]),
            "updated_at": live_location["updated_at"].strftime("%Y-%m-%d %H:%M:%S") if live_location.get("updated_at") else "",
            "driver_name": live_driver_name or "Delivery Driver",
        }

    return render_template(
        "track_order.html",
        order=order,
        tracking_updates=tracking_updates,
        live_location=live_location,
        driver_name=live_driver_name,
        initial_location=initial_location,
        initial_location_json=json.dumps(initial_location),
    )


@customer.route("/track-order/<int:order_id>/live-location")
def get_order_live_location(order_id):
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "message": "Please log in first."}), 401

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_order_tracking_schema(cursor)
    cursor.execute("SELECT id FROM orders WHERE id = %s AND user_id = %s LIMIT 1", (order_id, user_id))
    if not cursor.fetchone():
        cursor.close()
        conn.close()
        return jsonify({"success": False, "message": "Order not found."}), 404

    cursor.execute("""
        SELECT oll.latitude, oll.longitude, oll.updated_at, u.fullname AS driver_name
        FROM order_live_locations oll
        LEFT JOIN users u ON u.id = oll.driver_id
        WHERE oll.order_id = %s AND oll.is_active = 1
        LIMIT 1
    """, (order_id,))
    location = cursor.fetchone()
    cursor.close()
    conn.close()

    if not location:
        return jsonify({"success": True, "has_location": False})

    return jsonify({
        "success": True,
        "has_location": True,
        "latitude": float(location["latitude"]),
        "longitude": float(location["longitude"]),
        "updated_at": location["updated_at"].strftime("%Y-%m-%d %H:%M:%S") if location.get("updated_at") else "",
        "driver_name": location.get("driver_name") or "Delivery Driver",
    })
