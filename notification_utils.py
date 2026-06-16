import mysql.connector
from flask import current_app

from email_utils import send_email


LOW_STOCK_THRESHOLD = 5
CUSTOMER_LOW_STOCK_THRESHOLD = 3


def get_db_connection():
    return mysql.connector.connect(**current_app.config["DB_CONFIG"])


def table_exists(cursor, table_name):
    cursor.execute("SHOW TABLES LIKE %s", (table_name,))
    return cursor.fetchone() is not None


def get_table_columns(cursor, table_name):
    if not table_exists(cursor, table_name):
        return set()
    cursor.execute(f"SHOW COLUMNS FROM {table_name}")
    return {column["Field"] for column in cursor.fetchall()}


def ensure_notification_schema(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stock_notification_logs (
            id INT AUTO_INCREMENT PRIMARY KEY,
            plant_id INT NOT NULL,
            notification_type VARCHAR(50) NOT NULL,
            stock_level INT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY unique_stock_notification (plant_id, notification_type, stock_level)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_notification_logs (
            id INT AUTO_INCREMENT PRIMARY KEY,
            notification_key VARCHAR(150) UNIQUE NOT NULL,
            recipient_email VARCHAR(150) NOT NULL,
            subject VARCHAR(255) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


def get_customer_emails(cursor):
    user_columns = get_table_columns(cursor, "users")
    status_filter = "AND COALESCE(account_status, 'active') <> 'blocked'" if "account_status" in user_columns else ""
    cursor.execute(f"""
        SELECT email
        FROM users
        WHERE account_type = 'customer'
          AND email IS NOT NULL
          AND email <> ''
          {status_filter}
    """)
    return [row["email"] for row in cursor.fetchall()]


def get_owner_emails(cursor):
    cursor.execute("""
        SELECT email
        FROM users
        WHERE account_type = 'owner'
          AND email IS NOT NULL
          AND email <> ''
    """)
    return [row["email"] for row in cursor.fetchall()]


def get_admin_emails(cursor):
    cursor.execute("""
        SELECT email
        FROM users
        WHERE account_type = 'admin'
          AND email IS NOT NULL
          AND email <> ''
    """)
    return [row["email"] for row in cursor.fetchall()]


def send_bulk_email(recipients, subject, body):
    sent = 0
    failed = 0
    for recipient in recipients or []:
        if send_email(recipient, subject, body):
            sent += 1
        else:
            failed += 1
    return {"sent": sent, "failed": failed}


def email_already_sent(cursor, notification_key):
    ensure_notification_schema(cursor)
    cursor.execute(
        "SELECT id FROM email_notification_logs WHERE notification_key = %s LIMIT 1",
        (notification_key,),
    )
    return cursor.fetchone() is not None


def mark_email_sent(cursor, notification_key, recipient_email, subject):
    ensure_notification_schema(cursor)
    cursor.execute("""
        INSERT IGNORE INTO email_notification_logs (notification_key, recipient_email, subject)
        VALUES (%s, %s, %s)
    """, (notification_key, recipient_email, subject))


def stock_notification_already_sent(cursor, plant_id, notification_type, stock_level):
    ensure_notification_schema(cursor)
    cursor.execute("""
        SELECT id
        FROM stock_notification_logs
        WHERE plant_id = %s AND notification_type = %s AND stock_level = %s
        LIMIT 1
    """, (plant_id, notification_type, stock_level))
    return cursor.fetchone() is not None


def mark_stock_notification_sent(cursor, plant_id, notification_type, stock_level):
    ensure_notification_schema(cursor)
    cursor.execute("""
        INSERT IGNORE INTO stock_notification_logs (plant_id, notification_type, stock_level)
        VALUES (%s, %s, %s)
    """, (plant_id, notification_type, stock_level))


def peso_text(value):
    return f"PHP {float(value or 0):,.2f}"


def notify_customers_new_plant(plant_name, price, category, stock):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        ensure_notification_schema(cursor)
        recipients = get_customer_emails(cursor)
        result = send_bulk_email(
            recipients,
            "New Plant Available at Green Nursery",
            "Hello,\n\n"
            "A new plant has been added to Green Nursery!\n\n"
            f"Plant: {plant_name}\n"
            f"Category: {category or 'Plant'}\n"
            f"Price: {peso_text(price)}\n"
            f"Available Stock: {stock}\n\n"
            "Visit Green Nursery to check it out.\n\n"
            "Thank you,\n"
            "Green Nursery",
        )
        current_app.logger.info("New plant email result: %s", result)
        conn.commit()
        return result
    except Exception as error:
        conn.rollback()
        current_app.logger.exception("New plant notification failed: %s", error)
        return {"sent": 0, "failed": 0}
    finally:
        cursor.close()
        conn.close()


def notify_owner_low_stock(plant_name, stock, plant_id=None, cursor=None):
    if cursor and plant_id and stock_notification_already_sent(cursor, plant_id, "owner_low_stock", stock):
        return {"sent": 0, "failed": 0}

    owns_connection = cursor is None
    conn = None
    if owns_connection:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

    try:
        ensure_notification_schema(cursor)
        recipients = get_owner_emails(cursor)
        result = send_bulk_email(
            recipients,
            "Low Stock Alert - Green Nursery",
            "Hello,\n\n"
            "A plant is running low in your Green Nursery inventory.\n\n"
            f"Plant: {plant_name}\n"
            f"Remaining Stock: {stock}\n\n"
            "Please update or restock this plant in your Inventory page.\n\n"
            "Thank you,\n"
            "Green Nursery",
        )
        if plant_id and result["sent"] > 0:
            mark_stock_notification_sent(cursor, plant_id, "owner_low_stock", stock)
        if owns_connection:
            conn.commit()
        return result
    except Exception as error:
        if owns_connection:
            conn.rollback()
        current_app.logger.exception("Owner low-stock notification failed: %s", error)
        return {"sent": 0, "failed": 0}
    finally:
        if owns_connection:
            cursor.close()
            conn.close()


def notify_customers_low_stock(plant_name, stock, plant_id=None, cursor=None):
    if cursor and plant_id and stock_notification_already_sent(cursor, plant_id, "customer_low_stock", stock):
        return {"sent": 0, "failed": 0}

    owns_connection = cursor is None
    conn = None
    if owns_connection:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

    try:
        ensure_notification_schema(cursor)
        recipients = get_customer_emails(cursor)
        result = send_bulk_email(
            recipients,
            "Hurry! A Plant is Almost Sold Out",
            "Hello,\n\n"
            "One of our plants is almost sold out at Green Nursery.\n\n"
            f"Plant: {plant_name}\n"
            f"Only {stock} left in stock.\n\n"
            "Visit Green Nursery soon if you would like to order it.\n\n"
            "Thank you,\n"
            "Green Nursery",
        )
        if plant_id and result["sent"] > 0:
            mark_stock_notification_sent(cursor, plant_id, "customer_low_stock", stock)
        if owns_connection:
            conn.commit()
        return result
    except Exception as error:
        if owns_connection:
            conn.rollback()
        current_app.logger.exception("Customer low-stock notification failed: %s", error)
        return {"sent": 0, "failed": 0}
    finally:
        if owns_connection:
            cursor.close()
            conn.close()


def check_and_send_low_stock_notifications(cursor, plant_id):
    cursor.execute("SELECT id, name, stock FROM plants WHERE id = %s LIMIT 1", (plant_id,))
    plant = cursor.fetchone()
    if not plant:
        return

    stock = int(plant.get("stock") or 0)
    if stock <= LOW_STOCK_THRESHOLD:
        notify_owner_low_stock(plant["name"], stock, plant_id=plant["id"], cursor=cursor)
    if 0 < stock <= CUSTOMER_LOW_STOCK_THRESHOLD:
        notify_customers_low_stock(plant["name"], stock, plant_id=plant["id"], cursor=cursor)


def notify_admin_announcement(title, message, announcement_id=None):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    subject = f"Green Nursery Announcement: {title or 'Website Announcement'}"
    try:
        ensure_notification_schema(cursor)
        recipients = get_customer_emails(cursor) + get_owner_emails(cursor)
        sent = 0
        failed = 0
        for recipient in recipients:
            key = f"admin_announcement_{announcement_id}_{recipient}" if announcement_id else None
            if key and email_already_sent(cursor, key):
                continue
            ok = send_email(
                recipient,
                subject,
                "Hello,\n\n"
                f"{message}\n\n"
                "Thank you,\n"
                "Green Nursery",
            )
            if ok:
                sent += 1
                if key:
                    mark_email_sent(cursor, key, recipient, subject)
            else:
                failed += 1
        conn.commit()
        return {"sent": sent, "failed": failed}
    except Exception as error:
        conn.rollback()
        current_app.logger.exception("Announcement notification failed: %s", error)
        return {"sent": 0, "failed": 0}
    finally:
        cursor.close()
        conn.close()


def notify_customer_order_status(customer_email, customer_name, order_code, status, cursor=None, order_id=None):
    if not customer_email:
        return False

    status_label = status or "Updated"
    subject = {
        "Preparing": "Your Green Nursery Order is Being Prepared",
        "Packed": "Your Green Nursery Order is Packed",
        "Out for Delivery": "Your Green Nursery Order is Out for Delivery",
        "Delivered": "Your Green Nursery Order has been Delivered",
        "Cancelled": "Your Green Nursery Order was Cancelled",
    }.get(status_label, "Your Green Nursery Order Status Was Updated")

    key = f"order_status_{order_id}_{status_label}_{customer_email}" if cursor and order_id else None
    if key and email_already_sent(cursor, key):
        return False

    ok = send_email(
        customer_email,
        subject,
        f"Hello {customer_name or 'Customer'},\n\n"
        f"Your order #{order_code} is now {status_label}.\n\n"
        "Please check your My Purchases page for more details.\n\n"
        "Thank you,\n"
        "Green Nursery",
    )
    if ok and key:
        mark_email_sent(cursor, key, customer_email, subject)
    return ok


def notify_customer_payment_confirmed(customer_email, customer_name, order_code, receipt_no, total, cursor=None, order_id=None):
    if not customer_email:
        return False

    subject = "Payment Confirmed - Green Nursery"
    key = f"payment_confirmed_order_{order_id}_{customer_email}" if cursor and order_id else None
    if key and email_already_sent(cursor, key):
        return False

    ok = send_email(
        customer_email,
        subject,
        f"Hello {customer_name or 'Customer'},\n\n"
        "Your payment has been confirmed.\n\n"
        f"Order: #{order_code}\n"
        f"Receipt Number: {receipt_no}\n"
        f"Total Amount: {peso_text(total)}\n\n"
        "Your e-receipt is now available in My Purchases.\n\n"
        "Thank you,\n"
        "Green Nursery",
    )
    if ok and key:
        mark_email_sent(cursor, key, customer_email, subject)
    return ok


def notify_owner_new_order(owner_emails, order_code, customer_name, total, payment_method=None):
    body = (
        "Hello,\n\n"
        "A new order has been received at Green Nursery.\n\n"
        f"Order: #{order_code}\n"
        f"Customer: {customer_name or 'Customer'}\n"
        f"Total Amount: {peso_text(total)}\n"
    )
    if payment_method:
        body += f"Payment Method: {payment_method}\n"
    body += "\nPlease check the Orders page for details.\n\nThank you,\nGreen Nursery"
    return send_bulk_email(owner_emails, "New Order Received - Green Nursery", body)


def notify_customer_return_refund_update(customer_email, customer_name, order_code, decision, owner_response, cursor=None, request_id=None):
    if not customer_email:
        return False

    subject = "Return/Refund Request Update"
    decision_label = (decision or "reviewed").replace("_", " ").title()
    key = f"return_refund_{request_id}_{decision}_{customer_email}" if cursor and request_id else None
    if key and email_already_sent(cursor, key):
        return False

    ok = send_email(
        customer_email,
        subject,
        f"Hello {customer_name or 'Customer'},\n\n"
        f"Your return/refund request for order #{order_code} has been reviewed.\n\n"
        f"Decision: {decision_label}\n"
        f"Owner response: {owner_response or 'No additional owner response was provided.'}\n\n"
        "Please check your My Purchases page for more details.\n\n"
        "Thank you,\n"
        "Green Nursery",
    )
    if ok and key:
        mark_email_sent(cursor, key, customer_email, subject)
    return ok
