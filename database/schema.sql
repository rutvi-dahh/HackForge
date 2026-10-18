-- SQLite Schema for Receipt Graveyard (SPEC s6 v2)

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    email           TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    role            TEXT NOT NULL CHECK(role IN ('employee', 'admin')),
    employee_code   TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS receipts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER NOT NULL REFERENCES users(id),
    file_path           TEXT NOT NULL,
    file_hash           TEXT NOT NULL,
    source_type         TEXT NOT NULL CHECK(source_type IN ('image', 'pdf')),
    -- Employee-supplied fields
    category_user       TEXT NOT NULL,
    claimed_amount      REAL,           -- typed by employee; reimbursable = accepted_amount
    claimed_invoice_no  TEXT,           -- optional
    context             TEXT NOT NULL CHECK(context IN ('Trip', 'Office', 'trip', 'office')),
    attendees           INTEGER,
    is_client_related   INTEGER DEFAULT 0,
    client_name         TEXT,
    -- Extracted fields
    category_model      TEXT,
    merchant            TEXT,
    city                TEXT,
    country             TEXT,
    tier                TEXT,
    issue_date          TEXT,
    unique_id           TEXT,
    currency            TEXT,
    amount_original     REAL,           -- receipt total in original currency (accepted value)
    accepted_amount     REAL,           -- same as amount_original after voting
    amount_source       TEXT DEFAULT 'model' CHECK(amount_source IN ('model', 'ocr', 'user')),
    fx_rate             REAL,
    fx_rate_date        TEXT,
    amount_base         REAL,           -- amount_original * fx_rate
    subtotal            REAL,
    tax                 REAL,
    discount            REAL,
    shipping            REAL,
    nights              INTEGER,
    -- OCR reader 2 outputs
    ocr_amount          REAL,
    ocr_invoice_no      TEXT,
    ocr_text            TEXT,           -- masked card numbers
    -- JSON blobs
    items_json          TEXT,
    conf_json           TEXT,
    extraction_json     TEXT,
    -- Status & flags
    status              TEXT NOT NULL CHECK(status IN ('info_needed','rejected','verified','flagged','approved','denied')),
    flags_json          TEXT,
    missing_fields_json TEXT,
    dup_group_id        TEXT,
    uploaded_at         TEXT NOT NULL,
    is_seeded           INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id  INTEGER NOT NULL REFERENCES receipts(id),
    admin_id    INTEGER NOT NULL REFERENCES users(id),
    decision    TEXT NOT NULL CHECK(decision IN ('approved', 'denied')),
    reason      TEXT,       -- required when denied, null allowed when approved
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS queries (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id          INTEGER UNIQUE NOT NULL REFERENCES receipts(id),
    user_id             INTEGER NOT NULL REFERENCES users(id),
    message             TEXT NOT NULL,
    status              TEXT NOT NULL CHECK(status IN ('open', 'upheld', 'overturned')),
    admin_id            INTEGER REFERENCES users(id),
    admin_response      TEXT,
    resolution_reason   TEXT,       -- required when upheld
    created_at          TEXT NOT NULL,
    resolved_at         TEXT
);

CREATE TABLE IF NOT EXISTS fx_rates (
    currency    TEXT NOT NULL,
    base        TEXT NOT NULL DEFAULT 'INR',    -- base currency the rate is relative to
    rate_to_base REAL NOT NULL,                 -- units of `currency` per 1 `base`
    fetched_at  TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'api',
    PRIMARY KEY (currency, base)
);

CREATE TABLE IF NOT EXISTS city_tiers (
    city_key        TEXT PRIMARY KEY,   -- lowercase(city + '|' + country)
    canonical_city  TEXT NOT NULL,
    country         TEXT,
    tier            TEXT NOT NULL CHECK(tier IN ('A', 'B', 'C')),
    source          TEXT NOT NULL CHECK(source IN ('model', 'admin')) DEFAULT 'model',
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS extraction_cache (
    file_hash   TEXT PRIMARY KEY,
    model       TEXT NOT NULL,
    json        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

-- Default settings
INSERT OR IGNORE INTO settings (key, value) VALUES
    ('BASE_CURRENCY',      'INR'),
    ('HELPLINE_NUMBER',    'TBD'),
    ('CONF_THRESHOLD',     '0.7'),
    ('BANNED_THRESHOLD',   '0.7'),
    ('CITY_CONF_THRESHOLD','0.6');

-- First-run FX fallback (INR base; SPEC s5 / POLICY.md s7)
INSERT OR IGNORE INTO fx_rates (currency, base, rate_to_base, fetched_at, source) VALUES
    ('INR', 'INR', 1.0,  '2026-10-17', 'fallback'),
    ('MYR', 'INR', 23.4, '2026-10-17', 'fallback'),
    ('USD', 'INR', 96.5, '2026-10-17', 'fallback');

-- Initial model-classified guidance tiers (POLICY.md s8 / SPEC s5)
INSERT OR IGNORE INTO city_tiers (city_key, canonical_city, country, tier, source, created_at) VALUES
    ('bengaluru|india', 'Bengaluru', 'India', 'A', 'model', '2026-10-17'),
    ('bengaluru|', 'Bengaluru', 'India', 'A', 'model', '2026-10-17'),
    ('bangalore|india', 'Bengaluru', 'India', 'A', 'model', '2026-10-17'),
    ('bangalore|', 'Bengaluru', 'India', 'A', 'model', '2026-10-17'),
    ('mumbai|india', 'Mumbai', 'India', 'A', 'model', '2026-10-17'),
    ('mumbai|', 'Mumbai', 'India', 'A', 'model', '2026-10-17'),
    ('delhi|india', 'Delhi NCR', 'India', 'A', 'model', '2026-10-17'),
    ('delhi ncr|india', 'Delhi NCR', 'India', 'A', 'model', '2026-10-17'),
    ('gurgaon|india', 'Delhi NCR', 'India', 'A', 'model', '2026-10-17'),
    ('gurgaon|', 'Delhi NCR', 'India', 'A', 'model', '2026-10-17'),
    ('new york|usa', 'New York', 'USA', 'A', 'model', '2026-10-17'),
    ('new york|', 'New York', 'USA', 'A', 'model', '2026-10-17'),
    ('london|uk', 'London', 'UK', 'A', 'model', '2026-10-17'),
    ('singapore|singapore', 'Singapore', 'Singapore', 'A', 'model', '2026-10-17'),
    ('jaipur|india', 'Jaipur', 'India', 'B', 'model', '2026-10-17'),
    ('lucknow|india', 'Lucknow', 'India', 'B', 'model', '2026-10-17'),
    ('kochi|india', 'Kochi', 'India', 'B', 'model', '2026-10-17'),
    ('los angeles|usa', 'Los Angeles', 'USA', 'B', 'model', '2026-10-17'),
    ('johor bahru|malaysia', 'Johor Bahru', 'Malaysia', 'B', 'model', '2026-10-17');
