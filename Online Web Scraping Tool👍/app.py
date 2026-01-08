"""
Main Flask application

Run instructions:
1. pip install -r requirements.txt
2. copy .env.example to .env and set SECRET_KEY and MONGO_URI
3. flask run
"""
import os
from datetime import datetime
from bson.objectid import ObjectId
from flask import Flask, render_template, redirect, url_for, flash, request, send_file, abort
from flask_login import LoginManager, login_user, logout_user, login_required, current_user, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from io import StringIO
import csv
from dotenv import load_dotenv
from io import BytesIO, StringIO

# local modules
from forms import SignupForm, LoginForm, ScrapeForm
from models import init_db, create_user, get_user_by_email, get_user_by_id, save_scrape, get_scrape_by_id, get_user_history
from scraper import scrape_url
import sys
sys.stdout.reconfigure(encoding='utf-8')


load_dotenv()  # loads .env

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret')
app.config['MONGO_URI'] = os.getenv('MONGO_URI', 'mongodb://localhost:27017/scraper_db')

# Initialize DB (pymongo client saved in models module)
init_db(app.config['MONGO_URI'])

login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

# Simple User wrapper for Flask-Login
class User(UserMixin):
    def __init__(self, user_doc):
        self._doc = user_doc

    def get_id(self):
        # Flask-Login requires a string id
        return str(self._doc.get('_id'))

    @property
    def username(self):
        return self._doc.get('username')

    @property
    def email(self):
        return self._doc.get('email')

@login_manager.user_loader
def load_user(user_id):
    try:
        user_doc = get_user_by_id(user_id)
        if user_doc:
            return User(user_doc)
    except Exception:
        return None
    return None

@app.route('/')
def home():
    return render_template('home.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for('scrape'))
    form = SignupForm()
    if form.validate_on_submit():
        existing = get_user_by_email(form.email.data)
        if existing:
            flash('Email already registered. Please login.', 'warning')
            return redirect(url_for('login'))
        pw_hash = generate_password_hash(form.password.data)
        user_id = create_user(username=form.username.data, email=form.email.data, password_hash=pw_hash)
        flash('Account created. Please log in.', 'success')
        return redirect(url_for('login'))
    return render_template('signup.html', form=form)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('scrape'))
    form = LoginForm()
    if form.validate_on_submit():
        doc = get_user_by_email(form.email.data)
        if doc and check_password_hash(doc.get('password'), form.password.data):
            user = User(doc)
            login_user(user)
            flash('Logged in successfully.', 'success')
            next_page = request.args.get('next') or url_for('scrape')
            return redirect(next_page)
        flash('Invalid email or password.', 'danger')
    return render_template('login.html', form=form)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('home'))

@app.route('/scrape', methods=['GET', 'POST'])
@login_required
def scrape():
    form = ScrapeForm()
    result = None
    if form.validate_on_submit():
        url = form.url.data.strip()
        try:
            data = scrape_url(url)
            # Save job
            job_doc = {
                'user_id': ObjectId(current_user.get_id()),
                'url': url,
                'data': data,
                'summary': data.get('summary', {}),
                'created_at': datetime.utcnow()
            }
            job_id = save_scrape(job_doc)
            flash('Scrape completed and saved.', 'success')
            return redirect(url_for('view_result', job_id=str(job_id)))
        except Exception as e:
            flash(f'Scrape failed: {str(e)}', 'danger')
    return render_template('scrape.html', form=form, result=result)

@app.route('/result/<job_id>')
@login_required
def view_result(job_id):
    job = get_scrape_by_id(job_id)
    if not job:
        flash('Result not found.', 'warning')
        return redirect(url_for('profile'))
    # ensure ownership
    if str(job.get('user_id')) != current_user.get_id():
        abort(403)
    return render_template('result.html', job=job)

@app.route('/download/<job_id>')
@login_required
def download_csv(job_id):
    job = get_scrape_by_id(job_id)
    if not job:
        flash('Job not found.', 'danger')
        return redirect(url_for('dashboard'))

    # Create CSV content in memory
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(['Key', 'Value'])
    for k, v in job.get('data', {}).items():
        writer.writerow([k, v])

    # Convert to BytesIO for sending
    mem = BytesIO()
    mem.write(output.getvalue().encode('utf-8'))
    mem.seek(0)
    output.close()

    filename = f"scrape_{job_id}.csv"
    return send_file(
        mem,
        mimetype='text/csv',
        as_attachment=True,
        download_name=filename
    )

@app.route('/profile')
@login_required
def profile():
    history = get_user_history(current_user.get_id())
    return render_template('profile.html', user=current_user, history=history)

@app.route('/delete/<job_id>', methods=['POST'])
@login_required
def delete_history(job_id):
    from models import delete_scrape  # you'll create this function in models.py
    job = get_scrape_by_id(job_id)
    if not job:
        flash('Job not found.', 'danger')
        return redirect(url_for('profile'))
    if str(job.get('user_id')) != current_user.get_id():
        abort(403)

    delete_scrape(job_id)
    flash('History deleted successfully.', 'success')
    return redirect(url_for('profile'))

# Simple error handlers
@app.errorhandler(403)
def forbidden(e):
    return render_template('403.html'), 403

@app.errorhandler(404)
def not_found(e):
    return render_template('404.html'), 404

if __name__ == '__main__':
    app.run(debug=True)