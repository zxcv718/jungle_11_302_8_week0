from datetime import datetime, timezone
from typing import Optional, Dict, Any
from bson import ObjectId

from ..extensions import mongo
from ..extensions.socketio import socketio


def _room_for_user(user_id: ObjectId) -> str:
    return f"user:{str(user_id)}"


def create_notification(
    *,
    recipient_id: ObjectId,
    ntype: str,  # 'subscribe' | 'post_like' | 'comment_like'
    actor_id: ObjectId,
    post_id: Optional[ObjectId] = None,
    comment_id: Optional[ObjectId] = None,
) -> Dict[str, Any]:
    doc: Dict[str, Any] = {
        "user_id": recipient_id,
        "type": ntype,
        "actor_id": actor_id,
        "post_id": post_id,
        "comment_id": comment_id,
        "created_at": datetime.now(timezone.utc),
        "read": False,
    }
    res = mongo._db["notifications"].insert_one(doc)
    doc["_id"] = res.inserted_id
    # Emit to recipient room
    try:
        payload = serialize_notification(doc)
        socketio.emit("notify", payload, to=_room_for_user(recipient_id))
    except Exception:
        pass
    return doc


def serialize_notification(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Return a lean JSON payload for clients. Resolves actor name lazily."""
    actor_name = "누군가"
    try:
        actor = mongo._db["users"].find_one({"_id": doc.get("actor_id")}, {"name": 1})
        if actor and actor.get("name"):
            actor_name = actor["name"]
    except Exception:
        pass
    ntype = doc.get("type")
    if ntype == "subscribe":
        text = f"{actor_name}님이 나를 구독하였습니다."
    elif ntype == "post_like":
        text = f"{actor_name}님이 내 글을 추천했습니다."
    elif ntype == "comment_like":
        text = f"{actor_name}님이 내 댓글을 추천했습니다."
    else:
        text = f"{actor_name}님의 활동 알림"
    return {
        "id": str(doc.get("_id")),
        "type": ntype,
        "actor_name": actor_name,
        "text": text,
        "post_id": str(doc.get("post_id")) if doc.get("post_id") else None,
        "comment_id": str(doc.get("comment_id")) if doc.get("comment_id") else None,
        "read": bool(doc.get("read", False)),
        "created_at": (doc.get("created_at") or datetime.now(timezone.utc)).isoformat(),
    }
