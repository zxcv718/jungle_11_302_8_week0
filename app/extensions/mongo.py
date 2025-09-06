from pymongo import MongoClient
from pymongo.errors import ConfigurationError
client = None
_db = None
# Public alias so callers can use `mongo.db` (in addition to legacy `mongo._db`).
db = None
def init_mongo(app):
    global client, _db, db
    client = MongoClient(app.config["MONGO_URI"])  # SRV or standard URI
    try:
        db = client.get_default_database()
    except ConfigurationError:
        db = None
    _db = db if db is not None else client["login"]
    # Keep public alias in sync
    globals()["db"] = _db
    # Expose collections on app.extensions for easy import until repos added
    app.extensions["mongo"] = {
        "client": client,
        "db": _db,
        "users": _db["users"],
        "posts": _db["posts"],
        "comments": _db["comments"],
        "chat_rooms": _db["chat_rooms"],
        "chat_messages": _db["chat_messages"],
    }
    # Ensure indexes (best-effort)
    try:
        app.extensions["mongo"]["users"].create_index("email", unique=True)
        app.extensions["mongo"]["posts"].create_index([("user_id", 1), ("created_at", -1)])
        app.extensions["mongo"]["posts"].create_index([("created_at", -1), ("_id", -1)])
        app.extensions["mongo"]["posts"].create_index([("title", "text"), ("contents", "text")])
        app.extensions["mongo"]["chat_rooms"].create_index([("category", 1), ("name", 1)])
        app.extensions["mongo"]["chat_messages"].create_index([("room_id", 1), ("created_at", -1)])
        # notifications: user_id/read/_id for count and recent list
        try:
            _db["notifications"].create_index([("user_id", 1), ("read", 1), ("_id", -1)])
        except Exception:
            pass
    except Exception:
        # Don’t crash if index creation fails (e.g., permissions)
        pass