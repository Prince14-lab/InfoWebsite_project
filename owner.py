from flask import Blueprint, render_template, redirect, url_for, session, current_app, request, jsonify
from datetime import datetime
from uuid import uuid4
from werkzeug.utils import secure_filename
import os
import random
import mysql.connector
from werkzeug.security import generate_password_hash
from message_utils import (
    ensure_message_schema,
    fetch_thread_messages,
    get_admin_user,
    get_or_create_thread,
    insert_message,
    save_message_photos,
    serialize_message,
)
from email_utils import send_email
from notification_utils import (
    check_and_send_low_stock_notifications,
    notify_customer_order_status,
    notify_customer_return_refund_update,
    notify_customers_new_plant,
)
from security_utils import log_activity, password_matches
from tracking_utils import ensure_order_tracking_schema, ensure_return_refund_pickup_schema

owner = Blueprint("owner", __name__)
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}


@owner.before_request
def require_owner_role():
    if session.get("account_type") != "owner":
        if request.method == "GET":
            return redirect("/login")
        return jsonify({"success": False, "message": "Owner access is required."}), 403

def get_db_connection():
    return mysql.connector.connect(**current_app.config["DB_CONFIG"])


def allowed_image_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def save_uploaded_image(file_storage, folder_name, prefix):
    if not file_storage or not file_storage.filename or not allowed_image_file(file_storage.filename):
        return None

    upload_folder = os.path.join(current_app.root_path, "static", folder_name)
    os.makedirs(upload_folder, exist_ok=True)

    filename = secure_filename(file_storage.filename)
    extension = filename.rsplit(".", 1)[1].lower()
    saved_name = f"{prefix}_{uuid4().hex}.{extension}"
    file_storage.save(os.path.join(upload_folder, saved_name))
    return f"/static/{folder_name}/{saved_name}"


def save_uploaded_images(file_storages, folder_name, prefix):
    paths = []
    for index, file_storage in enumerate(file_storages or [], start=1):
        saved_path = save_uploaded_image(file_storage, folder_name, f"{prefix}_{index}")
        if saved_path:
            paths.append(saved_path)
    return paths


def parse_sample_paths(value):
    raw_value = value or ""
    paths = []
    for piece in raw_value.replace(",", "\n").splitlines():
        path = piece.strip()
        if path and path not in paths:
            paths.append(path)
    return paths


def serialize_sample_paths(paths):
    cleaned = []
    for path in paths:
        if path and path not in cleaned:
            cleaned.append(path)
    return "\n".join(cleaned) if cleaned else None



def order_status_label(status):
    labels = {
        "to_pay": "To Pay",
        "to_ship": "To Ship",
        "to_receive": "To Receive",
        "completed": "Completed",
        "return_refund": "Return / Refund",
        "cancelled": "Cancelled",
    }
    return labels.get(status, status.replace("_", " ").title())


def payment_method_label(payment_method):
    labels = {
        "cash_on_delivery": "Cash on Delivery",
        "gcash": "GCash",
        "bank": "Bank",
    }
    return labels.get(payment_method, (payment_method or "Pending").replace("_", " ").title())


def get_table_columns(cursor, table_name):
    cursor.execute(f"SHOW COLUMNS FROM {table_name}")
    return {column["Field"] for column in cursor.fetchall()}


def table_exists(cursor, table_name):
    cursor.execute("SHOW TABLES LIKE %s", (table_name,))
    return cursor.fetchone() is not None


def ensure_owner_schema(cursor):
    user_columns = get_table_columns(cursor, "users")
    if "profile_photo" not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN profile_photo VARCHAR(255) NULL AFTER address")
        user_columns.add("profile_photo")
    if "shop_name" not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN shop_name VARCHAR(150) NULL AFTER profile_photo")
        user_columns.add("shop_name")
    if "business_type" not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN business_type VARCHAR(100) NULL AFTER shop_name")
        user_columns.add("business_type")
    if "shop_contact" not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN shop_contact VARCHAR(30) NULL AFTER business_type")
        user_columns.add("shop_contact")
    if "shop_email" not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN shop_email VARCHAR(150) NULL AFTER shop_contact")
        user_columns.add("shop_email")
    if "shop_description" not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN shop_description TEXT NULL AFTER shop_email")

    order_columns = get_table_columns(cursor, "orders")
    if "sold_recorded" not in order_columns:
        cursor.execute("ALTER TABLE orders ADD COLUMN sold_recorded TINYINT(1) NOT NULL DEFAULT 0")

    plant_columns = get_table_columns(cursor, "plants")
    if "description" not in plant_columns:
        cursor.execute("ALTER TABLE plants ADD COLUMN description TEXT NULL AFTER category")
        plant_columns.add("description")
    if "sample_photo" not in plant_columns:
        cursor.execute("ALTER TABLE plants ADD COLUMN sample_photo VARCHAR(255) NULL AFTER image_url")
    if "sample_photos" not in plant_columns:
        cursor.execute("ALTER TABLE plants ADD COLUMN sample_photos TEXT NULL AFTER sample_photo")

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
    ensure_order_tracking_schema(cursor)
    ensure_return_refund_pickup_schema(cursor)


def get_table_column_types(cursor, table_name):
    cursor.execute(f"SHOW COLUMNS FROM {table_name}")
    return {column["Field"]: column["Type"] for column in cursor.fetchall()}


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


def status_key(status):
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


def get_order_items_table(cursor):
    cursor.execute("SHOW TABLES LIKE 'order_items'")
    if cursor.fetchone():
        return "order_items"

    cursor.execute("SHOW TABLES LIKE 'order_item'")
    if cursor.fetchone():
        return "order_item"

    return "order_items"


def normalize_order(order):
    order["order_code"] = order.get("order_code") or str(order["id"])
    raw_status = order.get("status") or order.get("order_status") or "to_pay"
    order["status"] = status_key(raw_status)
    order["order_status"] = order.get("order_status") or raw_status
    order["payment_method"] = order.get("payment_method") or "pending"
    order["payment_status"] = order.get("payment_status") or "pending"
    order["subtotal"] = order.get("subtotal") or 0
    order["delivery_fee"] = order.get("delivery_fee") or 0
    order["total"] = order.get("total") or order.get("total_amount") or order["subtotal"]
    order["delivery_address"] = order.get("delivery_address") or ""
    order["contact_number"] = order.get("contact_number") or ""
    order["order_at"] = order.get("ordered_at") or order.get("order_at") or order.get("created_at") or ""
    order["created_at"] = order.get("created_at") or order["order_at"]
    order["display_date"] = order["order_at"]
    return order


def peso_text(value):
    return f"PHP {float(value or 0):,.2f}"


def fetch_order_email_context(cursor, order_id):
    order_columns = get_table_columns(cursor, "orders")
    total_expr = "o.total_amount" if "total_amount" in order_columns else ("o.total" if "total" in order_columns else "o.subtotal")
    code_expr = "o.order_code" if "order_code" in order_columns else "o.id"
    status_expr = "o.order_status" if "order_status" in order_columns else ("o.status" if "status" in order_columns else "NULL")
    cursor.execute(f"""
        SELECT o.id, {code_expr} AS order_code, {total_expr} AS total_amount,
               {status_expr} AS current_status,
               u.fullname, u.email
        FROM orders o
        JOIN users u ON u.id = o.user_id
        WHERE o.id = %s
        LIMIT 1
    """, (order_id,))
    return cursor.fetchone()


def owner_nursery_location(cursor):
    user_columns = get_table_columns(cursor, "users")
    shop_name_expr = "shop_name" if "shop_name" in user_columns else "fullname"
    cursor.execute(f"""
        SELECT {shop_name_expr} AS shop_name, address
        FROM users
        WHERE account_type = 'owner'
        ORDER BY id ASC
        LIMIT 1
    """)
    owner_user = cursor.fetchone() or {}
    return owner_user.get("address") or owner_user.get("shop_name") or "Green Nursery Shop"


def send_order_status_email(order_info, status):
    if not order_info or not order_info.get("email"):
        return False

    status_label = {
        "to_ship": "Packed",
        "to_receive": "Out for Delivery",
        "completed": "Delivered",
        "cancelled": "Cancelled",
    }.get(status, order_status_label(status))
    subject = {
        "Packed": "Your Green Nursery Order is Packed",
        "Out for Delivery": "Your Green Nursery Order is Out for Delivery",
        "Delivered": "Your Green Nursery Order has been Delivered",
        "Cancelled": "Your Green Nursery Order was Cancelled",
    }.get(status_label, "Your Green Nursery Order Status Was Updated")
    explanation = {
        "Packed": "Your order has been packed and is being prepared for delivery.",
        "Out for Delivery": "Your order is now on the way to your delivery address.",
        "Delivered": "Your order has been marked as delivered. Thank you for shopping with Green Nursery.",
        "Cancelled": "Your order has been cancelled. Please check My Purchases for details.",
    }.get(status_label, "Your order status has been updated.")

    return send_email(
        order_info["email"],
        subject,
        f"Hello {order_info.get('fullname') or 'Customer'},\n\n"
        f"Order #{order_info.get('order_code') or order_info['id']} is now {status_label}.\n\n"
        f"{explanation}\n\n"
        "Please check your My Purchases page for more details.\n\n"
        "Thank you,\n"
        "Green Nursery",
    )


def send_return_request_email(request_info, decision, owner_response):
    if not request_info or not request_info.get("email"):
        return False

    decision_label = "Approved" if decision == "approved" else "Disapproved"
    subject = "Return/Refund Request Approved" if decision == "approved" else "Return/Refund Request Update"
    response_text = owner_response or "No additional owner response was provided."
    return send_email(
        request_info["email"],
        subject,
        f"Hello {request_info.get('fullname') or 'Customer'},\n\n"
        f"Your return/refund request for order #{request_info.get('order_code') or request_info['id']} was {decision_label}.\n\n"
        f"Owner Response:\n{response_text}\n\n"
        "Please check your My Purchases page for more details.\n\n"
        "Thank you,\n"
        "Green Nursery",
    )


def owner_sales_case_sql(order_columns, sales_column, status_column):
    payment_method_expr = "LOWER(COALESCE(payment_method, ''))" if "payment_method" in order_columns else "''"
    payment_status_expr = "LOWER(COALESCE(payment_status, ''))" if "payment_status" in order_columns else "''"
    status_expr = f"LOWER(COALESCE({status_column}, ''))" if status_column else "''"
    amount_expr = f"COALESCE({sales_column}, 0)"
    online_methods = "('paymongo gcash', 'paymongo card', 'gcash', 'bank transfer', 'bank')"
    cod_methods = "('cash on delivery', 'cash_on_delivery')"
    completed_statuses = "('delivered', 'completed')"
    cancelled_statuses = "('cancelled')"

    return f"""
        CASE
            WHEN {payment_method_expr} IN {online_methods}
                 AND {payment_status_expr} = 'paid'
            THEN
                CASE
                    WHEN {status_expr} IN {cancelled_statuses} THEN {amount_expr} * 0.5
                    ELSE {amount_expr}
                END
            WHEN ({payment_method_expr} IN {cod_methods} OR {payment_method_expr} = '')
                 AND {status_expr} IN {completed_statuses}
            THEN {amount_expr}
            ELSE 0
        END
    """


def owner_sales_condition_sql(order_columns, status_column):
    payment_method_expr = "LOWER(COALESCE(o.payment_method, ''))" if "payment_method" in order_columns else "''"
    payment_status_expr = "LOWER(COALESCE(o.payment_status, ''))" if "payment_status" in order_columns else "''"
    status_expr = f"LOWER(COALESCE(o.{status_column}, ''))" if status_column else "''"
    online_methods = "('paymongo gcash', 'paymongo card', 'gcash', 'bank transfer', 'bank')"
    cod_methods = "('cash on delivery', 'cash_on_delivery')"
    completed_statuses = "('delivered', 'completed')"
    return f"""
        (
            ({payment_method_expr} IN {online_methods} AND {payment_status_expr} = 'paid')
            OR
            (({payment_method_expr} IN {cod_methods} OR {payment_method_expr} = '') AND {status_expr} IN {completed_statuses})
        )
    """


def fetch_owner_orders(cursor, limit=None):
    ensure_owner_schema(cursor)
    order_columns = get_table_columns(cursor, "orders")
    order_sort = "order_at" if "order_at" in order_columns else (
        "created_at" if "created_at" in order_columns else "id"
    )
    query = """
        SELECT o.*, u.fullname AS customer_name,
               rr.id AS return_request_id,
               rr.reason AS return_reason,
               rr.proof_photo AS return_proof_photo,
               rr.request_status AS return_request_status,
               rr.owner_response AS return_owner_response,
               rr.pickup_status AS return_pickup_status,
               rr.pickup_driver_id AS return_pickup_driver_id,
               rr.pickup_assigned_at AS return_pickup_assigned_at,
               rr.item_received_at AS return_item_received_at,
               rr.refund_status AS return_refund_status,
               rr.refund_method AS return_refund_method,
               rr.refund_note AS return_refund_note,
               d.id AS assigned_driver_id,
               d.fullname AS assigned_driver_name,
               d.email AS assigned_driver_email,
               pd.fullname AS pickup_driver_name,
               pd.email AS pickup_driver_email,
               loc.updated_at AS driver_last_gps_update,
               loc.is_active AS driver_live_active
        FROM orders o
        JOIN users u ON o.user_id = u.id
        LEFT JOIN return_refund_requests rr ON rr.order_id = o.id
        LEFT JOIN order_driver_assignments oda ON oda.order_id = o.id AND oda.is_active = 1 AND oda.assignment_type = 'delivery'
        LEFT JOIN users d ON d.id = oda.driver_id
        LEFT JOIN users pd ON pd.id = rr.pickup_driver_id
        LEFT JOIN order_live_locations loc ON loc.order_id = o.id AND loc.driver_id = oda.driver_id
    """
    query += f" ORDER BY o.{order_sort} DESC"
    params = ()
    if limit:
        query += " LIMIT %s"
        params = (limit,)

    cursor.execute(query, params)
    orders = cursor.fetchall()

    if not orders:
        return []

    order_ids = [order["id"] for order in orders]
    placeholders = ", ".join(["%s"] * len(order_ids))
    order_items_table = get_order_items_table(cursor)
    item_columns = get_table_columns(cursor, order_items_table)
    quantity_expr = "oi.quantity" if "quantity" in item_columns else "1 AS quantity"
    size_expr = "oi.size" if "size" in item_columns else "'Small' AS size"
    price_expr = "oi.unit_price" if "unit_price" in item_columns else (
        "oi.price AS unit_price" if "price" in item_columns else "p.price AS unit_price"
    )
    cursor.execute(f"""
        SELECT oi.order_id, {quantity_expr}, {size_expr}, {price_expr},
               p.name AS plant_name, p.image_url
        FROM {order_items_table} oi
        JOIN plants p ON oi.plant_id = p.id
        WHERE oi.order_id IN ({placeholders})
        ORDER BY oi.id ASC
    """, tuple(order_ids))
    items = cursor.fetchall()

    items_by_order = {}
    for item in items:
        items_by_order.setdefault(item["order_id"], []).append(item)

    for order in orders:
        normalize_order(order)
        items_for_order = items_by_order.get(order["id"], [])
        order["items"] = items_for_order
        order["plants"] = ", ".join(
            f"{item['plant_name']} x{item['quantity']}" for item in items_for_order
        ) or "No items"
        order["status_label"] = order_status_label(order["status"])
        order["payment_method_label"] = payment_method_label(order["payment_method"])
        order["return_request_id"] = order.get("return_request_id")
        order["return_reason"] = order.get("return_reason")
        order["return_proof_photo"] = order.get("return_proof_photo")
        order["return_request_status"] = order.get("return_request_status")
        order["return_owner_response"] = order.get("return_owner_response")
        order["return_pickup_status"] = order.get("return_pickup_status")
        order["return_pickup_driver_id"] = order.get("return_pickup_driver_id")
        order["return_pickup_driver_name"] = order.get("pickup_driver_name")
        order["return_refund_status"] = order.get("return_refund_status")
        order["driver_live_active"] = order.get("driver_live_active")
        order["driver_last_gps_update"] = order.get("driver_last_gps_update")
        if order["return_request_id"]:
            order["status"] = "return_refund"
            order["status_label"] = order_status_label("return_refund")

    return orders


def record_sold_for_order(cursor, order_id):
    order_items_table = get_order_items_table(cursor)
    cursor.execute("SELECT sold_recorded FROM orders WHERE id = %s", (order_id,))
    order = cursor.fetchone()
    if not order or order.get("sold_recorded"):
        return

    cursor.execute(f"""
        SELECT plant_id, quantity
        FROM {order_items_table}
        WHERE order_id = %s
    """, (order_id,))
    items = cursor.fetchall()

    for item in items:
        cursor.execute("""
            UPDATE plants
            SET sold = COALESCE(sold, 0) + %s
            WHERE id = %s
        """, (item["quantity"], item["plant_id"]))

    cursor.execute("UPDATE orders SET sold_recorded = 1 WHERE id = %s", (order_id,))


@owner.route("/Owner")
def owner_dashboard():
    if not session.get("user_id"):
        return redirect("/login")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_owner_schema(cursor)
    ensure_order_tracking_schema(cursor)
    conn.commit()
    order_columns = get_table_columns(cursor, "orders")

    sales_column = "total_amount" if "total_amount" in order_columns else (
        "total" if "total" in order_columns else "subtotal"
    )
    date_column = "ordered_at" if "ordered_at" in order_columns else (
        "order_at" if "order_at" in order_columns else (
            "created_at" if "created_at" in order_columns else None
        )
    )
    status_column = "status" if "status" in order_columns else (
        "order_status" if "order_status" in order_columns else None
    )
    sales_case = owner_sales_case_sql(order_columns, sales_column, status_column)
    sales_condition = owner_sales_condition_sql(order_columns, status_column)
    cursor.execute(f"SELECT COALESCE(SUM({sales_case}), 0) AS total_sales FROM orders")
    total_sales = cursor.fetchone()["total_sales"]

    sales_overview = [
        {"key": "day", "label": "Today", "total": 0, "tooltip": "Today sales total: PHP 0", "height": 12},
        {"key": "week", "label": "This Week", "total": 0, "tooltip": "This week sales total: PHP 0", "height": 12},
        {"key": "month", "label": "This Month", "total": 0, "tooltip": "This month sales total: PHP 0", "height": 12},
        {"key": "year", "label": "This Year", "total": 0, "tooltip": "This year sales total: PHP 0", "height": 12},
    ]
    if date_column:
        cursor.execute(f"""
            SELECT
                COALESCE(SUM(CASE WHEN DATE({date_column}) = CURDATE() THEN {sales_case} ELSE 0 END), 0) AS today_sales,
                COALESCE(SUM(CASE WHEN YEARWEEK({date_column}, 1) = YEARWEEK(CURDATE(), 1) THEN {sales_case} ELSE 0 END), 0) AS week_sales,
                COALESCE(SUM(CASE WHEN YEAR({date_column}) = YEAR(CURDATE()) AND MONTH({date_column}) = MONTH(CURDATE()) THEN {sales_case} ELSE 0 END), 0) AS month_sales,
                COALESCE(SUM(CASE WHEN YEAR({date_column}) = YEAR(CURDATE()) THEN {sales_case} ELSE 0 END), 0) AS year_sales
            FROM orders
        """)
        overview_row = cursor.fetchone()
        overview_values = [
            ("day", "Today", overview_row["today_sales"], "Today sales total"),
            ("week", "This Week", overview_row["week_sales"], "This week sales total"),
            ("month", "This Month", overview_row["month_sales"], "This month sales total"),
            ("year", "This Year", overview_row["year_sales"], "This year sales total"),
        ]
        max_sale = max(float(total or 0) for _, _, total, _ in overview_values) or 0
        sales_overview = []
        for key, label, total, tooltip_label in overview_values:
            numeric_total = float(total or 0)
            height = 12 if max_sale == 0 else 18 + ((numeric_total / max_sale) * 72)
            sales_overview.append({
                "key": key,
                "label": label,
                "total": total,
                "tooltip": f"{tooltip_label}: PHP {total or 0}",
                "height": height,
            })

    cursor.execute("SELECT COUNT(*) AS total_orders FROM orders")
    total_orders = cursor.fetchone()["total_orders"]

    if status_column:
        pending_values = [
            db_status_value(get_table_column_types(cursor, "orders").get(status_column), status)
            for status in ("to_pay", "to_ship", "to_receive")
        ]
        placeholders = ", ".join(["%s"] * len(pending_values))
        cursor.execute(f"SELECT COUNT(*) AS pending_orders FROM orders WHERE {status_column} IN ({placeholders})", tuple(pending_values))
        pending_orders = cursor.fetchone()["pending_orders"]
    else:
        pending_orders = total_orders

    if status_column:
        completed_db = db_status_value(get_table_column_types(cursor, "orders").get(status_column), "completed")
        cursor.execute(f"SELECT COUNT(*) AS completed_orders FROM orders WHERE {status_column} = %s", (completed_db,))
        completed_orders = cursor.fetchone()["completed_orders"]

        order_items_table = get_order_items_table(cursor)
        cursor.execute(f"""
            SELECT COALESCE(SUM(oi.quantity), 0) AS total_sold
            FROM orders o
            JOIN {order_items_table} oi ON oi.order_id = o.id
            WHERE {sales_condition}
        """)
        total_sold = cursor.fetchone()["total_sold"]

        cursor.execute(f"""
            SELECT p.*, COALESCE(SUM(oi.quantity), 0) AS sold
            FROM orders o
            JOIN {order_items_table} oi ON oi.order_id = o.id
            JOIN plants p ON p.id = oi.plant_id
            WHERE {sales_condition}
            GROUP BY p.id
            ORDER BY sold DESC, p.name ASC
            LIMIT 5
        """)
        best_sellers = cursor.fetchall()
    else:
        completed_orders = 0
        cursor.execute("SELECT COALESCE(SUM(sold), 0) AS total_sold FROM plants")
        total_sold = cursor.fetchone()["total_sold"]
        cursor.execute("SELECT * FROM plants ORDER BY sold DESC, name ASC LIMIT 5")
        best_sellers = cursor.fetchall()

    cursor.execute("SELECT COUNT(*) AS refund_requests FROM return_refund_requests WHERE request_status = 'pending'")
    refund_requests = cursor.fetchone()["refund_requests"]

    cursor.execute("SELECT COUNT(*) AS low_stock FROM plants WHERE stock <= 5")
    low_stock = cursor.fetchone()["low_stock"]

    cursor.execute("SELECT * FROM plants WHERE stock <= 5 ORDER BY stock ASC, name ASC LIMIT 5")
    low_stock_plants = cursor.fetchall()

    recent_orders = fetch_owner_orders(cursor, limit=6)
    completed_sales_orders = [order for order in fetch_owner_orders(cursor) if order["status"] == "completed"][:6]

    sales_chart_data = {
    "day": {"labels": [], "values": []},
    "week": {"labels": [], "values": []},
    "month": {"labels": [], "values": []},
    "year": {"labels": [], "values": []},
}

    if date_column:
        cursor.execute(f"""
            SELECT HOUR({date_column}) AS label, COALESCE(SUM({sales_case}), 0) AS total
            FROM orders
            WHERE DATE({date_column}) = CURDATE()
            GROUP BY HOUR({date_column})
            ORDER BY HOUR({date_column})
        """)
        rows = cursor.fetchall()
        sales_chart_data["day"] = {
            "labels": [f"{row['label']}:00" for row in rows],
            "values": [float(row["total"]) for row in rows]
        }

        cursor.execute(f"""
            SELECT DAYNAME({date_column}) AS label, COALESCE(SUM({sales_case}), 0) AS total
            FROM orders
            WHERE YEARWEEK({date_column}, 1) = YEARWEEK(CURDATE(), 1)
            GROUP BY DAYOFWEEK({date_column}), DAYNAME({date_column})
            ORDER BY DAYOFWEEK({date_column})
        """)
        rows = cursor.fetchall()
        sales_chart_data["week"] = {
            "labels": [row["label"] for row in rows],
            "values": [float(row["total"]) for row in rows]
        }

        cursor.execute(f"""
            SELECT DAY({date_column}) AS label, COALESCE(SUM({sales_case}), 0) AS total
            FROM orders
            WHERE YEAR({date_column}) = YEAR(CURDATE())
            AND MONTH({date_column}) = MONTH(CURDATE())
            GROUP BY DAY({date_column})
            ORDER BY DAY({date_column})
        """)
        rows = cursor.fetchall()
        sales_chart_data["month"] = {
            "labels": [f"Day {row['label']}" for row in rows],
            "values": [float(row["total"]) for row in rows]
        }

        cursor.execute(f"""
            SELECT MONTHNAME({date_column}) AS label, COALESCE(SUM({sales_case}), 0) AS total
            FROM orders
            WHERE YEAR({date_column}) = YEAR(CURDATE())
            GROUP BY MONTH({date_column}), MONTHNAME({date_column})
            ORDER BY MONTH({date_column})
        """)
        rows = cursor.fetchall()
        sales_chart_data["year"] = {
            "labels": [row["label"] for row in rows],
            "values": [float(row["total"]) for row in rows]
        }

        cursor.close()
        conn.close()

    return render_template(
        "owner.html",
        sales_chart_data=sales_chart_data,
        total_sales=total_sales,
        total_orders=total_orders,
        pending_orders=pending_orders,
        completed_orders=completed_orders,
        total_sold=total_sold,
        refund_requests=refund_requests,
        low_stock=low_stock,
        low_stock_plants=low_stock_plants,
        best_sellers=best_sellers,
        recent_orders=recent_orders,
        sales_overview=sales_overview,
        completed_sales_orders=completed_sales_orders,
    )


@owner.route("/Orders")
def owner_orders():
    if not session.get("user_id"):
        return redirect("/login")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_owner_schema(cursor)
    ensure_order_tracking_schema(cursor)
    conn.commit()
    orders = fetch_owner_orders(cursor)
    cursor.execute("""
        SELECT id, fullname, email
        FROM users
        WHERE account_type = 'driver'
          AND (account_status IS NULL OR account_status != 'blocked')
        ORDER BY fullname ASC
    """)
    drivers = cursor.fetchall()
    cursor.close()
    conn.close()

    return render_template("order.html", orders=orders, drivers=drivers)


@owner.route("/update-order-status/<int:order_id>", methods=["POST"])
def update_order_status(order_id):
    if not session.get("user_id"):
        return redirect("/login")

    allowed_statuses = {"to_pay", "to_ship", "to_receive", "completed", "return_refund", "cancelled"}
    status = request.form.get("status")
    if status not in allowed_statuses:
        return redirect("/Orders")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_owner_schema(cursor)
    ensure_order_tracking_schema(cursor)
    order_columns = get_table_columns(cursor, "orders")
    order_column_types = get_table_column_types(cursor, "orders")
    status_columns = [column for column in ("status", "order_status") if column in order_columns]
    order_info = fetch_order_email_context(cursor, order_id)
    old_status_key = status_key(order_info.get("current_status")) if order_info else None

    if status_columns:
        assignments = []
        values = []
        for column in status_columns:
            assignments.append(f"{column} = %s")
            values.append(db_status_value(order_column_types.get(column), status))
        cursor.execute(f"""
            UPDATE orders
            SET {", ".join(assignments)}
            WHERE id = %s
        """, tuple(values + [order_id]))
        if status == "completed":
            record_sold_for_order(cursor, order_id)
            order_items_table = get_order_items_table(cursor)
            cursor.execute(f"SELECT plant_id FROM {order_items_table} WHERE order_id = %s", (order_id,))
            for order_item in cursor.fetchall():
                check_and_send_low_stock_notifications(cursor, order_item.get("plant_id"))
        if status == "to_ship" and old_status_key != "to_ship":
            nursery_location = owner_nursery_location(cursor)
            cursor.execute("""
                SELECT id
                FROM order_tracking
                WHERE order_id = %s AND tracking_status = %s
                LIMIT 1
            """, (order_id, "Preparing"))
            if not cursor.fetchone():
                cursor.execute("""
                    INSERT INTO order_tracking (order_id, tracking_status, location, note)
                    VALUES (%s, %s, %s, %s)
                """, (
                    order_id,
                    "Preparing",
                    nursery_location,
                    "Your order is being prepared at the nursery.",
                ))
    conn.commit()
    if status in {"to_ship", "to_receive", "completed", "cancelled"} and old_status_key != status and order_info:
        status_label = {
            "to_ship": "Packed",
            "to_receive": "Out for Delivery",
            "completed": "Delivered",
            "cancelled": "Cancelled",
        }.get(status, order_status_label(status))
        notify_customer_order_status(
            order_info.get("email"),
            order_info.get("fullname"),
            order_info.get("order_code") or order_id,
            status_label,
            cursor=cursor,
            order_id=order_id,
        )
        conn.commit()
    cursor.close()
    conn.close()

    return redirect("/Orders")


@owner.route("/owner/order/<int:order_id>/tracking", methods=["POST"])
def update_order_tracking(order_id):
    if session.get("account_type") != "owner":
        return redirect("/login")

    tracking_status = (request.form.get("tracking_status") or "").strip()
    location = (request.form.get("location") or "").strip()
    note = (request.form.get("note") or "").strip()
    allowed_tracking_statuses = {"Packed", "In Transit", "At Delivery Hub", "Out for Delivery", "Delivered"}

    if tracking_status not in allowed_tracking_statuses or not location:
        return redirect("/Orders")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_owner_schema(cursor)
    ensure_order_tracking_schema(cursor)

    cursor.execute("""
        INSERT INTO order_tracking (order_id, tracking_status, location, note)
        VALUES (%s, %s, %s, %s)
    """, (order_id, tracking_status, location, note))

    if tracking_status in {"Packed", "Out for Delivery", "Delivered"}:
        status_map = {
            "Packed": "to_ship",
            "Out for Delivery": "to_receive",
            "Delivered": "completed",
        }
        next_status = status_map[tracking_status]
        order_columns = get_table_columns(cursor, "orders")
        order_column_types = get_table_column_types(cursor, "orders")
        status_columns = [column for column in ("status", "order_status") if column in order_columns]
        if status_columns:
            assignments = []
            values = []
            for column in status_columns:
                assignments.append(f"{column} = %s")
                values.append(db_status_value(order_column_types.get(column), next_status))
            cursor.execute(f"""
                UPDATE orders
                SET {", ".join(assignments)}
                WHERE id = %s
            """, tuple(values + [order_id]))
            if next_status == "completed":
                record_sold_for_order(cursor, order_id)

    order_columns = get_table_columns(cursor, "orders")
    code_expr = "o.order_code" if "order_code" in order_columns else "o.id"
    cursor.execute(f"""
        SELECT {code_expr} AS order_code, u.fullname, u.email
        FROM orders o
        JOIN users u ON u.id = o.user_id
        WHERE o.id = %s
        LIMIT 1
    """, (order_id,))
    customer_info = cursor.fetchone() or {}
    conn.commit()

    if customer_info.get("email"):
        subject = {
            "Out for Delivery": "Your Green Nursery Order is Out for Delivery",
            "Delivered": "Your Green Nursery Order has been Delivered",
        }.get(tracking_status, "Tracking Update for Your Green Nursery Order")
        send_email(
            customer_info["email"],
            subject,
            f"Hello {customer_info.get('fullname') or 'Customer'},\n\n"
            f"Your order #{customer_info.get('order_code') or order_id} has a new tracking update.\n\n"
            f"Status: {tracking_status}\n"
            f"Location: {location}\n"
            f"Note: {note or 'No additional note.'}\n\n"
            "Please check My Purchases > Track Order for more details.\n\n"
            "Thank you,\n"
            "Green Nursery",
        )

    cursor.close()
    conn.close()
    return redirect("/Orders")


@owner.route("/owner/order/<int:order_id>/assign-driver", methods=["POST"])
def assign_driver_to_order(order_id):
    if session.get("account_type") != "owner":
        return redirect("/login")

    driver_id = request.form.get("driver_id", type=int)
    if not driver_id:
        return redirect("/Orders")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_owner_schema(cursor)
    ensure_order_tracking_schema(cursor)
    order_columns = get_table_columns(cursor, "orders")
    status_expr = "order_status" if "order_status" in order_columns else ("status" if "status" in order_columns else None)
    code_expr = "order_code" if "order_code" in order_columns else "id"
    status_select_expr = status_expr or "''"
    cursor.execute(
        f"SELECT id, {code_expr} AS order_code, {status_select_expr} AS order_status FROM orders WHERE id = %s LIMIT 1",
        (order_id,),
    )
    order_row = cursor.fetchone()
    if not order_row:
        flash("Order was not found.", "error")
        cursor.close()
        conn.close()
        return redirect("/Orders")
    if status_key(order_row.get("order_status")) == "cancelled":
        flash("Cannot assign a driver to a cancelled order.", "error")
        cursor.close()
        conn.close()
        return redirect("/Orders")

    cursor.execute("""
        SELECT oda.id, u.fullname
        FROM order_driver_assignments oda
        JOIN users u ON u.id = oda.driver_id
        WHERE oda.order_id = %s AND oda.assignment_type = 'delivery' AND oda.is_active = 1
        LIMIT 1
    """, (order_id,))
    existing_assignment = cursor.fetchone()
    if existing_assignment:
        flash("This order already has an assigned driver. Cancel the current assignment before assigning another driver.", "error")
        cursor.close()
        conn.close()
        return redirect("/Orders")

    cursor.execute("""
        SELECT id, fullname, email
        FROM users
        WHERE id = %s AND account_type = 'driver'
          AND (account_status IS NULL OR account_status != 'blocked')
        LIMIT 1
    """, (driver_id,))
    driver_user = cursor.fetchone()
    if not driver_user:
        flash("Selected driver account was not found or is blocked.", "error")
        cursor.close()
        conn.close()
        return redirect("/Orders")

    cursor.execute("""
        INSERT INTO order_driver_assignments (order_id, driver_id, assignment_type, assigned_at, is_active)
        VALUES (%s, %s, 'delivery', NOW(), 1)
        ON DUPLICATE KEY UPDATE
            driver_id = VALUES(driver_id),
            assigned_at = NOW(),
            is_active = 1,
            cancelled_at = NULL,
            cancelled_by = NULL,
            cancel_reason = NULL
    """, (order_id, driver_id))

    conn.commit()

    if driver_user.get("email"):
        send_email(
            driver_user["email"],
            "New Delivery Assigned - Green Nursery",
            f"Hello {driver_user.get('fullname') or 'Driver'},\n\n"
            f"You have been assigned to deliver order #{order_row.get('order_code') or order_id}.\n\n"
            "Please open your Driver Dashboard to view the delivery details and share live location when delivering.\n\n"
            "Thank you,\n"
            "Green Nursery",
        )

    flash("Driver assigned successfully.", "success")
    cursor.close()
    conn.close()
    return redirect("/Orders")


@owner.route("/owner/order/<int:order_id>/cancel-driver-assignment", methods=["POST"])
def cancel_driver_assignment(order_id):
    if session.get("account_type") != "owner":
        return redirect("/login")

    assignment_type = (request.form.get("assignment_type") or "delivery").strip()
    if assignment_type not in {"delivery", "return_pickup"}:
        assignment_type = "delivery"
    cancel_reason = (request.form.get("cancel_reason") or "").strip()

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_owner_schema(cursor)
    cursor.execute("""
        SELECT id, driver_id
        FROM order_driver_assignments
        WHERE order_id = %s AND assignment_type = %s AND is_active = 1
        LIMIT 1
    """, (order_id, assignment_type))
    assignment = cursor.fetchone()
    if assignment:
        cursor.execute("""
            UPDATE order_driver_assignments
            SET is_active = 0,
                cancelled_at = NOW(),
                cancelled_by = %s,
                cancel_reason = %s
            WHERE id = %s
        """, (session.get("user_id"), cancel_reason, assignment["id"]))
        if assignment_type == "delivery":
            cursor.execute("UPDATE order_live_locations SET is_active = 0 WHERE order_id = %s", (order_id,))
        conn.commit()
        flash("Driver assignment cancelled.", "success")
    else:
        flash("No active driver assignment was found.", "error")
    cursor.close()
    conn.close()
    return redirect("/Orders")


@owner.route("/review-return-request/<int:request_id>", methods=["POST"])
def review_return_request(request_id):
    if not session.get("user_id"):
        return redirect("/login")

    decision = request.form.get("decision")
    owner_response = (request.form.get("owner_response") or "").strip()
    if decision not in {"approved", "disapproved"}:
        return redirect("/Orders")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_owner_schema(cursor)
    ensure_return_refund_pickup_schema(cursor)

    cursor.execute("SELECT order_id FROM return_refund_requests WHERE id = %s", (request_id,))
    request_row = cursor.fetchone()
    request_info = fetch_order_email_context(cursor, request_row["order_id"]) if request_row else None
    if request_row:
        cursor.execute("""
            UPDATE return_refund_requests
            SET request_status = %s,
                owner_response = %s,
                reviewed_at = %s,
                pickup_status = %s,
                refund_status = %s
            WHERE id = %s
        """, (
            decision,
            owner_response,
            datetime.now(),
            "Pending Pickup" if decision == "approved" else "Pickup Cancelled",
            "Approved" if decision == "approved" else "Rejected",
            request_id,
        ))

        order_columns = get_table_columns(cursor, "orders")
        order_column_types = get_table_column_types(cursor, "orders")
        status_columns = [column for column in ("status", "order_status") if column in order_columns]
        if status_columns:
            assignments = []
            values = []
            for column in status_columns:
                assignments.append(f"{column} = %s")
                values.append(db_status_value(order_column_types.get(column), "return_refund"))
            cursor.execute(f"""
                UPDATE orders
                SET {", ".join(assignments)}
                WHERE id = %s
            """, tuple(values + [request_row["order_id"]]))

        conn.commit()
        if request_info:
            notify_customer_return_refund_update(
                request_info.get("email"),
                request_info.get("fullname"),
                request_info.get("order_code") or request_info["id"],
                decision,
                owner_response,
                cursor=cursor,
                request_id=request_id,
            )
            conn.commit()

    cursor.close()
    conn.close()
    return redirect("/Orders")


@owner.route("/owner/return-refund/<int:request_id>/assign-pickup-driver", methods=["POST"])
def assign_pickup_driver(request_id):
    if session.get("account_type") != "owner":
        return redirect("/login")

    driver_id = request.form.get("driver_id", type=int)
    if not driver_id:
        flash("Please select a pickup driver.", "error")
        return redirect("/Orders")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_owner_schema(cursor)
    cursor.execute("""
        SELECT id, order_id, request_status, pickup_status
        FROM return_refund_requests
        WHERE id = %s
        LIMIT 1
    """, (request_id,))
    request_row = cursor.fetchone()
    if not request_row or request_row.get("request_status") != "approved":
        flash("Pickup driver can only be assigned after the request is approved.", "error")
        cursor.close()
        conn.close()
        return redirect("/Orders")

    if request_row.get("pickup_status") in {"Received by Owner", "Pickup Cancelled"}:
        flash("Pickup assignment is not available for this request status.", "error")
        cursor.close()
        conn.close()
        return redirect("/Orders")

    cursor.execute("""
        SELECT id, fullname, email
        FROM users
        WHERE id = %s AND account_type = 'driver'
          AND (account_status IS NULL OR account_status != 'blocked')
        LIMIT 1
    """, (driver_id,))
    driver_user = cursor.fetchone()
    if not driver_user:
        flash("Selected driver account was not found or is blocked.", "error")
        cursor.close()
        conn.close()
        return redirect("/Orders")

    cursor.execute("""
        SELECT id
        FROM order_driver_assignments
        WHERE order_id = %s AND assignment_type = 'return_pickup' AND is_active = 1
        LIMIT 1
    """, (request_row["order_id"],))
    if cursor.fetchone():
        flash("This return/refund request already has an assigned pickup driver. Cancel it before assigning another driver.", "error")
        cursor.close()
        conn.close()
        return redirect("/Orders")

    cursor.execute("""
        INSERT INTO order_driver_assignments (order_id, driver_id, assignment_type, assigned_at, is_active)
        VALUES (%s, %s, 'return_pickup', NOW(), 1)
        ON DUPLICATE KEY UPDATE
            driver_id = VALUES(driver_id),
            assigned_at = NOW(),
            is_active = 1,
            cancelled_at = NULL,
            cancelled_by = NULL,
            cancel_reason = NULL
    """, (request_row["order_id"], driver_id))
    cursor.execute("""
        UPDATE return_refund_requests
        SET pickup_driver_id = %s,
            pickup_status = 'Driver Assigned',
            pickup_assigned_at = NOW()
        WHERE id = %s
    """, (driver_id, request_id))
    conn.commit()

    if driver_user.get("email"):
        send_email(
            driver_user["email"],
            "New Return Pickup Assigned - Green Nursery",
            f"Hello {driver_user.get('fullname') or 'Driver'},\n\n"
            f"You have been assigned to pick up a return item for request #{request_id}.\n\n"
            "Please check your Driver Dashboard for details.\n\n"
            "Thank you,\nGreen Nursery",
        )
    flash("Pickup driver assigned successfully.", "success")
    cursor.close()
    conn.close()
    return redirect("/Orders")


@owner.route("/owner/return-refund/<int:request_id>/cancel-pickup-driver", methods=["POST"])
def cancel_pickup_driver(request_id):
    if session.get("account_type") != "owner":
        return redirect("/login")

    cancel_reason = (request.form.get("cancel_reason") or "").strip()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_owner_schema(cursor)
    cursor.execute("SELECT order_id, pickup_driver_id FROM return_refund_requests WHERE id = %s LIMIT 1", (request_id,))
    request_row = cursor.fetchone()
    if request_row:
        cursor.execute("""
            UPDATE order_driver_assignments
            SET is_active = 0,
                cancelled_at = NOW(),
                cancelled_by = %s,
                cancel_reason = %s
            WHERE order_id = %s AND assignment_type = 'return_pickup' AND is_active = 1
        """, (session.get("user_id"), cancel_reason, request_row["order_id"]))
        cursor.execute("""
            UPDATE return_refund_requests
            SET pickup_driver_id = NULL,
                pickup_status = 'Pending Pickup'
            WHERE id = %s
        """, (request_id,))
        if request_row.get("pickup_driver_id"):
            cursor.execute("""
                UPDATE order_live_locations
                SET is_active = 0
                WHERE order_id = %s AND driver_id = %s
            """, (request_row["order_id"], request_row["pickup_driver_id"]))
        conn.commit()
        flash("Pickup driver assignment cancelled.", "success")
    cursor.close()
    conn.close()
    return redirect("/Orders")


@owner.route("/owner/return-refund/<int:request_id>/confirm-received", methods=["POST"])
def confirm_return_received(request_id):
    if session.get("account_type") != "owner":
        return redirect("/login")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_owner_schema(cursor)
    cursor.execute("SELECT order_id FROM return_refund_requests WHERE id = %s LIMIT 1", (request_id,))
    request_row = cursor.fetchone()
    if request_row:
        cursor.execute("""
            UPDATE return_refund_requests
            SET pickup_status = 'Received by Owner',
                item_received_at = NOW()
            WHERE id = %s
        """, (request_id,))
        cursor.execute("""
            INSERT INTO order_tracking (order_id, tracking_status, location, note)
            VALUES (%s, 'Return Received', 'Green Nursery', 'Returned item received by owner.')
        """, (request_row["order_id"],))
        cursor.execute("""
            UPDATE order_driver_assignments
            SET is_active = 0
            WHERE order_id = %s AND assignment_type = 'return_pickup' AND is_active = 1
        """, (request_row["order_id"],))
        conn.commit()
        flash("Returned item marked as received.", "success")
    cursor.close()
    conn.close()
    return redirect("/Orders")


@owner.route("/owner/return-refund/<int:request_id>/mark-refunded", methods=["POST"])
def mark_refunded(request_id):
    if session.get("account_type") != "owner":
        return redirect("/login")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_owner_schema(cursor)
    order_columns = get_table_columns(cursor, "orders")
    payment_expr = "o.payment_method" if "payment_method" in order_columns else "''"
    code_expr = "o.order_code" if "order_code" in order_columns else "o.id"
    cursor.execute(f"""
        SELECT rr.id, rr.order_id, {payment_expr} AS payment_method, {code_expr} AS order_code,
               u.email, u.fullname
        FROM return_refund_requests rr
        JOIN orders o ON o.id = rr.order_id
        JOIN users u ON u.id = rr.user_id
        WHERE rr.id = %s
        LIMIT 1
    """, (request_id,))
    request_row = cursor.fetchone()
    if request_row:
        payment_method = request_row.get("payment_method") or ""
        refund_method = "Cash Return" if payment_method == "Cash on Delivery" else "Manual/PayMongo Processing"
        refund_note = (
            "Refund returned manually through cash."
            if refund_method == "Cash Return"
            else "Online payment refund requires PayMongo/manual processing."
        )
        cursor.execute("""
            UPDATE return_refund_requests
            SET refund_status = 'Refunded',
                refund_method = %s,
                refund_note = %s
            WHERE id = %s
        """, (refund_method, refund_note, request_id))
        conn.commit()
        if request_row.get("email"):
            send_email(
                request_row["email"],
                "Return/Refund Request Refunded",
                f"Hello {request_row.get('fullname') or 'Customer'},\n\n"
                f"Your return/refund for order #{request_row.get('order_code') or request_row['order_id']} has been marked as refunded.\n\n"
                f"Refund Method: {refund_method}\n"
                f"Note: {refund_note}\n\n"
                "Please check My Purchases for more details.\n\n"
                "Thank you,\nGreen Nursery",
            )
        flash("Refund marked as refunded.", "success")
    cursor.close()
    conn.close()
    return redirect("/Orders")


@owner.route("/owner/order/<int:order_id>/driver-location")
def owner_driver_location(order_id):
    if session.get("account_type") != "owner":
        return redirect("/login")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_owner_schema(cursor)
    order_columns = get_table_columns(cursor, "orders")
    code_expr = "o.order_code" if "order_code" in order_columns else "o.id"
    cursor.execute(f"""
        SELECT o.id, {code_expr} AS order_code, d.fullname AS driver_name,
               loc.latitude, loc.longitude, loc.is_active, loc.updated_at
        FROM orders o
        LEFT JOIN order_driver_assignments oda ON oda.order_id = o.id AND oda.assignment_type = 'delivery' AND oda.is_active = 1
        LEFT JOIN users d ON d.id = oda.driver_id
        LEFT JOIN order_live_locations loc ON loc.order_id = o.id AND loc.driver_id = oda.driver_id
        WHERE o.id = %s
        LIMIT 1
    """, (order_id,))
    order = cursor.fetchone()
    cursor.execute("""
        SELECT tracking_status, location, note, created_at
        FROM order_tracking
        WHERE order_id = %s
        ORDER BY created_at DESC, id DESC
    """, (order_id,))
    tracking_updates = cursor.fetchall()
    cursor.close()
    conn.close()
    if not order:
        return redirect("/Orders")
    return render_template("owner_driver_location.html", order=order, tracking_updates=tracking_updates)


@owner.route("/owner/messages", methods=["GET"])
def owner_messages():
    owner_id = session.get("user_id")
    if not owner_id:
        return jsonify({"success": False, "message": "Please log in first."}), 401

    target = request.args.get("target", "customers")
    selected_thread_id = request.args.get("thread_id", type=int)

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_message_schema(cursor)

    if target == "admin":
        admin_user = get_admin_user(cursor)
        if not admin_user:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "No admin account found."}), 404

        selected_thread_id = get_or_create_thread(cursor, owner_id, admin_user["id"])
        conn.commit()
        messages = fetch_thread_messages(cursor, selected_thread_id)
        cursor.close()
        conn.close()

        return jsonify({
            "success": True,
            "target": "admin",
            "threads": [],
            "selected_thread_id": selected_thread_id,
            "selected_customer": {
                "name": f"{admin_user.get('fullname') or 'Green'} (Admin)",
                "photo": admin_user.get("profile_photo") or "/static/default-profile.jpg",
            },
            "admin_contact": {
                "id": admin_user["id"],
                "name": f"{admin_user.get('fullname') or 'Green'} (Admin)",
                "photo": admin_user.get("profile_photo") or "/static/default-profile.jpg",
            },
            "messages": [serialize_message(message, owner_id) for message in messages],
        })

    cursor.execute("""
        SELECT mt.id AS thread_id, mt.customer_id, mt.updated_at,
               u.fullname AS customer_name, u.account_type AS customer_type, u.profile_photo,
               (
                   SELECT body
                   FROM messages m
                   WHERE m.thread_id = mt.id
                   ORDER BY m.created_at DESC, m.id DESC
                   LIMIT 1
               ) AS last_message
        FROM message_threads mt
        JOIN users u ON u.id = mt.customer_id
        WHERE mt.owner_id = %s
        ORDER BY mt.updated_at DESC, mt.id DESC
    """, (owner_id,))
    threads = cursor.fetchall()

    if not selected_thread_id and threads:
        selected_thread_id = threads[0]["thread_id"]

    selected_thread = None
    messages = []
    if selected_thread_id:
        selected_thread = next(
            (thread for thread in threads if thread["thread_id"] == selected_thread_id),
            None,
        )
        if selected_thread:
            messages = fetch_thread_messages(cursor, selected_thread_id)

    cursor.close()
    conn.close()

    return jsonify({
        "success": True,
        "threads": [
            {
                "thread_id": thread["thread_id"],
                "customer_id": thread["customer_id"],
                "customer_name": (
                    f"{thread.get('customer_name') or 'Green'} (Admin)"
                    if thread.get("customer_type") == "admin"
                    else (thread.get("customer_name") or "Customer")
                ),
                "photo": thread.get("profile_photo") or "/static/default-profile.jpg",
                "last_message": thread.get("last_message") or "No messages yet.",
                "updated_at": thread["updated_at"].strftime("%Y-%m-%d %H:%M") if thread.get("updated_at") else "",
            }
            for thread in threads
        ],
        "selected_thread_id": selected_thread_id,
        "selected_customer": {
            "name": (
                f"{selected_thread.get('customer_name') or 'Green'} (Admin)"
                if selected_thread and selected_thread.get("customer_type") == "admin"
                else (selected_thread.get("customer_name") if selected_thread else "Customers")
            ),
            "photo": selected_thread.get("profile_photo") if selected_thread else "/static/default-profile.jpg",
        },
        "messages": [serialize_message(message, owner_id) for message in messages],
    })


@owner.route("/owner/messages", methods=["POST"])
def send_owner_message():
    owner_id = session.get("user_id")
    if not owner_id:
        return jsonify({"success": False, "message": "Please log in first."}), 401

    target = request.form.get("target", "customers")
    thread_id = request.form.get("thread_id", type=int)
    body = (request.form.get("message") or "").strip()
    photos = save_message_photos(request.files.getlist("photos"))
    if not body and not photos:
        return jsonify({"success": False, "message": "Enter a message or choose a photo."}), 400

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_message_schema(cursor)

    if target == "admin":
        admin_user = get_admin_user(cursor)
        if not admin_user:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "No admin account found."}), 404
        thread_id = get_or_create_thread(cursor, owner_id, admin_user["id"])
        insert_message(cursor, thread_id, owner_id, admin_user["id"], body, photos)
        conn.commit()
        messages = fetch_thread_messages(cursor, thread_id)
        cursor.close()
        conn.close()
        return jsonify({
            "success": True,
            "messages": [serialize_message(message, owner_id) for message in messages],
        })

    if not thread_id:
        cursor.close()
        conn.close()
        return jsonify({"success": False, "message": "Select a customer first."}), 400

    cursor.execute("""
        SELECT customer_id
        FROM message_threads
        WHERE id = %s AND owner_id = %s
        LIMIT 1
    """, (thread_id, owner_id))
    thread = cursor.fetchone()
    if not thread:
        cursor.close()
        conn.close()
        return jsonify({"success": False, "message": "Conversation not found."}), 404

    insert_message(cursor, thread_id, owner_id, thread["customer_id"], body, photos)
    conn.commit()
    messages = fetch_thread_messages(cursor, thread_id)
    cursor.close()
    conn.close()

    return jsonify({
        "success": True,
        "messages": [serialize_message(message, owner_id) for message in messages],
    })


def ownerpal_money(value):
    try:
        return f"PHP {float(value or 0):,.2f}"
    except (TypeError, ValueError):
        return "PHP 0.00"


def ownerpal_order_fields(cursor):
    order_columns = get_table_columns(cursor, "orders")
    total_column = "total_amount" if "total_amount" in order_columns else (
        "total" if "total" in order_columns else (
            "subtotal" if "subtotal" in order_columns else "0"
        )
    )
    status_column = "status" if "status" in order_columns else (
        "order_status" if "order_status" in order_columns else None
    )
    code_column = "order_code" if "order_code" in order_columns else "id"
    date_column = "ordered_at" if "ordered_at" in order_columns else (
        "order_at" if "order_at" in order_columns else (
            "created_at" if "created_at" in order_columns else "id"
        )
    )
    return order_columns, total_column, status_column, code_column, date_column


def ownerpal_completed_status(cursor, status_column):
    if not status_column:
        return None
    order_types = get_table_column_types(cursor, "orders")
    return db_status_value(order_types.get(status_column), "completed")


def ownerpal_pending_values(cursor, status_column):
    if not status_column:
        return []
    order_types = get_table_column_types(cursor, "orders")
    return [
        db_status_value(order_types.get(status_column), status)
        for status in ("to_pay", "to_ship", "to_receive")
    ]


@owner.route("/owner-chatbot", methods=["POST"])
def owner_chatbot():
    owner_id = session.get("user_id")
    if not owner_id or session.get("account_type") != "owner":
        return jsonify({"reply": "Please log in as the owner first so OwnerPal can check business information safely."}), 401

    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    lower_message = message.lower()
    if not message:
        return jsonify({"reply": "OwnerPal is ready. You can ask about sales, inventory, orders, low stock plants, best sellers, refunds, or customer support."})

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        order_columns, total_column, status_column, code_column, date_column = ownerpal_order_fields(cursor)
        completed_status = ownerpal_completed_status(cursor, status_column)
        pending_values = ownerpal_pending_values(cursor, status_column)
        sales_case = owner_sales_case_sql(order_columns, total_column, status_column)

        if any(word in lower_message for word in ("hello", "hi", "hey")):
            reply = random.choice([
                "Hello! I'm OwnerPal, your nursery management assistant. I can help you review sales, inventory, customer orders, and concerns.",
                "Hi! OwnerPal is ready to help you manage the nursery. Ask me for a business summary, low-stock plants, pending orders, or sales details.",
            ])

        elif "business summary" in lower_message or "summary" in lower_message or "overview" in lower_message or "dashboard" in lower_message:
            cursor.execute("SELECT COUNT(*) AS total_plants FROM plants")
            total_plants = cursor.fetchone()["total_plants"]
            cursor.execute("SELECT COUNT(*) AS low_stock FROM plants WHERE stock <= 5")
            low_stock = cursor.fetchone()["low_stock"]
            cursor.execute("SELECT COUNT(*) AS total_orders FROM orders")
            total_orders = cursor.fetchone()["total_orders"]

            if status_column and pending_values:
                placeholders = ", ".join(["%s"] * len(pending_values))
                cursor.execute(f"SELECT COUNT(*) AS pending_orders FROM orders WHERE {status_column} IN ({placeholders})", tuple(pending_values))
                pending_orders = cursor.fetchone()["pending_orders"]
            else:
                pending_orders = total_orders
            cursor.execute(f"SELECT COALESCE(SUM({sales_case}), 0) AS sales FROM orders")
            sales = cursor.fetchone()["sales"]

            reply = (
                f"Here is your business summary: you have {total_plants} plants in inventory, "
                f"{low_stock} low-stock plants, {total_orders} total orders, and {pending_orders} orders still in progress. "
                f"Completed sales total {ownerpal_money(sales)}. I suggest checking low-stock plants first, then reviewing pending orders."
            )

        elif "low stock" in lower_message or "stock alert" in lower_message or "stock monitoring" in lower_message:
            cursor.execute("""
                SELECT name, stock
                FROM plants
                WHERE stock <= 5
                ORDER BY stock ASC, name ASC
                LIMIT 10
            """)
            plants = cursor.fetchall()
            if plants:
                plant_list = ", ".join(f"{plant['name']} ({plant['stock']} left)" for plant in plants)
                reply = f"These plants are running low: {plant_list}. I recommend restocking them soon, especially if they are frequently ordered."
            else:
                reply = "Inventory looks healthy right now. I do not see plants with stock of 5 or below."

        elif any(word in lower_message for word in ("inventory", "add plant", "edit plant", "delete plant", "stock")):
            cursor.execute("SELECT COUNT(*) AS total_plants FROM plants")
            total_plants = cursor.fetchone()["total_plants"]
            reply = (
                f"You currently have {total_plants} plants listed. On the Inventory page, you can add a plant, edit its name/category/price/stock/photo, or delete plants that are no longer sold. "
                "A good routine is to update stock after deliveries, use clear plant photos, and keep best-selling plants available."
            )

        elif "pending" in lower_message or "in progress" in lower_message:
            if status_column and pending_values:
                placeholders = ", ".join(["%s"] * len(pending_values))
                cursor.execute(f"""
                    SELECT {code_column} AS order_code, {status_column} AS order_status, {total_column} AS total_amount
                    FROM orders
                    WHERE {status_column} IN ({placeholders})
                    ORDER BY {date_column} DESC, id DESC
                    LIMIT 5
                """, tuple(pending_values))
                orders = cursor.fetchall()
            else:
                orders = []
            if orders:
                order_list = "; ".join(
                    f"#{order['order_code']} - {order['order_status']} - {ownerpal_money(order['total_amount'])}"
                    for order in orders
                )
                reply = f"Here are recent orders still in progress: {order_list}. I suggest updating their statuses so customers can track them in My Purchases."
            else:
                reply = "I do not see pending or in-progress orders right now."

        elif "order status" in lower_message or "update order" in lower_message or "status flow" in lower_message:
            reply = (
                "You can update order status from the Orders page. Use Preparing while arranging the order, Packed when it is ready, "
                "Out for Delivery when it is on the way, and Delivered once the customer receives it. Use Cancelled only when the order should not continue. "
                "Keeping this updated helps customers track orders in My Purchases."
            )

        elif "sales" in lower_message or "total sales" in lower_message or "revenue" in lower_message:
            cursor.execute(f"""
                SELECT
                    COALESCE(SUM(CASE WHEN {sales_case} > 0 THEN 1 ELSE 0 END), 0) AS sales_orders,
                    COALESCE(SUM({sales_case}), 0) AS sales
                FROM orders
            """)
            row = cursor.fetchone()
            reply = (
                f"You have {row['sales_orders']} sales-counted orders with net sales of {ownerpal_money(row['sales'])}. "
                "Paid GCash and card orders count after payment, while COD still counts after delivery. Cancelled online paid orders keep only half in sales."
            )

        elif "best seller" in lower_message or "best-selling" in lower_message or "top plant" in lower_message:
            plant_columns = get_table_columns(cursor, "plants")
            if "sold" in plant_columns:
                cursor.execute("""
                    SELECT name, COALESCE(sold, 0) AS sold
                    FROM plants
                    ORDER BY sold DESC, name ASC
                    LIMIT 5
                """)
            else:
                order_items_table = get_order_items_table(cursor)
                status_filter = f"WHERE o.{status_column} = %s" if status_column else ""
                params = (completed_status,) if status_column else ()
                cursor.execute(f"""
                    SELECT p.name, COALESCE(SUM(oi.quantity), 0) AS sold
                    FROM {order_items_table} oi
                    JOIN orders o ON o.id = oi.order_id
                    JOIN plants p ON p.id = oi.plant_id
                    {status_filter}
                    GROUP BY p.id, p.name
                    ORDER BY sold DESC, p.name ASC
                    LIMIT 5
                """, params)
            plants = cursor.fetchall()
            if plants:
                plant_list = ", ".join(f"{plant['name']} ({plant['sold']} sold)" for plant in plants)
                reply = f"Your top plants are: {plant_list}. Keep these in stock and consider promoting them with better photos or bundle offers."
            else:
                reply = "I do not see best-selling data yet. Once orders are completed, OwnerPal can help identify your strongest plants."

        elif "pricing" in lower_message or "price" in lower_message:
            reply = (
                "For pricing, consider plant size, rarity, condition, pot quality, stock level, and customer demand. "
                "If a plant has high stock but low sales, improve the photo and description first before lowering the price."
            )

        elif "customer support" in lower_message or "customer concern" in lower_message or "reply" in lower_message:
            reply = (
                "When replying to customers, keep it polite and clear: confirm their concern, explain the next step, and give an update when possible. "
                "Example: 'Thank you for your message. Your order is currently being prepared, and we'll update you once it is out for delivery.'"
            )

        elif "refund" in lower_message or "return" in lower_message:
            if table_exists(cursor, "return_refund_requests"):
                cursor.execute("SELECT COUNT(*) AS pending_requests FROM return_refund_requests WHERE request_status = 'pending'")
                pending_requests = cursor.fetchone()["pending_requests"]
                reply = (
                    f"You currently have {pending_requests} pending return/refund request{'s' if pending_requests != 1 else ''}. "
                    "Open the Orders page to review the customer's statement and proof photo before approving or rejecting the request."
                )
            else:
                reply = "Refund and return handling can be managed from the Orders page once the return/refund request table is available."

        elif any(word in lower_message for word in ("profile", "password", "payout", "bank", "gcash")):
            reply = (
                "Owner profile, password, shop information, payout details, bank, and GCash details belong in the Owner Profile page. "
                "OwnerPal will guide you, but it will not change your profile information automatically."
            )

        elif "guide" in lower_message or "website" in lower_message or "page" in lower_message:
            reply = (
                "Owner page guide: Dashboard shows the business overview, sales, best sellers, and low-stock alerts. "
                "Orders lets you manage customer orders, statuses, and return/refund requests. Inventory lets you add, edit, or delete plants. "
                "Profile manages owner/shop information, password, and payout details."
            )

        else:
            reply = random.choice([
                "I can help with business summary, inventory, orders, sales, best sellers, pricing, refunds, and customer support. Try one of the quick replies if you want a fast start.",
                "OwnerPal is ready to help. Ask me about low-stock plants, pending orders, sales performance, pricing, or how to reply to customer concerns.",
            ])

    except mysql.connector.Error:
        reply = "OwnerPal had trouble checking the database just now, but I can still help with general inventory, order, sales, and customer support guidance."
    finally:
        cursor.close()
        conn.close()

    return jsonify({"reply": reply})



@owner.route("/Inventory")
def view_inventory():
     conn = get_db_connection()
     cursor = conn.cursor(dictionary=True)
     ensure_owner_schema(cursor)
     conn.commit()

     cursor.execute("SELECT * FROM plants ORDER BY id DESC")
     plants = cursor.fetchall()

     cursor.close()
     conn.close()
   
     return render_template("inventory.html", plants=plants)

@owner.route("/OwnerProfile")
def owner_profile():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_owner_schema(cursor)
    conn.commit()

    user_id = session.get("user_id")

    cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
    user = cursor.fetchone()

    cursor.execute("SELECT COUNT(*) AS total_plants FROM plants")
    total_plants = cursor.fetchone()["total_plants"]

    cursor.close()
    conn.close()
    return render_template("owner_profile.html", user=user, total_plants=total_plants)


@owner.route("/update-owner-profile-photo", methods=["POST"])
def update_owner_profile_photo():
    user_id = session.get("user_id")
    if not user_id:
        return redirect("/login")

    profile_photo = save_uploaded_image(request.files.get("profile_photo"), "profile", f"owner_{user_id}")
    if not profile_photo:
        return redirect("/OwnerProfile")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_owner_schema(cursor)
    cursor.execute("UPDATE users SET profile_photo = %s WHERE id = %s", (profile_photo, user_id))
    conn.commit()
    cursor.close()
    conn.close()

    return redirect("/OwnerProfile")

@owner.route("/update-owner-profile", methods=["POST"])
def update_owner_profile():
    user_id = session.get("user_id")

    fullname = request.form.get("fullname")
    email = request.form.get("email")
    phone = request.form.get("phone")
    address = request.form.get("address")

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


@owner.route("/update-shop-info", methods=["POST"])
def update_shop_info():
    user_id = session.get("user_id")
    if not user_id:
        return {"success": False, "message": "Please log in first."}, 401

    shop_name = (request.form.get("shop_name") or "").strip()
    business_type = (request.form.get("business_type") or "").strip()
    shop_contact = (request.form.get("shop_contact") or "").strip()
    shop_email = (request.form.get("shop_email") or "").strip()
    shop_description = (request.form.get("shop_description") or "").strip()

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        ensure_owner_schema(cursor)
        cursor.execute("""
            UPDATE users
            SET shop_name = %s,
                business_type = %s,
                shop_contact = %s,
                shop_email = %s,
                shop_description = %s
            WHERE id = %s AND account_type = 'owner'
        """, (shop_name, business_type, shop_contact, shop_email, shop_description, user_id))
        conn.commit()
    except mysql.connector.Error as exc:
        conn.rollback()
        current_app.logger.exception("Owner shop info update failed: %s", exc)
        return {"success": False, "message": "Unable to save shop information right now."}, 500
    finally:
        cursor.close()
        conn.close()

    return {"success": True, "message": "Shop information updated successfully!"}

@owner.route("/change-password", methods=["POST"])
def change_password():
    user_id = session.get("user_id")

    current = request.form.get("current_password")
    new = request.form.get("new_password")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT password FROM users WHERE id=%s", (user_id,))
    user = cursor.fetchone()

    if not user or not password_matches(user["password"], current):
        return {"success": False, "message": "Wrong current password!"}

    cursor.execute(
        "UPDATE users SET password=%s WHERE id=%s",
        (generate_password_hash(new), user_id)
    )

    conn.commit()
    cursor.close()
    conn.close()

    log_activity(user_id, "owner", "password changed", "Owner changed password.")
    session.clear()
    return {"success": True, "message": "Password updated. Please log in again."}

@owner.route("/add-plant", methods=["POST"])
def add_plant():
    name = request.form.get("plantName")
    category = request.form.get("plantCategory")
    description = (request.form.get("plantDescription") or "").strip()
    price = request.form.get("plantPrice")
    stock = request.form.get("plantStock")
    image = (
        save_uploaded_image(request.files.get("plantMainPhoto"), "plants", "plant")
        or request.form.get("plantImage")
        or "/static/snakeplant.jpg"
    )
    sample_photo = (
        save_uploaded_image(request.files.get("plantSamplePhoto"), "plant_samples", "sample")
        or save_uploaded_image(request.files.get("plantPhoto"), "plant_samples", "sample")
    )
    sample_paths = (
        parse_sample_paths(request.form.get("plantSampleImages"))
        + save_uploaded_images(request.files.getlist("plantSamplePhotos"), "plant_samples", "sample")
    )
    if sample_photo:
        sample_paths.insert(0, sample_photo)
    sample_photo = sample_paths[0] if sample_paths else None
    sample_photos = serialize_sample_paths(sample_paths)

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_owner_schema(cursor)
    plant_columns = get_table_columns(cursor, "plants")

    plant_data = {
        "name": name,
        "category": category,
        "description": description,
        "price": price,
        "stock": stock,
        "image_url": image,
        "sample_photo": sample_photo,
        "sample_photos": sample_photos,
    }
    insert_columns = [column for column in plant_data if column in plant_columns]
    placeholders = ", ".join(["%s"] * len(insert_columns))
    cursor.execute(f"""
        INSERT INTO plants ({", ".join(insert_columns)})
        VALUES ({placeholders})
    """, tuple(plant_data[column] for column in insert_columns))

    conn.commit()
    cursor.close()
    conn.close()

    notify_customers_new_plant(name, price, category, stock)

    return redirect(url_for("owner.view_inventory"))


@owner.route("/edit-plant/<int:plant_id>", methods=["POST"])
def edit_plant(plant_id):
    name = request.form.get("plantName")
    category = request.form.get("plantCategory")
    description = (request.form.get("plantDescription") or "").strip()
    price = request.form.get("plantPrice")
    stock = request.form.get("plantStock")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_owner_schema(cursor)
    plant_columns = get_table_columns(cursor, "plants")

    cursor.execute("SELECT image_url, sample_photo, sample_photos FROM plants WHERE id = %s", (plant_id,))
    existing_plant = cursor.fetchone() or {}
    image = (
        save_uploaded_image(request.files.get("plantMainPhoto"), "plants", f"plant_{plant_id}")
        or request.form.get("plantImage")
        or existing_plant.get("image_url")
        or "/static/snakeplant.jpg"
    )
    sample_photo = (
        save_uploaded_image(request.files.get("plantSamplePhoto"), "plant_samples", f"sample_{plant_id}")
        or save_uploaded_image(request.files.get("plantPhoto"), "plant_samples", f"sample_{plant_id}")
        or request.form.get("existingSamplePhoto")
        or existing_plant.get("sample_photo")
    )
    sample_paths = (
        parse_sample_paths(request.form.get("plantSampleImages"))
        + save_uploaded_images(request.files.getlist("plantSamplePhotos"), "plant_samples", f"sample_{plant_id}")
    )
    if sample_photo:
        sample_paths.insert(0, sample_photo)
    if not sample_paths:
        sample_paths = parse_sample_paths(existing_plant.get("sample_photos"))
    sample_photo = sample_paths[0] if sample_paths else None
    sample_photos = serialize_sample_paths(sample_paths)

    plant_data = {
        "name": name,
        "category": category,
        "description": description,
        "price": price,
        "stock": stock,
        "image_url": image,
        "sample_photo": sample_photo,
        "sample_photos": sample_photos,
    }
    update_columns = [column for column in plant_data if column in plant_columns]
    assignments = ", ".join(f"{column}=%s" for column in update_columns)
    cursor.execute(f"""
        UPDATE plants
        SET {assignments}
        WHERE id=%s
    """, tuple(plant_data[column] for column in update_columns) + (plant_id,))

    check_and_send_low_stock_notifications(cursor, plant_id)
    conn.commit()
    cursor.close()
    conn.close()

    return redirect(url_for("owner.view_inventory"))


@owner.route("/delete-plant/<int:plant_id>", methods=["POST"])
def delete_plant(plant_id):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM plants WHERE id = %s", (plant_id,))

    conn.commit()
    cursor.close()
    conn.close()

    return redirect(url_for("owner.view_inventory"))
