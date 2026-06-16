ALTER TABLE users
MODIFY account_type ENUM('admin','owner','customer','driver') NOT NULL DEFAULT 'customer';
