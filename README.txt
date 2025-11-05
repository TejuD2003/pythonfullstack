Project: projectinternship (modified)

1. Create venv and install:
   python -m venv venv
   venv\Scripts\activate    (Windows)
   source venv/bin/activate (macOS/Linux)
   pip install -r requirements.txt

2. Configure environment variables (or use .env loader):
   - DATABASE_URL (optional; default points to your Postgres)
   - EMAIL_USER and EMAIL_PASS (use app password for Gmail)
   - EMAIL_TO (fallback recipient)

   Example (Windows PowerShell):
   $env:EMAIL_USER="you@gmail.com"
   $env:EMAIL_PASS="your-app-password"
   $env:DATABASE_URL="sqlite:///test.db"   # for quick testing

3. Run:
   python app.py

4. Open http://127.0.0.1:5000/ and add tasks (use datetime-local). The scheduler checks every minute and will:
   - send a 1-day reminder once per task
   - send a 1-hour reminder once per task

Notes:
- scheduler job runs inside app.app_context() so DB queries work.
- If you use Postgres and tables already exist, consider adding the new columns via migration (Alembic) if needed.
