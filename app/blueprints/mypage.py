from flask import Blueprint, render_template, request, redirect, url_for, flash, make_response
from flask_jwt_extended import jwt_required, get_jwt_identity, unset_jwt_cookies
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

	# 1) 수집: 내가 작성한 게시글 ID들
	post_ids = [doc["_id"] for doc in mongo._db["posts"].find({"user_id": uid}, {"_id": 1})]

	# 2) 댓글 삭제: 내가 쓴 댓글 + 내 게시글에 달린 댓글
	try:
		mongo._db["comments"].delete_many({"user_id": uid})
		if post_ids:
			mongo._db["comments"].delete_many({"post_id": {"$in": post_ids}})
	except Exception:
		pass

	# 3) 좋아요 정리: 다른 댓글에서 내 좋아요 흔적 제거
	try:
		mongo._db["comments"].update_many({"likes": uid}, {"$pull": {"likes": uid}})
	except Exception:
		pass

	# 4) 구독 정리: 다른 유저들의 subscriptions에서 나 제거
	try:
		mongo._db["users"].update_many({"subscriptions": uid}, {"$pull": {"subscriptions": uid}})
	except Exception:
		pass

	# 5) 알림/채팅/세션/조회수 정리
	try:
		mongo._db["notifications"].delete_many({"$or": [{"user_id": uid}, {"actor_id": uid}]})
		mongo._db["chat_messages"].delete_many({"user_id": uid})
		# 사용자 세션 버전 정보 제거
		mongo._db.user_sessions.delete_many({"user_id": uid})
		# post_views 컬렉션 정리(있다면)
		try:
			mongo._db.post_views.delete_many({"user_id": uid})
		except Exception:
			pass
	except Exception:
		pass

	# 6) 다른 유저들의 liked_posts에서 내가 삭제한 게시글 ID들 제거
	try:
		if post_ids:
			mongo._db["users"].update_many({"liked_posts": {"$in": post_ids}}, {"$pull": {"liked_posts": {"$in": post_ids}}})
	except Exception:
		pass

	# 7) 내 게시글 삭제
	try:
		if post_ids:
			mongo._db["posts"].delete_many({"_id": {"$in": post_ids}})
	except Exception:
		pass

	# 8) 사용자 삭제
	try:
		mongo._db["users"].delete_one({"_id": uid})
	except Exception:
		pass

	flash("회원탈퇴가 완료되었습니다.", "success")
	# JWT 쿠키 제거 후 로그인 페이지로 이동
	resp = make_response(redirect(url_for("auth.login_get")))
	unset_jwt_cookies(resp)
	return resp


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
