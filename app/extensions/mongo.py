from pymongo import MongoClient
from pymongo.errors import ConfigurationError

client = None
db = None


def init_mongo(app):
    global client, db
    client = MongoClient(app.config["MONGO_URI"])  # SRV or standard URI
    try:
        _db_temp = client.get_default_database()
    except ConfigurationError:
        _db_temp = None
    db = _db_temp if _db_temp is not None else client["login"]
    # Expose collections on app.extensions for easy import until repos added
    app.extensions["mongo"] = {
        "client": client,
        "db": db,
        "users": db["users"],
        "posts": db["posts"],
        "comments": db["comments"],
        "chat_rooms": db["chat_rooms"],
        "chat_messages": db["chat_messages"],
    }
    # Ensure indexes (best-effort)
    try:
        app.extensions["mongo"]["users"].create_index("email", unique=True)
        app.extensions["mongo"]["posts"].create_index([("user_id", 1), ("created_at", -1)])
        app.extensions["mongo"]["posts"].create_index([("title", "text"), ("contents", "text")])
        app.extensions["mongo"]["chat_rooms"].create_index([("category", 1), ("name", 1)])
        app.extensions["mongo"]["chat_messages"].create_index([("room_id", 1), ("created_at", -1)])
    except Exception:
        # Don't crash if index creation fails (e.g., permissions)
        pass
