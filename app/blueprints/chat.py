from flask import Blueprint, render_template, abort, jsonify, request, current_app, url_for, Response, send_file
from flask_jwt_extended import jwt_required, get_jwt, get_jwt_identity, verify_jwt_in_request
from bson import ObjectId
from datetime import datetime, timezone
import os
import re
import mimetypes
from uuid import uuid4
from werkzeug.utils import secure_filename

from ..extensions.socketio import socketio
from ..extensions import mongo
from metadata import fetch_and_extract_metadata, normalize_url
from ..services.notifications import _room_for_user

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
        pm = m.get("preview") or None
        items.append({
            "id": str(m.get("_id")),
            "user_id": str(m.get("user_id")) if m.get("user_id") else None,
            "name": m.get("name"),
            "text": m.get("text"),
            "ts": (m.get("created_at") or datetime.now(timezone.utc)).isoformat(),
            "preview": pm,
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
    # Build preview metadata best-effort
    preview = None
    try:
        m = re.search(r"https?://[^\s]+", text)
        if m:
            raw = m.group(0)
            url, err = normalize_url(raw)
            if url and not err:
                meta = fetch_and_extract_metadata(url)
                ct = (meta.content_type or "").lower()
                if "application/pdf" not in ct and "application/octet-stream" not in ct:
                    ok = bool((meta.title and meta.title.strip()) or (meta.description and meta.description.strip()) or (meta.image and meta.image.strip()))
                    if ok:
                        preview = {
                            "ok": True,
                            "title": meta.title,
                            "description": meta.description,
                            "image": meta.image,
                            "url": meta.url,
                            "content_type": meta.content_type,
                        }
    except Exception:
        preview = None
    doc = {"room_id": rid_obj, "user_id": uid, "name": name, "text": text, "created_at": created}
    if preview:
        doc["preview"] = preview
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
            "preview": preview,
        }, to=room_id)
    except Exception:
        pass
    return jsonify({"id": mid, "ts": created.isoformat(), "name": name, "text": text, "cid": cid, "user_id": str(uid), "preview": preview}), 200


# Socket state
online_counts = {}
sid_rooms = {}
sid_names = {}


@socketio.on("connect")
def ws_connect():
    # Allow connection even if JWT cookies are not visible in WS handshake; we'll validate on send if possible.
    try:
        verify_jwt_in_request(optional=True)
    except Exception:
        pass
    sid_rooms[request.sid] = set()
    sid_names[request.sid] = "익명"

    # If user is authenticated, auto-join their personal notification room for real-time alerts
    try:
        ident = get_jwt_identity()
        if ident:
            from flask_socketio import join_room
            join_room(_room_for_user(ObjectId(ident)))
    except Exception:
        pass


@socketio.on("disconnect")
def ws_disconnect():
    from flask_socketio import emit
    rooms = sid_rooms.pop(request.sid, set())
    name = sid_names.pop(request.sid, "누군가")
    for rid in rooms:
        online_counts[rid] = max(0, online_counts.get(rid, 1) - 1)
        emit("room_peers", {"room_id": rid, "peers": online_counts.get(rid, 0)}, to=rid)
        # Broadcast leave system message
        try:
            text = f"{name}님이 퇴장하셨습니다."
            emit("new_message", {
                "room_id": rid,
                "name": "알림",
                "text": text,
                "ts": datetime.now(timezone.utc).isoformat(),
                "user_id": None,
                "id": None,
                "cid": None,
                "preview": None,
            }, to=rid, skip_sid=request.sid)
            emit("system_notice", {"room_id": rid, "text": text}, to=rid, skip_sid=request.sid)
        except Exception:
            pass
    # No need to explicitly leave personal room; it's implicit on disconnect


@socketio.on("join")
def ws_join(data):
    # Do not hard-fail on missing JWT to avoid room join issues due to cookie/samesite differences.
    try:
        verify_jwt_in_request(optional=True)
    except Exception:
        pass
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
    # Resolve display name: JWT -> payload.me_name -> default
    disp_name = "익명"
    try:
        j = get_jwt()
        dn = (j.get("name") or "").strip()
        if dn:
            disp_name = dn
    except Exception:
        pass
    if disp_name == "익명":
        try:
            pn = ((data or {}).get("me_name") or "").strip()
            if pn:
                disp_name = pn
        except Exception:
            pass
    sid_names[request.sid] = disp_name

    online_counts[rid] = online_counts.get(rid, 0) + 1
    emit("room_peers", {"room_id": rid, "peers": online_counts[rid]}, to=rid)
    emit("joined", {"room_id": rid, "peers": online_counts[rid]}, to=request.sid)
    # Broadcast join system message
    try:
        text = f"{disp_name}님이 입장하셨습니다."
        sys_msg = {
            "room_id": rid,
            "name": "알림",
            "text": text,
            "ts": datetime.now(timezone.utc).isoformat(),
            "user_id": None,
            "id": None,
            "cid": None,
            "preview": None,
        }
        # Send to everyone in the room EXCEPT the joiner
        emit("new_message", sys_msg, to=rid, skip_sid=request.sid)
        emit("system_notice", {"room_id": rid, "text": text}, to=rid, skip_sid=request.sid)
    except Exception:
        pass


@socketio.on("bind_user_notifications")
def ws_bind_user_notifications(data):
    """Explicitly bind current connection to the user's notification room.
    Useful when optional auth on WS connect didn't find cookies in handshake.
    """
    try:
        verify_jwt_in_request(optional=True)
    except Exception:
        pass
    try:
        ident = get_jwt_identity()
        if not ident:
            return
        from flask_socketio import join_room, emit
        join_room(_room_for_user(ObjectId(ident)))
        emit("notify_bound", {"ok": True})
    except Exception:
        pass


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
        # Broadcast leave system message
        try:
            name = sid_names.get(request.sid, "누군가")
            text = f"{name}님이 퇴장하셨습니다."
            emit("new_message", {
                "room_id": rid,
                "name": "알림",
                "text": text,
                "ts": datetime.now(timezone.utc).isoformat(),
                "user_id": None,
                "id": None,
                "cid": None,
                "preview": None,
            }, to=rid, skip_sid=request.sid)
            emit("system_notice", {"room_id": rid, "text": text}, to=rid, skip_sid=request.sid)
        except Exception:
            pass


@socketio.on("send_message")
def ws_send_message(data):
    """WebSocket: send a message to a room, persist to DB, and broadcast.
    Authentication is best-effort: use JWT if available, else fall back to client-provided me_id/me_name.
    """
    try:
        verify_jwt_in_request(optional=True)
    except Exception:
        pass
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
    # Build message user context
    uid = None
    name = "익명"
    try:
        ident = get_jwt_identity()
        if ident:
            uid = ObjectId(ident)
            j = get_jwt()
            name = (j.get("name") or name)
    except Exception:
        uid = None
    # Fallback to client-provided identity if JWT not present
    if uid is None:
        try:
            me_id = (payload.get("me_id") or "").strip()
            if me_id:
                uid = ObjectId(me_id)
        except Exception:
            uid = None
        me_name = (payload.get("me_name") or "").strip()
        if me_name:
            name = me_name
    # Build preview metadata best-effort
    preview = None
    try:
        m = re.search(r"https?://[^\s]+", text)
        if m:
            raw = m.group(0)
            url, err = normalize_url(raw)
            if url and not err:
                meta = fetch_and_extract_metadata(url)
                ct = (meta.content_type or "").lower()
                if "application/pdf" not in ct and "application/octet-stream" not in ct:
                    ok = bool((meta.title and meta.title.strip()) or (meta.description and meta.description.strip()) or (meta.image and meta.image.strip()))
                    if ok:
                        preview = {
                            "ok": True,
                            "title": meta.title,
                            "description": meta.description,
                            "image": meta.image,
                            "url": meta.url,
                            "content_type": meta.content_type,
                        }
    except Exception:
        preview = None
    created = datetime.now(timezone.utc)
    doc = {"room_id": rid_obj, "user_id": uid, "name": name, "text": text, "created_at": created}
    if preview:
        doc["preview"] = preview
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
            "user_id": str(uid) if uid else None,
            "name": name,
            "text": text,
            "ts": created.isoformat(),
            "cid": cid,
            "preview": preview,
        }, to=rid)
        # Optional: ACK back to sender
        from flask_socketio import emit as ws_emit
        ws_emit("send_ack", {"id": mid or None})
    except Exception:
        pass


# -------- Files: upload and ranged download --------

@bp.post("/api/chat/uploads/files")
@jwt_required()
def api_upload_file():
    f = (request.files.get("file") or request.files.get("upload"))
    if not f:
        return jsonify({"ok": False, "error": "no_file"}), 400
    max_size = 200 * 1024 * 1024  # 200MB
    try:
        clen = request.content_length or 0
        if clen and clen > max_size + 2048:
            return jsonify({"ok": False, "error": "too_large"}), 400
    except Exception:
        pass
    now = datetime.now(timezone.utc)
    yyyy = now.astimezone(timezone.utc).strftime("%Y")
    mm = now.astimezone(timezone.utc).strftime("%m")
    upload_dir = os.path.join(current_app.root_path, "static", "uploads", "files", yyyy, mm)
    os.makedirs(upload_dir, exist_ok=True)
    orig = secure_filename(f.filename or "file")
    base, ext = os.path.splitext(orig)
    if not ext:
        ext = ""
    fname = f"{uuid4().hex}{ext}"
    abs_path = os.path.join(upload_dir, fname)
    f.save(abs_path)
    rel_path = f"files/{yyyy}/{mm}/{fname}"
    # Use our ranged download route for resume support
    dl_url = url_for("chat.download_file", rel=rel_path)
    size = os.path.getsize(abs_path)
    return jsonify({
        "ok": True,
        "download_url": dl_url,
        "name": orig,
        "size": size,
        "content_type": f.mimetype or mimetypes.guess_type(orig)[0] or "application/octet-stream",
        "rel": rel_path,
    }), 200


@bp.get("/api/chat/download/<path:rel>")
@jwt_required()
def download_file(rel: str):
    # Only allow under static/uploads/files
    safe_rel = rel.strip().lstrip("/")
    if not safe_rel.startswith("files/"):
        abort(404)
    abs_path = os.path.join(current_app.root_path, "static", "uploads", safe_rel)
    abs_path = os.path.abspath(abs_path)
    base_root = os.path.abspath(os.path.join(current_app.root_path, "static", "uploads", "files"))
    if not abs_path.startswith(base_root) or not os.path.exists(abs_path):
        abort(404)
    file_size = os.path.getsize(abs_path)
    range_header = request.headers.get("Range", None)
    mime = mimetypes.guess_type(abs_path)[0] or "application/octet-stream"
    attach_name = os.path.basename(abs_path)
    if not range_header:
        return send_file(abs_path, as_attachment=True, download_name=attach_name, mimetype=mime, conditional=True)
    m = re.match(r"bytes=(\d+)-(\d*)", range_header)
    if not m:
        return send_file(abs_path, as_attachment=True, download_name=attach_name, mimetype=mime, conditional=True)
    start = int(m.group(1))
    end = m.group(2)
    end = int(end) if end else file_size - 1
    if start > end or start >= file_size:
        # Invalid range
        return Response(status=416, headers={
            "Content-Range": f"bytes */{file_size}",
        })
    length = end - start + 1
    def generate():
        with open(abs_path, 'rb') as f:
            f.seek(start)
            remaining = length
            chunk = 64 * 1024
            while remaining > 0:
                read_len = min(chunk, remaining)
                data = f.read(read_len)
                if not data:
                    break
                remaining -= len(data)
                yield data
    rv = Response(generate(), 206, mimetype=mime, direct_passthrough=True)
    rv.headers.add("Content-Range", f"bytes {start}-{end}/{file_size}")
    rv.headers.add("Accept-Ranges", "bytes")
    rv.headers.add("Content-Length", str(length))
    rv.headers["Content-Disposition"] = f"attachment; filename=\"{attach_name}\""
    return rv


# Optional: chunked upload to bypass proxy/content-length limits
@bp.post("/api/chat/uploads/files/chunked")
@jwt_required()
def api_upload_file_chunked():
    """Receive a chunk of a large file and assemble on the server.
    Form fields:
      - chunk (file): the binary chunk
      - upload_id (str, optional): stable id for this upload session
      - chunk_index (int): 0-based index
      - total_chunks (int): total number of chunks
      - name (str): original file name
      - size (int): full file size in bytes
    Returns ok True with intermediate status, and when complete returns download_url payload.
    """
    try:
        total_size = int(request.form.get("size", "0"))
    except Exception:
        total_size = 0
    max_size = 200 * 1024 * 1024  # 200MB
    if total_size and total_size > max_size + 2048:
        return jsonify({"ok": False, "error": "too_large"}), 400

    ch = (request.files.get("chunk") or request.files.get("file"))
    if not ch:
        return jsonify({"ok": False, "error": "no_chunk"}), 400

    try:
        idx = int(request.form.get("chunk_index", "0"))
        total = int(request.form.get("total_chunks", "1"))
    except Exception:
        return jsonify({"ok": False, "error": "bad_index"}), 400

    upload_id = request.form.get("upload_id") or uuid4().hex
    orig = secure_filename(request.form.get("name") or ch.filename or "file")

    # temp dir to store parts
    tmp_dir = os.path.join(current_app.root_path, "static", "uploads", "tmp", upload_id)
    os.makedirs(tmp_dir, exist_ok=True)
    part_path = os.path.join(tmp_dir, f"{idx:06d}.part")
    ch.save(part_path)

    # If not last chunk, acknowledge and return
    if idx + 1 < total:
        return jsonify({"ok": True, "upload_id": upload_id, "received": idx, "done": False})

    # Last chunk received -> assemble
    now = datetime.now(timezone.utc)
    yyyy = now.astimezone(timezone.utc).strftime("%Y")
    mm = now.astimezone(timezone.utc).strftime("%m")
    upload_dir = os.path.join(current_app.root_path, "static", "uploads", "files", yyyy, mm)
    os.makedirs(upload_dir, exist_ok=True)

    base, ext = os.path.splitext(orig)
    if not ext:
        ext = ""
    fname = f"{uuid4().hex}{ext}"
    abs_path = os.path.join(upload_dir, fname)

    # assemble in order
    with open(abs_path, "wb") as out:
        for i in range(total):
            p = os.path.join(tmp_dir, f"{i:06d}.part")
            if not os.path.exists(p):
                # missing part
                try:
                    os.remove(abs_path)
                except Exception:
                    pass
                return jsonify({"ok": False, "error": "missing_part", "missing": i}), 400
            with open(p, "rb") as inp:
                while True:
                    buf = inp.read(64 * 1024)
                    if not buf:
                        break
                    out.write(buf)

    # clean tmp
    try:
        for i in range(total):
            p = os.path.join(tmp_dir, f"{i:06d}.part")
            try:
                os.remove(p)
            except Exception:
                pass
        os.rmdir(tmp_dir)
    except Exception:
        pass

    size = os.path.getsize(abs_path)
    rel_path = f"files/{yyyy}/{mm}/{fname}"
    dl_url = url_for("chat.download_file", rel=rel_path)
    return jsonify({
        "ok": True,
        "download_url": dl_url,
        "name": orig,
        "size": size,
        "content_type": mimetypes.guess_type(orig)[0] or "application/octet-stream",
        "rel": rel_path,
        "done": True,
    }), 200
