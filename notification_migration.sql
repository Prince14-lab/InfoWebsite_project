CREATE TABLE IF NOT EXISTS stock_notification_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    plant_id INT NOT NULL,
    notification_type VARCHAR(50) NOT NULL,
    stock_level INT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY unique_stock_notification (plant_id, notification_type, stock_level)
);

CREATE TABLE IF NOT EXISTS email_notification_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    notification_key VARCHAR(150) UNIQUE NOT NULL,
    recipient_email VARCHAR(150) NOT NULL,
    subject VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
