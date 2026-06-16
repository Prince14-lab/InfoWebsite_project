ALTER TABLE users
MODIFY account_type ENUM('admin','owner','customer','driver') NOT NULL DEFAULT 'customer';

CREATE TABLE IF NOT EXISTS order_tracking (
    id INT AUTO_INCREMENT PRIMARY KEY,
    order_id INT NOT NULL,
    tracking_status VARCHAR(100) NOT NULL,
    location VARCHAR(255) NOT NULL,
    note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
);

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
);

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
);

ALTER TABLE order_driver_assignments
ADD COLUMN IF NOT EXISTS assignment_type VARCHAR(30) NOT NULL DEFAULT 'delivery',
ADD COLUMN IF NOT EXISTS cancelled_at DATETIME NULL,
ADD COLUMN IF NOT EXISTS cancelled_by INT NULL,
ADD COLUMN IF NOT EXISTS cancel_reason TEXT NULL;

ALTER TABLE return_refund_requests
ADD COLUMN IF NOT EXISTS pickup_status VARCHAR(50) DEFAULT 'Pending Pickup',
ADD COLUMN IF NOT EXISTS pickup_driver_id INT NULL,
ADD COLUMN IF NOT EXISTS pickup_assigned_at DATETIME NULL,
ADD COLUMN IF NOT EXISTS item_received_at DATETIME NULL,
ADD COLUMN IF NOT EXISTS refund_status VARCHAR(50) DEFAULT 'Pending',
ADD COLUMN IF NOT EXISTS refund_method VARCHAR(50) NULL,
ADD COLUMN IF NOT EXISTS refund_note TEXT NULL;
