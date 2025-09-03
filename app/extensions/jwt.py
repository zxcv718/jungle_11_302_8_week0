from flask_jwt_extended import JWTManager
from flask_jwt_extended import get_jwt
from bson.objectid import ObjectId
from ..extensions import mongo

jwt = JWTManager()


@jwt.token_in_blocklist_loader
def check_if_token_in_blocklist(jwt_header, jwt_payload: dict) -> bool:
	"""Return True if token should be treated as revoked/invalid.

	We use a per-user `session_version` stored in the users collection. Each time
	a user logs in we increment their session_version. The JWTs issued include
	the session_version at issuance (in additional_claims). If the token's
	session_version doesn't match the DB value, the token is invalid.
	"""
	try:
		ident = jwt_payload.get("sub")
		if not ident:
			return True
		# Mongo stores _id as ObjectId; convert if possible
		try:
			oid = ObjectId(ident)
		except Exception:
			# If it's not an ObjectId, use as-is
			oid = ident

		# session_version is stored in a separate user_sessions collection
		session_doc = mongo._db.user_sessions.find_one({"user_id": oid})
		if not session_doc:
			# No session doc -> treat as invalid
			return True

		token_sv = jwt_payload.get("session_version") or jwt_payload.get("sessionVersion") or jwt_payload.get("sv")
		# If token lacks session_version, treat as invalid (conservative)
		if token_sv is None:
			return True

		return int(token_sv) != int(session_doc.get("session_version", 1))
	except Exception:
		# On any unexpected error, consider the token invalid
		return True
