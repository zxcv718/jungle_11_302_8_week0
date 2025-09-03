from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity
from werkzeug.security import generate_password_hash
from bson import ObjectId
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from ..extensions import mongo

mypage_bp = Blueprint("mypage", __name__)

@mypage_bp.get("/mypage")
@jwt_required()
def mypage():
    user_id = ObjectId(get_jwt_identity())
    user = mongo.db["users"].find_one({"_id": user_id})
    kst = ZoneInfo("Asia/Seoul")
    created_at = user.get("created_at")
    if created_at and created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    user_info = {
        "name": user.get("name"),
        "email": user.get("email"),
        "created_at": created_at.astimezone(kst).strftime("%Y-%m-%d %H:%M") if created_at else None
    }
    my_posts = []
    for p in mongo.db["posts"].find({"user_id": user_id}).sort("created_at", -1):
        dt = p.get("created_at")
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        my_posts.append({
            "id": str(p["_id"]),
            "title": p.get("title", ""),
            "created_at": dt.astimezone(kst).strftime("%Y-%m-%d %H:%M") if dt else ""
        })
    my_comments = []
    for c in mongo.db["comments"].find({"user_id": user_id}).sort("created_at", -1):
        dt = c.get("created_at")
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        my_comments.append({
            "id": str(c["_id"]),
            "content": c.get("content", ""),
            "created_at": dt.astimezone(kst).strftime("%Y-%m-%d %H:%M") if dt else "",
            "post_id": str(c.get("post_id")) if c.get("post_id") else None
        })
    return render_template("mypage.html", user=user_info, my_posts=my_posts, my_comments=my_comments)

@mypage_bp.post("/user/delete")
@jwt_required()
def user_delete():
    user_id = ObjectId(get_jwt_identity())
    mongo.db["users"].delete_one({"_id": user_id})
    mongo.db["posts"].delete_many({"user_id": user_id})
    mongo.db["comments"].delete_many({"user_id": user_id})
    flash("회원 탈퇴가 완료되었습니다.", "success")
    resp = redirect(url_for("auth.login_get"))
    resp.delete_cookie("access_token_cookie")
    resp.delete_cookie("refresh_token_cookie")
    return resp

@mypage_bp.post("/multi_post_delete")
@jwt_required()
def multi_post_delete():
    user_id = ObjectId(get_jwt_identity())
    post_ids = request.form.getlist("post_ids")
    if post_ids:
        ids = [ObjectId(pid) for pid in post_ids]
        mongo.db["posts"].delete_many({"_id": {"$in": ids}, "user_id": user_id})
        flash(f"{len(ids)}개의 글이 삭제되었습니다.", "success")
    return redirect(url_for("mypage.mypage"))

@mypage_bp.post("/multi_comment_delete")
@jwt_required()
def multi_comment_delete():
    user_id = ObjectId(get_jwt_identity())
    comment_ids = request.form.getlist("comment_ids")
    if comment_ids:
        ids = [ObjectId(cid) for cid in comment_ids]
        mongo.db["comments"].delete_many({"_id": {"$in": ids}, "user_id": user_id})
        flash(f"{len(ids)}개의 댓글이 삭제되었습니다.", "success")
    return redirect(url_for("mypage.mypage"))

@mypage_bp.get("/profile/edit")
@jwt_required()
def profile_edit():
    user_id = ObjectId(get_jwt_identity())
    user = mongo.db["users"].find_one({"_id": user_id})
    return render_template("profile_edit.html", user=user)

@mypage_bp.post("/profile/edit")
@jwt_required()
def profile_edit_post():
    user_id = ObjectId(get_jwt_identity())
    name = request.form.get("name", "").strip()
    password = request.form.get("password", "")
    password_confirm = request.form.get("password_confirm", "")
    if not name:
        flash("이름을 입력하세요.", "error")
        return redirect(url_for("mypage.profile_edit"))
    update = {"name": name}
    if password:
        if password != password_confirm:
            flash("비밀번호가 일치하지 않습니다.", "error")
            return redirect(url_for("mypage.profile_edit"))
        update["password"] = generate_password_hash(password)
    mongo.db["users"].update_one({"_id": user_id}, {"$set": update})
    flash("개인 정보가 변경되었습니다.", "success")
    return redirect(url_for("mypage.mypage"))
