from flask import Flask, request
import os
from .extensions.socketio import socketio
from .extensions.jwt import jwt
from .extensions.mongo import init_mongo
from .extensions.mail import mail
from .blueprints.auth import bp as auth_bp
from .blueprints.home import bp as home_bp
from .blueprints.posts import bp as posts_bp
from .blueprints.comments import bp as comments_bp
from .blueprints.chat import bp as chat_bp
from .blueprints.notifications import bp as notifications_bp
from .blueprints.mypage import mypage_bp


def create_app(config_object: str = "config.Config") -> Flask:
    # templates/static are located at project root (../templates, ../static)
    base_dir = os.path.dirname(__file__)
    templates_dir = os.path.abspath(os.path.join(base_dir, "..", "templates"))
    static_dir = os.path.abspath(os.path.join(base_dir, "..", "static"))
    app = Flask(__name__, template_folder=templates_dir, static_folder=static_dir)
    app.config.from_object(config_object)

    # Extensions
    jwt.init_app(app)
    socketio.init_app(app, cors_allowed_origins=None, async_mode="threading", logger=False, engineio_logger=False)
    init_mongo(app)
    mail.init_app(app)

    # Blueprints
    app.register_blueprint(home_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(posts_bp)
    app.register_blueprint(comments_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(mypage_bp)
    app.register_blueprint(notifications_bp)

    # Prevent caching of dynamic pages so back button doesn't reveal protected content after logout
    @app.after_request
    def add_no_cache_headers(resp):
        try:
            p = request.path or ""
            # allow static assets to be cached normally
            if p.startswith("/static/"):
                return resp
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
            resp.headers["Pragma"] = "no-cache"
            resp.headers["Expires"] = "0"
        except Exception:
            pass
        return resp

    return app
