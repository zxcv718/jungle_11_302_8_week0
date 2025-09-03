# Flask Login + Register with JWT (Cookies) and MongoDB

Requirements:
- Flask, Jinja2
- MongoDB (pymongo)
- JWT (flask-jwt-extended)
- TailwindCSS via CDN

## Setup

1. Create and fill `.env` (copy from `.env.example`).
2. Install Python packages.
3. Run the app and open http://127.0.0.1:5050

### Quickstart

```
cp .env.example .env
# edit .env as needed (SECRET_KEY, JWT_SECRET_KEY, MONGO_URI)
```

Then run with Python 3.9+.

Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Notes
- App runs via `main.py` which loads `.env` automatically and uses the application factory in `app/`.
- Default port is 5050. Set HOST/PORT in `.env` if needed.

## Notes
- Access token expires in 15 minutes, Refresh token in 30 minutes.
- Tokens are stored in HttpOnly cookies. CSRF is disabled for brevity. Enable in production.
- Tailwind CSS is loaded via CDN, no Node setup required.
