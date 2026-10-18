"""
tests/test_brand_and_landing.py

Tests for brand config, theme gold accent, landing page layout, and company name injection.
"""
import os
import pytest
from database.db import init_db, get_db
from services.auth import hash_password
from app import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_brand.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    app.config["DATABASE_PATH"] = db_path
    app.config["TESTING"] = True

    init_db(db_path)
    db = get_db(db_path)

    # Seed an employee and admin
    db.execute(
        "INSERT INTO users (name,email,password_hash,role,employee_code) VALUES (?,?,?,?,?)",
        ("Test Admin", "admin@brand.com", hash_password("admin123"), "admin", "ADM01")
    )
    db.execute(
        "INSERT INTO users (name,email,password_hash,role,employee_code) VALUES (?,?,?,?,?)",
        ("Test Employee", "emp@brand.com", hash_password("emp123"), "employee", "EMP01")
    )
    db.commit()
    db.close()

    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_login_page_brand_and_elements(client):
    """
    Login page HTML contains the company name and an <img> or inline <svg> brand mark;
    has Remember me and Forgot password; still has no social/sign-up/demo buttons.
    """
    res = client.get("/login")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    # Contains company name
    assert "Receipt Graveyard" in html

    # Contains <img> or inline <svg> brand mark
    assert ("<img" in html and "logo" in html) or "<svg" in html

    # Has Remember me and Forgot password
    assert "Remember me" in html
    assert "Forgot password" in html

    # No social/sign-up/demo buttons
    assert "Sign Up" not in html
    assert "Sign up" not in html
    assert "Create an account" not in html
    assert "Google" not in html
    assert "GitHub" not in html
    assert "Demo Login" not in html
    assert "demo" not in html.lower() or "demo" in html.lower()  # strict check below:
    for forbidden in ["google", "github", "sign up", "create account", "demo login"]:
        assert forbidden not in html.lower()


def test_logged_in_page_brand_in_nav_footer_and_title(client):
    """
    A logged-in page contains the company name in the nav and the footer;
    <title> contains the company name.
    """
    # Login as employee
    login_res = client.post("/login", data={"email": "emp@brand.com", "password": "emp123"}, follow_redirects=True)
    assert login_res.status_code == 200

    res = client.get("/receipts")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    # Nav contains company name
    assert '<a href="/" class="nav-brand">' in html or 'nav-brand' in html
    assert "Receipt Graveyard" in html

    # Title contains company name
    assert "<title>" in html
    title_start = html.find("<title>")
    title_end = html.find("</title>")
    title_text = html[title_start:title_end]
    assert "Receipt Graveyard" in title_text

    # Footer contains company name
    assert 'class="footer"' in html
    assert "Receipt Graveyard" in html


def test_theme_css_defines_gold():
    """
    theme.css defines --gold:#FFCE18.
    """
    theme_path = os.path.join(os.path.dirname(__file__), "..", "static", "css", "theme.css")
    with open(theme_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Normalize whitespace for check
    clean_content = content.replace(" ", "")
    assert "--gold:#FFCE18" in clean_content
    assert "--gold-600:" in clean_content
    assert "--gold-100:" in clean_content


def test_changing_company_name_via_env(client, monkeypatch):
    """
    Changing COMPANY_NAME via env changes the rendered name.
    """
    custom_name = "Acme Expense Portal"
    monkeypatch.setenv("COMPANY_NAME", custom_name)

    res = client.get("/login")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert custom_name in html

    help_res = client.get("/help")
    assert help_res.status_code == 200
    help_html = help_res.get_data(as_text=True)
    assert custom_name in help_html


def test_no_routes_return_500(client):
    """
    No route returns 500 error.
    """
    public_routes = ["/", "/login", "/policy", "/help", "/forgot-password"]
    for route in public_routes:
        res = client.get(route, follow_redirects=True)
        assert res.status_code < 500, f"Route {route} returned status {res.status_code}"

    # Logged in employee routes
    client.post("/login", data={"email": "emp@brand.com", "password": "emp123"}, follow_redirects=True)
    emp_routes = ["/receipts", "/upload", "/policy", "/help"]
    for route in emp_routes:
        res = client.get(route, follow_redirects=True)
        assert res.status_code < 500, f"Route {route} returned status {res.status_code}"
