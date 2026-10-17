-- SQLite Schema for Receipt Graveyard

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('employee', 'admin')),
    employee_code TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    file_path TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK(source_type IN ('image', 'pdf')),
    category_user TEXT NOT NULL,
    category_model TEXT,
    context TEXT NOT NULL CHECK(context IN ('Trip', 'Office', 'trip', 'office')),
    attendees INTEGER,
    is_client_related INTEGER DEFAULT 0,
    client_name TEXT,
    merchant TEXT,
    city TEXT,
    country TEXT,
    tier TEXT,
    issue_date TEXT,
    unique_id TEXT,
    currency TEXT,
    amount_original REAL,
    fx_rate REAL,
    amount_base REAL,
    subtotal REAL,
    tax REAL,
    discount REAL,
    shipping REAL,
    nights INTEGER,
    items_json TEXT,
    conf_json TEXT,
    extraction_json TEXT,
    status TEXT NOT NULL CHECK(status IN ('info_needed', 'rejected', 'verified', 'flagged', 'approved', 'denied')),
    flags_json TEXT,
    missing_fields_json TEXT,
    dup_group_id TEXT,
    uploaded_at TEXT NOT NULL,
    is_seeded INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id INTEGER NOT NULL REFERENCES receipts(id),
    admin_id INTEGER NOT NULL REFERENCES users(id),
    decision TEXT NOT NULL CHECK(decision IN ('approved', 'denied')),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS queries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id INTEGER UNIQUE NOT NULL REFERENCES receipts(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    message TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('open', 'upheld', 'overturned')),
    admin_id INTEGER REFERENCES users(id),
    admin_response TEXT,
    resolution_reason TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS fx_rates (
    currency TEXT PRIMARY KEY,
    rate_to_inr REAL NOT NULL,
    set_date TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS extraction_cache (
    file_hash TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Seed initial settings if not exists
INSERT OR IGNORE INTO settings (key, value) VALUES
    ('BASE_CURRENCY', 'INR'),
    ('HELPLINE_NUMBER', '+91 800-422-4467'),
    ('CONF_THRESHOLD', '0.7'),
    ('BANNED_THRESHOLD', '0.7'),
    ('GROQ_MODEL', 'llama-3.2-11b-vision-preview');

-- Seed initial static fx_rates
INSERT OR IGNORE INTO fx_rates (currency, rate_to_inr, set_date) VALUES
    ('INR', 1.0, '2026-10-17'),
    ('MYR', 23.4, '2026-10-17'),
    ('USD', 96.5, '2026-10-17');
