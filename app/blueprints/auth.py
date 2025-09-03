from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, make_response
from werkzeug.security import check_password_hash, generate_password_hash
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
from datetime import datetime, timezone
from ..extensions import mongo

bp = Blueprint("auth", __name__)


@bp.get("/register")
def register_get():
    try:
        verify_jwt_in_request(optional=True)
        if get_jwt_identity():
            return redirect(url_for("home.root"))
    except Exception:
        pass
    return render_template("register.html", title="회원가입")


@bp.post("/register")
def register_post():
    email = request.form.get("email", "").strip().lower()
    name = request.form.get("name", "").strip()
    password = request.form.get("password", "")
    password_confirm = request.form.get("password_confirm", "")

    if not email or not name or not password or not password_confirm:
        flash("모든 필드를 입력하세요.", "error")
    return redirect(url_for("auth.register_get"))
    if password != password_confirm:
        flash("비밀번호가 일치하지 않습니다.", "error")
    return redirect(url_for("auth.register_get"))

    if mongo.db.users.find_one({"email": email}):
        flash("이미 가입된 이메일입니다.", "error")
        return redirect(url_for("auth.register_get"))

    pw_hash = generate_password_hash(password)
    mongo.db.users.insert_one(
        {"email": email, "name": name, "password": pw_hash, "created_at": datetime.now(timezone.utc), "liked_posts": [], "subscriptions": []}
    )
    flash("가입이 완료되었습니다. 로그인해주세요.", "success")
    return redirect(url_for("auth.login_get"))


@bp.get("/api/check-email")
def api_check_email():
    email = request.args.get("email", "").strip().lower()
    if not email:
        return jsonify({"exists": False}), 200
    exists = mongo.db["users"].find_one({"email": email}) is not None
    return jsonify({"exists": exists}), 200


@bp.get("/login")
def login_get():
    try:
        verify_jwt_in_request(optional=True)
        if get_jwt_identity():
            return redirect(url_for("home.root"))
    except Exception:
        pass
    return render_template("login.html", title="로그인")


@bp.post("/login")
def login_post():
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    user = mongo.db.users.find_one({"email": email})
    if not user or not check_password_hash(user["password"], password):
        flash("이메일 또는 비밀번호가 올바르지 않습니다.", "error")
        return redirect(url_for("auth.login_get"))

    identity = str(user.get("_id"))
    claims = {"email": user["email"], "name": user.get("name", "")}

    access_token = create_access_token(identity=identity, additional_claims=claims)
    refresh_token = create_refresh_token(identity=identity, additional_claims=claims)

    resp = make_response(redirect(url_for("home.root")))
    set_access_cookies(resp, access_token)
    set_refresh_cookies(resp, refresh_token)
    return resp


@bp.post("/refresh")
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


@bp.post("/logout")
def logout():
    resp = make_response(redirect(url_for("auth.login_get")))
    unset_jwt_cookies(resp)
    flash("로그아웃되었습니다.", "success")
    return resp


# JWT error handlers -> redirect to login or show flash message
from ..extensions.jwt import jwt


@jwt.unauthorized_loader
def handle_unauthorized(reason):
    from flask import request
    if request.path.startswith("/api") or request.path.startswith("/refresh"):
        return jsonify({"error": "unauthorized"}), 401
    return redirect(url_for("auth.login_get"))


@jwt.invalid_token_loader
def handle_invalid(reason):
    from flask import request
    if request.path.startswith("/api") or request.path.startswith("/refresh"):
        return jsonify({"error": "invalid_token"}), 401
    return redirect(url_for("auth.login_get"))


@jwt.expired_token_loader
def handle_expired(jwt_header, jwt_payload):
    from flask import request
    if request.path.startswith("/refresh") or (jwt_payload or {}).get("type") == "refresh":
        return jsonify({"error": "refresh_expired"}), 401
    if request.path.startswith("/api"):
        return jsonify({"error": "access_expired"}), 401
    orig = request.full_path if request.query_string else request.path
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
""" % (url_for('auth.refresh'), orig, url_for('auth.login_get'), url_for('auth.login_get'))
    return html, 401, {"Content-Type": "text/html; charset=utf-8"}


@bp.get("/api/session-status")
def api_session_status():
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
