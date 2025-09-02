from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import os

from dotenv import load_dotenv
from flask import Flask, flash, make_response, redirect, render_template, request, url_for, jsonify, abort
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
try:
    users.create_index("email", unique=True)
    posts.create_index([("user_id", 1), ("created_at", -1)])
    posts.create_index([("title", "text"), ("contents", "text")])
except Exception as e:
    # Avoid crashing on startup if DB requires auth. Handlers will still fail until MONGO_URI is correct.
    print("[warn] Could not ensure indexes:", e)

@app.get("/")
def root():
    # Redirect root to dashboard if logged-in, otherwise login page
    try:
        verify_jwt_in_request(optional=True)
        if get_jwt_identity():
            return redirect(url_for("dashboard"))
    except Exception:
        pass
    return redirect(url_for("login_get"))


@app.get("/register")
def register_get():
    # If already authenticated, redirect to dashboard to avoid showing unauthenticated page on back navigation
    try:
        verify_jwt_in_request(optional=True)
        if get_jwt_identity():
            return redirect(url_for("dashboard"))
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
        {"email": email, "name": name, "password": pw_hash, "created_at": datetime.now(timezone.utc)}
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
            return redirect(url_for("dashboard"))
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

    resp = make_response(redirect(url_for("dashboard")))
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
    })
    flash("글이 등록되었습니다.", "success")
    return redirect(url_for("dashboard"))


@app.get("/post/<id>")
@jwt_required()
def post_detail(id):
    # Verify ownership
    try:
        oid = ObjectId(id)
    except Exception:
        abort(404)
    uid = ObjectId(get_jwt_identity())
    doc = posts.find_one({"_id": oid, "user_id": uid})
    if not doc:
        flash("요청한 글을 찾을 수 없습니다.", "error")
        return redirect(url_for("dashboard"))

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
        },
        meta=meta,
        hide_top_nav=True,
    )


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


@app.get("/api/preview-url")
@jwt_required()
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
    app.run(host=host, port=port, debug=bool(int(os.getenv("FLASK_DEBUG", "1"))))
