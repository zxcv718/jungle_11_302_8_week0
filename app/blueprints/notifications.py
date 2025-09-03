from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity
from bson import ObjectId

from ..extensions import mongo
from ..services.notifications import serialize_notification


bp = Blueprint("notifications", __name__)


@bp.get("/api/notifications/count")
@jwt_required()
def api_notifications_count():
    uid = ObjectId(get_jwt_identity())
    cnt = mongo._db["notifications"].count_documents({"user_id": uid, "read": False})
    return jsonify({"ok": True, "count": int(cnt)})


@bp.get("/api/notifications/list")
@jwt_required()
def api_notifications_list():
    uid = ObjectId(get_jwt_identity())
    try:
        limit = min(50, max(1, int(request.args.get("limit", 10))))
    except Exception:
        limit = 10
    cur = mongo._db["notifications"].find({"user_id": uid}).sort([("_id", -1)]).limit(limit)
    items = [serialize_notification(doc) for doc in cur]
    return jsonify({"ok": True, "items": items})


@bp.post("/api/notifications/read")
@jwt_required()
def api_notifications_mark_read():
    uid = ObjectId(get_jwt_identity())
    ids = request.json.get("ids") if request.is_json else None
    if not ids:
        # mark all as read
        mongo._db["notifications"].update_many({"user_id": uid, "read": False}, {"$set": {"read": True}})
        return jsonify({"ok": True})
    oids = []
    for s in ids:
        try:
            oids.append(ObjectId(s))
        except Exception:
            pass
    if oids:
        mongo._db["notifications"].update_many({"_id": {"$in": oids}, "user_id": uid}, {"$set": {"read": True}})
    return jsonify({"ok": True})
