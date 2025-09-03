from flask import Blueprint, request, redirect, url_for, flash, jsonify, render_template_string
from flask_jwt_extended import jwt_required, get_jwt_identity
from bson import ObjectId
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ..extensions import mongo

bp = Blueprint("comments", __name__)


def _best_comments_html(post_id: ObjectId) -> str:
    kst = ZoneInfo("Asia/Seoul")
    items = []
    for c in mongo._db["comments"].find({"post_id": post_id}):
        user = mongo._db["users"].find_one({"_id": c["user_id"]})
        dt = c.get("created_at")
        if isinstance(dt, datetime) and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        items.append({
            "id": str(c["_id"]),
            "user_id": str(c["user_id"]),
            "user_name": user["name"] if user else "알 수 없음",
            "content": c.get("content", ""),
            "created_at": dt.astimezone(kst).strftime("%Y-%m-%d %H:%M") if isinstance(dt, datetime) else "",
            "like_count": len(c.get("likes", [])),
        })
    best = [x for x in items if x["like_count"] > 0]
    best.sort(key=lambda x: x["like_count"], reverse=True)
    best = best[:3]
    return render_template_string(
        '{% if best and best|length > 0 %}'
        '<div class="mb-6">'
        '  <h2 class="text-lg font-bold text-emerald-700 mb-2">베스트 댓글</h2>'
        '  <div class="space-y-2">'
        '  {% for c in best %}'
        '    <div class="border rounded px-3 py-2 bg-yellow-50 flex justify-between items-center">'
        '      <div>'
        '        <span class="font-semibold">{{ c.user_name }}</span>'
        '        <span class="text-xs text-gray-500 ml-2">{{ c.created_at }}</span>'
        '        <div class="mt-1">{{ c.content }}</div>'
        '      </div>'
        '      <span class="text-emerald-600 font-bold text-sm">추천 수 {{ c.like_count }}</span>'
        '    </div>'
        '  {% endfor %}'
        '  </div>'
        '</div>'
        '{% endif %}', best=best)


@bp.post("/post/<id>/comments")
@jwt_required()
def post_comments(id):
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    try:
        post_id = ObjectId(id)
        user_id = ObjectId(get_jwt_identity())
    except Exception:
        msg = "잘못된 요청입니다."
        return (jsonify({"ok": False, "message": msg}), 400) if is_ajax else redirect(url_for("posts.dashboard"))

    content = (request.form.get("content") or "").strip()
    if not content:
        msg = "댓글 내용을 입력하세요."
        return (jsonify({"ok": False, "message": msg}), 400) if is_ajax else redirect(url_for("posts.post_detail", id=id))

    now = datetime.now(timezone.utc)
    ins = mongo._db["comments"].insert_one({
        "post_id": post_id,
        "user_id": user_id,
        "content": content,
        "created_at": now,
        "likes": [],
    })
    user = mongo._db["users"].find_one({"_id": user_id})

    kst = ZoneInfo("Asia/Seoul")
    c = {
        "id": str(ins.inserted_id),
        "user_name": user["name"] if user else "알 수 없음",
        "content": content,
        "created_at": now.astimezone(kst).strftime("%Y-%m-%d %H:%M"),
    }
    comment_html = render_template_string(
        '<div class="border rounded px-3 py-2 bg-white flex justify-between items-center">'
        '  <div>'
        '    <span class="font-semibold">{{ c.user_name }}</span>'
        '    <span class="text-xs text-gray-500 ml-2">{{ c.created_at }}</span>'
        '    <div class="mt-1">{{ c.content }}</div>'
        '    <button type="button" class="text-emerald-600 text-xs px-2 py-1 rounded border" data-cid="{{ c.id }}" onclick="likeComment(this.dataset.cid, this)">추천</button>'
        '    <span class="ml-1 text-xs text-gray-700" id="like-count-{{ c.id }}">0</span>'
        '  </div>'
        '  <form action="' + url_for('comments.comment_delete', comment_id="__CID__") + '" method="post" onsubmit="return confirm(\'댓글을 삭제하시겠습니까?\');">'
        '    <button class="text-red-600 text-xs px-2 py-1 rounded">삭제</button>'
        '  </form>'
        '</div>'
    ).replace("__CID__", c["id"])  # 간단 치환

    best_html = _best_comments_html(post_id)

    if is_ajax:
        return jsonify({"ok": True, "comment_html": comment_html, "best_comments_html": best_html})
    flash("댓글이 등록되었습니다.", "success")
    return redirect(url_for("posts.post_detail", id=id))


@bp.post("/delete/<comment_id>/comment")
@jwt_required()
def comment_delete(comment_id):
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    try:
        cid = ObjectId(comment_id)
        uid = ObjectId(get_jwt_identity())
    except Exception:
        msg = "잘못된 요청입니다."
        return (jsonify({"ok": False, "message": msg}), 400) if is_ajax else redirect(url_for("posts.dashboard"))

    comment = mongo._db["comments"].find_one({"_id": cid, "user_id": uid})
    post_id = comment.get("post_id") if comment else None
    res = mongo._db["comments"].delete_one({"_id": cid, "user_id": uid})
    if not res.deleted_count:
        msg = "삭제할 수 없습니다."
        return (jsonify({"ok": False, "message": msg}), 400) if is_ajax else redirect(url_for("posts.dashboard"))

    if is_ajax and post_id:
        best_html = _best_comments_html(post_id)
        return jsonify({"ok": True, "comment_id": str(comment_id), "best_comments_html": best_html, "message": "댓글이 삭제되었습니다."})

    flash("댓글이 삭제되었습니다.", "success")
    return redirect(url_for("posts.post_detail", id=str(post_id))) if post_id else redirect(url_for("posts.dashboard"))


@bp.post("/like/<comment_id>/comment")
@jwt_required()
def comment_like(comment_id):
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    try:
        cid = ObjectId(comment_id)
        uid = ObjectId(get_jwt_identity())
    except Exception:
        msg = "잘못된 요청입니다."
        return (jsonify({"ok": False, "message": msg}), 400) if is_ajax else redirect(url_for("posts.dashboard"))

    comment = mongo._db["comments"].find_one({"_id": cid})
    if not comment:
        msg = "이미 삭제된 댓글입니다."
        return (jsonify({"ok": False, "message": msg}), 404) if is_ajax else redirect(url_for("posts.dashboard"))

    likes = comment.get("likes", [])
    if isinstance(likes, int):
        likes = []
    if uid in likes:
        mongo._db["comments"].update_one({"_id": cid}, {"$pull": {"likes": uid}})
        msg = "댓글 추천을 취소했습니다."
    else:
        mongo._db["comments"].update_one({"_id": cid}, {"$push": {"likes": uid}})
        msg = "댓글을 추천했습니다."

    new_comment = mongo._db["comments"].find_one({"_id": cid})
    like_count = len(new_comment.get("likes", [])) if new_comment else 0

    if is_ajax:
        best_html = _best_comments_html(comment.get("post_id"))
        return jsonify({"ok": True, "like_count": like_count, "message": msg, "best_comments_html": best_html})

    flash(msg, "success")
    post_id = comment.get("post_id")
    return redirect(url_for("posts.post_detail", id=str(post_id))) if post_id else redirect(url_for("posts.dashboard"))
