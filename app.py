# app.py
from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
import os
import smtplib
from email.message import EmailMessage
import traceback

# optional .env loader
try:
    from dotenv import load_dotenv
    
    load_dotenv()
except Exception:
    pass


# ------------- Config & App -------------
app = Flask(__name__)

# DATABASE_URL should be set in .env (recommended)
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv(
    'DATABASE_URL',
    'sqlite:///test.db'  # fallback for quick testing
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# SocketIO: allow local testing origins; set to specific origin(s) in production
# socketio = SocketIO(cors_allowed_origins="*")

# db = SQLAlchemy(app)
# scheduler = BackgroundScheduler()
# Ensure async worker is available (eventlet recommended for Flask-SocketIO)
# install with: pip install eventlet
# then create socketio with the app attached
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

db = SQLAlchemy(app)
scheduler = BackgroundScheduler()

# ------------- Email config (env) -------------
EMAIL_HOST = os.getenv('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))  # 587=STARTTLS, 465=SSL
EMAIL_USER = os.getenv('EMAIL_USER', '')         # your email (e.g. your@gmail.com)
EMAIL_PASS = os.getenv('EMAIL_PASS', '')         # app password (recommended)
DEFAULT_FROM = os.getenv('EMAIL_FROM') or EMAIL_USER
DEFAULT_TO = os.getenv('EMAIL_TO', '')           # fallback recipient
EMAIL_DEBUG = os.getenv('EMAIL_DEBUG', 'false').lower() in ('1','true','yes')

def send_email(to_address, subject, body):
    """Send a plain-text email. Returns True on success, False on failure."""
    if not EMAIL_USER or not EMAIL_PASS:
        print("[Email] ERROR: EMAIL_USER or EMAIL_PASS not set in environment.")
        return False

    try:
        msg = EmailMessage()
        msg['Subject'] = subject
        msg['From'] = DEFAULT_FROM or EMAIL_USER
        msg['To'] = to_address
        msg.set_content(body)

        # choose SSL vs STARTTLS
        if EMAIL_PORT == 465:
            with smtplib.SMTP_SSL(EMAIL_HOST, EMAIL_PORT, timeout=30) as server:
                if EMAIL_DEBUG:
                    server.set_debuglevel(1)
                server.login(EMAIL_USER, EMAIL_PASS)
                server.send_message(msg)
        else:
            with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT, timeout=30) as server:
                if EMAIL_DEBUG:
                    server.set_debuglevel(1)
                server.ehlo()
                if EMAIL_PORT in (587, 25):
                    server.starttls()
                    server.ehlo()
                server.login(EMAIL_USER, EMAIL_PASS)
                server.send_message(msg)

        print(f"[Email] Sent to {to_address}: {subject}")
        return True

    except Exception as e:
        print("[Email] Failed to send — exception:")
        traceback.print_exc()
        return False

# ------------- Database model -------------
class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    due_date = db.Column(db.DateTime, nullable=False)  # stored as naive local datetime (from datetime-local)
    status = db.Column(db.String(50), default='Pending')

    notify_email = db.Column(db.String(255), nullable=True)
    notified_1day = db.Column(db.Boolean, default=False)
    notified_1hour = db.Column(db.Boolean, default=False)

    def notify_recipient(self):
        return self.notify_email or DEFAULT_TO

# ------------- Web routes -------------
@app.route('/')
def index():
    tasks = Task.query.order_by(Task.due_date).all()
    return render_template('index.html', tasks=tasks)

@app.route('/add', methods=['POST'])
def add_task():
    title = request.form.get('title')
    description = request.form.get('description')
    due_raw = request.form.get('due_date')  # "YYYY-MM-DDTHH:MM"
    notify_email = request.form.get('notify_email') or None

    if not title or not due_raw:
        return "Missing fields", 400

    try:
        due_date = datetime.strptime(due_raw, '%Y-%m-%dT%H:%M')
    except Exception:
        try:
            due_date = datetime.fromisoformat(due_raw)
        except Exception:
            return "Invalid date format. Use YYYY-MM-DDTHH:MM", 400

    task = Task(title=title, description=description, due_date=due_date, notify_email=notify_email)
    db.session.add(task)
    db.session.commit()

    # send immediate confirmation to the provided email (optional)
    if notify_email:
        subject = f"Task received: {title}"
        body = f"Your task '{title}' has been created and is due on {task.due_date}.\nYou will receive reminders 1 day and 1 hour before the deadline."
        ok = send_email(notify_email, subject, body)
        print(f"[add_task] confirmation email sent? {ok}")

    return redirect(url_for('index'))

# ------------- Scheduler job -------------
def check_deadlines():
    with app.app_context():
        now = datetime.now()             # naive local
        day_threshold = now + timedelta(days=1)
        hour_threshold = now + timedelta(hours=1)

        # 1-day reminders
        try:
            tasks_day = Task.query.filter(
                Task.status == 'Pending',
                Task.due_date <= day_threshold,
                Task.due_date > now,
                Task.notified_1day == False
            ).all()
        except Exception as e:
            print("[check_deadlines] DB query (day) failed:", e)
            tasks_day = []

        for task in tasks_day:
            recipient = task.notify_recipient()
            if not recipient:
                print(f"[check_deadlines] no recipient for task {task.id}, skipping")
                continue
            subject = f"Reminder: '{task.title}' is due in ~1 day"
            body = f"Task: {task.title}\nDue: {task.due_date}\n\n{task.description or ''}"
            if send_email(recipient, subject, body):
                task.notified_1day = True
                db.session.add(task)
                try:
                   socketio.emit('deadline_alert', {'title': task.title, 'due': str(task.due_date), 'when': '1 day'})

                except Exception as e:
                    print("[check_deadlines] socket emit failed:", e)

        # 1-hour reminders
        try:
            tasks_hour = Task.query.filter(
                Task.status == 'Pending',
                Task.due_date <= hour_threshold,
                Task.due_date > now,
                Task.notified_1hour == False
            ).all()
        except Exception as e:
            print("[check_deadlines] DB query (hour) failed:", e)
            tasks_hour = []

        for task in tasks_hour:
            recipient = task.notify_recipient()
            if not recipient:
                print(f"[check_deadlines] no recipient for task {task.id}, skipping")
                continue
            subject = f"Urgent: '{task.title}' is due in ~1 hour"
            body = f"Task: {task.title}\nDue: {task.due_date}\n\n{task.description or ''}"
            if send_email(recipient, subject, body):
                task.notified_1hour = True
                db.session.add(task)
                try:
                    socketio.emit('deadline_alert', {'title': task.title, 'due': str(task.due_date), 'when': '1 hour'}, broadcast=True)
                except Exception as e:
                    print("[check_deadlines] socket emit failed:", e)

        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print("[check_deadlines] DB commit failed:", e)

# ------------- SocketIO -------------
@socketio.on('connect')
def handle_connect():
    print('Client connected')

# ------------- Run (safe startup) -------------
if __name__ == '__main__':
    # create tables if needed (safe for sqlite; on Postgres use migrations in prod)
    with app.app_context():
        try:
            db.create_all()
        except Exception as e:
            print("db.create_all() failed:", e)

    # schedule job after app is ready
    try:
        scheduler.add_job(func=check_deadlines, trigger='interval', minutes=1, id='check_deadlines_job')
        scheduler.start()
    except Exception as e:
        print("Scheduler start failed:", e)

    # run server (use 127.0.0.1 for local dev)
    socketio.run(app, debug=True, host='127.0.0.1', port=5000)
