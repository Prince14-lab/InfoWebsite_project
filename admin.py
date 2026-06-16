from functools import wraps
from uuid import uuid4
import os

from flask import Blueprint, current_app, flash, redirect, render_template, request, session
import mysql.connector
from werkzeug.security import generate_password_hash
from werkzeug.utils import secure_filename
from message_utils import (
    ensure_message_schema,
    fetch_thread_messages,
    get_owner_user,
    get_or_create_thread,
    insert_message,
    save_message_photos,
    serialize_message,
)
from email_utils import send_email
from notification_utils import notify_admin_announcement
from security_utils import log_activity, password_matches
from tracking_utils import ensure_order_tracking_schema, ensure_return_refund_pickup_schema

admin = Blueprint("admin", __name__)
ALLOWED_ADMIN_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}


def get_db_connection():
    return mysql.connector.connect(**current_app.config["DB_CONFIG"])


def allowed_admin_image(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_ADMIN_IMAGE_EXTENSIONS


def save_admin_profile_photo(file_storage, user_id):
    if not file_storage or not file_storage.filename or not allowed_admin_image(file_storage.filename):
        return None

    upload_folder = os.path.join(current_app.root_path, "static", "profile")
    os.makedirs(upload_folder, exist_ok=True)
    filename = secure_filename(file_storage.filename)
    extension = filename.rsplit(".", 1)[1].lower()
    saved_name = f"admin_{user_id}_{uuid4().hex}.{extension}"
    file_storage.save(os.path.join(upload_folder, saved_name))
    return f"/static/profile/{saved_name}"


def save_announcement_photo(file_storage):
    if not file_storage or not file_storage.filename or not allowed_admin_image(file_storage.filename):
        return None

    upload_folder = os.path.join(current_app.root_path, "static", "announcements")
    os.makedirs(upload_folder, exist_ok=True)
    filename = secure_filename(file_storage.filename)
    extension = filename.rsplit(".", 1)[1].lower()
    saved_name = f"announcement_{uuid4().hex}.{extension}"
    file_storage.save(os.path.join(upload_folder, saved_name))
    return f"/static/announcements/{saved_name}"


def admin_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if session.get("account_type") != "admin":
            if request.path == "/admin-chatbot":
                return {"reply": "Please log in as admin first so AdminPal can check system information safely."}, 401
            return redirect("/login")
        return view(*args, **kwargs)

    return wrapped_view


def table_exists(cursor, table_name):
    cursor.execute("SHOW TABLES LIKE %s", (table_name,))
    return cursor.fetchone() is not None


def get_table_columns(cursor, table_name):
    if not table_exists(cursor, table_name):
        return set()
    cursor.execute(f"SHOW COLUMNS FROM {table_name}")
    return {column["Field"] for column in cursor.fetchall()}


def get_table_column_types(cursor, table_name):
    if not table_exists(cursor, table_name):
        return {}
    cursor.execute(f"SHOW COLUMNS FROM {table_name}")
    return {column["Field"]: column["Type"] for column in cursor.fetchall()}


def ensure_admin_schema(cursor):
    if table_exists(cursor, "users"):
        user_columns = get_table_columns(cursor, "users")
        if "account_status" not in user_columns:
            cursor.execute(
                "ALTER TABLE users ADD COLUMN account_status VARCHAR(20) NOT NULL DEFAULT 'active'"
            )
        if "profile_photo" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN profile_photo VARCHAR(255) NULL")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_logs (
            id INT AUTO_INCREMENT PRIMARY KEY,
            admin_id INT NULL,
            action VARCHAR(255) NOT NULL,
            details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS reports (
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
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS announcements (
            id INT AUTO_INCREMENT PRIMARY KEY,
            admin_id INT NULL,
            body TEXT NOT NULL,
            photo_url VARCHAR(255),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    ensure_order_tracking_schema(cursor)
    ensure_return_refund_pickup_schema(cursor)


def log_admin_action(cursor, action, details=None):
    if table_exists(cursor, "admin_logs"):
        cursor.execute(
            "INSERT INTO admin_logs (admin_id, action, details) VALUES (%s, %s, %s)",
            (session.get("user_id"), action, details),
        )


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


def admin_status_key(status):
    normalized = (status or "").strip()
    return {
        "Preparing": "pending",
        "Packed": "pending",
        "Out for Delivery": "shipped",
        "Delivered": "completed",
        "Cancelled": "cancelled",
        "to_pay": "pending",
        "to_ship": "pending",
        "to_receive": "shipped",
        "completed": "completed",
        "cancelled": "cancelled",
        "return_refund": "cancelled",
    }.get(normalized, (normalized or "pending").lower().replace(" ", "_"))


def money(value):
    try:
        return f"{float(value or 0):,.2f}"
    except (TypeError, ValueError):
        return "0.00"


def first_existing(columns, *names):
    for name in names:
        if name in columns:
            return name
    return None


def sql_expr(columns, alias, *names, default="''"):
    column = first_existing(columns, *names)
    return f"{alias}.{column}" if column else default


def get_order_items_table(cursor):
    if table_exists(cursor, "order_items"):
        return "order_items"
    if table_exists(cursor, "order_item"):
        return "order_item"
    return None


def order_fields(cursor):
    order_columns = get_table_columns(cursor, "orders")
    user_columns = get_table_columns(cursor, "users")
    total_expr = sql_expr(order_columns, "o", "total_amount", "total", "subtotal", default="0")
    subtotal_expr = sql_expr(order_columns, "o", "subtotal", "total_amount", "total", default="0")
    delivery_fee_expr = sql_expr(order_columns, "o", "delivery_fee", default="0")
    status_expr = sql_expr(order_columns, "o", "order_status", "status", default="''")
    status_column = first_existing(order_columns, "order_status", "status")
    payment_expr = sql_expr(order_columns, "o", "payment_method", default="''")
    payment_status_expr = sql_expr(order_columns, "o", "payment_status", default="''")
    code_expr = sql_expr(order_columns, "o", "order_code", default="o.id")
    contact_default = "u.phone" if "phone" in user_columns else "''"
    address_default = "u.address" if "address" in user_columns else "''"
    contact_expr = sql_expr(order_columns, "o", "contact_number", default=contact_default)
    address_expr = sql_expr(order_columns, "o", "delivery_address", "address", default=address_default)
    date_expr = sql_expr(order_columns, "o", "ordered_at", "order_at", "created_at", default="o.id")
    return {
        "columns": order_columns,
        "total_expr": total_expr,
        "subtotal_expr": subtotal_expr,
        "delivery_fee_expr": delivery_fee_expr,
        "status_expr": status_expr,
        "status_column": status_column,
        "payment_expr": payment_expr,
        "payment_status_expr": payment_status_expr,
        "code_expr": code_expr,
        "contact_expr": contact_expr,
        "address_expr": address_expr,
        "date_expr": date_expr,
    }


def fetch_order_items(cursor, order_ids):
    order_items_table = get_order_items_table(cursor)
    if not order_ids or not order_items_table:
        return {}

    item_columns = get_table_columns(cursor, order_items_table)
    placeholders = ", ".join(["%s"] * len(order_ids))
    name_expr = sql_expr(item_columns, "oi", "plant_name", default="p.name")
    species_expr = sql_expr(item_columns, "oi", "species", default="p.species")
    size_expr = sql_expr(item_columns, "oi", "size", default="''")
    qty_expr = sql_expr(item_columns, "oi", "quantity", default="1")
    price_expr = sql_expr(item_columns, "oi", "unit_price", "price", default="0")
    subtotal_expr = sql_expr(item_columns, "oi", "subtotal", default=f"({qty_expr} * {price_expr})")

    cursor.execute(
        f"""
        SELECT oi.order_id, {name_expr} AS plant_name, {species_expr} AS species,
               {size_expr} AS size, {qty_expr} AS quantity, {price_expr} AS unit_price,
               {subtotal_expr} AS subtotal
        FROM {order_items_table} oi
        LEFT JOIN plants p ON p.id = oi.plant_id
        WHERE oi.order_id IN ({placeholders})
        ORDER BY oi.id ASC
        """,
        tuple(order_ids),
    )
    items = cursor.fetchall()

    items_by_order = {}
    for item in items:
        item["unit_price_display"] = money(item.get("unit_price"))
        item["subtotal_display"] = money(item.get("subtotal"))
        items_by_order.setdefault(item["order_id"], []).append(item)
    return items_by_order


def fetch_admin_orders(cursor, limit=None):
    if not table_exists(cursor, "orders"):
        return []

    fields = order_fields(cursor)
    query = f"""
        SELECT o.id, {fields["code_expr"]} AS order_code,
               {fields["subtotal_expr"]} AS subtotal,
               {fields["delivery_fee_expr"]} AS delivery_fee,
               {fields["total_expr"]} AS total_amount,
               {fields["status_expr"]} AS order_status,
               {fields["payment_expr"]} AS payment_method,
               {fields["payment_status_expr"]} AS payment_status,
               {fields["contact_expr"]} AS contact_number,
               {fields["address_expr"]} AS delivery_address,
               {fields["date_expr"]} AS ordered_at,
               u.fullname AS customer_name
        FROM orders o
        LEFT JOIN users u ON u.id = o.user_id
        ORDER BY {fields["date_expr"]} DESC, o.id DESC
    """
    params = ()
    if limit:
        query += " LIMIT %s"
        params = (limit,)
    cursor.execute(query, params)
    orders = cursor.fetchall()

    items_by_order = fetch_order_items(cursor, [order["id"] for order in orders])
    for order in orders:
        order_items = items_by_order.get(order["id"], [])
        order["items"] = order_items
        order["items_text"] = ", ".join(
            f"{item.get('plant_name') or 'Plant'} x{item.get('quantity') or 1}"
            for item in order_items
        ) or "No items"
        order["items_detail"] = "\n".join(
            (
                f"{item.get('plant_name') or 'Plant'}"
                f"{' - ' + item.get('species') if item.get('species') else ''}"
                f"{' (' + item.get('size') + ')' if item.get('size') else ''}"
                f" x{item.get('quantity') or 1}"
                f" | PHP {item.get('unit_price_display')}"
                f" | Subtotal PHP {item.get('subtotal_display')}"
            )
            for item in order_items
        ) or "No items recorded."
        order["status_key"] = admin_status_key(order.get("order_status"))
        order["subtotal_display"] = money(order.get("subtotal"))
        order["delivery_fee_display"] = money(order.get("delivery_fee"))
        order["total_display"] = money(order.get("total_amount"))
    return orders


def count_orders_for_status(cursor, values):
    fields = order_fields(cursor)
    status_column = fields["status_column"]
    if not status_column or not values:
        return 0
    placeholders = ", ".join(["%s"] * len(values))
    cursor.execute(
        f"SELECT COUNT(*) AS total FROM orders WHERE {status_column} IN ({placeholders})",
        tuple(values),
    )
    return cursor.fetchone()["total"]


def admin_counts(cursor):
    counts = {
        "total_customers": 0,
        "active_customers": 0,
        "blocked_customers": 0,
        "new_customers_month": 0,
        "total_owners": 0,
        "total_plants": 0,
        "total_orders": 0,
        "active_orders": 0,
        "completed_orders": 0,
        "cancelled_orders": 0,
        "total_reports": 0,
        "pending_reports": 0,
        "resolved_reports": 0,
        "escalated_reports": 0,
        "messages": 0,
        "low_stock": 0,
    }

    if table_exists(cursor, "users"):
        user_columns = get_table_columns(cursor, "users")
        cursor.execute("SELECT COUNT(*) AS total FROM users WHERE account_type = 'customer'")
        counts["total_customers"] = cursor.fetchone()["total"]
        cursor.execute("SELECT COUNT(*) AS total FROM users WHERE account_type = 'owner'")
        counts["total_owners"] = cursor.fetchone()["total"]
        if "account_status" in user_columns:
            cursor.execute(
                "SELECT COUNT(*) AS total FROM users WHERE account_type = 'customer' AND account_status = 'blocked'"
            )
            counts["blocked_customers"] = cursor.fetchone()["total"]
            cursor.execute(
                "SELECT COUNT(*) AS total FROM users WHERE account_type = 'customer' AND account_status = 'active'"
            )
            counts["active_customers"] = cursor.fetchone()["total"]
        else:
            counts["active_customers"] = counts["total_customers"]
        if "created_at" in user_columns:
            cursor.execute(
                """
                SELECT COUNT(*) AS total
                FROM users
                WHERE account_type = 'customer'
                  AND MONTH(created_at) = MONTH(CURDATE())
                  AND YEAR(created_at) = YEAR(CURDATE())
                """
            )
            counts["new_customers_month"] = cursor.fetchone()["total"]

    if table_exists(cursor, "plants"):
        cursor.execute("SELECT COUNT(*) AS total FROM plants")
        counts["total_plants"] = cursor.fetchone()["total"]
        cursor.execute("SELECT COUNT(*) AS total FROM plants WHERE stock <= 5")
        counts["low_stock"] = cursor.fetchone()["total"]

    if table_exists(cursor, "orders"):
        cursor.execute("SELECT COUNT(*) AS total FROM orders")
        counts["total_orders"] = cursor.fetchone()["total"]
        fields = order_fields(cursor)
        status_column = fields["status_column"]
        if status_column:
            order_types = get_table_column_types(cursor, "orders")
            pending_values = [
                db_status_value(order_types.get(status_column), status)
                for status in ("to_pay", "to_ship", "to_receive")
            ]
            counts["active_orders"] = count_orders_for_status(cursor, pending_values)
            counts["completed_orders"] = count_orders_for_status(
                cursor, [db_status_value(order_types.get(status_column), "completed")]
            )
            counts["cancelled_orders"] = count_orders_for_status(
                cursor, [db_status_value(order_types.get(status_column), "cancelled")]
            )
        else:
            counts["active_orders"] = counts["total_orders"]

    if table_exists(cursor, "return_refund_requests"):
        cursor.execute("SELECT COUNT(*) AS total FROM return_refund_requests")
        counts["total_reports"] += cursor.fetchone()["total"]
        request_columns = get_table_columns(cursor, "return_refund_requests")
        if "request_status" in request_columns:
            cursor.execute(
                "SELECT COUNT(*) AS total FROM return_refund_requests WHERE request_status = 'pending'"
            )
            counts["pending_reports"] += cursor.fetchone()["total"]
            cursor.execute(
                "SELECT COUNT(*) AS total FROM return_refund_requests WHERE request_status = 'approved'"
            )
            counts["resolved_reports"] += cursor.fetchone()["total"]
            cursor.execute(
                "SELECT COUNT(*) AS total FROM return_refund_requests WHERE request_status = 'disapproved'"
            )
            counts["escalated_reports"] += cursor.fetchone()["total"]

    if table_exists(cursor, "reports"):
        cursor.execute("SELECT COUNT(*) AS total FROM reports")
        counts["total_reports"] += cursor.fetchone()["total"]
        report_columns = get_table_columns(cursor, "reports")
        status_column = first_existing(report_columns, "status", "report_status")
        if status_column:
            cursor.execute(f"SELECT COUNT(*) AS total FROM reports WHERE {status_column} = 'pending'")
            counts["pending_reports"] += cursor.fetchone()["total"]
            cursor.execute(f"SELECT COUNT(*) AS total FROM reports WHERE {status_column} = 'read'")
            counts["resolved_reports"] += cursor.fetchone()["total"]

    if table_exists(cursor, "messages"):
        cursor.execute("SELECT COUNT(*) AS total FROM messages")
        counts["messages"] = cursor.fetchone()["total"]

    return counts


def fetch_admin_user(cursor):
    user_id = session.get("user_id")
    if user_id and table_exists(cursor, "users"):
        cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        user = cursor.fetchone()
        if user:
            return user
    cursor.execute("SELECT * FROM users WHERE account_type = 'admin' ORDER BY id ASC LIMIT 1")
    return cursor.fetchone() or {}


def fetch_customer_rows(cursor):
    if not table_exists(cursor, "users"):
        return []

    user_columns = get_table_columns(cursor, "users")
    status_expr = "u.account_status" if "account_status" in user_columns else "'active'"
    created_expr = "u.created_at" if "created_at" in user_columns else "u.id"
    address_expr = "u.address" if "address" in user_columns else "''"
    phone_expr = "u.phone" if "phone" in user_columns else "''"

    if table_exists(cursor, "orders"):
        order_count_expr = "COALESCE(order_counts.order_count, 0)"
        order_join = """
        LEFT JOIN (
            SELECT user_id, COUNT(*) AS order_count
            FROM orders
            GROUP BY user_id
        ) order_counts ON order_counts.user_id = u.id
        """
    else:
        order_count_expr = "0"
        order_join = ""
    cursor.execute(
        f"""
        SELECT u.id, u.fullname, u.email, u.username, {phone_expr} AS phone,
               {address_expr} AS address, {created_expr} AS created_at,
               {status_expr} AS account_status, {order_count_expr} AS order_count
        FROM users u
        {order_join}
        WHERE u.account_type = 'customer'
        ORDER BY {created_expr} DESC, u.id DESC
        """
    )
    customers = cursor.fetchall()

    if table_exists(cursor, "orders"):
        fields = order_fields(cursor)
        for customer in customers:
            cursor.execute(
                f"""
                SELECT {fields["code_expr"]} AS order_code, {fields["status_expr"]} AS order_status,
                       {fields["total_expr"]} AS total_amount
                FROM orders o
                WHERE o.user_id = %s
                ORDER BY {fields["date_expr"]} DESC, o.id DESC
                LIMIT 3
                """,
                (customer["id"],),
            )
            recent_orders = cursor.fetchall()
            customer["recent_orders_text"] = "\n".join(
                f"#{order.get('order_code')} - {order.get('order_status') or 'No status'} - PHP {money(order.get('total_amount'))}"
                for order in recent_orders
            ) or "No recent orders."
    return customers


def fetch_driver_rows(cursor):
    if not table_exists(cursor, "users"):
        return []

    ensure_order_tracking_schema(cursor)
    user_columns = get_table_columns(cursor, "users")
    status_expr = "u.account_status" if "account_status" in user_columns else "'active'"
    phone_expr = "u.phone" if "phone" in user_columns else "''"
    address_expr = "u.address" if "address" in user_columns else "''"

    if table_exists(cursor, "orders"):
        fields = order_fields(cursor)
        status_expr_order = f"LOWER(COALESCE({fields['status_expr']}, ''))"
        completed_values = "('delivered', 'completed')"
        active_values = "('preparing', 'packed', 'out for delivery', 'to_pay', 'to_ship', 'to_receive')"
        assignment_join = f"""
        LEFT JOIN (
            SELECT oda.driver_id,
                   COUNT(*) AS total_assigned,
                   SUM(CASE WHEN oda.is_active = 1 AND oda.assignment_type = 'delivery' AND {status_expr_order} IN {active_values} THEN 1 ELSE 0 END) AS active_deliveries,
                   SUM(CASE WHEN oda.assignment_type = 'delivery' AND {status_expr_order} IN {completed_values} THEN 1 ELSE 0 END) AS completed_deliveries,
                   SUM(CASE WHEN oda.is_active = 1 AND oda.assignment_type = 'return_pickup' THEN 1 ELSE 0 END) AS active_return_pickups
            FROM order_driver_assignments oda
            LEFT JOIN orders o ON o.id = oda.order_id
            GROUP BY oda.driver_id
        ) stats ON stats.driver_id = u.id
        """
    else:
        assignment_join = """
        LEFT JOIN (
            SELECT driver_id, COUNT(*) AS total_assigned,
                   0 AS active_deliveries, 0 AS completed_deliveries,
                   SUM(CASE WHEN is_active = 1 AND assignment_type = 'return_pickup' THEN 1 ELSE 0 END) AS active_return_pickups
            FROM order_driver_assignments
            GROUP BY driver_id
        ) stats ON stats.driver_id = u.id
        """

    cursor.execute(
        f"""
        SELECT u.id, u.fullname, u.email, u.username, {phone_expr} AS phone,
               {address_expr} AS address, {status_expr} AS account_status,
               COALESCE(stats.total_assigned, 0) AS total_assigned,
               COALESCE(stats.active_deliveries, 0) AS active_deliveries,
               COALESCE(stats.completed_deliveries, 0) AS completed_deliveries,
               COALESCE(stats.active_return_pickups, 0) AS active_return_pickups,
               gps.last_live_update,
               gps.live_active
        FROM users u
        {assignment_join}
        LEFT JOIN (
            SELECT driver_id, MAX(updated_at) AS last_live_update,
                   MAX(CASE WHEN is_active = 1 THEN 1 ELSE 0 END) AS live_active
            FROM order_live_locations
            GROUP BY driver_id
        ) gps ON gps.driver_id = u.id
        WHERE u.account_type = 'driver'
        ORDER BY u.id DESC
        """
    )
    return cursor.fetchall()


def fetch_driver_detail_data(cursor, driver_id):
    drivers = fetch_driver_rows(cursor)
    driver = next((row for row in drivers if row["id"] == driver_id), None)
    if not driver:
        return None, []

    fields = order_fields(cursor)
    cursor.execute(
        f"""
        SELECT oda.assignment_type, oda.assigned_at, oda.is_active,
               o.id AS order_id, {fields["code_expr"]} AS order_code,
               {fields["status_expr"]} AS order_status,
               u.fullname AS customer_name,
               loc.is_active AS live_active,
               loc.updated_at AS last_gps_update
        FROM order_driver_assignments oda
        JOIN orders o ON o.id = oda.order_id
        LEFT JOIN users u ON u.id = o.user_id
        LEFT JOIN order_live_locations loc ON loc.order_id = o.id AND loc.driver_id = oda.driver_id
        WHERE oda.driver_id = %s
        ORDER BY oda.assigned_at DESC, oda.id DESC
        """,
        (driver_id,),
    )
    return driver, cursor.fetchall()


def normalize_return_refund_reports(cursor):
    if not table_exists(cursor, "return_refund_requests"):
        return []

    order_columns = get_table_columns(cursor, "orders")
    order_code_expr = "o.order_code" if "order_code" in order_columns else "o.id"
    request_columns = get_table_columns(cursor, "return_refund_requests")
    owner_response_expr = (
        "rr.owner_response" if "owner_response" in request_columns else "''"
    )
    admin_response_expr = (
        "rr.admin_response" if "admin_response" in request_columns else "''"
    )
    proof_expr = sql_expr(request_columns, "rr", "proof_photo", "proof_image", "photo_proof", default="''")
    reason_expr = sql_expr(request_columns, "rr", "reason", "statement", "description", default="''")
    status_expr = sql_expr(request_columns, "rr", "request_status", "status", default="'pending'")
    created_expr = sql_expr(request_columns, "rr", "created_at", "requested_at", default="rr.id")
    reviewed_expr = sql_expr(request_columns, "rr", "reviewed_at", "updated_at", default="''")

    user_join = "LEFT JOIN users u ON u.id = rr.user_id" if table_exists(cursor, "users") and "user_id" in request_columns else ""
    order_join = "LEFT JOIN orders o ON o.id = rr.order_id" if table_exists(cursor, "orders") and "order_id" in request_columns else ""
    customer_expr = "u.fullname" if user_join else "'Customer'"
    order_id_expr = "rr.order_id" if "order_id" in request_columns else "''"
    order_code_select = f"{order_code_expr} AS order_code" if order_join else f"{order_id_expr} AS order_code"

    cursor.execute(
        f"""
        SELECT rr.id, {order_id_expr} AS order_id, {customer_expr} AS customer_name,
               {order_code_select}, {reason_expr} AS reason,
               {proof_expr} AS proof_photo, {status_expr} AS status,
               {owner_response_expr} AS owner_response,
               {admin_response_expr} AS admin_response,
               {created_expr} AS created_at, {reviewed_expr} AS reviewed_at
        FROM return_refund_requests rr
        {user_join}
        {order_join}
        ORDER BY {created_expr} DESC, rr.id DESC
        """
    )
    rows = cursor.fetchall()
    for row in rows:
        row.update(
            {
                "report_id": f"RR-{row['id']}",
                "reporter_name": row.get("customer_name") or "Customer",
                "reporter_type": "Customer",
                "issue_type": "Return / Refund",
                "reference": row.get("order_code") or row.get("order_id") or "N/A",
                "source": "return_refund_requests",
            }
        )
    return rows


def normalize_general_reports(cursor):
    if not table_exists(cursor, "reports"):
        return []

    columns = get_table_columns(cursor, "reports")
    select_parts = ["r.id"]
    mappings = {
        "reporter_type": ("reporter_type", "user_type", "account_type"),
        "issue_type": ("issue_type", "category", "type", "subject"),
        "reference": ("order_code", "order_id", "reference"),
        "reason": ("description", "reason", "message", "details"),
        "proof_photo": ("proof_photo", "photo", "image_url", "attachment"),
        "status": ("status", "report_status"),
        "owner_response": ("owner_response",),
        "admin_response": ("admin_response", "response"),
        "created_at": ("created_at", "reported_at"),
        "reviewed_at": ("reviewed_at", "updated_at"),
    }
    for alias, names in mappings.items():
        expression = sql_expr(columns, "r", *names, default="''")
        select_parts.append(f"{expression} AS {alias}")

    reporter_id = first_existing(columns, "user_id", "reporter_id", "customer_id")
    join = ""
    if reporter_id and table_exists(cursor, "users"):
        select_parts.append("u.fullname AS reporter_name")
        join = f"LEFT JOIN users u ON u.id = r.{reporter_id}"
    else:
        select_parts.append("'Reporter' AS reporter_name")

    created_order = first_existing(columns, "created_at", "reported_at", "id") or "id"
    cursor.execute(
        f"""
        SELECT {', '.join(select_parts)}
        FROM reports r
        {join}
        ORDER BY r.{created_order} DESC, r.id DESC
        """
    )
    rows = cursor.fetchall()
    for row in rows:
        row.update(
            {
                "report_id": f"RPT-{row['id']}",
                "reporter_name": row.get("reporter_name") or "Reporter",
                "issue_type": row.get("issue_type") or "Platform Concern",
                "reference": row.get("reference") or "N/A",
                "source": "reports",
            }
        )
    return rows


@admin.route("/admin")
@admin.route("/admin.html")
@admin_required
def admin_dashboard():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_admin_schema(cursor)
    conn.commit()
    counts = admin_counts(cursor)

    recent_customers = []
    if table_exists(cursor, "users"):
        user_columns = get_table_columns(cursor, "users")
        created_expr = "created_at" if "created_at" in user_columns else "id"
        cursor.execute(
            f"""
            SELECT fullname, email, {created_expr} AS created_at
            FROM users
            WHERE account_type = 'customer'
            ORDER BY {created_expr} DESC, id DESC
            LIMIT 5
            """
        )
        recent_customers = cursor.fetchall()
    recent_orders = fetch_admin_orders(cursor, limit=5)

    admin_logs = []
    if table_exists(cursor, "admin_logs"):
        cursor.execute(
            "SELECT action, details, created_at FROM admin_logs ORDER BY created_at DESC, id DESC LIMIT 5"
        )
        admin_logs = cursor.fetchall()
    cursor.execute("""
        SELECT body, photo_url, created_at
        FROM announcements
        ORDER BY created_at DESC, id DESC
        LIMIT 5
    """)
    announcements = cursor.fetchall()

    cursor.close()
    conn.close()
    return render_template(
        "admin.html",
        counts=counts,
        recent_customers=recent_customers,
        recent_orders=recent_orders,
        admin_logs=admin_logs,
        announcements=announcements,
    )


@admin.route("/admin/announcements", methods=["POST"])
@admin_required
def post_announcement():
    body = (request.form.get("announcement") or "").strip()
    photo_url = save_announcement_photo(request.files.get("announcement_photo"))
    if not body and not photo_url:
        return redirect("/admin")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_admin_schema(cursor)
    cursor.execute(
        "INSERT INTO announcements (admin_id, body, photo_url) VALUES (%s, %s, %s)",
        (session.get("user_id"), body, photo_url),
    )
    announcement_id = cursor.lastrowid
    log_admin_action(cursor, "announcement posted", "Admin posted a new announcement.")
    conn.commit()
    notify_admin_announcement(
        "Website Announcement",
        body or "A new Green Nursery announcement has been posted. Please check your messages for details.",
        announcement_id=announcement_id,
    )

    cursor.close()
    conn.close()
    return redirect("/admin")


@admin.route("/admin/test-email", methods=["POST"])
@admin_required
def test_email():
    test_email_address = (request.form.get("test_email") or current_app.config.get("SMTP_FROM_EMAIL") or "").strip()
    if not test_email_address:
        flash("Please enter an email address for the SMTP test.", "error")
        return redirect("/admin/profile")

    sent = send_email(
        test_email_address,
        "Green Nursery SMTP Test",
        "This is a test email from your Green Nursery website. SMTP is working.",
    )
    if sent:
        flash("Test email sent successfully.", "success")
    else:
        flash("Test email was not sent. Please check the Flask terminal and your SMTP settings.", "error")
    return redirect("/admin/profile")


@admin.route("/admin/test-notification-email", methods=["POST"])
@admin_required
def test_notification_email():
    test_email_address = (request.form.get("test_email") or current_app.config.get("SMTP_FROM_EMAIL") or "").strip()
    if not test_email_address:
        flash("Please enter an email address for the notification test.", "error")
        return redirect("/admin/profile")

    sent = send_email(
        test_email_address,
        "Green Nursery Notification Test",
        "This is a test notification email from Green Nursery. Your website email notification system is working.",
    )
    if sent:
        flash("Notification test email sent successfully.", "success")
    else:
        flash("Notification test email was not sent. Please check the Flask terminal and SMTP settings.", "error")
    return redirect("/admin/profile")


@admin.route("/announcements", methods=["GET"])
def public_announcements():
    if session.get("account_type") not in ("customer", "owner", "admin", "driver"):
        return {"success": False, "message": "Please log in first."}, 401

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_admin_schema(cursor)
    conn.commit()
    cursor.execute("""
        SELECT id, body, photo_url, created_at
        FROM announcements
        ORDER BY created_at ASC, id ASC
    """)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    return {
        "success": True,
        "announcements": [
            {
                "id": row["id"],
                "body": row.get("body") or "",
                "created_at": row["created_at"].strftime("%Y-%m-%d %H:%M") if row.get("created_at") else "",
                "attachments": (
                    [{"file_url": row["photo_url"], "original_name": "Announcement photo"}]
                    if row.get("photo_url") else []
                ),
                "is_mine": False,
            }
            for row in rows
        ],
    }


@admin.route("/admin/customers")
@admin.route("/admin_customer.html")
@admin_required
def admin_customers():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_admin_schema(cursor)
    conn.commit()
    counts = admin_counts(cursor)
    customers = fetch_customer_rows(cursor)
    cursor.close()
    conn.close()
    return render_template("admin_customer.html", counts=counts, customers=customers)


@admin.route("/admin/customer/<int:customer_id>/toggle-status", methods=["POST"])
@admin_required
def toggle_customer_status(customer_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_admin_schema(cursor)
    cursor.execute(
        "SELECT id, fullname, account_status FROM users WHERE id = %s AND account_type = 'customer'",
        (customer_id,),
    )
    customer = cursor.fetchone()
    if customer:
        next_status = "active" if customer.get("account_status") == "blocked" else "blocked"
        cursor.execute(
            "UPDATE users SET account_status = %s WHERE id = %s",
            (next_status, customer_id),
        )
        log_admin_action(
            cursor,
            f"customer {next_status}",
            f"{customer.get('fullname') or 'Customer'} was set to {next_status}.",
        )
    conn.commit()
    cursor.close()
    conn.close()
    return redirect("/admin/customers")


@admin.route("/admin/drivers")
@admin_required
def admin_drivers():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_admin_schema(cursor)
    conn.commit()
    counts = admin_counts(cursor)
    drivers = fetch_driver_rows(cursor)
    driver_counts = {
        "total": len(drivers),
        "active": sum(1 for driver in drivers if (driver.get("account_status") or "active") == "active"),
        "blocked": sum(1 for driver in drivers if (driver.get("account_status") or "active") == "blocked"),
        "active_deliveries": sum(int(driver.get("active_deliveries") or 0) for driver in drivers),
        "active_return_pickups": sum(int(driver.get("active_return_pickups") or 0) for driver in drivers),
    }
    cursor.close()
    conn.close()
    return render_template("admin_drivers.html", counts=counts, driver_counts=driver_counts, drivers=drivers)


@admin.route("/admin/drivers/add", methods=["POST"])
@admin_required
def admin_add_driver():
    fullname = (request.form.get("fullname") or "").strip()
    email = (request.form.get("email") or "").strip()
    username = (request.form.get("username") or "").strip()
    temporary_password = (request.form.get("temporary_password") or "").strip()
    phone = (request.form.get("phone") or "").strip()
    address = (request.form.get("address") or "").strip()

    if not fullname or not email or not username or not temporary_password:
        flash("Full name, email, username, and temporary password are required.", "error")
        return redirect("/admin/drivers")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        ensure_admin_schema(cursor)
        cursor.execute(
            "SELECT id FROM users WHERE email = %s OR username = %s LIMIT 1",
            (email, username),
        )
        if cursor.fetchone():
            flash("A user with that email or username already exists.", "error")
            return redirect("/admin/drivers")

        cursor.execute(
            """
            INSERT INTO users (fullname, email, username, password, phone, address, account_type, account_status)
            VALUES (%s, %s, %s, %s, %s, %s, 'driver', 'active')
            """,
            (fullname, email, username, generate_password_hash(temporary_password), phone, address),
        )
        driver_id = cursor.lastrowid
        log_admin_action(cursor, "admin created driver", f"Created driver account #{driver_id}.")
        conn.commit()

        send_email(
            email,
            "Green Nursery Driver Account Created",
            f"Hello {fullname},\n\n"
            "Your Green Nursery driver account has been created.\n\n"
            f"Username: {username}\n"
            f"Temporary Password: {temporary_password}\n\n"
            "Please log in and change your password immediately.\n\n"
            "Thank you,\nGreen Nursery",
        )
        flash("Driver account created successfully.", "success")
    except mysql.connector.IntegrityError:
        conn.rollback()
        flash("A user with that email or username already exists.", "error")
    except mysql.connector.Error as exc:
        conn.rollback()
        current_app.logger.exception("Admin add driver failed: %s", exc)
        flash("Unable to add driver right now.", "error")
    finally:
        cursor.close()
        conn.close()

    return redirect("/admin/drivers")


@admin.route("/admin/drivers/<int:driver_id>/edit", methods=["POST"])
@admin_required
def admin_edit_driver(driver_id):
    fullname = (request.form.get("fullname") or "").strip()
    email = (request.form.get("email") or "").strip()
    username = (request.form.get("username") or "").strip()
    phone = (request.form.get("phone") or "").strip()
    address = (request.form.get("address") or "").strip()
    account_status = (request.form.get("account_status") or "active").strip()
    new_password = (request.form.get("new_password") or "").strip()
    if account_status not in {"active", "blocked"}:
        account_status = "active"

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        ensure_admin_schema(cursor)
        cursor.execute("SELECT id FROM users WHERE id = %s AND account_type = 'driver'", (driver_id,))
        if not cursor.fetchone():
            flash("Driver account was not found.", "error")
            return redirect("/admin/drivers")

        cursor.execute(
            "SELECT id FROM users WHERE (email = %s OR username = %s) AND id != %s LIMIT 1",
            (email, username, driver_id),
        )
        if cursor.fetchone():
            flash("Another account already uses that email or username.", "error")
            return redirect("/admin/drivers")

        fields = [
            "fullname = %s",
            "email = %s",
            "username = %s",
            "phone = %s",
            "address = %s",
            "account_status = %s",
        ]
        values = [fullname, email, username, phone, address, account_status]
        if new_password:
            fields.append("password = %s")
            values.append(generate_password_hash(new_password))
        values.append(driver_id)

        cursor.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = %s AND account_type = 'driver'", tuple(values))
        log_admin_action(cursor, "admin edited driver", f"Updated driver account #{driver_id}.")
        if new_password:
            log_admin_action(cursor, "admin reset driver password", f"Reset password for driver account #{driver_id}.")
        conn.commit()
        flash("Driver account updated successfully.", "success")
    except mysql.connector.IntegrityError:
        conn.rollback()
        flash("Another account already uses that email or username.", "error")
    except mysql.connector.Error as exc:
        conn.rollback()
        current_app.logger.exception("Admin edit driver failed: %s", exc)
        flash("Unable to update driver right now.", "error")
    finally:
        cursor.close()
        conn.close()

    return redirect("/admin/drivers")


@admin.route("/admin/drivers/<int:driver_id>/toggle-status", methods=["POST"])
@admin_required
def admin_toggle_driver_status(driver_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        ensure_admin_schema(cursor)
        cursor.execute("SELECT id, email, fullname, account_status FROM users WHERE id = %s AND account_type = 'driver'", (driver_id,))
        driver = cursor.fetchone()
        if not driver:
            flash("Driver account was not found.", "error")
            return redirect("/admin/drivers")

        current_status = driver.get("account_status") or "active"
        new_status = "blocked" if current_status == "active" else "active"
        cursor.execute("UPDATE users SET account_status = %s WHERE id = %s", (new_status, driver_id))
        if new_status == "blocked":
            cursor.execute("UPDATE order_live_locations SET is_active = 0 WHERE driver_id = %s", (driver_id,))
        log_admin_action(cursor, f"admin {new_status} driver", f"Set driver account #{driver_id} to {new_status}.")
        conn.commit()

        if driver.get("email"):
            send_email(
                driver["email"],
                "Green Nursery Driver Account Update",
                f"Hello {driver.get('fullname') or 'Driver'},\n\n"
                f"Your Green Nursery driver account has been {new_status}.\n\n"
                "Thank you,\nGreen Nursery",
            )
        flash(f"Driver account {new_status}.", "success")
    except mysql.connector.Error as exc:
        conn.rollback()
        current_app.logger.exception("Admin toggle driver failed: %s", exc)
        flash("Unable to update driver status right now.", "error")
    finally:
        cursor.close()
        conn.close()

    return redirect("/admin/drivers")


@admin.route("/admin/drivers/<int:driver_id>")
@admin_required
def admin_driver_detail(driver_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_admin_schema(cursor)
    conn.commit()
    counts = admin_counts(cursor)
    driver, assignments = fetch_driver_detail_data(cursor, driver_id)
    cursor.close()
    conn.close()
    if not driver:
        flash("Driver account was not found.", "error")
        return redirect("/admin/drivers")
    return render_template("admin_drivers.html", counts=counts, driver_counts=None, drivers=[driver], selected_driver=driver, assignments=assignments)


@admin.route("/admin/orders")
@admin.route("/admin_orders.html")
@admin_required
def admin_orders():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_admin_schema(cursor)
    conn.commit()
    counts = admin_counts(cursor)
    orders = fetch_admin_orders(cursor)
    cursor.close()
    conn.close()
    return render_template("admin_orders.html", counts=counts, orders=orders)


@admin.route("/admin/reports")
@admin.route("/admin_reports.html")
@admin_required
def admin_reports():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_admin_schema(cursor)
    conn.commit()
    counts = admin_counts(cursor)
    reports = normalize_return_refund_reports(cursor) + normalize_general_reports(cursor)
    cursor.close()
    conn.close()
    return render_template("admin_reports.html", counts=counts, reports=reports)


@admin.route("/admin/reports/<int:report_id>/respond", methods=["POST"])
@admin_required
def respond_to_report(report_id):
    response = (request.form.get("admin_response") or "").strip()
    mark_read = request.form.get("mark_read") == "1"

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_admin_schema(cursor)
    if table_exists(cursor, "reports"):
        updates = []
        params = []
        if response:
            updates.append("admin_response = %s")
            params.append(response)
        if mark_read:
            updates.append("status = %s")
            params.append("read")
            updates.append("reviewed_at = NOW()")
        if updates:
            params.append(report_id)
            cursor.execute(f"UPDATE reports SET {', '.join(updates)} WHERE id = %s", tuple(params))
            log_admin_action(cursor, "report reviewed", f"Admin responded to report #{report_id}.")
            conn.commit()
    cursor.close()
    conn.close()
    return redirect("/admin/reports")


@admin.route("/admin/profile")
@admin.route("/admin_profile.html")
@admin_required
def admin_profile():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_admin_schema(cursor)
    conn.commit()
    counts = admin_counts(cursor)
    admin_user = fetch_admin_user(cursor)
    cursor.close()
    conn.close()
    return render_template("admin_profile.html", counts=counts, admin_user=admin_user)


@admin.route("/admin/profile/update", methods=["POST"])
@admin_required
def update_admin_profile():
    user_id = session.get("user_id") or request.form.get("user_id", type=int)
    if not user_id:
        return redirect("/admin/profile")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        ensure_admin_schema(cursor)
        user_columns = get_table_columns(cursor, "users")
        updates = ["fullname = %s", "email = %s"]
        params = [request.form.get("fullname"), request.form.get("email")]
        if "phone" in user_columns:
            updates.append("phone = %s")
            params.append(request.form.get("phone"))
        if "address" in user_columns:
            updates.append("address = %s")
            params.append(request.form.get("address"))
        params.append(user_id)
        cursor.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = %s", tuple(params))
        log_admin_action(cursor, "admin profile updated", "Admin updated profile information.")
        conn.commit()
    except mysql.connector.IntegrityError:
        conn.rollback()
    finally:
        cursor.close()
        conn.close()
    return redirect("/admin/profile")


@admin.route("/admin/profile/photo", methods=["POST"])
@admin_required
def update_admin_profile_photo():
    user_id = session.get("user_id") or request.form.get("user_id", type=int)
    if not user_id:
        return redirect("/admin/profile")

    profile_photo = save_admin_profile_photo(request.files.get("profile_photo"), user_id)
    if not profile_photo:
        return redirect("/admin/profile")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_admin_schema(cursor)
    cursor.execute("UPDATE users SET profile_photo = %s WHERE id = %s", (profile_photo, user_id))
    log_admin_action(cursor, "profile photo updated", "Admin changed profile photo.")
    conn.commit()
    cursor.close()
    conn.close()
    return redirect("/admin/profile")


@admin.route("/admin/profile/username", methods=["POST"])
@admin_required
def update_admin_username():
    user_id = session.get("user_id") or request.form.get("user_id", type=int)
    username = (request.form.get("username") or "").strip()
    if not user_id or not username:
        return redirect("/admin/profile")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        ensure_admin_schema(cursor)
        cursor.execute("UPDATE users SET username = %s WHERE id = %s", (username, user_id))
        log_admin_action(cursor, "username changed", f"Admin username changed to {username}.")
        conn.commit()
    except mysql.connector.IntegrityError:
        conn.rollback()
    finally:
        cursor.close()
        conn.close()
    return redirect("/admin/profile")


@admin.route("/admin/profile/password", methods=["POST"])
@admin_required
def update_admin_password():
    user_id = session.get("user_id") or request.form.get("user_id", type=int)
    current_password = request.form.get("current_password")
    password = request.form.get("password")
    confirm = request.form.get("confirm_password")
    if not user_id or not current_password or not password or password != confirm:
        return redirect("/admin/profile")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_admin_schema(cursor)
    cursor.execute("SELECT password FROM users WHERE id = %s", (user_id,))
    admin_user = cursor.fetchone()
    if admin_user and password_matches(admin_user.get("password"), current_password):
        cursor.execute("UPDATE users SET password = %s WHERE id = %s", (generate_password_hash(password), user_id))
        log_admin_action(cursor, "password changed", "Admin account password was changed.")
        conn.commit()
        log_activity(user_id, "admin", "password changed", "Admin changed password.")
        session.clear()
    else:
        conn.rollback()
    cursor.close()
    conn.close()
    return redirect("/admin/profile")


@admin.route("/admin/messages", methods=["GET"])
@admin_required
def admin_messages():
    admin_id = session.get("user_id")
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_message_schema(cursor)
    owner_user = get_owner_user(cursor)
    if not owner_user:
        cursor.close()
        conn.close()
        return {"success": False, "message": "No owner account found."}, 404

    thread_id = get_or_create_thread(cursor, admin_id, owner_user["id"])
    conn.commit()
    messages = fetch_thread_messages(cursor, thread_id)

    cursor.close()
    conn.close()

    return {
        "success": True,
        "threads": [{
            "thread_id": thread_id,
            "reporter_id": owner_user["id"],
            "reporter_name": owner_user.get("fullname") or "Owner",
            "reporter_type": "Owner",
            "photo": owner_user.get("profile_photo") or "/static/default-profile.jpg",
        }],
        "selected_thread_id": thread_id,
        "selected_reporter": {
            "name": owner_user.get("fullname") or "Owner",
            "photo": owner_user.get("profile_photo") or "/static/default-profile.jpg",
        },
        "messages": [serialize_message(message, admin_id) for message in messages],
    }


@admin.route("/admin/messages", methods=["POST"])
@admin_required
def send_admin_message():
    admin_id = session.get("user_id")
    body = (request.form.get("message") or "").strip()
    photos = save_message_photos(request.files.getlist("photos"))
    if not body and not photos:
        return {"success": False, "message": "Enter a message or choose a photo."}, 400

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_message_schema(cursor)
    owner_user = get_owner_user(cursor)
    if not owner_user:
        cursor.close()
        conn.close()
        return {"success": False, "message": "No owner account found."}, 404

    thread_id = get_or_create_thread(cursor, admin_id, owner_user["id"])
    insert_message(cursor, thread_id, admin_id, owner_user["id"], body, photos)
    conn.commit()
    messages = fetch_thread_messages(cursor, thread_id)
    cursor.close()
    conn.close()
    return {
        "success": True,
        "messages": [serialize_message(message, admin_id) for message in messages],
    }


def adminpal_money(value):
    try:
        return f"PHP {float(value or 0):,.2f}"
    except (TypeError, ValueError):
        return "PHP 0.00"


@admin.route("/admin-chatbot", methods=["POST"])
@admin_required
def admin_chatbot():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    lower_message = message.lower()

    if not message:
        return {
            "reply": "AdminPal is ready. You can ask for a system summary, customer counts, active orders, pending reports, low stock alerts, or admin security guidance."
        }

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        ensure_admin_schema(cursor)
        conn.commit()
        counts = admin_counts(cursor)

        if any(word in lower_message for word in ("hello", "hi", "hey")):
            reply = (
                "Hello! I'm AdminPal. I can help you monitor customers, orders, reports, low stock alerts, announcements, and admin account security."
            )

        elif "system summary" in lower_message or "summary" in lower_message or "dashboard" in lower_message or "overview" in lower_message:
            reply = (
                f"Here is the system summary: {counts['total_customers']} customers, "
                f"{counts['total_orders']} total orders, {counts['active_orders']} active orders, "
                f"{counts['completed_orders']} completed orders, {counts['pending_reports']} pending reports, "
                f"and {counts['low_stock']} low-stock plants. I suggest checking pending reports first, then active orders and low stock alerts."
            )

        elif "blocked" in lower_message:
            reply = (
                f"There are {counts['blocked_customers']} blocked customer account(s). "
                "Use blocking for suspicious activity, abusive behavior, or accounts that need temporary restriction. Unblock only after the concern is reviewed."
            )

        elif "new customer" in lower_message or "this month" in lower_message:
            reply = f"There are {counts['new_customers_month']} new customer registration(s) this month."

        elif "customer" in lower_message or "active customer" in lower_message:
            reply = (
                f"Customer Management currently has {counts['total_customers']} customers: "
                f"{counts['active_customers']} active and {counts['blocked_customers']} blocked. "
                "You can search, filter by status, view customer details, and block or unblock accounts from the Customers page."
            )

        elif "order status" in lower_message or "status flow" in lower_message:
            reply = (
                "The usual order status flow is Preparing -> Packed -> Out for Delivery -> Delivered. "
                "Use Cancelled when an order should not continue. The admin monitors order activity, while the owner handles fulfillment updates."
            )

        elif "recent order" in lower_message:
            orders = fetch_admin_orders(cursor, limit=5)
            if orders:
                order_lines = "; ".join(
                    f"#{order.get('order_code')} - {order.get('customer_name') or 'Customer'} - {order.get('order_status') or 'No status'}"
                    for order in orders
                )
                reply = f"Here are the latest orders: {order_lines}."
            else:
                reply = "There are no recent orders yet."

        elif "order" in lower_message or "active order" in lower_message or "completed order" in lower_message or "cancelled" in lower_message:
            reply = (
                f"Order monitoring shows {counts['total_orders']} total orders, "
                f"{counts['active_orders']} active, {counts['completed_orders']} completed, "
                f"and {counts['cancelled_orders']} cancelled. Admin monitors orders, while the owner remains responsible for fulfillment and delivery status updates."
            )

        elif "return" in lower_message or "refund" in lower_message:
            reply = (
                "Normal return/refund approval is handled by the owner because it depends on the product and delivery condition. "
                "Admin monitors return/refund concerns and handles escalated, account-related, suspicious, or system-level issues."
            )

        elif "report" in lower_message or "concern" in lower_message or "pending report" in lower_message:
            reply = (
                f"Reports overview: {counts['total_reports']} total report(s), with {counts['pending_reports']} pending review. "
                "Admin handles platform concerns such as account issues, suspicious activity, website bugs, payment display problems, and escalated matters."
            )

        elif "low stock" in lower_message or "stock alert" in lower_message:
            if table_exists(cursor, "plants"):
                plant_columns = get_table_columns(cursor, "plants")
                name_expr = "name" if "name" in plant_columns else "id"
                stock_expr = "stock" if "stock" in plant_columns else "0"
                cursor.execute(f"""
                    SELECT {name_expr} AS name, {stock_expr} AS stock
                    FROM plants
                    WHERE {stock_expr} <= 5
                    ORDER BY {stock_expr} ASC, id ASC
                    LIMIT 10
                """)
                plants = cursor.fetchall()
            else:
                plants = []
            if plants:
                plant_list = ", ".join(f"{plant.get('name') or 'Plant'} ({plant.get('stock') or 0} left)" for plant in plants)
                reply = f"Low stock plants: {plant_list}. The owner should restock or update inventory soon."
            else:
                reply = "I do not see low-stock plants right now."

        elif "recent customer" in lower_message:
            if table_exists(cursor, "users"):
                user_columns = get_table_columns(cursor, "users")
                created_expr = "created_at" if "created_at" in user_columns else "id"
                cursor.execute(f"""
                    SELECT fullname, email, {created_expr} AS created_at
                    FROM users
                    WHERE account_type = 'customer'
                    ORDER BY {created_expr} DESC, id DESC
                    LIMIT 5
                """)
                customers = cursor.fetchall()
            else:
                customers = []
            if customers:
                customer_lines = "; ".join(
                    f"{customer.get('fullname') or 'Customer'} ({customer.get('email') or 'no email'})"
                    for customer in customers
                )
                reply = f"Recent customer registrations: {customer_lines}."
            else:
                reply = "There are no recent customer registrations yet."

        elif "profile" in lower_message or "security" in lower_message or "password" in lower_message or "logout" in lower_message:
            reply = (
                "Admin Profile lets you update your information, username, password, and profile photo. "
                "For security, use a strong password, avoid sharing admin credentials, and log out after using a shared computer."
            )

        elif "what does admin do" in lower_message or "admin role" in lower_message or "role guide" in lower_message or "guide" in lower_message:
            reply = (
                "Admin role guide: Dashboard gives the system overview, Customers manages customer accounts, Orders monitors order activity, "
                "Reports reviews platform concerns and escalations, and Profile manages the admin account. The owner handles product inventory and normal fulfillment."
            )

        else:
            reply = (
                "I can help with system summary, customer management, blocked customers, orders, reports, low stock alerts, recent activity, and admin profile security. "
                "Try one of the quick replies if you want a fast start."
            )

    except mysql.connector.Error:
        reply = "AdminPal had trouble checking the database just now, but I can still help with general admin guidance."
    finally:
        cursor.close()
        conn.close()

    return {"reply": reply}
