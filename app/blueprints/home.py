from flask import Blueprint, render_template, jsonify, request
from flask_jwt_extended import verify_jwt_in_request, get_jwt, get_jwt_identity, jwt_required
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from bson import ObjectId

# extensions와 utils에서 필요한 모듈을 임포트합니다.
from ..extensions import mongo
from metadata import fetch_and_extract_metadata
from ..services.notifications import serialize_notification

bp = Blueprint("home", __name__)


@bp.get("/")
def root():
    user_info = None
    current_user_doc = None
    try:
        verify_jwt_in_request(optional=True)
        identity = get_jwt_identity()
        if identity:
            uid = ObjectId(identity)
            current_user_doc = mongo._db.users.find_one({"_id": uid})
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

    # We'll fill author names after collecting all user_ids and cache lookups
    author_map = {}

    def ensure_oid(x):
        """Normalize possible ObjectId/string to ObjectId; return None if invalid."""
        if isinstance(x, ObjectId):
            return x
        if isinstance(x, str):
            try:
                return ObjectId(x)
            except Exception:
                return None
        return None

    def get_author_name(uid):
        if not uid:
            return None
        # Normalize id for consistent cache/db lookups
        oid = ensure_oid(uid)
        if oid is None:
            return "알 수 없음"
        try:
            return author_map[oid]
        except KeyError:
            try:
                u = mongo._db.users.find_one({"_id": oid}, {"name": 1})
                name = u.get("name", "알 수 없음") if u else "알 수 없음"
                author_map[oid] = name
                return name
            except Exception:
                return None

    def process_post_doc(doc):
        # ... (기존 코드와 동일)
        dt = doc.get("created_at")
        date_text = None
        if isinstance(dt, datetime):
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            kst_dt = dt.astimezone(kst)
            date_text = kst_dt.strftime("%Y-%m-%d %H:%M")
        # Prefer author_name provided by pipeline; fallback to cache/db lookup
        author_name = (doc.get("author_name") or
                       get_author_name(doc.get("user_id")))

        return {
            "id": str(doc.get("_id")),
            "category": doc.get("category"),
            "title": doc.get("title", ""),
            "url": doc.get("url", ""),
            "contents": doc.get("contents", ""),
            "date_text": date_text,
            "author_name": author_name,
            "meta": doc.get("meta", {}),
            "like_count": doc.get("like_count", 0),
        }

    # 전체 글 (추천 수 많은 순으로 자동 정렬)
    pipeline_all = [
        {"$addFields": {"like_count": {"$size": {"$ifNull": ["$likes", []]}}}},
        {"$sort": {"like_count": -1, "created_at": -1}},
        {"$limit": 15},  # 캐러셀(5) + 전체 글 목록(10)
        {"$lookup": {"from": "users", "localField": "user_id", "foreignField": "_id", "as": "_author"}},
        {"$addFields": {"author_name": {"$arrayElemAt": ["$_author.name", 0]}}},
        {"$project": {
            "user_id": 1, "category": 1, "title": 1, "url": 1, "contents": 1, "created_at": 1, "meta": 1,
            "like_count": 1, "author_name": 1
        }},
    ]
    # Fetch raw posts first to gather user_ids
    raw_sorted_posts = list(mongo._db.posts.aggregate(pipeline_all))
    try:
        # Normalize collected user ids to ObjectId for $in query
        user_ids = list({ensure_oid(doc.get("user_id")) for doc in raw_sorted_posts if doc.get("user_id")})
        user_ids = [uid for uid in user_ids if uid is not None]
        if user_ids:
            users = mongo._db.users.find({"_id": {"$in": user_ids}}, {"name": 1})
            for u in users:
                author_map[u["_id"]] = u.get("name", "알 수 없음")
    except Exception:
        pass
    # Now process posts with author_map available via closure
    sorted_posts = [process_post_doc(doc) for doc in raw_sorted_posts]

    # 캐러셀을 위한 인기글 Top 5
    popular_posts = sorted_posts[:5]

    # 전체 글 목록 (Top 5 제외)
    all_posts = sorted_posts[5:]

    # 구독한 저자 글 (로그인 시)
    subscribed_posts = []
    if current_user_doc:
        subscribed_author_ids = current_user_doc.get("subscriptions", [])
        if subscribed_author_ids:
            # 구독한 저자의 글만 필터링하여 최신순으로 정렬
            # Normalize subscription ids to ObjectIds for matching
            sa_oids = [ensure_oid(x) for x in subscribed_author_ids]
            sa_oids = [x for x in sa_oids if x is not None]
            sub_pipeline = [
                {"$match": {"user_id": {"$in": sa_oids}}},
                {"$addFields": {"like_count": {"$size": {"$ifNull": ["$likes", []]}}}},
                {"$sort": {"created_at": -1}},
                {"$lookup": {"from": "users", "localField": "user_id", "foreignField": "_id", "as": "_author"}},
                {"$addFields": {"author_name": {"$arrayElemAt": ["$_author.name", 0]}}},
                {"$project": {
                    "user_id": 1,
                    "category": 1, "title": 1, "url": 1, "contents": 1, "created_at": 1, "meta": 1,
                    "like_count": 1, "author_name": 1
                }},
            ]
            raw_sub_posts = list(mongo._db.posts.aggregate(sub_pipeline))
            # Preload any missing authors into cache
            try:
                sub_user_ids = list({ensure_oid(doc.get("user_id")) for doc in raw_sub_posts if doc.get("user_id")})
                sub_user_ids = [uid for uid in sub_user_ids if uid is not None]
                missing = [uid for uid in sub_user_ids if uid not in author_map]
                if missing:
                    users2 = mongo._db.users.find({"_id": {"$in": missing}}, {"name": 1})
                    for u in users2:
                        author_map[u["_id"]] = u.get("name", "알 수 없음")
            except Exception:
                pass
            subscribed_posts = [process_post_doc(doc) for doc in raw_sub_posts]

    # 주의: 과거에는 여기서 외부 URL에 동기 요청하여 메타 이미지를 채웠습니다.
    # 이로 인해 홈 렌더링이 느려질 수 있어 제거했습니다.
    # 메타 이미지는 글 상세 페이지 접근 시 캐시되며, 추후 비동기 로딩 API로 대체 가능.

    return render_template(
        "home.html", 
        posts=all_posts,  # 전체 글 목록 (Top 5 제외)
        subscribed_posts=subscribed_posts, # 구독 글 목록
        popular_posts=popular_posts, # 인기글 Top 5
        user_info=user_info
    )


# Fallback notification APIs (same paths used by client). Keep here to ensure availability.
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