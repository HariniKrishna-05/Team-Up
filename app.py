import os
import math
from flask import Flask, render_template, request, redirect, session, flash, jsonify
from flask import *
import oracledb
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import random, smtplib, datetime
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
from backend.db_connection import get_connection
from backend.role_assignment import assign_roles
from backend.clustering import perform_hackathon_clustering
from backend.team_formation import form_balanced_role_teams
import pandas as pd
import time
import uuid


# Import backend modules using absolute path
import sys
from pathlib import Path
backend_path = str(Path(__file__).parent / 'backend')
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from clustering import perform_hackathon_clustering  # type: ignore
from role_assignment import assign_roles  # type: ignore
from team_formation import form_balanced_role_teams, get_teams_by_hackathon  # type: ignore
from flask import send_file

load_dotenv()  # load variables from .env

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "teamup123")

# ---------------- GLOBAL TEMPLATE VARIABLE ----------------
@app.context_processor
def inject_user():
    project_count = 0
    student_id = session.get('student_id')

    if student_id:
        try:
            cur = connection.cursor()   # ✅ NEW cursor

            cur.execute("""
                SELECT COUNT(*)
                FROM STUDENT_PROJECTS
                WHERE STUDENT_ID = :sid
            """, {"sid": student_id})

            project_count = cur.fetchone()[0]
            cur.close()  # ✅ close cursor

        except Exception as e:
            print("Error:", e)
            project_count = 0

    return dict(
        name=session.get("student_name"),
        project_count=project_count
    )
# File upload configuration
app.config['UPLOAD_FOLDER'] = 'static/uploads'
ALLOWED_EXTENSIONS = {"png","jpg","jpeg","gif","zip","pdf","mp4"}

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# helper to check allowed file extensions
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.',1)[1].lower() in ALLOWED_EXTENSIONS

# ----------------- Oracle SQL Connection -----------------
connection = oracledb.connect(
    user=os.environ.get("ORACLE_USER", "system"),
    password=os.environ.get("ORACLE_PASSWORD", "system123"),
    dsn=os.environ.get("ORACLE_DSN", "localhost:1521/XEPDB1")
)
cursor = connection.cursor()

#----------------- Fetch Recent Hackathons -----------------
def get_recent_hackathons(limit=None):
    query = """
        SELECT 
            HACKATHON_ID,
            HACKATHON_NAME,
            HACKATHON_DATE,
            VENUE,
            DESCRIPTION,
            STATUS
        FROM HACKATHONS
        ORDER BY HACKATHON_DATE ASC
    """

    if limit:
        query += f" FETCH FIRST {limit} ROWS ONLY"

    cursor.execute(query)

    columns = [col[0].lower() for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


# ----------------- Activity Logger -----------------
from datetime import datetime

def log_activity(description):
    cursor.execute("""
        INSERT INTO ACTIVITY_LOG (DESCRIPTION)
        VALUES (:activity)
    """, {"activity": description})
    
    connection.commit()

# -------- Time Ago Formatter --------
def time_ago(time):
    now = datetime.now()
    diff = now - time

    seconds = diff.total_seconds()

    if seconds < 60:
        return "just now"

    elif seconds < 3600:
        minutes = int(seconds/60)
        return f"{minutes} minutes ago"

    elif seconds < 86400:
        hours = int(seconds/3600)
        return f"{hours} hours ago"

    else:
        days = int(seconds/86400)
        return f"{days} days ago"
    
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

            log_activity(f"{name} registered into our TeamUp application")# Log the signup activity

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
        expiry = datetime.now() + timedelta(minutes=10)
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
            if otp_db == otp_input and expiry_time >datetime.now():
                # HACKATHON_STUDENTS doesn't have IS_VERIFIED, so we'll just mark OTP as used by storing in session
                flash("Email verified! Please login.")
                return redirect('/login')
        flash("Invalid or expired OTP")
    return render_template('verify_otp.html')

# Resend OTP
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
        expiry = datetime.now() + timedelta(minutes=10)
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
            name = student[1]
            log_activity(f"{name} logged into the TeamUp application")

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
        expiry = datetime.now() + timedelta(minutes=10)
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
            if otp_db == otp_input and expiry_time > datetime.now():
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

    name = session['student_name']
    student_id = session['student_id']

    # ✅ Latest hackathons
    hackathons = get_recent_hackathons(4)

    # ✅ Fetch latest 3 projects (IMPORTANT)
    cursor = connection.cursor()
    cursor.execute("""
        SELECT project_title, tech_stack, project_status, created_at
        FROM STUDENT_PROJECTS
        WHERE student_id = :id
        ORDER BY created_at DESC
        FETCH FIRST 4 ROWS ONLY
    """, {'id': student_id})

    projects = cursor.fetchall()

    return render_template(
        'dashboard.html',
        name=name,
        hackathons=hackathons,
        projects=projects
    )

# ---------------- PROFILE VIEW ----------------
@app.route("/profile")
def view_profile():
    if 'student_id' not in session:
        return redirect('/login')

    student_id = session['student_id']

    cursor.execute("""
        SELECT STUDENT_NAME, EMAIL_ID, COLLEGE_NAME,
               BRANCH, YEAR_OF_STUDY, GITHUB_LINK,
               LINKEDIN_LINK, BIO
        FROM HACKATHON_STUDENTS
        WHERE STUDENT_ID = :id
    """, {"id": student_id})

    student = cursor.fetchone()

    if not student:
        flash("Profile not found")
        return redirect('/dashboard')

    return render_template(
        "view_profile.html",
        name=student[0],
        email=student[1],
        college=student[2],
        branch=student[3],
        year=student[4],
        github=student[5],
        linkedin=student[6],
        bio=student[7]
    )


# ---------------- PROFILE EDIT ----------------

@app.route("/edit_profile", methods=["GET", "POST"])
def edit_profile():
    if 'student_id' not in session:
        return redirect('/login')
    
    student_id = session['student_id']

    if request.method == "POST":
        # Collect form fields
        name = request.form['name']
        email = request.form['email']
        college = request.form['college']
        branch = request.form['branch']
        year = int(request.form['year']) if request.form['year'] else None
        github = request.form['github']
        linkedin = request.form['linkedin']
        bio = request.form['bio']


        # Update profile info
        cursor.execute("""
            UPDATE HACKATHON_STUDENTS
            SET STUDENT_NAME = :name,
                EMAIL_ID = :email,
                COLLEGE_NAME = :college,
                BRANCH = :branch,
                YEAR_OF_STUDY = :year,
                GITHUB_LINK = :github,
                LINKEDIN_LINK = :linkedin,
                BIO = :bio
            WHERE STUDENT_ID = :id
        """, {
            "name": name,
            "email": email,
            "college": college,
            "branch": branch,
            "year": year,
            "github": github,
            "linkedin": linkedin,
            "bio": bio,
            "id": student_id
        })

        connection.commit()
        log_activity(f"{name} student record updated")# Log the profile update activity
        session['student_name'] = name
        flash("Profile updated successfully!")
        return redirect("/profile")

    # GET request: load existing data
    cursor.execute("""
        SELECT STUDENT_NAME, EMAIL_ID, COLLEGE_NAME,
               BRANCH, YEAR_OF_STUDY, GITHUB_LINK,
               LINKEDIN_LINK, BIO
        FROM HACKATHON_STUDENTS
        WHERE STUDENT_ID = :id
    """, {"id": student_id})
    student = cursor.fetchone()

    return render_template("edit_profile.html",
        name=student[0],
        email=student[1],
        college=student[2],
        branch=student[3],
        year=student[4],
        github=student[5],
        linkedin=student[6],
        bio=student[7],
    )

#----LOGOUT----
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


#----------------- Add Project Route -----------------
@app.route("/add_project", methods=["GET", "POST"])
def add_project():
    if 'student_id' not in session:
        return redirect('/login')

    student_id = session['student_id']

    if request.method == "POST":
        title = request.form.get("title")
        description = request.form.get("description")

        # -------- Project File --------
        project_file = request.files.get("file")
        file_name = None

        if project_file and project_file.filename != "" and allowed_file(project_file.filename):
            filename = secure_filename(project_file.filename)
            ext = filename.rsplit('.', 1)[1]
            file_name = f"{student_id}_{int(time.time())}.{ext}"
            project_file.save(os.path.join(app.config['UPLOAD_FOLDER'], file_name))

        # -------- Project Image (Single) --------
        image = request.files.get("image")
        image_name = None

        if image and image.filename != "" and allowed_file(image.filename):
            filename = secure_filename(image.filename)
            ext = filename.rsplit('.', 1)[1]
            image_name = f"{student_id}_img_{int(time.time())}.{ext}"
            image.save(os.path.join(app.config['UPLOAD_FOLDER'], image_name))

        # -------- Other Fields --------
        category = request.form.get("category")
        tech_stack = request.form.get("tech_stack")
        print("TECH STACK:", tech_stack)
        project_link = request.form.get("project_link")

        status = request.form.get("status", "").capitalize()
        valid_status = ["Planning", "Ongoing", "Completed"]

        if status not in valid_status:
            status = "None"

        # -------- Insert into DB --------
        cursor.execute("""
            INSERT INTO STUDENT_PROJECTS
            (STUDENT_ID, PROJECT_TITLE, PROJECT_DESCRIPTION, PROJECT_FILE, PROJECT_IMAGE,
             PROJECT_LINK, CREATED_AT, PROJECT_STATUS, PROJECT_CATEGORY, TECH_STACK)
            VALUES (:sid, :title, :pdesc, :pfile, :pimage, :plink, :created_at, :status, :category, :tech_stack)
        """, {
            "sid": student_id,
            "title": title,
            "pdesc": description,
            "pfile": file_name,
            "pimage": image_name,
            "plink": project_link,
            "created_at": datetime.now(),
            "status": status,
            "category": category,
            "tech_stack": tech_stack
        })

        connection.commit()
        name = session.get('name') or session.get("student_name")
        log_activity(f"{name} added a new project: {title}")# Log the add project activity

        flash("✅ Project added successfully")
        return redirect(url_for("my_projects"))

    return render_template("add_project.html")

#----------------- My Projects Route -----------------
@app.route("/my_projects")
def my_projects():
    if 'student_id' not in session:
        return redirect('/login')

    student_id = session['student_id']

    cursor.execute("""
        SELECT PROJECT_ID, PROJECT_TITLE, PROJECT_DESCRIPTION,
               PROJECT_FILE, PROJECT_IMAGE, CREATED_AT,
               PROJECT_STATUS, PROJECT_CATEGORY, TECH_STACK, PROJECT_LINK
        FROM STUDENT_PROJECTS
        WHERE STUDENT_ID=:sid
        ORDER BY CREATED_AT DESC
    """, {"sid": student_id})

    rows = cursor.fetchall()
    projects = []

    for r in rows:
        description = r[2].read() if hasattr(r[2], "read") else r[2]

        projects.append({
            "id": r[0],
            "title": r[1],
            "description": description,
            "file": r[3],
            "image": r[4],   # ✅ single image
            "created_at": r[5],
            "status": r[6],
            "category": r[7],
            "tech_stack": r[8],
            "link": r[9]
        })

    name = session.get("student_name")
    return render_template("my_projects.html", projects=projects, name=name)
#----------------- Delete Project Route -----------------
@app.route("/delete_project/<int:project_id>", methods=["POST"])
def delete_project(project_id):
    if 'student_id' not in session:
        return redirect('/login')

    print("✅ Delete route called with ID:", project_id)

    try:
        # Get file + image names before deleting
        cursor.execute("""
            SELECT PROJECT_FILE, PROJECT_IMAGE
            FROM STUDENT_PROJECTS
            WHERE PROJECT_ID=:id
        """, {"id": project_id})

        row = cursor.fetchone()

        if row:
            file_name, image_name = row

            # Delete project from DB
            cursor.execute("""
                DELETE FROM STUDENT_PROJECTS
                WHERE PROJECT_ID=:id
            """, {"id": project_id})

            connection.commit()
            

            # -------- Delete files from folder --------
            if file_name:
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], file_name)
                if os.path.exists(file_path):
                    os.remove(file_path)

            if image_name:
                image_path = os.path.join(app.config['UPLOAD_FOLDER'], image_name)
                if os.path.exists(image_path):
                    os.remove(image_path)

            flash("✅ Project deleted successfully")
            
        else:
            flash("⚠️ Project not found")

    except Exception as e:
        connection.rollback()
        print("❌ Delete Error:", e)
        flash(f"Error deleting project: {str(e)}")

        connection.commit()
        # name = session.get('name') or session.get("student_name")
        # log_activity(f"{name} deleted a project with: {project_title}")# Log the delete project activity

    return redirect("/my_projects")

#----------------- Edit Project Route -----------------
@app.route("/edit_project/<int:project_id>", methods=["POST"])
def edit_project(project_id):
    if 'student_id' not in session:
        return redirect('/login')

    try:
        title = request.form.get("title")
        description = request.form.get("description")
        tech_stack = request.form.get("tech_stack")
        print("TECH STACK:", tech_stack)
        project_link = request.form.get("project_link")

        status = request.form.get("status", "").capitalize()
        valid_status = ["Planning", "Ongoing", "Completed"]

        if status not in valid_status:
            status = "None"

        print("✅ Edit route called for ID:", project_id)

        # -------- Get old file/image --------
        cursor.execute("""
            SELECT PROJECT_FILE, PROJECT_IMAGE
            FROM STUDENT_PROJECTS
            WHERE PROJECT_ID=:pid
        """, {"pid": project_id})

        old_data = cursor.fetchone()
        old_file, old_image = old_data if old_data else (None, None)

        # -------- Handle new file --------
        project_file = request.files.get("file")
        file_name = old_file

        if project_file and project_file.filename != "" and allowed_file(project_file.filename):
            filename = secure_filename(project_file.filename)
            ext = filename.rsplit('.', 1)[1]
            file_name = f"{project_id}_file_{int(time.time())}.{ext}"
            project_file.save(os.path.join(app.config['UPLOAD_FOLDER'], file_name))

            # delete old file
            if old_file:
                old_path = os.path.join(app.config['UPLOAD_FOLDER'], old_file)
                if os.path.exists(old_path):
                    os.remove(old_path)

        # -------- Handle new image --------
        image = request.files.get("image")
        image_name = old_image

        if image and image.filename != "" and allowed_file(image.filename):
            filename = secure_filename(image.filename)
            ext = filename.rsplit('.', 1)[1]
            image_name = f"{project_id}_img_{int(time.time())}.{ext}"
            image.save(os.path.join(app.config['UPLOAD_FOLDER'], image_name))

            # delete old image
            if old_image:
                old_path = os.path.join(app.config['UPLOAD_FOLDER'], old_image)
                if os.path.exists(old_path):
                    os.remove(old_path)

        # -------- Update DB --------
        cursor.execute("""
            UPDATE STUDENT_PROJECTS
            SET PROJECT_TITLE=:title,
                PROJECT_DESCRIPTION=:pdesc,
                TECH_STACK=:tech,
                PROJECT_STATUS=:status,
                PROJECT_LINK=:plink,
                PROJECT_FILE=:pfile,
                PROJECT_IMAGE=:pimage
            WHERE PROJECT_ID=:pid
        """, {
            "title": title,
            "pdesc": description,
            "tech": tech_stack,
            "status": status,
            "plink": project_link,
            "pfile": file_name,
            "pimage": image_name,
            "pid": project_id
        })

        connection.commit()
        name = session.get('name') or session.get("student_name")
        log_activity(f"{name} edited a project: {title}")# Log the edit project

        flash("✅ Project updated successfully!")

    except Exception as e:
        connection.rollback()
        print("❌ Edit Error:", e)
        flash(f"Error updating project: {str(e)}")

    return redirect("/my_projects")

# ----------------- Hackathon Teams Route -----------------
@app.route('/hackathons')
def hackathons():
    if 'student_id' not in session:
        return redirect('/login')

    student_id = session['student_id']

    hackathons = get_recent_hackathons(10)

    updated_hackathons = []

    for h in hackathons:
        hackathon_name = h['hackathon_name']

        # ✅ CHECK TEAM EXISTENCE FOR THIS HACKATHON
        cursor.execute("""
            SELECT TEAM_ID
            FROM HACKATHON_STUDENTS
            WHERE STUDENT_ID = :sid
            AND HACKATHON_PREFERENCE = :hack
        """, {
            "sid": student_id,
            "hack": hackathon_name
        })

        row = cursor.fetchone()

        # ✅ True if team exists
        h['has_team'] = True if row and row[0] else False

        updated_hackathons.append(h)

    return render_template(
        'hackathon.html',
        hackathons=updated_hackathons,
        name=session.get('student_name')
    )

# ----------------- Join Hackathon Route -----------------
@app.route('/join_hackathon/<int:hackathon_id>', methods=['GET', 'POST'])
def join_hackathon(hackathon_id):
    print(f"Join hackathon called with id: {hackathon_id}")
    if 'student_id' not in session:
        print("No student_id in session")
        return redirect('/login')

    student_id = session['student_id']
    print(f"Student ID: {student_id}")
    try:
        # Fetch hackathon name
        cursor.execute("SELECT HACKATHON_NAME FROM HACKATHONS WHERE HACKATHON_ID=:id", id=hackathon_id)
        row = cursor.fetchone()
        if not row:
            print("Hackathon not found")
            flash("Hackathon not found.")
            return redirect('/hackathons')
        hackathon_name = row[0]
        print(f"Hackathon name: {hackathon_name}")

        # Update student's hackathon preference
        cursor.execute("UPDATE HACKATHON_STUDENTS SET HACKATHON_PREFERENCE=:hack WHERE STUDENT_ID=:sid", hack=hackathon_name, sid=student_id)
        connection.commit()
        print("Updated preference")

        # Store selected hackathon_id in session
        session['selected_hackathon_id'] = hackathon_id
        print(f"Set session hackathon_id: {hackathon_id}")

        return redirect('/rate')
    except Exception as e:
        print(f"Error in join_hackathon: {e}")
        app.logger.exception("Error in join_hackathon")
        flash(f"Database error: {str(e)}")
        return redirect('/hackathons')

# ----------------- Rate Skills Route -----------------
@app.route('/rate')
def rate_skills():
    print("Rate skills called")
    if 'student_id' not in session:
        print("No student_id in session for rate")
        return redirect('/login')
    hackathon_id = session.get('selected_hackathon_id')
    print(f"Hackathon ID from session: {hackathon_id}")
    if not hackathon_id:
        print("No hackathon selected")
        flash('No hackathon selected.')
        return redirect('/hackathons')
    try:
        cursor.execute("SELECT HACKATHON_ID, HACKATHON_NAME, VENUE, TO_CHAR(HACKATHON_DATE,'YYYY-MM-DD'), DESCRIPTION, STATUS FROM HACKATHONS WHERE HACKATHON_ID=:id", id=hackathon_id)
        row = cursor.fetchone()
        if not row:
            print("Hackathon not found in rate")
            flash('Hackathon not found.')
            return redirect('/hackathons')
        hackathon = {
            'id': row[0],
            'name': row[1],
            'venue': row[2],
            'date': row[3],
            'description': row[4],
            'status': row[5]
        }
        print(f"Rendering rate for hackathon: {hackathon['name']}")
        return render_template('rate.html', hackathon=hackathon)
    except Exception as e:
        print(f"Error in rate_skills: {e}")
        app.logger.exception("Error in rate_skills")
        flash(f"Database error: {str(e)}")
        return redirect('/hackathons')
# ----------------- Create Team Logic (AJAX endpoint) -----------------
@app.route('/create_team', methods=['POST'])
def create_team():
    try:
        if 'student_id' not in session:
            return jsonify({'error': 'Not logged in'}), 401

        student_id = session['student_id']
        hackathon_id = session.get('selected_hackathon_id')

        if not hackathon_id:
            return jsonify({'error': 'No hackathon selected'}), 400

        # ✅ Always fresh connection
        conn = get_connection()
        cursor = conn.cursor()

        # 1️⃣ Get ratings
        frontend = int(request.form.get('frontend', 0))
        backend = int(request.form.get('backend', 0))
        communication = int(request.form.get('communication', 0))
        leadership = int(request.form.get('leadership', 0))

        # 2️⃣ Update skills
        cursor.execute("""
            UPDATE HACKATHON_STUDENTS
            SET FRONTEND_SKILL=:frontend,
                BACKEND_SKILL=:backend,
                COMMUNICATION_SKILL=:communication,
                LEADERSHIP_SKILL=:leadership
            WHERE STUDENT_ID=:student_id
        """, {
            "frontend": frontend,
            "backend": backend,
            "communication": communication,
            "leadership": leadership,
            "student_id": student_id
        })

        # 3️⃣ Get hackathon name
        cursor.execute("""
            SELECT HACKATHON_NAME 
            FROM HACKATHONS 
            WHERE HACKATHON_ID=:id
        """, {"id": hackathon_id})

        row = cursor.fetchone()

        if not row:
            cursor.close()
            conn.close()
            return jsonify({'error': 'Hackathon not found'}), 404

        hackathon_name = row[0]

        # 4️⃣ Save preference
        cursor.execute("""
            UPDATE HACKATHON_STUDENTS 
            SET HACKATHON_PREFERENCE=:hack 
            WHERE STUDENT_ID=:sid
        """, {
            "hack": hackathon_name,
            "sid": student_id
        })

        conn.commit()

        # ❌ DO NOT PASS cursor/connection
        # ✅ Let each function handle its own DB
        perform_hackathon_clustering()
        assign_roles()
        form_balanced_role_teams()

        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'redirect': '/myteam'
        })

    except Exception as e:
        print("ERROR in create_team:", e)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# ----------------- My Team Page -----------------
@app.route('/myteam')
def myteam():
    if 'student_id' not in session:
        return redirect('/login')
    student_id = session['student_id']

    # ✅ ALWAYS create fresh connection
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT TEAM_ID, HACKATHON_PREFERENCE FROM HACKATHON_STUDENTS WHERE STUDENT_ID=:id", id=student_id)
    row = cursor.fetchone()
    team_id = row[0] if row else None
    hackathon = row[1] if row else None
    if team_id:
        cursor.execute("""
            SELECT STUDENT_NAME, COLLEGE_NAME, ROLE, EMAIL_ID
            FROM HACKATHON_STUDENTS
            WHERE TEAM_ID=:team_id
        """, team_id=team_id)
        members = cursor.fetchall()
        team_members = [
            {'name': m[0], 'institution': m[1], 'role': m[2], 'email': m[3]} for m in members
        ]
        return render_template('myteam.html', team_members=team_members, has_team=True)
    else:
        # Determine reason
        reason = "Team not formed yet."
        if hackathon:
            cursor.execute("SELECT COUNT(*) FROM HACKATHON_STUDENTS WHERE HACKATHON_PREFERENCE=:hack", hack=hackathon)
            count = cursor.fetchone()[0]
            if count < 4:
                reason = f"Insufficient participants in {hackathon}. At least 4 students are required to form a team."
            else:
                reason = f"Unable to form a balanced team in {hackathon}. This may be due to uneven distribution of roles or clustering issues."
        return render_template('myteam.html', team_members=[], has_team=False, reason=reason)
    
# ----------------- Admin Credentials -----------------
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@teamup.com")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

#------------------ Admin Login -----------------
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    error = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        if email == ADMIN_EMAIL and password == ADMIN_PASSWORD:
            session['admin'] = email
            session['is_admin'] = True
            return redirect('/admin/dashboard')
        error = "Invalid admin credentials."
    return render_template('admin_login.html', error=error)

#---------------------- Admin Logout -----------------
@app.route('/admin/logout')
def admin_logout():
    session.pop('admin', None)
    session.pop('is_admin', None)
    return redirect('/admin/login')

#----------------------- Admin Dashboard -----------------

@app.route('/admin/dashboard')
def admin_dashboard():
    with connection.cursor() as cursor:

        # TOTAL STUDENTS
        cursor.execute("SELECT COUNT(*) FROM HACKATHON_STUDENTS")
        total_students = cursor.fetchone()[0]

        # TOTAL HACKATHONS
        cursor.execute("SELECT COUNT(*) FROM HACKATHONS")
        total_hackathons = cursor.fetchone()[0]

        # LATEST 4 HACKATHONS
        cursor.execute("""
            SELECT HACKATHON_NAME,
                   TO_CHAR(HACKATHON_DATE,'Mon DD'),
                   VENUE,
                   STATUS
            FROM HACKATHONS
            WHERE UPPER(STATUS) = 'ACTIVE'
            ORDER BY HACKATHON_DATE DESC, HACKATHON_ID DESC
            FETCH FIRST 4 ROWS ONLY
        """)

        hackathons = cursor.fetchall()

        recent_hackathons = [
            {
                "name": h[0],
                "date": h[1],
                "venue": h[2],
                "status": h[3]
            }
            for h in hackathons
        ]

        # PARTICIPANTS
        cursor.execute("""
            SELECT COUNT(*)
            FROM HACKATHON_STUDENTS
            WHERE HACKATHON_PREFERENCE IS NOT NULL
        """)
        total_participants = cursor.fetchone()[0]

        # TEAMS GENERATED
        cursor.execute("""
            SELECT COUNT(DISTINCT TEAM_ID)
            FROM HACKATHON_STUDENTS
            WHERE TEAM_ID IS NOT NULL
        """)
        teams_generated = cursor.fetchone()[0]

        cursor.execute("""
            SELECT DESCRIPTION, CREATED_AT
            FROM ACTIVITY_LOG
            ORDER BY CREATED_AT DESC
            FETCH FIRST 5 ROWS ONLY
        """)

        rows = cursor.fetchall()

        recent_activities = []

        for r in rows:
            recent_activities.append({
                "text": r[0],
                "time": time_ago(r[1])
            })

    return render_template(
        "admin_dash.html",
        admin_name="Admin",
        time_greeting="Welcome...",

        total_participants=total_participants,

        total_students=total_students,
        
        total_hackathons=total_hackathons,

        teams_generated=teams_generated,

        recent_hackathons=recent_hackathons,

        recent_activities=recent_activities

    )


#----------------------- Admin Activity Log -----------------
@app.route('/admin/activity')
def admin_activity():

    cursor.execute("""
        SELECT DESCRIPTION, CREATED_AT
        FROM ACTIVITY_LOG
        ORDER BY CREATED_AT DESC
    """)

    rows = cursor.fetchall()

    activities = []

    for description, time in rows:
        now = datetime.now()
        diff = now - time

        minutes = int(diff.total_seconds() / 60)

        if minutes < 1:
            ago = "just now"
        elif minutes == 1:
            ago = "1 minute ago"
        elif minutes < 60:
            ago = f"{minutes} minutes ago"
        else:
            hours = minutes // 60
            ago = f"{hours} hours ago"

        activities.append({
            "description":  description,
            "time": ago
        })

    return render_template("admin_activity.html", activities=activities)

# ==================== TEAM MANAGEMENT ROUTES ====================
# These routes call the backend logic for role assignment, clustering, and team formation, and then redirect to the team display page.
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
# Note: Clustering should ideally be done before team formation, as it assigns cluster IDs that the team formation logic relies on.
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
# Team formation should be done after roles are assigned and clustering is performed, as it relies on that data to create balanced teams.
@app.route('/form-teams')
def form_teams_route():
    """Form balanced teams from students"""
    if 'student_id' not in session:
        return redirect('/login')
    
    try:
        form_balanced_role_teams(cursor, connection)
        cursor.execute("""
            SELECT DISTINCT HACKATHON_PREFERENCE
            FROM HACKATHON_STUDENTS
            WHERE TEAM_ID IS NOT NULL
        """)

        row = cursor.fetchone()

        if row:
            hackathon = row[0]
            log_activity(f"Teams generated successfully for {hackathon}")# Log the team formation activity with hackathon name
        flash("Teams formed successfully")
    except Exception as e:
        app.logger.exception("Error forming teams")
        flash(f"Error forming teams: {str(e)}")
    
    return redirect('/view-teams')
#------------------ View Teams -----------------
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
#------------------ Backend Logic for Team Formation (called by route) -----------------
@app.route('/team-setup')
def team_setup():
    """Admin page to setup teams (assign roles → cluster → form teams)"""
    if 'student_id' not in session:
        return redirect('/login')
    
    return render_template('team_setup.html')
# This route can be called by buttons on the team_setup.html page to trigger each step of the process. The actual logic is in the backend modules.
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





# ----------------- Hackathon Management -----------------
@app.route('/hackathon_events')
def hackathon_events():

    # -------- SIDEBAR COUNTS --------
    cursor.execute("SELECT COUNT(*) FROM HACKATHON_STUDENTS")
    total_students = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM HACKATHONS")
    total_hackathons = cursor.fetchone()[0]

    # -------- HACKATHON LIST --------
    cursor.execute("""
        SELECT h.HACKATHON_ID,
               h.HACKATHON_NAME,
               TO_CHAR(h.HACKATHON_DATE,'YYYY-MM-DD'),
               h.VENUE,
               h.DESCRIPTION,
               h.STATUS,
               NVL(COUNT(s.STUDENT_ID),0) AS TOTAL_REGISTERED
        FROM HACKATHONS h
        LEFT JOIN HACKATHON_STUDENTS s
            ON h.HACKATHON_NAME = s.HACKATHON_PREFERENCE
        GROUP BY h.HACKATHON_ID,
                 h.HACKATHON_NAME,
                 h.HACKATHON_DATE,
                 h.VENUE,
                 h.DESCRIPTION,
                 h.STATUS
        ORDER BY h.HACKATHON_DATE
    """)

    hackathons = cursor.fetchall()

    hackathon_list = [
        {
            'id': h[0],
            'name': h[1],
            'date': h[2],
            'venue': h[3],
            'description': h[4],
            'status': h[5],
            'count': h[6]
        }
        for h in hackathons
    ]

    return render_template(
        "hackathon_events.html",
        hackathons=hackathon_list,

        # ⭐ sidebar values
        total_students=total_students,
        total_hackathons=total_hackathons
    )
#------------------ Backend Logic for Team Formation (called by route) -----------------
@app.route('/add_hackathon', methods=['POST'])
def add_hackathon():
    try:
        name = request.form['name']
        date = request.form['date']
        venue = request.form['venue']
        description = request.form['description']
        status = request.form['status']

        cursor.execute("""
            INSERT INTO HACKATHONS
            (HACKATHON_NAME, HACKATHON_DATE, VENUE, DESCRIPTION, STATUS)
            VALUES (:1, TO_DATE(:2,'YYYY-MM-DD'), :3, :4, :5)
        """, (name, date, venue, description, status))
        connection.commit()
        log_activity(f"New hackathon {name} created")# Log the creation of a new hackathon
        flash("Hackathon added successfully")
    except Exception as e:
        app.logger.exception("Failed to add hackathon")
        flash(f"Error adding hackathon: {str(e)}")
    
    # Redirect to the dashboard to reload the latest hackathons
    return redirect('/hackathon_events')

#------------------ Update and Delete Hackathon -----------------
@app.route('/update_hackathon', methods=['POST'])
def update_hackathon():
    hackathon_id = request.form['id']
    name = request.form['name']
    date = request.form['date']
    venue = request.form['venue']
    description = request.form['description']
    status = request.form['status']
    
    cursor.execute("""
        UPDATE HACKATHONS
        SET HACKATHON_NAME = :1,
            HACKATHON_DATE = TO_DATE(:2,'YYYY-MM-DD'),
            VENUE = :3,
            DESCRIPTION = :4,
            STATUS = :5
        WHERE HACKATHON_ID = :6
    """, (name, date, venue, description, status, hackathon_id))
    connection.commit()

    log_activity(f"{name} hackathon is updated")# Log the update of a hackathon (name variable should be defined in the context where this route is called, or you can fetch the name before update for logging)log_activity(f"{hackathon_name} hackathon is updated")# Log the update of a hackathon (hackathon_name variable should be defined in the context where this route is called, or you can fetch the name before update for logging)
    
    flash("Hackathon updated successfully")
    return redirect('/hackathon_events')

#------------------ Delete Hackathon -----------------
@app.route("/delete-hackathon/<int:hackathon_id>", methods=["POST"])
def delete_hackathon(hackathon_id):
    # 1️⃣ Fetch hackathon name first
    cursor.execute(
        "SELECT HACKATHON_NAME FROM HACKATHONS WHERE HACKATHON_ID=:id",
        {"id": hackathon_id}
    )
    row = cursor.fetchone()
    hackathon_name = row[0] if row else None  # None if hackathon not found

    if not hackathon_name:
        flash("Hackathon not found!")
        return redirect("/hackathon_events")

    # 2️⃣ Delete students who selected this hackathon
    cursor.execute(
        "DELETE FROM HACKATHON_STUDENTS WHERE HACKATHON_PREFERENCE=:name",
        {"name": hackathon_name}
    )

    # 3️⃣ Delete the hackathon itself
    cursor.execute(
        "DELETE FROM HACKATHONS WHERE HACKATHON_ID=:id",
        {"id": hackathon_id}
    )

    connection.commit()

    # 4️⃣ Log activity
    log_activity(f"{hackathon_name} hackathon is deleted from the hackathon list")

    # 5️⃣ Flash success message
    flash("Hackathon deleted successfully")
    return redirect("/hackathon_events")
# ---------------- ADMIN VIEW TEAMS ----------------
@app.route('/admin/view-teams/<hackathon_name>')
def view_teams(hackathon_name):

    search = request.args.get("search", "").lower()

    cursor = connection.cursor()

    # -------- SIDEBAR COUNTS --------
    cursor.execute("SELECT COUNT(*) FROM HACKATHON_STUDENTS")
    total_students = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM HACKATHONS")
    total_hackathons = cursor.fetchone()[0]

    # -------- GET STUDENTS FOR THIS HACKATHON --------
    query = """
        SELECT
            STUDENT_NAME,
            EMAIL_ID,
            ROLE,
            COLLEGE_NAME,
            TEAM_ID
        FROM HACKATHON_STUDENTS
        WHERE HACKATHON_PREFERENCE = :hackathon_name
        ORDER BY TEAM_ID, STUDENT_NAME
    """

    cursor.execute(query, {"hackathon_name": hackathon_name})
    rows = cursor.fetchall()

    # -------- GROUP BY TEAM --------
    teams = {}

    for row in rows:
        team_id = row[4]

        member = {
            "name": row[0],
            "email": row[1],
            "role": row[2],
            "college": row[3],
            "highlight": False
        }

        if team_id not in teams:
            teams[team_id] = []

        teams[team_id].append(member)

    # -------- SEARCH FUNCTION --------
    if search:

        matched_team_id = None
        matched_member_index = None

        for tid, members in teams.items():
            for i, m in enumerate(members):
                if search in m["name"].lower() or search in m["email"].lower():
                    matched_team_id = tid
                    matched_member_index = i
                    m["highlight"] = True
                    break
            if matched_team_id:
                break

        # Move searched student to top
        if matched_team_id is not None:
            members = teams[matched_team_id]
            member = members.pop(matched_member_index)
            members.insert(0, member)

            # Move that team to top
            ordered = {matched_team_id: members}

            for tid, mem in teams.items():
                if tid != matched_team_id:
                    ordered[tid] = mem

            teams = ordered

    cursor.close()

    return render_template(
        "teams.html",
        teams=teams,
        hackathon=hackathon_name,
        total_students=total_students,
        total_hackathons=total_hackathons
    )
 #---------------- ADMIN STUDENT MANAGEMENT ----------------
@app.route('/admin/students')
def admin_students():

    search = request.args.get("search", "")
    institution = request.args.get("institution", "")
    hackathon = request.args.get("hackathon", "")
    page = int(request.args.get("page", 1))

    per_page = 9
    offset = (page - 1) * per_page

    # ---------------- TOTAL COUNTS FOR SIDEBAR ----------------
    cursor.execute("SELECT COUNT(*) FROM HACKATHON_STUDENTS")
    total_students = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM HACKATHONS")
    total_hackathons = cursor.fetchone()[0]

    # ---------------- BASE QUERY ----------------
    base_query = """
        FROM HACKATHON_STUDENTS
        WHERE 1=1
    """

    params = {}

    if search:
        base_query += """
        AND (LOWER(STUDENT_NAME) LIKE :search
             OR LOWER(EMAIL_ID) LIKE :search)
        """
        params["search"] = f"%{search.lower()}%"

    if institution:
        base_query += " AND COLLEGE_NAME = :institution"
        params["institution"] = institution

    if hackathon:
        base_query += " AND HACKATHON_PREFERENCE = :hackathon"
        params["hackathon"] = hackathon

    # ---------------- TOTAL STUDENTS COUNT ----------------
    count_query = "SELECT COUNT(*) " + base_query
    cursor.execute(count_query, params)
    total = cursor.fetchone()[0]

    total_pages = max(1, math.ceil(total / per_page))

    # ---------------- FETCH STUDENTS ----------------
    data_query = f"""
        SELECT
            STUDENT_ID,
            STUDENT_NAME,
            EMAIL_ID,
            COLLEGE_NAME,
            HACKATHON_PREFERENCE,
            FRONTEND_SKILL,
            BACKEND_SKILL,
            COMMUNICATION_SKILL,
            LEADERSHIP_SKILL
        {base_query}
        ORDER BY STUDENT_NAME
        OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY
    """

    params["offset"] = offset
    params["limit"] = per_page

    cursor.execute(data_query, params)
    students = cursor.fetchall()

    # ---------------- DROPDOWNS ----------------
    cursor.execute("SELECT DISTINCT COLLEGE_NAME FROM HACKATHON_STUDENTS")
    institutions = [r[0] for r in cursor.fetchall()]

    cursor.execute("SELECT DISTINCT HACKATHON_PREFERENCE FROM HACKATHON_STUDENTS")
    hackathons = [r[0] for r in cursor.fetchall()]

    return render_template(
        "student_management.html",
        students=students,
        institutions=institutions,
        hackathons=hackathons,
        page=page,
        total_pages=total_pages,
        search=search,
        institution=institution,
        hackathon=hackathon,

        # ⭐ ADD THESE
        total_students=total_students,
        total_hackathons=total_hackathons
    )
 #---------------- EXPORT STUDENTS CSV ----------------
@app.route("/export-csv")
def export_csv():

    cursor.execute("""
        SELECT STUDENT_NAME, EMAIL_ID, COLLEGE_NAME,
               HACKATHON_PREFERENCE
        FROM HACKATHON_STUDENTS
    """)

    rows = cursor.fetchall()

    import pandas as pd
    df = pd.DataFrame(rows,
        columns=["Name","Email","Institution","Hackathon"])

    path = "students.csv"
    df.to_csv(path, index=False)

    return send_file(path, as_attachment=True)
 #---------------- EDIT STUDENT ----------------
@app.route("/edit-student/<int:student_id>", methods=["POST"])
def edit_student(student_id):
    name = request.form["name"]
    email = request.form["email"]
    college = request.form["college"]
    frontend = request.form.get("frontend", 0)
    backend = request.form.get("backend", 0)
    comm = request.form.get("comm", 0)
    lead = request.form.get("lead", 0)

    cursor.execute("""
        UPDATE HACKATHON_STUDENTS
        SET STUDENT_NAME=:name, EMAIL_ID=:email, COLLEGE_NAME=:college,
            FRONTEND_SKILL=:frontend, BACKEND_SKILL=:backend,
            COMMUNICATION_SKILL=:comm, LEADERSHIP_SKILL=:lead
        WHERE STUDENT_ID=:id
    """, name=name, email=email, college=college,
         frontend=frontend, backend=backend, comm=comm, lead=lead, id=student_id)
    connection.commit()
    flash("Student updated successfully")
    return redirect("/admin/students")
 #---------------- DELETE STUDENT ----------------
@app.route("/delete-student/<int:student_id>", methods=["POST"])
def delete_student(student_id):

    # Get student name first
    cursor.execute("SELECT STUDENT_NAME FROM HACKATHON_STUDENTS WHERE STUDENT_ID=:id", {"id": student_id})
    row = cursor.fetchone()

    if row:
        student_name = row[0]
    else:
        student_name = "Unknown Student"

    # delete child records first
    cursor.execute("DELETE FROM OTP_VERIFICATION WHERE STUDENT_ID=:id", id=student_id)

    # delete student
    cursor.execute("DELETE FROM HACKATHON_STUDENTS WHERE STUDENT_ID=:id", id=student_id)

    connection.commit()

    # log activity
    log_activity(f"{student_name} deleted from the student list")

    flash("Student deleted successfully")
    return redirect("/admin/students")

# root redirect
def index():
    if 'student_id' in session:
        return redirect('/dashboard')
    return redirect('/login')
#----------------- Home Route -----------------
@app.route('/')
def home():
    return index()

if __name__ == '__main__':
    app.run(debug=True)