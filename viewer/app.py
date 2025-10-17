import os
from flask import Flask, render_template, request, redirect, url_for, session, flash
from dotenv import load_dotenv
import pymysql
from functools import wraps

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key-change-this')

# Database configuration
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME'),
    'cursorclass': pymysql.cursors.DictCursor
}

# Admin credentials (from environment)
ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'admin')


def get_db_connection():
    """Create a database connection"""
    try:
        connection = pymysql.connect(**DB_CONFIG)
        return connection
    except Exception as e:
        print(f"Database connection error: {e}")
        return None


def login_required(f):
    """Decorator to require login for routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


@app.route('/')
def index():
    """Redirect to members page if logged in, otherwise to login"""
    if 'logged_in' in session:
        return redirect(url_for('members'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['logged_in'] = True
            session['username'] = username
            flash('Successfully logged in!', 'success')
            return redirect(url_for('members'))
        else:
            flash('Invalid username or password.', 'danger')
    
    return render_template('login.html')


@app.route('/logout')
def logout():
    """Logout and clear session"""
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))


@app.route('/members')
@login_required
def members():
    """Display all members from the database"""
    connection = get_db_connection()
    
    if not connection:
        flash('Database connection failed.', 'danger')
        return render_template('members.html', members=[], error=True)
    
    try:
        with connection.cursor() as cursor:
            # Adjust this query based on your actual table structure
            # This is a generic query - modify table name and columns as needed
            cursor.execute("SELECT * FROM members ORDER BY id")
            members = cursor.fetchall()
        
        return render_template('members.html', members=members, error=False)
    
    except Exception as e:
        print(f"Error fetching members: {e}")
        flash(f'Error fetching members: {str(e)}', 'danger')
        return render_template('members.html', members=[], error=True)
    
    finally:
        connection.close()


@app.errorhandler(404)
def not_found(e):
    """404 error handler"""
    return render_template('404.html'), 404


@app.errorhandler(500)
def server_error(e):
    """500 error handler"""
    return render_template('500.html'), 500


if __name__ == '__main__':
    # This is for development only
    # In production, use gunicorn
    app.run(host='0.0.0.0', port=5000, debug=False)
