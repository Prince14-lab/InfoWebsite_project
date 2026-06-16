Use infomanagement_db;

Create Table users(
    id INT AUTO_INCREMENT PRIMARY KEY,
    fullname VARCHAR(100) NOT NULL,
    email VARCHAR(100) UNIQUE,
    username VARCHAR(50) UNIQUE NOT NULL,
    password VARCHAR(255) NOT NULL,
    account_type ENUM('admin', 'owner', 'customer', 'driver') NOT NULL DEFAULT 'customer',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO users (fullname, email, username, password, account_type)
VALUES ("Prince Maque Villanueva", "villanueva.pm.v.bscs@gmail.com", "owner123", "prince", "owner"),
("John Jorcel Ocampo", "ocampo.j.j.bscs@gmail.com", "admin123", "ocampo", "owner");

CREATE TABLE plants (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    category VARCHAR(50) NOT NULL,
    price DECIMAL(10,2) NOT NULL,
    stock INT NOT NULL DEFAULT 0,
    image_url VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO plants (name, category, price, stock, image_url)
VALUES
('Snake Plant', 'indoor', 220, 15, '/static/snakeplant.jpg'),
('Pothos', 'indoor', 180, 10, '/static/pothos.jpg'),
('Calamansi', 'fruit', 70, 22, '/static/calamasiplant.jpg'),
('Rosal', 'flowering', 200, 8, '/static/rosalplant.jpg'),
('African Talisay', 'outdoor', 500, 2, '/static/talisay.jpg');

ALTER TABLE users
ADD COLUMN phone VARCHAR(20) AFTER password,
ADD COLUMN address TEXT AFTER phone,
ADD COLUMN profile_photo VARCHAR(255) AFTER address;

ALTER TABLE plants
ADD COLUMN sold INT DEFAULT 0 AFTER stock;

ALTER TABLE plants
ADD COLUMN average_rating DECIMAL(3,2) DEFAULT 0 AFTER sold,
ADD COLUMN rating_count INT DEFAULT 0 AFTER average_rating;


CREATE TABLE cart (
    id INT AUTO_INCREMENT PRIMARY KEY,

    user_id INT NOT NULL,

    plant_id INT NOT NULL,

    quantity INT DEFAULT 1,

    size VARCHAR(20) DEFAULT 'small',

    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (plant_id)
    REFERENCES plants(id)
    ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS orders (
    id INT AUTO_INCREMENT PRIMARY KEY,
    order_code VARCHAR(30) UNIQUE,
    user_id INT NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'to_pay',
    payment_method VARCHAR(30) NOT NULL DEFAULT 'pending',
    payment_status VARCHAR(30) NOT NULL DEFAULT 'pending',
    subtotal DECIMAL(10,2) NOT NULL DEFAULT 0,
    delivery_fee DECIMAL(10,2) NOT NULL DEFAULT 0,
    total DECIMAL(10,2) NOT NULL DEFAULT 0,
    delivery_address TEXT,
    sold_recorded TINYINT(1) NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS order_items (
    id INT AUTO_INCREMENT PRIMARY KEY,
    order_id INT NOT NULL,
    plant_id INT NOT NULL,
    quantity INT NOT NULL DEFAULT 1,
    size VARCHAR(20) DEFAULT 'small',
    unit_price DECIMAL(10,2) NOT NULL DEFAULT 0,
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
    FOREIGN KEY (plant_id) REFERENCES plants(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS plant_reviews (
    id INT AUTO_INCREMENT PRIMARY KEY,
    plant_id INT NOT NULL,
    user_id INT NOT NULL,
    order_id INT NULL,
    order_item_id INT NULL,
    rating INT NOT NULL,
    comment TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (plant_id) REFERENCES plants(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS return_refund_requests (
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
);
