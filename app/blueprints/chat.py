from flask import Blueprint, render_template, abort, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt, get_jwt_identity, verify_jwt_in_request
from bson import ObjectId
from datetime import datetime, timezone

from ..extensions.socketio import socketio
from ..extensions import mongo

bp = Blueprint("chat", __name__)


def get_categories():
    return [
        "프로그래밍언어","자료구조","알고리즘","컴퓨터구조","운영체제",
        "시스템프로그래밍","데이터베이스","AI","보안","네트워크","기타"
    ]


@bp.get("/chat")
@jwt_required()
def chat_home():
    return render_template("chat_home.html", title="카테고리 채팅", hide_top_nav=True)


@bp.get("/chat/<category>")
@jwt_required()
def chat_category(category):
    if category not in get_categories():
        abort(404)
    return render_template("chat_category.html", title=f"{category} 채팅", category=category, hide_top_nav=True)


@bp.get("/chat/<category>/<room_id>")
@jwt_required()
def chat_room(category, room_id):
    if category not in get_categories():
        abort(404)
    room = mongo._db["chat_rooms"].find_one({"_id": ObjectId(room_id), "category": category})
    if not room:
        abort(404)
    ident = get_jwt_identity()
    user = mongo._db["users"].find_one({"_id": ObjectId(ident)})
    name = user.get("name") if user else "익명"
    return render_template("chat_room.html", title=f"{room.get('name')} - {category}", category=category, room={"id": room_id, "name": room.get("name")}, me_name=name, me_id=str(ident), hide_top_nav=True)


# APIs
@bp.get("/api/chat/<category>/rooms")
@jwt_required()
def api_list_rooms(category):
    if category not in get_categories():
        return jsonify({"rooms": []}), 200
    cur = mongo._db["chat_rooms"].find({"category": category}).sort([("_id", -1)])
    out = []
    for doc in cur:
        rid = str(doc.get("_id"))
        out.append({"id": rid, "name": doc.get("name", ""), "peers": online_counts.get(rid, 0)})
    return jsonify({"rooms": out}), 200


@bp.post("/api/chat/<category>/rooms")
@jwt_required()
def api_create_room(category):
    if category not in get_categories():
        return jsonify({"error": "bad_category"}), 400
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name_required"}), 400
    doc = {"category": category, "name": name, "created_at": datetime.now(timezone.utc)}
    res = mongo._db["chat_rooms"].insert_one(doc)
    return jsonify({"id": str(res.inserted_id)}), 200


@bp.get("/api/chat/<category>/<room_id>/messages")
@jwt_required()
def api_room_messages(category, room_id):
    if category not in get_categories():
        return jsonify({"messages": []}), 200
    try:
        rid = ObjectId(room_id)
    except Exception:
        return jsonify({"messages": []}), 200
    try:
        limit = min(200, max(1, int(request.args.get("limit", 50))))
    except ValueError:
        limit = 50
    cur = mongo._db["chat_messages"].find({"room_id": rid}).sort([("created_at", -1)]).limit(limit)
    items = []
    for m in cur:
        items.append({
            "id": str(m.get("_id")),
            "user_id": str(m.get("user_id")) if m.get("user_id") else None,
            "name": m.get("name"),
            "text": m.get("text"),
            "ts": (m.get("created_at") or datetime.now(timezone.utc)).isoformat(),
        })
    items.reverse()
    return jsonify({"messages": items}), 200


@bp.get("/api/chat/<category>/<room_id>/peers")
@jwt_required()
def api_room_peers(category, room_id):
    try:
        _ = ObjectId(room_id)
    except Exception:
        return jsonify({"peers": 0}), 200
    peers = online_counts.get(room_id, 0)
    return jsonify({"peers": peers}), 200


@bp.post("/api/chat/<category>/<room_id>/send")
@jwt_required()
def api_send_message(category, room_id):
    if category not in get_categories():
        return jsonify({"error": "bad_category"}), 400
    try:
        rid_obj = ObjectId(room_id)
    except Exception:
        return jsonify({"error": "bad_room"}), 400
    if not mongo._db["chat_rooms"].find_one({"_id": rid_obj, "category": category}):
        return jsonify({"error": "not_found"}), 404
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    cid = (data.get("cid") or "").strip() or None
    if not text:
        return jsonify({"error": "text_required"}), 400
    from bson import ObjectId as OID
    uid = OID(get_jwt_identity())
    j = get_jwt()
    name = j.get("name") or "익명"
    created = datetime.now(timezone.utc)
    doc = {"room_id": rid_obj, "user_id": uid, "name": name, "text": text, "created_at": created}
    try:
        res = mongo._db["chat_messages"].insert_one(doc)
        mid = str(res.inserted_id)
    except Exception:
        mid = None
    try:
        # Use shared SocketIO instance for consistency
        socketio.emit("new_message", {
            "id": mid or "temp-" + str(int(created.timestamp()*1000)),
            "room_id": room_id,
            "user_id": str(uid),
            "name": name,
            "text": text,
            "ts": created.isoformat(),
            "cid": cid,
        }, to=room_id)
    except Exception:
        pass
    return jsonify({"id": mid, "ts": created.isoformat(), "name": name, "text": text, "cid": cid, "user_id": str(uid)}), 200


# Socket state
online_counts = {}
sid_rooms = {}


@socketio.on("connect")
def ws_connect():
    try:
        verify_jwt_in_request(optional=False)
        uid = get_jwt_identity()
    except Exception:
        return False
    sid_rooms[request.sid] = set()


@socketio.on("disconnect")
def ws_disconnect():
    from flask_socketio import emit
    rooms = sid_rooms.pop(request.sid, set())
    for rid in rooms:
        online_counts[rid] = max(0, online_counts.get(rid, 1) - 1)
        emit("room_peers", {"room_id": rid, "peers": online_counts.get(rid, 0)}, to=rid)


@socketio.on("join")
def ws_join(data):
    try:
        verify_jwt_in_request(optional=False)
    except Exception:
        return
    rid = (data or {}).get("room_id")
    if not rid:
        return
    try:
        room_doc = mongo._db["chat_rooms"].find_one({"_id": ObjectId(rid)})
    except Exception:
        room_doc = None
    if not room_doc:
        return
    from flask_socketio import join_room, emit
    join_room(rid)
    sid_rooms.setdefault(request.sid, set()).add(rid)
    online_counts[rid] = online_counts.get(rid, 0) + 1
    emit("room_peers", {"room_id": rid, "peers": online_counts[rid]}, to=rid)
    emit("joined", {"room_id": rid, "peers": online_counts[rid]}, to=request.sid)


@socketio.on("leave")
def ws_leave(data):
    from flask_socketio import leave_room, emit
    rid = (data or {}).get("room_id")
    if not rid:
        return
    leave_room(rid)
    if request.sid in sid_rooms and rid in sid_rooms[request.sid]:
        sid_rooms[request.sid].remove(rid)
        online_counts[rid] = max(0, online_counts.get(rid, 1) - 1)
        emit("room_peers", {"room_id": rid, "peers": online_counts.get(rid, 0)}, to=rid)


@socketio.on("send_message")
def ws_send_message(data):
    """WebSocket: send a message to a room, persist to DB, and broadcast."""
    try:
        verify_jwt_in_request(optional=False)
    except Exception:
        return
    payload = data or {}
    rid = (payload.get("room_id") or "").strip()
    text = (payload.get("text") or "").strip()
    cid = (payload.get("cid") or "").strip() or None
    if not rid or not text:
        return
    # Validate room
    try:
        rid_obj = ObjectId(rid)
    except Exception:
        return
    if not mongo._db["chat_rooms"].find_one({"_id": rid_obj}):
        return
    # Build message
    uid = ObjectId(get_jwt_identity())
    j = get_jwt()
    name = j.get("name") or "익명"
    created = datetime.now(timezone.utc)
    doc = {"room_id": rid_obj, "user_id": uid, "name": name, "text": text, "created_at": created}
    try:
        res = mongo._db["chat_messages"].insert_one(doc)
        mid = str(res.inserted_id)
    except Exception:
        mid = None
    # Broadcast to room
    try:
        socketio.emit("new_message", {
            "id": mid or "temp-" + str(int(created.timestamp()*1000)),
            "room_id": rid,
            "user_id": str(uid),
            "name": name,
            "text": text,
            "ts": created.isoformat(),
            "cid": cid,
        }, to=rid)
        # Optional: ACK back to sender
        from flask_socketio import emit as ws_emit
        ws_emit("send_ack", {"id": mid or None})
    except Exception:
        pass
