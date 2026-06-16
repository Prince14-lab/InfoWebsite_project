from functools import wraps
from urllib.parse import quote_plus

import mysql.connector
from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session

from tracking_utils import ensure_order_tracking_schema, ensure_return_refund_pickup_schema

driver = Blueprint("driver", __name__)


def get_db_connection():
    return mysql.connector.connect(**current_app.config["DB_CONFIG"])


def ensure_driver_navigation_schema(cursor):
    order_columns = get_table_columns(cursor, "orders")
    if "delivery_latitude" not in order_columns:
        cursor.execute("ALTER TABLE orders ADD COLUMN delivery_latitude DECIMAL(10,8) NULL")
        order_columns.add("delivery_latitude")
    if "delivery_longitude" not in order_columns:
        cursor.execute("ALTER TABLE orders ADD COLUMN delivery_longitude DECIMAL(11,8) NULL")
        order_columns.add("delivery_longitude")
    return order_columns


def driver_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if session.get("account_type") != "driver":
            if request.path.endswith("live-location-json") or request.path.endswith("stop-live-location"):
                return jsonify({"success": False, "message": "Driver access is required."}), 403
            return redirect("/login")
        return view(*args, **kwargs)

    return wrapped_view


def get_table_columns(cursor, table_name):
    cursor.execute(f"SHOW COLUMNS FROM {table_name}")
    return {column["Field"] for column in cursor.fetchall()}


def order_field_expr(order_columns, preferred, fallback=None, default="''"):
    for column in preferred:
        if column in order_columns:
            return f"o.{column}"
    if fallback:
        return fallback
    return default


def driver_order_fields(cursor):
    order_columns = ensure_driver_navigation_schema(cursor)
    return {
        "code": order_field_expr(order_columns, ["order_code"], fallback="o.id"),
        "status": order_field_expr(order_columns, ["order_status", "status"]),
        "total": order_field_expr(order_columns, ["total_amount", "total", "subtotal"], default="0"),
        "address": order_field_expr(order_columns, ["delivery_address"], default="u.address"),
        "contact": order_field_expr(order_columns, ["contact_number"], default="u.phone"),
        "payment_method": order_field_expr(order_columns, ["payment_method"]),
        "payment_status": order_field_expr(order_columns, ["payment_status"]),
        "date": order_field_expr(order_columns, ["ordered_at", "order_at", "created_at"], fallback="o.id"),
        "latitude": order_field_expr(order_columns, ["delivery_latitude"], default="NULL"),
        "longitude": order_field_expr(order_columns, ["delivery_longitude"], default="NULL"),
    }


def build_navigation_url(latitude, longitude, address):
    if latitude is not None and longitude is not None:
        try:
            return f"https://www.google.com/maps?q={float(latitude)},{float(longitude)}"
        except (TypeError, ValueError):
            pass

    clean_address = (address or "").strip()
    if clean_address:
        return f"https://www.google.com/maps/search/?api=1&query={quote_plus(clean_address)}"

    return "https://www.google.com/maps"


def driver_assigned_order(cursor, order_id, driver_id, assignment_type=None):
    type_filter = "AND assignment_type = %s" if assignment_type else ""
    params = [order_id, driver_id]
    if assignment_type:
        params.append(assignment_type)
    cursor.execute("""
        SELECT id
        FROM order_driver_assignments
        WHERE order_id = %s AND driver_id = %s AND is_active = 1
        """ + type_filter + """
        LIMIT 1
    """, tuple(params))
    return cursor.fetchone() is not None


@driver.route("/driver")
@driver.route("/driver/dashboard")
@driver_required
def driver_dashboard():
    driver_id = session.get("user_id")
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_order_tracking_schema(cursor)
    ensure_return_refund_pickup_schema(cursor)
    conn.commit()
    fields = driver_order_fields(cursor)

    cursor.execute(f"""
        SELECT o.id,
               {fields["code"]} AS order_code,
               {fields["status"]} AS order_status,
               {fields["total"]} AS total_amount,
               {fields["address"]} AS delivery_address,
               {fields["contact"]} AS contact_number,
               {fields["payment_method"]} AS payment_method,
               {fields["payment_status"]} AS payment_status,
               {fields["date"]} AS order_date,
               {fields["latitude"]} AS delivery_latitude,
               {fields["longitude"]} AS delivery_longitude,
               u.fullname AS customer_name,
               u.email AS customer_email,
               oda.assigned_at,
               oda.assignment_type,
               rr.id AS return_request_id,
               rr.reason AS return_reason,
               rr.proof_photo AS return_proof_photo,
               rr.pickup_status,
               rr.refund_status
        FROM order_driver_assignments oda
        JOIN orders o ON o.id = oda.order_id
        JOIN users u ON u.id = o.user_id
        LEFT JOIN return_refund_requests rr ON rr.order_id = o.id
        WHERE oda.driver_id = %s AND oda.is_active = 1
        ORDER BY oda.assigned_at DESC, o.id DESC
    """, (driver_id,))
    orders = cursor.fetchall()

    cursor.execute("SELECT fullname FROM users WHERE id = %s LIMIT 1", (driver_id,))
    driver_user = cursor.fetchone() or {}
    cursor.close()
    conn.close()

    completed = [
        order for order in orders
        if order.get("assignment_type") == "delivery"
        and str(order.get("order_status") or "").lower() in {"delivered", "completed"}
    ]
    pickups = [order for order in orders if order.get("assignment_type") == "return_pickup"]
    deliveries = [order for order in orders if order.get("assignment_type") != "return_pickup"]
    for order in orders:
        order["navigation_url"] = build_navigation_url(
            order.get("delivery_latitude"),
            order.get("delivery_longitude"),
            order.get("delivery_address"),
        )
    return render_template(
        "driver_dashboard.html",
        driver_user=driver_user,
        orders=orders,
        deliveries=deliveries,
        pickups=pickups,
        total_assigned=len(orders),
        active_deliveries=len(deliveries) - len(completed),
        completed_deliveries=len(completed),
        active_pickups=len(pickups),
    )


@driver.route("/driver/order/<int:order_id>/share-location")
@driver_required
def share_live_location_page(order_id):
    driver_id = session.get("user_id")
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_order_tracking_schema(cursor)
    ensure_return_refund_pickup_schema(cursor)
    conn.commit()
    if not driver_assigned_order(cursor, order_id, driver_id):
        cursor.close()
        conn.close()
        flash("You are not assigned to this order.", "error")
        return redirect("/driver")

    fields = driver_order_fields(cursor)
    cursor.execute(f"""
        SELECT o.id, {fields["code"]} AS order_code,
               {fields["address"]} AS delivery_address,
               {fields["contact"]} AS contact_number,
               {fields["latitude"]} AS delivery_latitude,
               {fields["longitude"]} AS delivery_longitude,
               u.fullname AS customer_name
        FROM orders o
        JOIN users u ON u.id = o.user_id
        WHERE o.id = %s
        LIMIT 1
    """, (order_id,))
    order = cursor.fetchone()
    cursor.close()
    conn.close()
    if not order:
        return redirect("/driver")
    return render_template("share_location.html", order=order)


@driver.route("/driver/order/<int:order_id>/live-location-json", methods=["POST"])
@driver_required
def update_live_location_json(order_id):
    driver_id = session.get("user_id")
    data = request.get_json(silent=True) or {}
    try:
        latitude = float(data.get("latitude"))
        longitude = float(data.get("longitude"))
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Invalid coordinates."}), 400

    if latitude < -90 or latitude > 90 or longitude < -180 or longitude > 180:
        return jsonify({"success": False, "message": "Coordinates are out of range."}), 400

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_order_tracking_schema(cursor)
    if not driver_assigned_order(cursor, order_id, driver_id):
        cursor.close()
        conn.close()
        return jsonify({"success": False, "message": "Order is not assigned to this driver."}), 403

    cursor.execute("""
        INSERT INTO order_live_locations (order_id, driver_id, latitude, longitude, is_active, updated_at)
        VALUES (%s, %s, %s, %s, 1, CURRENT_TIMESTAMP)
        ON DUPLICATE KEY UPDATE
            driver_id = VALUES(driver_id),
            latitude = VALUES(latitude),
            longitude = VALUES(longitude),
            is_active = 1,
            updated_at = CURRENT_TIMESTAMP
    """, (order_id, driver_id, latitude, longitude))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"success": True})


@driver.route("/driver/return-pickup/<int:request_id>/mark-picked-up", methods=["POST"])
@driver_required
def driver_mark_picked_up(request_id):
    driver_id = session.get("user_id")
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_order_tracking_schema(cursor)
    ensure_return_refund_pickup_schema(cursor)
    cursor.execute("""
        SELECT rr.id, rr.order_id, rr.pickup_driver_id, o.delivery_address
        FROM return_refund_requests rr
        JOIN orders o ON o.id = rr.order_id
        WHERE rr.id = %s
        LIMIT 1
    """, (request_id,))
    request_row = cursor.fetchone()
    if not request_row or request_row.get("pickup_driver_id") != driver_id:
        cursor.close()
        conn.close()
        flash("This pickup request is not assigned to you.", "error")
        return redirect("/driver")

    if not driver_assigned_order(cursor, request_row["order_id"], driver_id, "return_pickup"):
        cursor.close()
        conn.close()
        flash("This pickup assignment is not active.", "error")
        return redirect("/driver")

    cursor.execute("""
        UPDATE return_refund_requests
        SET pickup_status = 'Picked Up'
        WHERE id = %s
    """, (request_id,))
    cursor.execute("""
        INSERT INTO order_tracking (order_id, tracking_status, location, note)
        VALUES (%s, 'Return Picked Up', %s, 'Driver picked up the returned item.')
    """, (request_row["order_id"], request_row.get("delivery_address") or "Customer Address"))
    cursor.execute("""
        UPDATE order_live_locations
        SET is_active = 0
        WHERE order_id = %s AND driver_id = %s
    """, (request_row["order_id"], driver_id))
    conn.commit()
    cursor.close()
    conn.close()
    flash("Return item marked as picked up.", "success")
    return redirect("/driver")


@driver.route("/driver/order/<int:order_id>/stop-live-location", methods=["POST"])
@driver_required
def stop_live_location(order_id):
    driver_id = session.get("user_id")
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    ensure_order_tracking_schema(cursor)
    if not driver_assigned_order(cursor, order_id, driver_id):
        cursor.close()
        conn.close()
        return jsonify({"success": False, "message": "Order is not assigned to this driver."}), 403

    cursor.execute("""
        UPDATE order_live_locations
        SET is_active = 0
        WHERE order_id = %s AND driver_id = %s
    """, (order_id, driver_id))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"success": True})
