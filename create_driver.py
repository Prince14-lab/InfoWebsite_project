from werkzeug.security import generate_password_hash

from info_backend import app, get_db_connection
from security_utils import init_security_schema


DRIVER = {
    "fullname": "Delivery Driver",
    "email": "driver01@greennursery.com",
    "username": "driver01",
    "password": "Driver@123",
    "account_type": "driver",
    "account_status": "active",
}


def main():
    with app.app_context():
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        init_security_schema(cursor)
        conn.commit()

        cursor.execute(
            "SELECT id FROM users WHERE username = %s OR email = %s LIMIT 1",
            (DRIVER["username"], DRIVER["email"]),
        )
        if cursor.fetchone():
            print("Driver account already exists.")
            cursor.close()
            conn.close()
            return

        cursor.execute("""
            INSERT INTO users (fullname, email, username, password, account_type, account_status)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            DRIVER["fullname"],
            DRIVER["email"],
            DRIVER["username"],
            generate_password_hash(DRIVER["password"]),
            DRIVER["account_type"],
            DRIVER["account_status"],
        ))
        conn.commit()
        cursor.close()
        conn.close()

    print("Driver account created successfully.")
    print("Username: driver01")
    print("Password: Driver@123")


if __name__ == "__main__":
    main()
