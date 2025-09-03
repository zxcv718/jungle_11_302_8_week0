from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import os

from dotenv import load_dotenv
from flask_socketio import SocketIO, join_room, leave_room, emit
from flask import Flask, flash, make_response, redirect, render_template, request, url_for, jsonify, abort, render_template_string
from flask_jwt_extended import (
    JWTManager,
    create_access_token,
    create_refresh_token,
    get_jwt,
    get_jwt_identity,
    verify_jwt_in_request,
    jwt_required,
    set_access_cookies,
    set_refresh_cookies,
    unset_jwt_cookies,
)
from pymongo import MongoClient
from werkzeug.security import check_password_hash, generate_password_hash
from bson import ObjectId
from urllib.parse import urlparse
from metadata import fetch_and_extract_metadata, normalize_url

load_dotenv()

app = Flask(__name__)
app.config.from_object("config.Config")

jwt = JWTManager(app)
# SocketIO setup (threading async for simplicity). In production, consider eventlet/gevent.
socketio = SocketIO(app, cors_allowed_origins=None, async_mode="threading", logger=True, engineio_logger=True)
@app.after_request
def add_no_cache_headers(response):
    # Prevent caching to avoid back navigation showing protected content after logout
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

# MongoDB
client = MongoClient(app.config["MONGO_URI"])  # SRV or standard URI
_db = client.get_default_database()
if _db is None:
    # Fallback DB name when URI doesn't include one; align with Cloud db name
    _db = client["login"]
users = _db["users"]
posts = _db["posts"]
comments = _db["comments"]
chat_rooms = _db["chat_rooms"]
chat_messages = _db["chat_messages"]
try:
    users.create_index("email", unique=True)
    posts.create_index([("user_id", 1), ("created_at", -1)])
    posts.create_index([("title", "text"), ("contents", "text")])
except Exception as e:
    # Avoid crashing on startup if DB requires auth. Handlers will still fail until MONGO_URI is correct.
    print("[warn] Could not ensure indexes:", e)
if os.getenv("SKIP_INDEX", "0") != "1":
    try:
        users.create_index("email", unique=True)
        posts.create_index([("user_id", 1), ("created_at", -1)])
        posts.create_index([("title", "text"), ("contents", "text")])
        chat_rooms.create_index([("category", 1), ("name", 1)])
        chat_messages.create_index([("room_id", 1), ("created_at", -1)])
    except Exception as e:
        # Avoid crashing on startup if DB requires auth. Handlers will still fail until MONGO_URI is correct.
        print("[warn] Could not ensure indexes:", e)

@app.get("/")
def root():
    user_info = None
    current_user_doc = None
    try:
        verify_jwt_in_request(optional=True)
        identity = get_jwt_identity()
        if identity:
            uid = ObjectId(identity)
            current_user_doc = users.find_one({"_id": uid})
            if current_user_doc:
                jwt_data = get_jwt()
                user_info = {
                    "id": identity,
                    "name": jwt_data.get("name"),
                    "email": jwt_data.get("email"),
                }
    except Exception:
        pass

    kst = ZoneInfo("Asia/Seoul")

    def process_post_doc(doc):
        dt = doc.get("created_at")
        date_text = None
        if isinstance(dt, datetime):
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            kst_dt = dt.astimezone(kst)
            date_text = kst_dt.strftime("%Y-%m-%d %H:%M")
        
        return {
            "id": str(doc.get("_id")),
            "category": doc.get("category"),
            "title": doc.get("title", ""),
            "url": doc.get("url", ""),
            "contents": doc.get("contents", ""),
            "date_text": date_text,
            "meta": doc.get("meta", {}),
            "like_count": doc.get("like_count", 0),
        }

    # 전체 글 (추천 수 많은 순으로 자동 정렬)
    pipeline_all = [
        {"$project": {
            "user_id": 1, "category": 1, "title": 1, "url": 1, "contents": 1, "created_at": 1, "meta": 1,
            "like_count": {"$size": {"$ifNull": ["$likes", []]}}
        }},
        {"$sort": {"like_count": -1, "created_at": -1}}
    ]
    all_posts = [process_post_doc(doc) for doc in posts.aggregate(pipeline_all)]

    # 구독한 저자 글 (로그인 시)
    subscribed_posts = []
    if current_user_doc:
        subscribed_author_ids = current_user_doc.get("subscriptions", [])
        if subscribed_author_ids:
            # 구독한 저자의 글만 필터링하여 최신순으로 정렬
            sub_pipeline = [
                {"$match": {"user_id": {"$in": subscribed_author_ids}}},
                {"$sort": {"created_at": -1}},
                {"$project": {
                    "category": 1, "title": 1, "url": 1, "contents": 1, "created_at": 1, "meta": 1,
                    "like_count": {"$size": {"$ifNull": ["$likes", []]}}
                }},
            ]
            subscribed_posts = [process_post_doc(doc) for doc in posts.aggregate(sub_pipeline)]

    # 캐러셀을 위한 인기글 Top 5
    # all_posts가 이미 추천순으로 정렬되어 있으므로, 앞에서 5개만 잘라서 사용
    popular_posts = all_posts[:5]

    # 인기글에 대한 메타데이터(이미지)를 가져와 채워줍니다.
    for post in popular_posts:
        if post.get("url"):
            meta = fetch_and_extract_metadata(post["url"])
            post["meta"] = {
                "image": meta.image
            }

    return render_template(
        "home.html", 
        posts=all_posts,  # 전체 글 목록 (추천순 정렬)
        subscribed_posts=subscribed_posts, # 구독 글 목록
        popular_posts=popular_posts, # 인기글 Top 5
        user_info=user_info
    )


@app.get("/register")
def register_get():
    # If already authenticated, redirect to dashboard to avoid showing unauthenticated page on back navigation
    try:
        verify_jwt_in_request(optional=True)
        if get_jwt_identity():
            return redirect(url_for("root"))
    except Exception:
        pass
    return render_template("register.html", title="회원가입")


@app.post("/register")
def register_post():
    email = request.form.get("email", "").strip().lower()
    name = request.form.get("name", "").strip()
    password = request.form.get("password", "")
    password_confirm = request.form.get("password_confirm", "")

    if not email or not name or not password or not password_confirm:
        flash("모든 필드를 입력하세요.", "error")
        return redirect(url_for("register_get"))
    if password != password_confirm:
        flash("비밀번호가 일치하지 않습니다.", "error")
        return redirect(url_for("register_get"))

    if users.find_one({"email": email}):
        flash("이미 가입된 이메일입니다.", "error")
        return redirect(url_for("register_get"))

    pw_hash = generate_password_hash(password)
    users.insert_one(
        {"email": email, "name": name, "password": pw_hash, "created_at": datetime.now(timezone.utc), "liked_posts": [], "subscriptions": []}
    )
    flash("가입이 완료되었습니다. 로그인해주세요.", "success")
    return redirect(url_for("login_get"))


@app.get("/api/check-email")
def api_check_email():
    email = request.args.get("email", "").strip().lower()
    if not email:
        return jsonify({"exists": False}), 200
    exists = users.find_one({"email": email}) is not None
    return jsonify({"exists": exists}), 200


@app.get("/login")
def login_get():
    # If already authenticated, redirect to dashboard
    try:
        verify_jwt_in_request(optional=True)
        if get_jwt_identity():
            return redirect(url_for("root"))
    except Exception:
        pass
    return render_template("login.html", title="로그인")


@app.post("/login")
def login_post():
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    user = users.find_one({"email": email})
    if not user or not check_password_hash(user["password"], password):
        flash("이메일 또는 비밀번호가 올바르지 않습니다.", "error")
        return redirect(url_for("login_get"))

    identity = str(user.get("_id"))
    claims = {"email": user["email"], "name": user.get("name", "")}

    access_token = create_access_token(identity=identity, additional_claims=claims)
    refresh_token = create_refresh_token(identity=identity, additional_claims=claims)

    resp = make_response(redirect(url_for("root")))
    set_access_cookies(resp, access_token)
    set_refresh_cookies(resp, refresh_token)
    return resp


@app.get("/dashboard")
@jwt_required()
def dashboard():
    identity = get_jwt_identity()
    jwt_data = get_jwt()
    email = jwt_data.get("email")
    name = jwt_data.get("name")
    return render_template("dashboard.html", title="대시보드", name=name, email=email, identity=identity, hide_top_nav=True)


# ---- Chat: views & APIs ----

def get_categories():
    return ["사회","경제","과학","문화","기술","환경","스포츠","생활","역사","철학","기타"]


@app.get("/chat")
@jwt_required()
def chat_home():
    return render_template("chat_home.html", title="카테고리 채팅", hide_top_nav=True)


@app.get("/chat/<category>")
@jwt_required()
def chat_category(category):
    if category not in get_categories():
        abort(404)
    print(f"[http] chat_category category={category}")
    return render_template("chat_category.html", title=f"{category} 채팅", category=category, hide_top_nav=True)


@app.get("/chat/<category>/<room_id>")
@jwt_required()
def chat_room(category, room_id):
    if category not in get_categories():
        abort(404)
    room = chat_rooms.find_one({"_id": ObjectId(room_id), "category": category})
    if not room:
        abort(404)
    print(f"[http] chat_room category={category} room_id={room_id}")
    ident = get_jwt_identity()
    user = users.find_one({"_id": ObjectId(ident)})
    name = user.get("name") if user else "익명"
    return render_template(
        "chat_room.html",
        title=f"{room.get('name')} - {category}",
        category=category,
        room={"id": room_id, "name": room.get("name")},
        me_name=name,
        me_id=str(ident),
        hide_top_nav=True,
    )


@app.get("/api/chat/<category>/rooms")
@jwt_required()
def api_list_rooms(category):
    if category not in get_categories():
        return jsonify({"rooms": []}), 200
    cur = chat_rooms.find({"category": category}).sort([("_id", -1)])
    # Attach online peer counts from memory
    out = []
    for doc in cur:
        rid = str(doc.get("_id"))
        out.append({"id": rid, "name": doc.get("name", ""), "peers": online_counts.get(rid, 0)})
    print(f"[api] list_rooms category={category} count={len(out)}")
    return jsonify({"rooms": out}), 200


@app.post("/api/chat/<category>/rooms")
@jwt_required()
def api_create_room(category):
    if category not in get_categories():
        return jsonify({"error": "bad_category"}), 400
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name_required"}), 400
    doc = {"category": category, "name": name, "created_at": datetime.now(timezone.utc)}
    res = chat_rooms.insert_one(doc)
    return jsonify({"id": str(res.inserted_id)}), 200


@app.get("/api/chat/<category>/<room_id>/messages")
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
    cur = chat_messages.find({"room_id": rid}).sort([("created_at", -1)]).limit(limit)
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
    print(f"[api] messages category={category} room_id={room_id} returned={len(items)}")
    return jsonify({"messages": items}), 200


@app.get("/api/chat/<category>/<room_id>/peers")
@jwt_required()
def api_room_peers(category, room_id):
    try:
        _ = ObjectId(room_id)
    except Exception:
        return jsonify({"peers": 0}), 200
    peers = online_counts.get(room_id, 0)
    print(f"[api] peers category={category} room_id={room_id} peers={peers}")
    return jsonify({"peers": peers}), 200


@app.post("/api/chat/<category>/<room_id>/send")
@jwt_required()
def api_send_message(category, room_id):
    if category not in get_categories():
        return jsonify({"error": "bad_category"}), 400
    try:
        rid_obj = ObjectId(room_id)
    except Exception:
        return jsonify({"error": "bad_room"}), 400
    if not chat_rooms.find_one({"_id": rid_obj, "category": category}):
        return jsonify({"error": "not_found"}), 404
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    cid = (data.get("cid") or "").strip() or None
    if not text:
        return jsonify({"error": "text_required"}), 400
    uid = ObjectId(get_jwt_identity())
    j = get_jwt()
    name = j.get("name") or "익명"
    created = datetime.now(timezone.utc)
    doc = {"room_id": rid_obj, "user_id": uid, "name": name, "text": text, "created_at": created}
    try:
        res = chat_messages.insert_one(doc)
        mid = str(res.inserted_id)
    except Exception as e:
        print("[api] message insert failed:", e)
        mid = None
    # Broadcast if any listeners via Socket.IO
    try:
        socketio.emit("new_message", {
            "id": mid or "temp-" + str(int(created.timestamp()*1000)),
            "room_id": room_id,
            "user_id": str(uid),
            "name": name,
            "text": text,
            "ts": created.isoformat(),
            "cid": cid,
        }, to=room_id)
    except Exception as e:
        print("[api] broadcast failed:", e)
    print(f"[api] send category={category} room_id={room_id} by={str(uid)} mid={mid} cid={cid}")
    return jsonify({"id": mid, "ts": created.isoformat(), "name": name, "text": text, "cid": cid, "user_id": str(uid)}), 200


# In-memory online counters (best-effort)
online_counts = {}
sid_rooms = {}


@socketio.on("connect")
def ws_connect():
    # Enforce JWT via cookies for websocket handshake
    try:
        verify_jwt_in_request(optional=False)
        uid = get_jwt_identity()
        origin = request.headers.get('Origin')
        ua = request.headers.get('User-Agent')
        ck = list(request.cookies.keys()) if request.cookies else []
        print(f"[ws] connect ok sid={request.sid} uid={uid} origin={origin} ua={(ua or '')[:80]} cookies={ck}")
    except Exception as e:
        print("[ws] connect denied:", e)
        return False
    # OK
    sid_rooms[request.sid] = set()


@socketio.on("disconnect")
def ws_disconnect():
    rooms = sid_rooms.pop(request.sid, set())
    print(f"[ws] disconnect sid={request.sid} rooms={list(rooms)}")
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
    # verify room exists
    try:
        room_doc = chat_rooms.find_one({"_id": ObjectId(rid)})
    except Exception:
        room_doc = None
    if not room_doc:
        return
    print(f"[ws] join sid={request.sid} room={rid}")
    join_room(rid)
    sid_rooms.setdefault(request.sid, set()).add(rid)
    online_counts[rid] = online_counts.get(rid, 0) + 1
    emit("room_peers", {"room_id": rid, "peers": online_counts[rid]}, to=rid)
    emit("joined", {"room_id": rid, "peers": online_counts[rid]}, to=request.sid)


@socketio.on("leave")
def ws_leave(data):
    rid = (data or {}).get("room_id")
    if not rid:
        return
    print(f"[ws] leave sid={request.sid} room={rid}")
    leave_room(rid)
    if request.sid in sid_rooms and rid in sid_rooms[request.sid]:
        sid_rooms[request.sid].remove(rid)
        online_counts[rid] = max(0, online_counts.get(rid, 1) - 1)
        emit("room_peers", {"room_id": rid, "peers": online_counts.get(rid, 0)}, to=rid)


@socketio.on("send_message")
def ws_send_message(data):
    try:
        verify_jwt_in_request(optional=False)
    except Exception:
        return
    rid = (data or {}).get("room_id")
    text = (data or {}).get("text", "").strip()
    cid = (data or {}).get("cid") or None
    if not rid or not text:
        return
    # Verify room
    try:
        rid_obj = ObjectId(rid)
    except Exception:
        return
    if not chat_rooms.find_one({"_id": rid_obj}):
        return
    uid = ObjectId(get_jwt_identity())
    j = get_jwt()
    name = j.get("name") or "익명"
    created = datetime.now(timezone.utc)
    doc = {
        "room_id": rid_obj,
        "user_id": uid,
        "name": name,
        "text": text,
        "created_at": created,
    }
    try:
        res = chat_messages.insert_one(doc)
        mid = str(res.inserted_id)
    except Exception as e:
        print("[ws] message insert failed:", e)
        mid = None
    payload = {
        "id": mid or "temp-" + str(int(created.timestamp()*1000)),
        "room_id": rid,
        "user_id": str(uid),
        "name": name,
        "text": text,
        "ts": created.isoformat(),
        "cid": cid,
    }
    print(f"[ws] new_message room={rid} by={str(uid)} id={payload['id']} cid={cid}")
    emit("new_message", payload, to=rid)
    emit("send_ack", {"id": payload["id"]}, to=request.sid)


@app.get("/api/my-posts")
@jwt_required()
def api_my_posts():
    # Params
    category = request.args.get("category", "전체")
    q = request.args.get("q", "").strip()
    sort = request.args.get("sort", "new")  # new|old
    try:
        page = int(request.args.get("page", 1))
    except ValueError:
        page = 1
    try:
        limit = min(50, max(1, int(request.args.get("limit", 10))))
    except ValueError:
        limit = 10

    user_id = ObjectId(get_jwt_identity())
    filt = {"user_id": user_id}
    if category and category != "전체":
        filt["category"] = category
    if q:
        rx = {"$regex": q, "$options": "i"}
        filt["$or"] = [{"title": rx}, {"contents": rx}]

    sort_spec = [("created_at", -1 if sort == "new" else 1), ("_id", -1 if sort == "new" else 1)]
    skip = (page - 1) * limit

    cur = posts.find(filt).sort(sort_spec).skip(skip).limit(limit)
    items = []
    kst = ZoneInfo("Asia/Seoul")
    for doc in cur:
        # Build KST datetime robustly
        date_iso = None
        date_text = None
        dt = doc.get("created_at")
        if isinstance(dt, datetime):
            # Treat naive as UTC (legacy data)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            kst_dt = dt.astimezone(kst)
            date_iso = kst_dt.isoformat()
            date_text = kst_dt.strftime("%Y-%m-%d %H:%M")
        else:
            # No datetime available
            date_iso = None
            date_text = None

        items.append({
            "id": str(doc.get("_id")),
            "category": doc.get("category"),
            "title": doc.get("title", ""),
            "url": doc.get("url", ""),
            "contents": doc.get("contents", ""),
            "date": date_iso,
            "date_text": date_text,
        })

    return jsonify({"items": items, "page": page, "limit": limit})


@app.get("/api/posts")
def api_posts():
    # Params
    try:
        page = int(request.args.get("page", 1))
    except ValueError:
        page = 1
    try:
        limit = min(50, max(1, int(request.args.get("limit", 10))))
    except ValueError:
        limit = 10
    
    exclude_ids_str = request.args.get("exclude", "")
    
    filt = {}
    if exclude_ids_str:
        try:
            exclude_ids = [ObjectId(id_str) for id_str in exclude_ids_str.split(',') if id_str]
            if exclude_ids:
                filt["_id"] = {"$nin": exclude_ids}
        except Exception:
            pass # Ignore invalid IDs

    sort_spec = [("created_at", -1), ("_id", -1)]  # Sort by newest
    skip = (page - 1) * limit

    cur = posts.find(filt).sort(sort_spec).skip(skip).limit(limit)
    items = []
    kst = ZoneInfo("Asia/Seoul")
    for doc in cur:
        # Build KST datetime robustly
        date_iso = None
        date_text = None
        dt = doc.get("created_at")
        if isinstance(dt, datetime):
            # Treat naive as UTC (legacy data)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            kst_dt = dt.astimezone(kst)
            date_iso = kst_dt.isoformat()
            date_text = kst_dt.strftime("%Y-%m-%d %H:%M")
        else:
            # No datetime available
            date_iso = None
            date_text = None

        items.append({
            "id": str(doc.get("_id")),
            "category": doc.get("category"),
            "title": doc.get("title", ""),
            "url": doc.get("url", ""),
            "contents": doc.get("contents", ""),
            "date": date_iso,
            "date_text": date_text,
            "meta": doc.get("meta", {}),
        })

    return jsonify({"items": items, "page": page, "limit": limit})

@app.get("/post/new")
@jwt_required()
def post_new_get():
    categories = ["전체","사회","경제","과학","문화","기술","환경","스포츠","생활","역사","철학","기타"]
    return render_template("post_new.html", title="새 글 작성", categories=[c for c in categories if c != "전체"], hide_top_nav=True)


@app.post("/post/new")
@jwt_required()
def post_new_post():
    user_id = ObjectId(get_jwt_identity())
    category = request.form.get("category", "")
    title = request.form.get("title", "").strip()
    url = request.form.get("url", "").strip()
    contents = request.form.get("contents", "").strip()
    # Basic validation: require non-empty and proper URL scheme and category not '선택'
    if not title or not contents or not category or category == "선택":
        flash("카테고리, 제목, 내용을 입력하세요.", "error")
        return redirect(url_for("post_new_get"))
    try:
        pu = urlparse(url)
        if pu.scheme not in ("http", "https") or not pu.netloc:
            raise ValueError("invalid url")
    except Exception:
        flash("유효한 URL을 입력하세요.", "error")
        return redirect(url_for("post_new_get"))
    # Store created_at to comply with Atlas validator; avoid extra fields
    now_utc = datetime.now(timezone.utc)
    posts.insert_one({
        "user_id": user_id,
        "category": category,
        "title": title,
        "url": url,
        "contents": contents,
        "created_at": now_utc,
        "likes": [],
    })
    flash("글이 등록되었습니다.", "success")
    return redirect(url_for("dashboard"))


@app.get("/post/<id>")
@jwt_required(optional=True)
def post_detail(id):
    try:
        oid = ObjectId(id)
    except Exception:
        abort(404)

    doc = posts.find_one({"_id": oid})
    if not doc:
        flash("요청한 글을 찾을 수 없습니다.", "error")
        return redirect(url_for("root"))

    # 글 작성자 정보 조회
    author = users.find_one({"_id": doc["user_id"]})

    # 현재 로그인 사용자 정보 및 상태
    current_user = None
    is_liked = False
    is_subscribed = False
    identity = get_jwt_identity()
    if identity:
        uid = ObjectId(identity)
        user_doc = users.find_one({"_id": uid})
        if user_doc:
            jwt_data = get_jwt()
            current_user = {
                "id": str(uid),
                "name": jwt_data.get("name"),
                "email": jwt_data.get("email"),
            }
            # Robust check for likes (handles both ObjectId and string)
            likes_list = doc.get("likes", [])
            if uid in likes_list or str(uid) in likes_list:
                is_liked = True
            
            # Robust check for subscriptions (handles both ObjectId and string)
            if author:
                subscriptions_list = user_doc.get("subscriptions", [])
                if author["_id"] in subscriptions_list or str(author["_id"]) in subscriptions_list:
                    is_subscribed = True

    # Build KST date text
    kst = ZoneInfo("Asia/Seoul")
    date_text = None
    dt = doc.get("created_at")
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        date_text = dt.astimezone(kst).strftime("%Y-%m-%d %H:%M")
    else:
        date_text = None

    url = doc.get("url") or ""
    meta = fetch_and_extract_metadata(url) if url else None
    
    # 댓글 리스트: DB에서 해당 게시글의 댓글을 조회 (최신순)
    comment_cur = comments.find({"post_id": oid}).sort("created_at", -1) 
    comment_list = []
    for c in comment_cur:
        user = users.find_one({"_id": c["user_id"]})
        dt = c["created_at"]
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        created_at_kst = dt.astimezone(kst).strftime("%Y-%m-%d %H:%M")
        comment_list.append({
            "id": str(c["_id"]),
            "user_id": str(c["user_id"]),
            "user_name": user["name"] if user else "알 수 없음",
            "content": c["content"],
            "created_at": created_at_kst,
            "like_count": len(c.get("likes", [])),
        })
    # 댓글 리스트 생성 완료 후, 추천수 기준 상위 3개 추출 (추천수 1 이상만)
    best_comments = [c for c in comment_list if c["like_count"] > 0]
    best_comments = sorted(best_comments, key=lambda x: x["like_count"], reverse=True)[:3]
    
    return render_template(
        "post_detail.html",
        title="글 상세",
        post={
            "id": str(doc.get("_id")),
            "category": doc.get("category"),
            "title": doc.get("title", ""),
            "contents": doc.get("contents", ""),
            "date_text": date_text,
            "url": url,
            "like_count": len(doc.get("likes", [])),
            "author": {
                "id": str(author["_id"]),
                "name": author.get("name", "알 수 없음")
            } if author else None
        },
        meta=meta,
        hide_top_nav=True,
        current_user=current_user,
        is_liked=is_liked,
        is_subscribed=is_subscribed,
        comments=comment_list,
        best_comments=best_comments,
    )

@app.post("/post/<id>/like")
@jwt_required()
def post_like(id):
    try:
        oid = ObjectId(id)
        uid = ObjectId(get_jwt_identity())
    except Exception:
        return jsonify({"ok": False, "message": "잘못된 요청입니다."}), 400

    post = posts.find_one({"_id": oid})
    if not post:
        return jsonify({"ok": False, "message": "글을 찾을 수 없습니다."}), 404

    if uid in post.get("likes", []):
        # Unlike
        posts.update_one({"_id": oid}, {"$pull": {"likes": uid}})
        users.update_one({"_id": uid}, {"$pull": {"liked_posts": oid}})
        action = "unliked"
    else:
        # Like
        posts.update_one({"_id": oid}, {"$push": {"likes": uid}})
        users.update_one({"_id": uid}, {"$push": {"liked_posts": oid}})
        action = "liked"

    new_like_count = len(posts.find_one({"_id": oid}).get("likes", []))
    return jsonify({"ok": True, "action": action, "like_count": new_like_count})


@app.post("/user/<id>/subscribe")
@jwt_required()
def user_subscribe(id):
    try:
        author_id = ObjectId(id)
        subscriber_id = ObjectId(get_jwt_identity())
    except Exception:
        return jsonify({"ok": False, "message": "잘못된 요청입니다."}), 400

    if author_id == subscriber_id:
        return jsonify({"ok": False, "message": "스스로를 구독할 수 없습니다."}), 400

    subscriber = users.find_one({"_id": subscriber_id})
    if not subscriber:
        return jsonify({"ok": False, "message": "사용자를 찾을 수 없습니다."}), 404

    if author_id in subscriber.get("subscriptions", []):
        # Unsubscribe
        users.update_one({"_id": subscriber_id}, {"$pull": {"subscriptions": author_id}})
        action = "unsubscribed"
    else:
        # Subscribe
        users.update_one({"_id": subscriber_id}, {"$push": {"subscriptions": author_id}})
        action = "subscribed"

    return jsonify({"ok": True, "action": action})


@app.post("/post/<id>/delete")
@jwt_required()
def post_delete(id):
    try:
        oid = ObjectId(id)
    except Exception:
        abort(404)
    uid = ObjectId(get_jwt_identity())
    res = posts.delete_one({"_id": oid, "user_id": uid})
    if res.deleted_count:
        flash("삭제되었습니다.", "success")
    else:
        flash("삭제할 수 없습니다.", "error")
    return redirect(url_for("dashboard"))


@app.get("/post/<id>/edit")
@jwt_required()
def post_edit_get(id):
    try:
        oid = ObjectId(id)
    except Exception:
        abort(404)
    uid = ObjectId(get_jwt_identity())
    doc = posts.find_one({"_id": oid, "user_id": uid})
    if not doc:
        flash("요청한 글을 찾을 수 없습니다.", "error")
        return redirect(url_for("dashboard"))
    categories = [c for c in ["사회","경제","과학","문화","기술","환경","스포츠","생활","역사","철학","기타"]]
    # Normalize post object similar to detail view (use string id)
    post_obj = {
        "id": str(doc.get("_id")),
        "category": doc.get("category"),
        "title": doc.get("title", ""),
        "url": doc.get("url", ""),
        "contents": doc.get("contents", ""),
    }
    return render_template("post_edit.html", title="글 수정", categories=categories, post=post_obj, hide_top_nav=True)


@app.post("/post/<id>/edit")
@jwt_required()
def post_edit_post(id):
    try:
        oid = ObjectId(id)
    except Exception:
        abort(404)
    uid = ObjectId(get_jwt_identity())
    doc = posts.find_one({"_id": oid, "user_id": uid})
    if not doc:
        abort(404)

    category = request.form.get("category", "")
    title = request.form.get("title", "").strip()
    url = request.form.get("url", "").strip()
    contents = request.form.get("contents", "").strip()

    if not title or not contents or not category or category == "선택":
        flash("카테고리, 제목, 내용을 입력하세요.", "error")
        return redirect(url_for("post_edit_get", id=id))
    try:
        pu = urlparse(url)
        if pu.scheme not in ("http", "https") or not pu.netloc:
            raise ValueError("invalid url")
    except Exception:
        flash("유효한 URL을 입력하세요.", "error")
        return redirect(url_for("post_edit_get", id=id))

    # Update only allowed fields and remove legacy fields blocked by validator
    posts.update_one(
        {"_id": oid, "user_id": uid},
        {
            "$set": {
                "category": category,
                "title": title,
                "url": url,
                "contents": contents,
            },
            "$unset": {"date": "", "date_kst": "", "updated_at": "", "tag": ""},
        },
    )
    flash("수정되었습니다.", "success")
    return redirect(url_for("post_detail", id=id))

# 댓글 작성
@app.post("/post/<id>/comments")
@jwt_required()
def post_comments(id):
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    try:
        post_id = ObjectId(id)
        user_id = ObjectId(get_jwt_identity())
        content = request.form.get("content", "").strip()
        if not content:
            msg = "댓글 내용을 입력하세요."
            if is_ajax:
                return jsonify({"ok": False, "message": msg}), 400
            flash(msg, "error")
            return redirect(url_for("post_detail", id=id))
        now = datetime.now(timezone.utc)
        comment_doc = {
            "post_id": post_id,
            "user_id": user_id,
            "content": content,
            "created_at": now,
            "likes": []
        }
        inserted = comments.insert_one(comment_doc)
        # 새 댓글 정보
        user = users.find_one({"_id": user_id})
        kst = ZoneInfo("Asia/Seoul")
        created_at_kst = now.astimezone(kst).strftime("%Y-%m-%d %H:%M")
        comment_data = {
            "id": str(inserted.inserted_id),
            "user_id": str(user_id),
            "user_name": user["name"] if user else "알 수 없음",
            "content": content,
            "created_at": created_at_kst,
            "like_count": 0
        }
        # 댓글 HTML (SSR 스타일)
        comment_html = render_template_string(
            '<div class="border rounded px-3 py-2 bg-white flex justify-between items-center">'
            '  <div>'
            '    <span class="font-semibold">{{ c.user_name }}</span>'
            '    <span class="text-xs text-gray-500 ml-2">{{ c.created_at }}</span>'
            '    <div class="mt-1">{{ c.content }}</div>'
            '    <button type="button" class="text-emerald-600 text-xs px-2 py-1 rounded border" data-cid="{{ c.id }}" onclick="likeComment(this.dataset.cid, this)">추천</button>'
            '    <span class="ml-1 text-xs text-gray-700" id="like-count-{{ c.id }}">0</span>'
            '  </div>'
            '  <form action="' + url_for('comment_delete', comment_id=comment_data["id"]) + '" method="post" onsubmit="return confirm(\'댓글을 삭제하시겠습니까?\');">'
            '    <button class="text-red-600 text-xs px-2 py-1 rounded">삭제</button>'
            '  </form>'
            '</div>',
            c=comment_data
        )
        # 베스트 댓글 영역 갱신
        all_comments = list(comments.find({"post_id": post_id}))
        comment_list = []
        for c in all_comments:
            user = users.find_one({"_id": c["user_id"]})
            dt = c["created_at"]
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            created_at_kst = dt.astimezone(kst).strftime("%Y-%m-%d %H:%M")
            comment_list.append({
                "id": str(c["_id"]),
                "user_id": str(c["user_id"]),
                "user_name": user["name"] if user else "알 수 없음",
                "content": c["content"],
                "created_at": created_at_kst,
                "like_count": len(c.get("likes", [])),
            })
        best_comments = [c for c in comment_list if c["like_count"] > 0]
        best_comments = sorted(best_comments, key=lambda x: x["like_count"], reverse=True)[:3]
        best_comments_html = render_template_string(
            '{% if best_comments and best_comments|length > 0 %}'
            '<div class="mb-6">'
            '  <h2 class="text-lg font-bold text-emerald-700 mb-2">베스트 댓글</h2>'
            '  <div class="space-y-2">'
            '    {% for c in best_comments %}'
            '    <div class="border rounded px-3 py-2 bg-yellow-50 flex justify-between items-center">'
            '      <div>'
            '        <span class="font-semibold">{{ c.user_name }}</span>'
            '        <span class="text-xs text-gray-500 ml-2">{{ c.created_at }}</span>'
            '        <div class="mt-1">{{ c.content }}</div>'
            '      </div>'
            '      <span class="text-emerald-600 font-bold text-sm">추천 수 {{ c.like_count }}</span>'
            '    </div>'
            '    {% endfor %}'
            '  </div>'
            '</div>'
            '{% endif %}',
            best_comments=best_comments
        )
        if is_ajax:
            return jsonify({"ok": True, "comment_html": comment_html, "best_comments_html": best_comments_html})
        flash("댓글이 등록되었습니다.", "success")
        return redirect(url_for("post_detail", id=id))
    except Exception as e:
        if is_ajax:
            return jsonify({"ok": False, "message": "오류: " + str(e)}), 400
        flash("오류가 발생했습니다.", "error")
        return redirect(url_for("post_detail", id=id))

# 댓글 삭제
@app.post("/delete/<comment_id>/comment")
@jwt_required()
def comment_delete(comment_id):
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    try:
        cid = ObjectId(comment_id)
        uid = ObjectId(get_jwt_identity())
        # 삭제 전에 post_id를 미리 조회
        comment = comments.find_one({"_id": cid, "user_id": uid})
        post_id = comment.get("post_id") if comment else None
        res = comments.delete_one({"_id": cid, "user_id": uid})
        if res.deleted_count:
            msg = "댓글이 삭제되었습니다."
            # AJAX: 삭제된 댓글 id, 베스트 댓글 영역 반환
            if is_ajax and post_id:
                # 베스트 댓글 영역 갱신
                all_comments = list(comments.find({"post_id": post_id}))
                comment_list = []
                kst = ZoneInfo("Asia/Seoul")
                for c in all_comments:
                    user = users.find_one({"_id": c["user_id"]})
                    dt = c["created_at"]
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    created_at_kst = dt.astimezone(kst).strftime("%Y-%m-%d %H:%M")
                    comment_list.append({
                        "id": str(c["_id"]),
                        "user_id": str(c["user_id"]),
                        "user_name": user["name"] if user else "알 수 없음",
                        "content": c["content"],
                        "created_at": created_at_kst,
                        "like_count": len(c.get("likes", [])),
                    })
                best_comments = [c for c in comment_list if c["like_count"] > 0]
                best_comments = sorted(best_comments, key=lambda x: x["like_count"], reverse=True)[:3]
                best_comments_html = render_template_string(
                    '{% if best_comments and best_comments|length > 0 %}'
                    '<div class="mb-6">'
                    '  <h2 class="text-lg font-bold text-emerald-700 mb-2">베스트 댓글</h2>'
                    '  <div class="space-y-2">'
                    '    {% for c in best_comments %}'
                    '    <div class="border rounded px-3 py-2 bg-yellow-50 flex justify-between items-center">'
                    '      <div>'
                    '        <span class="font-semibold">{{ c.user_name }}</span>'
                    '        <span class="text-xs text-gray-500 ml-2">{{ c.created_at }}</span>'
                    '        <div class="mt-1">{{ c.content }}</div>'
                    '      </div>'
                    '      <span class="text-emerald-600 font-bold text-sm">추천 수 {{ c.like_count }}</span>'
                    '    </div>'
                    '    {% endfor %}'
                    '  </div>'
                    '</div>'
                    '{% endif %}',
                    best_comments=best_comments
                )
                return jsonify({"ok": True, "comment_id": str(comment_id), "best_comments_html": best_comments_html, "message": msg})
            flash(msg, "success")
        else:
            msg = "삭제할 수 없습니다."
            if is_ajax:
                return jsonify({"ok": False, "message": msg}), 400
            flash(msg, "error")
        if post_id:
            return redirect(url_for("post_detail", id=str(post_id)))
        return redirect(url_for("dashboard"))
    except Exception as e:
        if is_ajax:
            return jsonify({"ok": False, "message": "오류: " + str(e)}), 400
        flash("오류가 발생했습니다.", "error")
        return redirect(url_for("dashboard"))

#댓글 추천
@app.post("/like/<comment_id>/comment")
@jwt_required()
def comment_like(comment_id):
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    try:
        cid = ObjectId(comment_id)
        uid = ObjectId(get_jwt_identity())
        comment = comments.find_one({"_id": cid})
        if not comment:
            msg = "이미 삭제된 댓글입니다."
            if is_ajax:
                return jsonify({"ok": False, "message": msg}), 404
            flash(msg, "error")
            return redirect(url_for("dashboard"))
        post_id = comment.get("post_id")
        likes = comment.get("likes", [])
        if isinstance(likes, int):
            likes = []
        if uid in likes:
            comments.update_one({"_id": cid}, {"$pull": {"likes": uid}})
            msg = "댓글 추천을 취소했습니다."
        else:
            comments.update_one({"_id": cid}, {"$push": {"likes": uid}})
            msg = "댓글을 추천했습니다."
        new_comment = comments.find_one({"_id": cid})
        like_count = len(new_comment.get("likes", [])) if new_comment else 0
        if is_ajax:
            # --- 베스트 댓글 영역 동적 렌더링 ---
            # 해당 포스트의 전체 댓글을 조회
            all_comments = list(comments.find({"post_id": post_id}))
            comment_list = []
            kst = ZoneInfo("Asia/Seoul")
            for c in all_comments:
                user = users.find_one({"_id": c["user_id"]})
                dt = c["created_at"]
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                created_at_kst = dt.astimezone(kst).strftime("%Y-%m-%d %H:%M")
                comment_list.append({
                    "id": str(c["_id"]),
                    "user_id": str(c["user_id"]),
                    "user_name": user["name"] if user else "알 수 없음",
                    "content": c["content"],
                    "created_at": created_at_kst,
                    "like_count": len(c.get("likes", [])),
                })
            best_comments = [c for c in comment_list if c["like_count"] > 0]
            best_comments = sorted(best_comments, key=lambda x: x["like_count"], reverse=True)[:3]
            # 베스트 댓글 영역만 렌더링 (템플릿 조각)
            best_comments_html = render_template_string(
                '{% if best_comments and best_comments|length > 0 %}'
                '<div class="mb-6">'
                '  <h2 class="text-lg font-bold text-emerald-700 mb-2">베스트 댓글</h2>'
                '  <div class="space-y-2">'
                '    {% for c in best_comments %}'
                '    <div class="border rounded px-3 py-2 bg-yellow-50 flex justify-between items-center">'
                '      <div>'
                '        <span class="font-semibold">{{ c.user_name }}</span>'
                '        <span class="text-xs text-gray-500 ml-2">{{ c.created_at }}</span>'
                '        <div class="mt-1">{{ c.content }}</div>'
                '      </div>'
                '      <span class="text-emerald-600 font-bold text-sm">추천 수 {{ c.like_count }}</span>'
                '    </div>'
                '    {% endfor %}'
                '  </div>'
                '</div>'
                '{% endif %}',
                best_comments=best_comments
            )
            return jsonify({"ok": True, "like_count": like_count, "message": msg, "best_comments_html": best_comments_html})
        flash(msg, "success")
        if post_id:
            return redirect(url_for("post_detail", id=str(post_id)))
        return redirect(url_for("dashboard"))
    except Exception as e:
        if is_ajax:
            return jsonify({"ok": False, "message": "오류: " + str(e)}), 400
        flash("오류가 발생했습니다.", "error")
        return redirect(url_for("dashboard"))

@app.get("/api/preview-url")
#@jwt_required()
def api_preview_url():
    """Validate URL using metadata module (requests+bs4) for permissive preview.
    Returns JSON: {ok, title, description, image, url, content_type}.
    """
    raw = request.args.get("url", "").strip()
    if not raw:
        return jsonify({"ok": False, "error": "empty"}), 400

    url, err = normalize_url(raw)
    if err:
        return jsonify({"ok": False, "error": "invalid_url", "reason": err}), 200

    meta = fetch_and_extract_metadata(url)
    ok = bool((meta.title and meta.title.strip()) or (meta.description and meta.description.strip()) or (meta.image and meta.image.strip()))
    # If content-type strongly indicates binary like pdf, mark unsupported
    ct = (meta.content_type or "").lower()
    if "application/pdf" in ct or "application/octet-stream" in ct:
        return jsonify({"ok": False, "error": "unsupported_content", "content_type": meta.content_type}), 200

    return jsonify({
        "ok": ok,
        "title": meta.title,
        "description": meta.description,
        "image": meta.image,
        "url": meta.url,
        "content_type": meta.content_type,
    }), 200


@app.post("/refresh")
@jwt_required(refresh=True)
def refresh():
    jwt_data = get_jwt()
    identity = get_jwt_identity()
    email = jwt_data.get("email")
    name = jwt_data.get("name")

    new_access = create_access_token(identity=identity, additional_claims={"email": email, "name": name})

    resp = make_response({"msg": "access token refreshed"})
    set_access_cookies(resp, new_access)
    return resp


@app.post("/logout")
def logout():
    resp = make_response(redirect(url_for("login_get")))
    unset_jwt_cookies(resp)
    flash("로그아웃되었습니다.", "success")
    return resp


# JWT error handlers -> redirect to login or show flash message
@jwt.unauthorized_loader
def handle_unauthorized(reason):
    print(f"[auth] unauthorized: path={request.path} reason={reason}")
    # For APIs, return JSON so client can handle refresh/redirect
    if request.path.startswith("/api") or request.path.startswith("/refresh"):
        return jsonify({"error": "unauthorized"}), 401
    return redirect(url_for("login_get"))


@jwt.invalid_token_loader
def handle_invalid(reason):
    print(f"[auth] invalid_token: path={request.path} reason={reason}")
    if request.path.startswith("/api") or request.path.startswith("/refresh"):
        return jsonify({"error": "invalid_token"}), 401
    return redirect(url_for("login_get"))


@jwt.expired_token_loader
def handle_expired(jwt_header, jwt_payload):
        token_type = (jwt_payload or {}).get("type")
        print(f"[auth] expired_token: path={request.path} type={token_type}")
        # If refresh token expired
        if request.path.startswith("/refresh") or token_type == "refresh":
                return jsonify({"error": "refresh_expired"}), 401
        # Access token expired for API
        if request.path.startswith("/api"):
                return jsonify({"error": "access_expired"}), 401
        # Page routes: attempt silent refresh via bridge page, then return to original URL
        orig = request.full_path if request.query_string else request.path
        # Use a small inline page to avoid template deps; use %% formatting to avoid brace escaping issues
        html = """
<!doctype html>
<html lang=\"ko\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>세션 갱신</title></head>
<body>
<script>
    (async function() {
        try {
            const r = await fetch('%s', { method: 'POST', credentials: 'same-origin' });
            if (r.ok) {
                window.location.replace(%r);
                return;
            }
            try {
                const j = await r.json();
                if (j && j.error === 'refresh_expired') {
                    window.location.replace('%s');
                    return;
                }
            } catch {}
        } catch {}
        window.location.replace('%s');
    })();
</script>
</body></html>
""" % (url_for('refresh'), orig, url_for('login_get'), url_for('login_get'))
        return html, 401, {"Content-Type": "text/html; charset=utf-8"}


@app.get("/api/session-status")
def api_session_status():
    """진단용: 현재 세션/토큰 상태를 확인합니다.
    Returns {authenticated: bool, type?: 'access', exp?: int, email?, name?}
    """
    try:
        verify_jwt_in_request(optional=True)
    except Exception:
        pass
    ident = None
    try:
        ident = get_jwt_identity()
    except Exception:
        ident = None
    if not ident:
        return jsonify({"authenticated": False}), 200
    j = get_jwt()
    return jsonify({
        "authenticated": True,
        "type": j.get("type", "access"),
        "exp": j.get("exp"),
        "email": j.get("email"),
        "name": j.get("name"),
    }), 200

if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "5050"))
    # app.run(host=host, port=port, debug=bool(int(os.getenv("FLASK_DEBUG", "1"))))
    socketio.run(app, host=host, port=port, debug=bool(int(os.getenv("FLASK_DEBUG", "1"))))