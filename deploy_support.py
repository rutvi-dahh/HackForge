"""Deployment helpers. Call configure_for_deploy(app) once, at the end of app.py (module level)."""
import os
from werkzeug.middleware.proxy_fix import ProxyFix


def configure_for_deploy(app):
    # Never run the interactive debugger in a deployed app
    app.debug = False

    # Render sits behind a proxy: trust its forwarded headers so https and client IP work
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # Secret key from the environment
    if os.environ.get("SECRET_KEY"):
        app.secret_key = os.environ["SECRET_KEY"]

    # Safe cookies (secure flag only when served over https on the host)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    if os.environ.get("RENDER") or os.environ.get("COOKIE_SECURE") == "1":
        app.config["SESSION_COOKIE_SECURE"] = True

    # Health check for Render (no login, no database writes)
    if "healthz" not in app.view_functions:
        @app.route("/healthz")
        def healthz():
            return "ok", 200

    return app
