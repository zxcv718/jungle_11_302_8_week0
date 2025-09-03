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

    # Mail server settings
    MAIL_SERVER = os.getenv("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = int(os.getenv("MAIL_PORT", 587))
    MAIL_USE_TLS = os.getenv("MAIL_USE_TLS", "true").lower() == "true"
    MAIL_USERNAME = os.getenv("MAIL_USERNAME", "hsmun002@gmail.com")
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "lqiz lzxo fkwc gypw")
    MAIL_DEFAULT_SENDER = os.getenv("MAIL_DEFAULT_SENDER", "hsmun002@gmail.com")
