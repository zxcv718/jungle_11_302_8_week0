import os
from datetime import timedelta

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret")
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-jwt-secret")
    MONGO_URI = os.getenv(
        "MONGO_URI",
        "mongodb+srv://USERNAME:PASSWORD@cluster0.sashzbh.mongodb.net/login?retryWrites=true&w=majority&appName=Cluster0",
    )

    # JWT in HttpOnly cookies
    JWT_TOKEN_LOCATION = ["cookies"]
    JWT_COOKIE_SECURE = os.getenv("COOKIE_SECURE", "False").lower() == "true"
    JWT_COOKIE_SAMESITE = "Lax"
    # For demo simplicity; enable in production
    JWT_COOKIE_CSRF_PROTECT = False

    # Expirations
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(minutes=15)
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(minutes=30)
