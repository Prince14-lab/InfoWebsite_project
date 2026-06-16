def ensure_order_tracking_schema(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS order_tracking (
            id INT AUTO_INCREMENT PRIMARY KEY,
            order_id INT NOT NULL,
            tracking_status VARCHAR(100) NOT NULL,
            location VARCHAR(255) NOT NULL,
            note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS order_live_locations (
            id INT AUTO_INCREMENT PRIMARY KEY,
            order_id INT NOT NULL,
            driver_id INT NULL,
            latitude DECIMAL(10,8) NOT NULL,
            longitude DECIMAL(11,8) NOT NULL,
            is_active TINYINT(1) NOT NULL DEFAULT 1,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
            FOREIGN KEY (driver_id) REFERENCES users(id) ON DELETE SET NULL,
            UNIQUE KEY unique_order_live_location (order_id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS order_driver_assignments (
            id INT AUTO_INCREMENT PRIMARY KEY,
            order_id INT NOT NULL,
            driver_id INT NOT NULL,
            assignment_type VARCHAR(30) NOT NULL DEFAULT 'delivery',
            assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active TINYINT(1) NOT NULL DEFAULT 1,
            cancelled_at DATETIME NULL,
            cancelled_by INT NULL,
            cancel_reason TEXT NULL,
            FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
            FOREIGN KEY (driver_id) REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE KEY unique_order_driver_assignment (order_id, assignment_type)
        )
    """)

    cursor.execute("SHOW COLUMNS FROM order_driver_assignments")
    assignment_columns = {column["Field"] for column in cursor.fetchall()}
    if "assignment_type" not in assignment_columns:
        cursor.execute("ALTER TABLE order_driver_assignments ADD COLUMN assignment_type VARCHAR(30) NOT NULL DEFAULT 'delivery' AFTER driver_id")
    if "cancelled_at" not in assignment_columns:
        cursor.execute("ALTER TABLE order_driver_assignments ADD COLUMN cancelled_at DATETIME NULL AFTER is_active")
    if "cancelled_by" not in assignment_columns:
        cursor.execute("ALTER TABLE order_driver_assignments ADD COLUMN cancelled_by INT NULL AFTER cancelled_at")
    if "cancel_reason" not in assignment_columns:
        cursor.execute("ALTER TABLE order_driver_assignments ADD COLUMN cancel_reason TEXT NULL AFTER cancelled_by")

    cursor.execute("""
        SELECT COUNT(*) AS exists_count
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'order_driver_assignments'
          AND index_name = 'unique_order_driver_assignment'
          AND column_name = 'assignment_type'
    """)
    has_assignment_type_unique = cursor.fetchone()["exists_count"] > 0
    if not has_assignment_type_unique:
        cursor.execute("""
            SELECT COUNT(*) AS exists_count
            FROM information_schema.statistics
            WHERE table_schema = DATABASE()
              AND table_name = 'order_driver_assignments'
              AND index_name = 'unique_order_driver_assignment'
        """)
        if cursor.fetchone()["exists_count"] > 0:
            cursor.execute("""
                SELECT COUNT(*) AS exists_count
                FROM information_schema.statistics
                WHERE table_schema = DATABASE()
                  AND table_name = 'order_driver_assignments'
                  AND index_name = 'idx_order_driver_assignments_order_id'
            """)
            if cursor.fetchone()["exists_count"] == 0:
                cursor.execute("""
                    ALTER TABLE order_driver_assignments
                    ADD INDEX idx_order_driver_assignments_order_id (order_id)
                """)
            cursor.execute("ALTER TABLE order_driver_assignments DROP INDEX unique_order_driver_assignment")
        cursor.execute("""
            ALTER TABLE order_driver_assignments
            ADD UNIQUE KEY unique_order_driver_assignment (order_id, assignment_type)
        """)


def ensure_return_refund_pickup_schema(cursor):
    cursor.execute("SHOW TABLES LIKE 'return_refund_requests'")
    if not cursor.fetchone():
        return

    cursor.execute("SHOW COLUMNS FROM return_refund_requests")
    columns = {column["Field"] for column in cursor.fetchall()}
    if "pickup_status" not in columns:
        cursor.execute("ALTER TABLE return_refund_requests ADD COLUMN pickup_status VARCHAR(50) DEFAULT 'Pending Pickup'")
    if "pickup_driver_id" not in columns:
        cursor.execute("ALTER TABLE return_refund_requests ADD COLUMN pickup_driver_id INT NULL")
    if "pickup_assigned_at" not in columns:
        cursor.execute("ALTER TABLE return_refund_requests ADD COLUMN pickup_assigned_at DATETIME NULL")
    if "item_received_at" not in columns:
        cursor.execute("ALTER TABLE return_refund_requests ADD COLUMN item_received_at DATETIME NULL")
    if "refund_status" not in columns:
        cursor.execute("ALTER TABLE return_refund_requests ADD COLUMN refund_status VARCHAR(50) DEFAULT 'Pending'")
    if "refund_method" not in columns:
        cursor.execute("ALTER TABLE return_refund_requests ADD COLUMN refund_method VARCHAR(50) NULL")
    if "refund_note" not in columns:
        cursor.execute("ALTER TABLE return_refund_requests ADD COLUMN refund_note TEXT NULL")
