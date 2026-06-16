-- PayMongo checkout support for Green Nursery orders.
-- Run only the ALTER statements for columns that are missing in your database.

ALTER TABLE orders ADD COLUMN paymongo_checkout_id VARCHAR(100) NULL;
ALTER TABLE orders ADD COLUMN paymongo_payment_id VARCHAR(100) NULL;
ALTER TABLE orders ADD COLUMN payment_reference VARCHAR(100) NULL;
ALTER TABLE orders ADD COLUMN receipt_no VARCHAR(50) UNIQUE NULL;
ALTER TABLE orders ADD COLUMN paid_at DATETIME NULL;

-- If these columns are ENUM in your database, make sure the values below are included.
-- If they are VARCHAR columns, no ENUM change is needed.
-- order_status: To Pay, Preparing, Packed, Out for Delivery, Delivered, Cancelled
-- payment_method: Cash on Delivery, PayMongo GCash, PayMongo Card
-- payment_status: Pending, Paid, Failed
