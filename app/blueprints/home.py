from flask import Blueprint, render_template
from flask_jwt_extended import verify_jwt_in_request, get_jwt, get_jwt_identity
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from bson import ObjectId

# extensions와 utils에서 필요한 모듈을 임포트합니다.
from ..extensions import mongo
from metadata import fetch_and_extract_metadata

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

    def process_post_doc(doc):
        # ... (기존 코드와 동일)
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
        {"$sort": {"like_count": -1, "created_at": -1}},
        {"$limit": 15} # 캐러셀(5) + 전체 글 목록(10)
    ]
    sorted_posts = [process_post_doc(doc) for doc in mongo._db.posts.aggregate(pipeline_all)]

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
            sub_pipeline = [
                {"$match": {"user_id": {"$in": subscribed_author_ids}}},
                {"$sort": {"created_at": -1}},
                {"$project": {
                    "category": 1, "title": 1, "url": 1, "contents": 1, "created_at": 1, "meta": 1,
                    "like_count": {"$size": {"$ifNull": ["$likes", []]}}
                }},
            ]
            subscribed_posts = [process_post_doc(doc) for doc in mongo._db.posts.aggregate(sub_pipeline)]

    # 인기글에 대한 메타데이터(이미지)를 가져와 채워줍니다.
    for post in popular_posts:
        if post.get("url"):
            meta = fetch_and_extract_metadata(post["url"])
            post["meta"] = {
                "image": meta.image
            }

    return render_template(
        "home.html", 
        posts=all_posts,  # 전체 글 목록 (Top 5 제외)
        subscribed_posts=subscribed_posts, # 구독 글 목록
        popular_posts=popular_posts, # 인기글 Top 5
        user_info=user_info
    )