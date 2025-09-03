from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_jwt_extended import jwt_required, get_jwt_identity
from bson import ObjectId
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ..extensions import mongo

mypage_bp = Blueprint("mypage", __name__)


@mypage_bp.get("/mypage", endpoint="mypage")
@jwt_required()
def mypage_home():
	# Fetch minimal user + authored posts/comments for page
	uid = ObjectId(get_jwt_identity())
	user = mongo._db["users"].find_one({"_id": uid}) or {}
	# format created_at -> KST date string
	created_str = None
	try:
		dt = user.get("created_at")
		if isinstance(dt, datetime):
			if dt.tzinfo is None:
				dt = dt.replace(tzinfo=timezone.utc)
			kst = dt.astimezone(ZoneInfo("Asia/Seoul"))
			created_str = kst.strftime("%Y-%m-%d %H:%M")
	except Exception:
		created_str = None
	my_posts = []
	for p in mongo._db["posts"].find({"user_id": uid}).sort([("_id", -1)]).limit(100):
		my_posts.append({"id": str(p["_id"]), "title": p.get("title", ""), "views": p.get("views", 0), "created_at": ""})
	my_comments = []
	for c in mongo._db["comments"].find({"user_id": uid}).sort([("_id", -1)]).limit(100):
		my_comments.append({"id": str(c["_id"]), "content": c.get("content", ""), "created_at": "", "post_id": str(c.get("post_id")) if c.get("post_id") else None})
	return render_template(
		"mypage.html",
		title="마이페이지",
		hide_top_nav=True,
		user={"name": user.get("name"), "email": user.get("email"), "created_at": created_str},
		my_posts=my_posts,
		my_comments=my_comments,
	)


@mypage_bp.get("/mypage/profile-edit", endpoint="profile_edit")
@jwt_required()
def profile_edit_get():
	uid = ObjectId(get_jwt_identity())
	user = mongo._db["users"].find_one({"_id": uid}) or {}
	return render_template("profile_edit.html", title="개인 정보 변경", hide_top_nav=True, user={"name": user.get("name", ""), "email": user.get("email", "")})


@mypage_bp.post("/mypage/profile-edit", endpoint="profile_edit_post")
@jwt_required()
def profile_edit_post():
	uid = ObjectId(get_jwt_identity())
	name = (request.form.get("name") or "").strip()
	password = (request.form.get("password") or "").strip()
	password_confirm = (request.form.get("password_confirm") or "").strip()
	updates = {}
	if name:
		updates["name"] = name
	if password:
		if password != password_confirm:
			flash("비밀번호가 일치하지 않습니다.", "error")
			return redirect(url_for("mypage.profile_edit"))
		from werkzeug.security import generate_password_hash
		updates["password"] = generate_password_hash(password)
	if updates:
		mongo._db["users"].update_one({"_id": uid}, {"$set": updates})
		flash("수정되었습니다.", "success")
	return redirect(url_for("mypage.mypage"))


@mypage_bp.post("/mypage/delete", endpoint="user_delete")
@jwt_required()
def user_delete():
	uid = ObjectId(get_jwt_identity())
	# Minimal behavior: do nothing but redirect with flash (safety)
	flash("계정 삭제는 데모에서 비활성화되어 있습니다.", "error")
	return redirect(url_for("mypage.mypage"))


@mypage_bp.post("/mypage/posts/delete", endpoint="multi_post_delete")
@jwt_required()
def multi_post_delete():
	uid = ObjectId(get_jwt_identity())
	ids = request.form.getlist("post_ids")
	oids = []
	for s in ids:
		try:
			oids.append(ObjectId(s))
		except Exception:
			pass
	if oids:
		mongo._db["posts"].delete_many({"_id": {"$in": oids}, "user_id": uid})
		flash("선택한 글이 삭제되었습니다.", "success")
	return redirect(url_for("mypage.mypage"))


@mypage_bp.post("/mypage/comments/delete", endpoint="multi_comment_delete")
@jwt_required()
def multi_comment_delete():
	uid = ObjectId(get_jwt_identity())
	ids = request.form.getlist("comment_ids")
	oids = []
	for s in ids:
		try:
			oids.append(ObjectId(s))
		except Exception:
			pass
	if oids:
		mongo._db["comments"].delete_many({"_id": {"$in": oids}, "user_id": uid})
		flash("선택한 댓글이 삭제되었습니다.", "success")
	return redirect(url_for("mypage.mypage"))
