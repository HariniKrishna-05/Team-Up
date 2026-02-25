import os
from flask import Flask, render_template, request, redirect, session, flash, jsonify
import oracledb
from werkzeug.security import generate_password_hash, check_password_hash
import random, smtplib, datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
import pandas as pd

# Import backend modules using absolute path
import sys
from pathlib import Path
backend_path = str(Path(__file__).parent / 'backend')
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from clustering import perform_hackathon_clustering  # type: ignore
from role_assignment import assign_roles  # type: ignore
from team_formation import form_balanced_role_teams, get_teams_by_hackathon  # type: ignore

load_dotenv()  # load variables from .env

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "teamup123")

# ----------------- Oracle SQL Connection -----------------
connection = oracledb.connect(
    user=os.environ.get("ORACLE_USER", "system"),
    password=os.environ.get("ORACLE_PASSWORD", "system123"),
    dsn=os.environ.get("ORACLE_DSN", "localhost:1521/XEPDB1")
)
cursor = connection.cursor()

# ----------------- Helper: Send OTP Email -----------------
def send_otp_email(receiver_email, otp):
    """Send a one‑time password via email. Environment variables must define
    EMAIL_USER and EMAIL_PASS (an app password for Gmail)."""
    sender_email = os.environ.get("EMAIL_USER")
    sender_password = os.environ.get("EMAIL_PASS")

    if not sender_email or not sender_password:
        app.logger.error("Email credentials not set in environment")
        return False

    message = MIMEMultipart()
    message['From'] = sender_email
    message['To'] = receiver_email
    message['Subject'] = "Hackathon OTP Verification"

    body = f"Your OTP is: {otp}"
    message.attach(MIMEText(body, 'plain'))

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender_email, sender_password)
        server.send_message(message)
        server.quit()
        return True
    except Exception as e:
        app.logger.exception("failed to send OTP email")
        return False

# ----------------- Routes -----------------

# Signup
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        institution = request.form['institution']
        password = generate_password_hash(request.form['password'])

        try:
            # Insert student without STUDENT_ID (let database auto-generate with GENERATED ALWAYS)
            cursor.execute("""
                INSERT INTO HACKATHON_STUDENTS (STUDENT_NAME, EMAIL_ID, COLLEGE_NAME, PASS_WORD)
                VALUES (:name, :email, :institution, :password)
            """, name=name, email=email, institution=institution, password=password)
            connection.commit()

            # Retrieve the auto-generated STUDENT_ID
            cursor.execute("SELECT MAX(STUDENT_ID) FROM HACKATHON_STUDENTS WHERE EMAIL_ID = :email", email=email)
            student_id = cursor.fetchone()[0]
        except Exception as e:
            app.logger.exception("error during signup")
            msg = str(e)
            # if the error indicates missing tables, prompt init-db
            if 'ORA-00942' in msg:
                flash("Database error: tables missing. Initialization will be attempted.")
                return redirect('/init-db')
            # unique constraint on email
            if 'ORA-00001' in msg or 'unique' in msg.lower():
                flash("An account with that email already exists.")
                return redirect('/signup')
            # otherwise just show message
            flash(f"Database error: {msg}")
            return redirect('/signup')

        # remember name in session immediately so dashboards don't crash
        session['student_name'] = name

        # store the student_id in session
        session['student_id'] = student_id

        # Generate OTP
        otp = str(random.randint(100000, 999999))
        expiry = datetime.datetime.now() + datetime.timedelta(minutes=10)
        cursor.execute("""
            INSERT INTO OTP_VERIFICATION (STUDENT_ID, OTP, EXPIRY_TIME)
            VALUES (:student_id, :otp, :expiry_time)
        """, student_id=student_id, otp=otp, expiry_time=expiry)
        connection.commit()

        # Send OTP email
        if send_otp_email(email, otp):
            flash("OTP sent to your email")
        else:
            flash("Unable to send OTP. Check server logs and email configuration.")
        session['student_id'] = student_id
        return redirect('/verify-otp')
    return render_template('signup.html')

# OTP Verification
@app.route('/verify-otp', methods=['GET', 'POST'])
def verify_otp():
    if request.method == 'POST':
        otp_input = request.form['otp']
        student_id = session.get('student_id')

        cursor.execute("""
            SELECT OTP, EXPIRY_TIME FROM OTP_VERIFICATION 
            WHERE STUDENT_ID=:student_id 
            ORDER BY OTP_ID DESC FETCH FIRST 1 ROWS ONLY
        """, student_id=student_id)
        otp_record = cursor.fetchone()
        if otp_record:
            otp_db, expiry_time = otp_record
            expiry_time = expiry_time  # Oracle DATE to Python datetime
            if otp_db == otp_input and expiry_time > datetime.datetime.now():
                # HACKATHON_STUDENTS doesn't have IS_VERIFIED, so we'll just mark OTP as used by storing in session
                flash("Email verified! Please login.")
                return redirect('/login')
        flash("Invalid or expired OTP")
    return render_template('verify_otp.html')

@app.route('/resend-otp')
def resend_otp():
    student_id = session.get('student_id')
    if not student_id:
        return redirect('/signup')
    
    cursor.execute("SELECT EMAIL_ID FROM HACKATHON_STUDENTS WHERE STUDENT_ID=:id", id=student_id)
    row = cursor.fetchone()
    if row:
        email = row[0]
        otp = str(random.randint(100000, 999999))
        expiry = datetime.datetime.now() + datetime.timedelta(minutes=10)
        cursor.execute("""
            INSERT INTO OTP_VERIFICATION (STUDENT_ID, OTP, EXPIRY_TIME)
            VALUES (:student_id, :otp, :expiry_time)
        """, student_id=student_id, otp=otp, expiry_time=expiry)
        connection.commit()
        if send_otp_email(email, otp):
            flash("New OTP sent")
        else:
            flash("Unable to resend OTP")
    return redirect('/verify-otp')

# Login
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        cursor.execute("SELECT STUDENT_ID, STUDENT_NAME, PASS_WORD FROM HACKATHON_STUDENTS WHERE EMAIL_ID=:email", email=email)
        student = cursor.fetchone()
        if student and check_password_hash(student[2], password):
            session['student_id'] = student[0]
            session['student_name'] = student[1]
            return redirect('/dashboard')
        flash("Invalid login or email not verified")
    return render_template('login.html')

# Forgot password (request OTP)
@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form['email']
        cursor.execute("SELECT STUDENT_ID FROM HACKATHON_STUDENTS WHERE EMAIL_ID=:email", email=email)
        row = cursor.fetchone()
        if not row:
            flash("No verified account found with that email")
            return redirect('/forgot-password')
        student_id = row[0]
        otp = str(random.randint(100000, 999999))
        expiry = datetime.datetime.now() + datetime.timedelta(minutes=10)
        cursor.execute("""
            INSERT INTO OTP_VERIFICATION (STUDENT_ID, OTP, EXPIRY_TIME)
            VALUES (:student_id, :otp, :expiry_time)
        """, student_id=student_id, otp=otp, expiry_time=expiry)
        connection.commit()
        if send_otp_email(email, otp):
            flash("OTP for password reset sent to your email")
        else:
            flash("Failed to send OTP email")
        session['student_id'] = student_id
        return redirect('/reset-password')
    return render_template('forgot_password.html')

# Reset password flow
@app.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    if request.method == 'POST':
        otp_input = request.form['otp']
        new_password = request.form['new_password']
        student_id = session.get('student_id')
        cursor.execute("""
            SELECT OTP, EXPIRY_TIME FROM OTP_VERIFICATION 
            WHERE STUDENT_ID=:student_id 
            ORDER BY OTP_ID DESC FETCH FIRST 1 ROWS ONLY
        """, student_id=student_id)
        otp_record = cursor.fetchone()
        if otp_record:
            otp_db, expiry_time = otp_record
            if otp_db == otp_input and expiry_time > datetime.datetime.now():
                hashed = generate_password_hash(new_password)
                cursor.execute("UPDATE HACKATHON_STUDENTS SET PASS_WORD=:pwd WHERE STUDENT_ID=:student_id", pwd=hashed, student_id=student_id)
                connection.commit()
                flash("Password updated. Please login.")
                return redirect('/login')
        flash("Invalid or expired OTP")
    return render_template('reset_password.html')

# Dashboard
@app.route('/dashboard')
def dashboard():
    if 'student_id' not in session:
        return redirect('/login')
    # fall back to empty string if name not set (e.g. user bypassed login)
    name = session.get('student_name', "")
    return render_template('dashboard.html', name=name)

# Logout
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# helper: create tables if missing
@app.route('/init-db')
def init_db():
    """Create necessary tables. Run once manually if they don't exist."""
    statements = [
        """
        CREATE TABLE HACKATHON_STUDENTS (
            STUDENT_ID NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            STUDENT_NAME VARCHAR2(100),
            EMAIL_ID VARCHAR2(100),
            PASS_WORD VARCHAR2(255),
            COLLEGE_NAME VARCHAR2(150),
            HACKATHON_PREFERENCE VARCHAR2(100),
            FRONTEND_SKILL NUMBER,
            BACKEND_SKILL NUMBER,
            COMMUNICATION_SKILL NUMBER,
            LEADERSHIP_SKILL NUMBER,
            CLUSTER_ID NUMBER,
            TEAM_ID NUMBER,
            ROLE VARCHAR2(50)
        )
        """,
        """
        CREATE TABLE OTP_VERIFICATION (
            OTP_ID NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            STUDENT_ID NUMBER REFERENCES HACKATHON_STUDENTS(STUDENT_ID),
            OTP VARCHAR2(6),
            EXPIRY_TIME DATE
        )
        """
    ]
    results = []
    for stmt in statements:
        try:
            cursor.execute(stmt)
            results.append('created')
        except Exception as exc:
            # ignore already exists errors
            results.append(str(exc))
    connection.commit()
    flash('Database initialization attempted: ' + '; '.join(results))
    return redirect('/')

# ==================== TEAM MANAGEMENT ROUTES ====================

@app.route('/assign-roles')
def assign_roles_route():
    """Assign roles to all students based on their skills"""
    if 'student_id' not in session:
        return redirect('/login')
    
    try:
        assign_roles(cursor, connection)
        flash("Roles assigned successfully to all students")
    except Exception as e:
        app.logger.exception("Error assigning roles")
        flash(f"Error assigning roles: {str(e)}")
    
    return redirect('/view-teams')

@app.route('/clustering')
def clustering_route():
    """Perform K-Means clustering of students by skills"""
    if 'student_id' not in session:
        return redirect('/login')
    
    try:
        perform_hackathon_clustering(cursor, connection)
        flash("Clustering completed successfully")
    except Exception as e:
        app.logger.exception("Error during clustering")
        flash(f"Error during clustering: {str(e)}")
    
    return redirect('/view-teams')

@app.route('/form-teams')
def form_teams_route():
    """Form balanced teams from students"""
    if 'student_id' not in session:
        return redirect('/login')
    
    try:
        form_balanced_role_teams(cursor, connection)
        flash("Teams formed successfully")
    except Exception as e:
        app.logger.exception("Error forming teams")
        flash(f"Error forming teams: {str(e)}")
    
    return redirect('/view-teams')

@app.route('/view-teams')
def view_teams_route():
    """Display all teams organized by hackathon"""
    if 'student_id' not in session:
        return redirect('/login')
    
    try:
        teams_data = get_teams_by_hackathon(connection)
        return render_template('teams.html', teams_data=teams_data)
    except Exception as e:
        app.logger.exception("Error retrieving teams")
        flash(f"Error retrieving teams: {str(e)}")
        return redirect('/dashboard')

@app.route('/team-setup')
def team_setup():
    """Admin page to setup teams (assign roles → cluster → form teams)"""
    if 'student_id' not in session:
        return redirect('/login')
    
    return render_template('team_setup.html')

@app.route('/api/teams-json')
def teams_json():
    """API endpoint to get teams as JSON"""
    if 'student_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        teams_data = get_teams_by_hackathon(connection)
        return jsonify(teams_data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# root redirect
def index():
    if 'student_id' in session:
        return redirect('/dashboard')
    return redirect('/login')

@app.route('/')
def home():
    return index()

if __name__ == '__main__':
    app.run(debug=True)