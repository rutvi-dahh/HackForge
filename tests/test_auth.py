import pytest
import sqlite3
from app import app
from database.db import init_db, get_db
from services.auth import hash_password

@pytest.fixture
def client(tmp_path):
    test_db_path = tmp_path / "test_app.db"
    init_db(str(test_db_path))

    # Add demo accounts
    conn = sqlite3.connect(str(test_db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """INSERT INTO users (id, name, email, password_hash, role, employee_code) VALUES
        (1, 'Admin User', 'admin@company.com', ?, 'admin', 'ADM01'),
        (2, 'Alex Rivera', 'e1@company.com', ?, 'employee', 'E1')""",
        (hash_password("admin123"), hash_password("emp123"))
    )
    conn.commit()
    conn.close()

    app.config["TESTING"] = True
    app.config["DATABASE_PATH"] = str(test_db_path)
    
    with app.test_client() as client:
        yield client

def test_login_and_logout(client):
    # Failed login
    res = client.post("/login", data={"email": "admin@company.com", "password": "wrong"})
    assert res.status_code == 200
    assert b"Invalid email or password" in res.data

    # Successful employee login
    res = client.post("/login", data={"email": "e1@company.com", "password": "emp123"}, follow_redirects=True)
    assert res.status_code == 200
    assert b"My Expense Claims" in res.data

    # Logout
    res_out = client.get("/logout", follow_redirects=True)
    assert res_out.status_code == 200
    assert b"Sign In" in res_out.data

def test_role_security_employee_gets_403_on_admin_routes(client):
    # Log in as employee E1
    client.post("/login", data={"email": "e1@company.com", "password": "emp123"}, follow_redirects=True)

    # Employee tries to access admin queue -> 403 Forbidden!
    res_queue = client.get("/admin")
    assert res_queue.status_code == 403

    # Employee tries to access admin settings -> 403 Forbidden!
    res_settings = client.get("/admin/settings")
    assert res_settings.status_code == 403

    # Employee tries to access rejected queries tab -> 403 Forbidden!
    res_rejected = client.get("/admin/rejected")
    assert res_rejected.status_code == 403

def test_admin_can_access_admin_routes(client):
    # Log in as admin
    client.post("/login", data={"email": "admin@company.com", "password": "admin123"}, follow_redirects=True)

    res_queue = client.get("/admin")
    assert res_queue.status_code == 200
    assert b"Expense Review Queue" in res_queue.data

    res_settings = client.get("/admin/settings")
    assert res_settings.status_code == 200
    assert b"General System Settings" in res_settings.data

    res_rejected = client.get("/admin/rejected")
    assert res_rejected.status_code == 200
    assert b"Rejected Claims & Employee Queries" in res_rejected.data
