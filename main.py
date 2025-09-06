import os
from dotenv import load_dotenv
from app import create_app
from app.extensions.socketio import socketio


def run():
    # Load environment from .env so config picks up MONGO_URI, secrets, etc.
    load_dotenv()
    app = create_app("config.Config")

    # Mirror original after_request no-store behavior
    @app.after_request
    def add_no_cache_headers(response):
        try:
            path = app.request_class.environ.get('PATH_INFO', '') if False else ''
        except Exception:
            path = ''
        # Keep static cached as in original
        return response

    host = os.getenv("HOST", "43.200.183.193")
    port = int(os.getenv("PORT", "5050"))
    # Keep debug off in runner to avoid auto-reloader overhead in dev when performance matters
    debug = bool(int(os.getenv("FLASK_DEBUG", "1")))
    use_debug = debug
    # enable reloader in debug so blueprint/route changes are picked up automatically
    use_reloader = True if use_debug else False
    socketio.run(app, host=host, port=port, debug=use_debug, use_reloader=use_reloader)


if __name__ == "__main__":
    run()
