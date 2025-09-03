from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, abort
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt, verify_jwt_in_request
from bson import ObjectId
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import re

from ..extensions import mongo
from ..services.markdown_service import render_markdown_sanitized
from ..utils.text import to_plain_preview, first_image_from_markdown
from flask import current_app
from werkzeug.utils import secure_filename
from uuid import uuid4
from urllib.parse import urlparse
import os

from metadata import fetch_and_extract_metadata, normalize_url
from ..services.meta_cache import get_or_fetch
from ..services.notifications import create_notification

bp = Blueprint("posts", __name__)


def get_categories():
    return [
        "프로그래밍언어","자료구조","알고리즘","컴퓨터구조","운영체제",
        "시스템프로그래밍","데이터베이스","AI","보안","네트워크","기타"
    ]


@bp.get("/dashboard")
@jwt_required()
def dashboard():
    identity = get_jwt_identity()
    jwt_data = get_jwt()
    email = jwt_data.get("email")
    name = jwt_data.get("name")
    user_id = ObjectId(identity)
    sort_spec = [("created_at", -1), ("_id", -1)]
    cur = mongo._db["posts"].find({"user_id": user_id}).sort(sort_spec).limit(10)
    items = []
    kst = ZoneInfo("Asia/Seoul")
    for doc in cur:
        date_text = None
        dt = doc.get("created_at")
        if isinstance(dt, datetime):
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            date_text = dt.astimezone(kst).strftime("%Y-%m-%d %H:%M")
        contents = doc.get("contents", "")
        first_img = first_image_from_markdown(contents)
        text = to_plain_preview(contents)
        items.append({
            "id": str(doc.get("_id")),
            "category": doc.get("category"),
            "title": doc.get("title", ""),
            "contents_text": text,
            "first_image": first_img,
            "date_text": date_text,
        })
    return render_template(
        "dashboard.html",
        title="대시보드",
        name=name,
        email=email,
        identity=identity,
        hide_top_nav=True,
        initial_items=items,
    )


@bp.get("/post/new")
@jwt_required()
def post_new_get():
    categories = ["전체", *get_categories()]
    return render_template("post_new.html", title="새 글 작성", categories=[c for c in categories if c != "전체"], hide_top_nav=True)


@bp.post("/post/new")
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
    result = mongo._db.posts.insert_one({
        "user_id": user_id,
        "category": category,
        "title": title,
        "url": url,
        "contents": contents,
        "created_at": now_utc,
        "likes": [],
        "views": 0,
    })
    # --- post_views 컬렉션에 글 작성일 기준 첫 조회수 1건 생성 ---
    try:
        post_id = result.inserted_id
        # user_id는 이미 ObjectId
        mongo._db.post_views.update_one(
            {"post_id": post_id, "user_id": user_id, "date": now_utc.strftime("%Y-%m-%d")},
            {"$inc": {"count": 1}},
            upsert=True
        )
        # 실제로 들어갔는지 확인
        pv = mongo._db.post_views.find_one({"post_id": post_id, "user_id": user_id, "date": now_utc.strftime("%Y-%m-%d")})
        print(f"[post_views insert on post_new] {pv}")
    except Exception as e:
        print(f"[post_views upsert error on post_new] {e}")
    flash("글이 등록되었습니다.", "success")
    return redirect(url_for("posts.dashboard"))



@bp.get("/post/<id>")
@jwt_required(optional=True)
def post_detail(id):
    try:
        oid = ObjectId(id)
    except Exception:
        abort(404)

    doc = mongo._db.posts.find_one({"_id": oid})
    mongo._db.posts.update_one({"_id": oid}, {"$inc": {"views": 1}})
    # --- post_views 컬렉션에 일별 조회수 upsert ---
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if doc:
        user_id = doc["user_id"]
        try:
            if not isinstance(user_id, ObjectId):
                user_id = ObjectId(user_id)
            mongo._db.post_views.insert_one(
                {"user_id": user_id, "date": today},
                {"$inc": {"count": 1}},
                upsert=True
            )
        except Exception as e:
            print(f"[post_views upsert error on post_detail] {e}")
    
    if not doc:
        flash("요청한 글을 찾을 수 없습니다.", "error")
        return redirect(url_for("home.root"))

    # 글 작성자 정보 조회
    author = mongo._db.users.find_one({"_id": doc["user_id"]})

    # 현재 로그인 사용자 정보 및 상태
    current_user = None
    is_liked = False
    is_subscribed = False
    identity = get_jwt_identity()
    if identity:
        uid = ObjectId(identity)
        user_doc = mongo._db.users.find_one({"_id": uid})
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
    # 메타데이터: 캐시 데이터를 기존 문서 메타와 병합하여 항상 템플릿에 전달합니다.
    meta = doc.get("meta") or {}
    if url:
        try:
            cached = get_or_fetch(url)
        except Exception:
            cached = {}
        def pick(a, b):
            a_ok = bool((a or "").strip()) if isinstance(a, str) else (a is not None)
            b_ok = bool((b or "").strip()) if isinstance(b, str) else (b is not None)
            return a if a_ok else (b if b_ok else None)
        merged = {
            "title": pick(meta.get("title"), cached.get("title")),
            "description": pick(meta.get("description"), cached.get("description")),
            "image": pick(meta.get("image"), cached.get("image")),
            "site_name": pick(meta.get("site_name"), cached.get("site_name")),
            "favicon": pick(meta.get("favicon"), cached.get("favicon")),
            "content_type": pick(meta.get("content_type"), cached.get("content_type")),
        }
        # 템플릿용 메타로 교체
        meta = merged
        # DB에 저장된 메타가 덜 풍부하면 보강 저장
        try:
            def norm(x):
                return (x or "").strip() if isinstance(x, str) else x
            improved = {}
            for k, v in merged.items():
                if norm(v) and not norm((doc.get("meta") or {}).get(k)):
                    improved[k] = v
            if improved:
                mongo._db.posts.update_one({"_id": oid}, {"$set": {**{f"meta.{k}": v for k, v in improved.items()}}})
        except Exception:
            pass
    
    # 댓글 리스트: _DB에서 해당 게시글의 댓글을 조회 (최신순)
    comment_cur = mongo._db.comments.find({"post_id": oid}).sort("created_at", -1) 
    comment_list = []
    for c in comment_cur:
        user = mongo._db.users.find_one({"_id": c["user_id"]})
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
            "views" : doc.get("views", 0),
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

@bp.post("/post/<id>/like")
@jwt_required()
def post_like(id):
    try:
        oid = ObjectId(id)
        uid = ObjectId(get_jwt_identity())
    except Exception:
        return jsonify({"ok": False, "message": "잘못된 요청입니다."}), 400

    post = mongo._db.posts.find_one({"_id": oid})
    if not post:
        return jsonify({"ok": False, "message": "글을 찾을 수 없습니다."}), 404

    if uid in post.get("likes", []):
        # Unlike
        mongo._db.posts.update_one({"_id": oid}, {"$pull": {"likes": uid}})
        mongo._db.users.update_one({"_id": uid}, {"$pull": {"liked_posts": oid}})
        action = "unliked"
    else:
        # Like
        mongo._db.posts.update_one({"_id": oid}, {"$push": {"likes": uid}})
        mongo._db.users.update_one({"_id": uid}, {"$push": {"liked_posts": oid}})
        action = "liked"
        # Notify post owner if someone else liked their post
        try:
            owner_id = post.get("user_id")
            if owner_id and owner_id != uid:
                create_notification(recipient_id=owner_id, ntype="post_like", actor_id=uid, post_id=oid)
        except Exception:
            pass

    new_like_count = len(mongo._db.posts.find_one({"_id": oid}).get("likes", []))
    return jsonify({"ok": True, "action": action, "like_count": new_like_count})

@bp.post("/user/<id>/subscribe")
@jwt_required()
def user_subscribe(id):
    try:
        author_id = ObjectId(id)
        subscriber_id = ObjectId(get_jwt_identity())
    except Exception:
        return jsonify({"ok": False, "message": "잘못된 요청입니다."}), 400

    if author_id == subscriber_id:
        return jsonify({"ok": False, "message": "스스로를 구독할 수 없습니다."}), 400

    subscriber = mongo._db.users.find_one({"_id": subscriber_id})
    if not subscriber:
        return jsonify({"ok": False, "message": "사용자를 찾을 수 없습니다."}), 404

    if author_id in subscriber.get("subscriptions", []):
        # Unsubscribe
        mongo._db.users.update_one({"_id": subscriber_id}, {"$pull": {"subscriptions": author_id}})
        action = "unsubscribed"
    else:
        # Subscribe
        mongo._db.users.update_one({"_id": subscriber_id}, {"$push": {"subscriptions": author_id}})
        action = "subscribed"
        # Notify author
        try:
            create_notification(recipient_id=author_id, ntype="subscribe", actor_id=subscriber_id)
        except Exception:
            pass

    return jsonify({"ok": True, "action": action})

@bp.get("/post/<id>/edit")
@jwt_required()
def post_edit_get(id):
    try:
        oid = ObjectId(id)
    except Exception:
        return redirect(url_for("posts.dashboard"))
    uid = ObjectId(get_jwt_identity())
    doc = mongo._db["posts"].find_one({"_id": oid, "user_id": uid})
    if not doc:
        flash("요청한 글을 찾을 수 없습니다.", "error")
        return redirect(url_for("posts.dashboard"))
    categories = get_categories()
    post_obj = {
        "id": str(doc.get("_id")),
        "category": doc.get("category"),
        "title": doc.get("title", ""),
        "url": doc.get("url", ""),
        "contents": doc.get("contents", ""),
    }
    return render_template("post_edit.html", title="글 수정", categories=categories, post=post_obj, hide_top_nav=True)


@bp.post("/post/<id>/edit")
@jwt_required()
def post_edit_post(id):
    try:
        oid = ObjectId(id)
    except Exception:
        return redirect(url_for("posts.dashboard"))
    uid = ObjectId(get_jwt_identity())
    doc = mongo._db["posts"].find_one({"_id": oid, "user_id": uid})
    if not doc:
        return redirect(url_for("posts.dashboard"))
    category = request.form.get("category", "")
    title = request.form.get("title", "").strip()
    url = request.form.get("url", "").strip()
    contents = request.form.get("contents", "").strip()
    if not title or not contents or not category or category == "선택":
        flash("카테고리, 제목, 내용을 입력하세요.", "error")
        return redirect(url_for("posts.post_edit_get", id=id))
    try:
        pu = urlparse(url)
        if pu.scheme not in ("http", "https") or not pu.netloc:
            raise ValueError("invalid url")
    except Exception:
        flash("유효한 URL을 입력하세요.", "error")
        return redirect(url_for("posts.post_edit_get", id=id))
    mongo._db["posts"].update_one({"_id": oid, "user_id": uid},{"$set": {
        "category": category,
        "title": title,
        "url": url,
        "contents": contents,
    }, "$unset": {"date": "", "date_kst": "", "updated_at": "", "tag": ""}})
    flash("수정되었습니다.", "success")
    return redirect(url_for("posts.post_detail", id=id))


@bp.post("/post/<id>/delete")
@jwt_required()
def post_delete(id):
    try:
        oid = ObjectId(id)
    except Exception:
        return redirect(url_for("posts.dashboard"))
    uid = ObjectId(get_jwt_identity())
    res = mongo._db["posts"].delete_one({"_id": oid, "user_id": uid})
    if res.deleted_count:
        flash("삭제되었습니다.", "success")
    else:
        flash("삭제할 수 없습니다.", "error")
    return redirect(url_for("posts.dashboard"))


@bp.get("/api/my-posts")
@jwt_required()
def api_my_posts():
    category = request.args.get("category", "전체")
    q = request.args.get("q", "").strip()
    sort = request.args.get("sort", "new")
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
    cur = mongo._db["posts"].find(filt).sort(sort_spec).skip(skip).limit(limit)
    items = []
    kst = ZoneInfo("Asia/Seoul")
    for doc in cur:
        date_iso = None
        date_text = None
        dt = doc.get("created_at")
        if isinstance(dt, datetime):
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            kst_dt = dt.astimezone(kst)
            date_iso = kst_dt.isoformat()
            date_text = kst_dt.strftime("%Y-%m-%d %H:%M")
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


@bp.get("/api/posts")
def api_posts():
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
            pass
    sort_spec = [("created_at", -1), ("_id", -1)]
    skip = (page - 1) * limit
    cur = mongo._db["posts"].find(filt).sort(sort_spec).skip(skip).limit(limit)
    items = []
    kst = ZoneInfo("Asia/Seoul")
    for doc in cur:
        date_iso = None
        date_text = None
        dt = doc.get("created_at")
        if isinstance(dt, datetime):
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            kst_dt = dt.astimezone(kst)
            date_iso = kst_dt.isoformat()
            date_text = kst_dt.strftime("%Y-%m-%d %H:%M")
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


@bp.get("/api/preview-url")
def api_preview_url():
    raw = request.args.get("url", "").strip()
    if not raw:
        return jsonify({"ok": False, "error": "empty"}), 400
    url, err = normalize_url(raw)
    if err:
        return jsonify({"ok": False, "error": "invalid_url", "reason": err}), 200
    # Use cached metadata for speed; fetch if not cached.
    m = get_or_fetch(url)
    ok = bool(
        (m.get("title") or "").strip()
        or (m.get("description") or "").strip()
        or (m.get("image") or "").strip()
    )
    ct = (m.get("content_type") or "").lower()
    if "application/pdf" in ct or "application/octet-stream" in ct:
        return jsonify({"ok": False, "error": "unsupported_content", "content_type": m.get("content_type")}), 200
    return jsonify({
        "ok": ok,
        "title": m.get("title"),
        "description": m.get("description"),
        "image": m.get("image"),
        "url": url,
        "content_type": m.get("content_type"),
    }), 200


@bp.post("/api/uploads/images")
@jwt_required()
def api_upload_image():
    f = (request.files.get("image") or request.files.get("file"))
    if not f:
        return jsonify({"ok": False, "error": "no_file"}), 400
    ct = (f.mimetype or "").lower()
    if not ct.startswith("image/"):
        return jsonify({"ok": False, "error": "bad_type"}), 400
    max_size = 5 * 1024 * 1024
    try:
        clen = request.content_length or 0
        if clen and clen > max_size + 1024:
            return jsonify({"ok": False, "error": "too_large"}), 400
    except Exception:
        pass
    now = datetime.now(timezone.utc)
    yyyy = now.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y")
    mm = now.astimezone(ZoneInfo("Asia/Seoul")).strftime("%m")
    # Use Flask's configured static folder to ensure files are served correctly
    upload_dir = os.path.join(current_app.static_folder, "uploads", yyyy, mm)
    os.makedirs(upload_dir, exist_ok=True)
    orig = secure_filename(f.filename or "image")
    base, ext = os.path.splitext(orig)
    if not ext:
        if "/png" in ct:
            ext = ".png"
        elif "/jpeg" in ct or "/jpg" in ct:
            ext = ".jpg"
        elif "/webp" in ct:
            ext = ".webp"
        elif "/gif" in ct:
            ext = ".gif"
        else:
            ext = ".img"
    fname = f"{uuid4().hex}{ext}"
    abs_path = os.path.join(upload_dir, fname)
    f.save(abs_path)
    rel = f"uploads/{yyyy}/{mm}/{fname}"
    from flask import url_for as _url_for
    url = _url_for('static', filename=rel, _external=False)
    return jsonify({"ok": True, "url": url}), 200
