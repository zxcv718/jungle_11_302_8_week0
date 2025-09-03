from flask import Blueprint, render_template, request
from flask_jwt_extended import verify_jwt_in_request, get_jwt, get_jwt_identity
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ..extensions import mongo

bp = Blueprint("home", __name__)


@bp.get("/")
def root():
    all_posts = []
    user_info = None
    try:
        verify_jwt_in_request(optional=True)
        identity = get_jwt_identity()
        if identity:
            jwt_data = get_jwt()
            user_info = {
                "id": identity,
                "name": jwt_data.get("name"),
                "email": jwt_data.get("email"),
            }
    except Exception:
        pass

    try:
        cur = mongo._db["posts"].find({}).sort([("created_at", -1)])
        kst = ZoneInfo("Asia/Seoul")
        for doc in cur:
            dt = doc.get("created_at")
            date_text = None
            if isinstance(dt, datetime):
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                kst_dt = dt.astimezone(kst)
                date_text = kst_dt.strftime("%Y-%m-%d %H:%M")
            all_posts.append({
                "id": str(doc.get("_id")),
                "category": doc.get("category"),
                "title": doc.get("title", ""),
                "url": doc.get("url", ""),
                "contents": doc.get("contents", ""),
                "date_text": date_text,
                "meta": doc.get("meta", {}),
            })
    except Exception:
        # DB 연결 문제 등은 홈을 빈 목록으로 표시
        all_posts = []

    return render_template("home.html", posts=all_posts, user_info=user_info)
