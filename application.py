import os
import sys
import logging
import shutil
from datetime import datetime, timedelta
from tempfile import mkdtemp

import jwt
from cs50 import SQL
from flask import (Flask, flash, redirect, render_template, request,
                   send_from_directory, session)
from flask_mail import Mail
from flask_session import Session
from flask_wtf.csrf import CSRFProtect
from werkzeug.exceptions import HTTPException, InternalServerError, default_exceptions
from werkzeug.security import check_password_hash, generate_password_hash

from helpers import (admin_required, generate_password, login_required, send_email,
                     read_file, verify_text, check_captcha)

app = Flask(__name__)
maintenance_mode = False
try:
    app.config.from_object('settings')
except Exception as e:
    sys.stderr.write(str(e))
    app.config.from_object('default_settings')
app.config['SESSION_FILE_DIR'] = mkdtemp()
app.jinja_env.globals['CLUB_NAME'] = app.config['CLUB_NAME']
app.jinja_env.globals['USE_CAPTCHA'] = app.config['USE_CAPTCHA']

# Configure logging
try:
    logging.basicConfig(
        filename=app.config['LOGGING_FILE_LOCATION'],
        level=logging.DEBUG,
        format='%(asctime)s %(levelname)s %(name)s %(threadName)s : %(message)s',
    )
    logging.getLogger().addHandler(logging.StreamHandler())
except Exception as e:  # when testing
    sys.stderr.write(str(e))
    os.mkdir('logs')
    logging.basicConfig(
        filename='logs/application.log',
        level=logging.DEBUG,
        format='%(asctime)s %(levelname)s %(name)s %(threadName)s : %(message)s'
    )
    logging.getLogger().addHandler(logging.StreamHandler())

# Configure session to use filesystem (instead of signed cookies)
Session(app)

# Configure CS50 Library to use SQLite database
try:
    db = SQL("sqlite:///database.db")
except Exception as e:  # when testing
    sys.stderr.write(str(e))
    open("database_test.db", "w").close()
    db = SQL("sqlite:///database_test.db")

# Configure flask-mail
mail = Mail(app)


# Configure flask-WTF
csrf = CSRFProtect(app)
csrf.init_app(app)


@app.before_request
def check_for_maintenance():
    # crappy if/elses used here for future expandability
    global maintenance_mode
    # don't block the user if they only have the csrf token
    if maintenance_mode and request.path != '/login':
        if not session:
            return render_template("error/maintenance.html"), 503
        elif not session['admin']:
            return render_template("error/maintenance.html"), 503


@app.route("/")
@login_required
def index():
    announcements = db.execute("SELECT * FROM announcements ORDER BY id DESC")
    for i in range(len(announcements)):
        aid = announcements[i]["id"]

        announcements[i]["description"] = read_file(
            'metadata/announcements/' + str(aid) + '.md')

    return render_template("index.html", data=announcements)


@app.route("/assets/<path:path>/<filename>")
def get_asset(path, filename):
    return send_from_directory("assets/" + path, filename)


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/dl/<path:path>/<filename>")
@login_required
def dl(path, filename):
    return send_from_directory("dl/" + path, filename, as_attachment=True)


@csrf.exempt
@app.route("/login", methods=["GET", "POST"])
def login():
    # Forget user id
    session.clear()

    if request.method == "GET":
        return render_template("login.html", site_key=app.config['HCAPTCHA_SITE'])

    # Reached using POST

    # Ensure username and password were submitted
    if not request.form.get("username") or not request.form.get("password"):
        return render_template("login.html",
                               message="Username and password cannot be blank",
                               site_key=app.config['HCAPTCHA_SITE']), 400

    # Ensure captcha is valid
    if app.config['USE_CAPTCHA']:
        if not check_captcha(app.config['HCAPTCHA_SECRET'], request.form.get('h-captcha-response'), app.config['HCAPTCHA_SITE']):
            return render_template("login.html",
                                   message="CAPTCHA invalid",
                                   site_key=app.config['HCAPTCHA_SITE']), 400

    # Ensure username exists and password is correct
    rows = db.execute("SELECT * FROM users WHERE username = :username",
                      username=request.form.get("username"))
    if len(rows) != 1 or not check_password_hash(rows[0]["password"], request.form.get("password")):
        return render_template("login.html",
                               message="Incorrect username/password",
                               site_key=app.config['HCAPTCHA_SITE']), 401

    # Ensure user is not banned
    if rows[0]["banned"]:
        return render_template("login.html",
                               message="You are banned! Please message an admin to appeal the ban.",
                               site_key=app.config['HCAPTCHA_SITE']), 403

    # Ensure user has confirmed account
    if not rows[0]["verified"]:
        return render_template("login.html",
                               message="You have not confirmed your account yet. Please check your email.",
                               site_key=app.config['HCAPTCHA_SITE']), 403

    # Remember which user has logged in
    session["user_id"] = rows[0]["id"]
    session["username"] = rows[0]["username"]
    session["admin"] = rows[0]["admin"]

    # Redirect user to next page
    if request.form.get("next"):
        return redirect(request.form.get("next"))
    return redirect("/")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


@csrf.exempt
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("register.html", site_key=app.config['HCAPTCHA_SITE'])

    # Reached using POST

    # Ensure username is valid
    if not request.form.get("username"):
        return render_template("register.html",
                               message="Username cannot be blank",
                               site_key=app.config['HCAPTCHA_SITE']), 400
    if not verify_text(request.form.get("username")):
        return render_template("register.html",
                               message="Invalid username",
                               site_key=app.config['HCAPTCHA_SITE']), 400

    # Ensure password is not blank
    if not request.form.get("password") or len(request.form.get("password")) < 8:
        return render_template("register.html",
                               message="Password must be at least 8 characters",
                               site_key=app.config['HCAPTCHA_SITE']), 400
    if not request.form.get("confirmation") or request.form.get("password") != request.form.get("confirmation"):
        return render_template("register.html",
                               message="Passwords do not match",
                               site_key=app.config['HCAPTCHA_SITE']), 400

    # Ensure captcha is valid
    if app.config['USE_CAPTCHA']:
        if not check_captcha(app.config['HCAPTCHA_SECRET'], request.form.get('h-captcha-response'), app.config['HCAPTCHA_SITE']):
            return render_template("register.html",
                                   message="CAPTCHA invalid",
                                   site_key=app.config['HCAPTCHA_SITE']), 400

    # Ensure username and email do not already exist
    rows = db.execute("SELECT * FROM users WHERE username = :username",
                      username=request.form.get("username"))
    if len(rows) > 0:
        return render_template("register.html",
                               message="Username already exists",
                               site_key=app.config['HCAPTCHA_SITE']), 409
    rows = db.execute("SELECT * FROM users WHERE email = :email",
                      email=request.form.get("email"))
    if len(rows) > 0:
        return render_template("register.html",
                               message="Email already exists",
                               site_key=app.config['HCAPTCHA_SITE']), 409

    exp = datetime.utcnow() + timedelta(seconds=1800)
    email = request.form.get('email')
    token = jwt.encode(
        {
            'email': email,
            'expiration': exp.isoformat()
        },
        app.config['SECRET_KEY'],
        algorithm='HS256'
    ).decode('utf-8')
    text = render_template('email/confirm_account_text.txt',
                           username=request.form.get('username'), token=token)

    db.execute("INSERT INTO users(username, password, email, join_date) VALUES(:username, :password, :email, datetime('now'))",
               username=request.form.get("username"),
               password=generate_password_hash(request.form.get("password")),
               email=request.form.get("email"))

    send_email('Confirm Your CTF Account',
               app.config['MAIL_DEFAULT_SENDER'], [email], text, mail)

    return render_template("register.html",
                           message='An account creation confirmation email has been sent to the email address you provided. Be sure to check your spam folder!',
                           site_key=app.config['HCAPTCHA_SITE'])


@app.route('/confirmregister/<token>')
def confirm_register(token):
    try:
        token = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
    except Exception as e:
        sys.stderr.write(str(e))
        token = 0
    if not token:
        flash("Email verification link invalid")
        return redirect("/register")
    if datetime.strptime(token["expiration"], "%Y-%m-%dT%H:%M:%S.%f") < datetime.utcnow():
        db.execute(
            "DELETE FROM users WHERE verified=0 and email=:email", email=token['email'])
        flash("Email verification link expired; Please re-register")
        return redirect("/register")

    db.execute("UPDATE users SET verified=1 WHERE email=:email", email=token['email'])

    # Log user in
    user = db.execute(
        "SELECT * FROM users WHERE email = :email", email=token['email'])[0]
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["admin"] = False

    return redirect("/problem/helloworld")


@app.route("/changepassword", methods=["GET", "POST"])
@login_required
def changepassword():
    if request.method == "GET":
        return render_template("changepassword.html")

    # Reached using POST

    # Ensure passwords were submitted and they match
    if not request.form.get("password"):
        return render_template("changepassword.html",
                               message="Password cannot be blank"), 400
    if not request.form.get("newPassword") or len(request.form.get("newPassword")) < 8:
        return render_template("changepassword.html",
                               message="New password must be at least 8 characters"), 400
    if not request.form.get("confirmation") or request.form.get("newPassword") != request.form.get("confirmation"):
        return render_template("changepassword.html",
                               message="Passwords do not match"), 400

    # Ensure username exists and password is correct
    rows = db.execute("SELECT * FROM users WHERE id = :id",
                      id=session["user_id"])
    if len(rows) != 1 or not check_password_hash(rows[0]["password"], request.form.get("password")):
        return render_template("changepassword.html", message="Incorrect password"), 401

    db.execute("UPDATE users SET password = :new WHERE id = :id",
               new=generate_password_hash(request.form.get("newPassword")),
               id=session["user_id"])

    return redirect("/")


@csrf.exempt
@app.route("/forgotpassword", methods=["GET", "POST"])
def forgotpassword():
    session.clear()

    if request.method == "GET":
        return render_template("forgotpassword.html",
                               site_key=app.config['HCAPTCHA_SITE'])

    # Reached via POST

    email = request.form.get("email")
    if not email:
        return render_template("forgotpassword.html",
                               message="Email cannot be blank"), 400

    # Ensure captcha is valid
    if app.config['USE_CAPTCHA']:
        if not check_captcha(app.config['HCAPTCHA_SECRET'], request.form.get('h-captcha-response'), app.config['HCAPTCHA_SITE']):
            return render_template("forgotpassword.html",
                                   message="CAPTCHA invalid",
                                   site_key=app.config['HCAPTCHA_SITE']), 400

    rows = db.execute("SELECT * FROM users WHERE email = :email",
                      email=request.form.get("email"))

    if len(rows) == 1:
        exp = datetime.utcnow() + timedelta(seconds=1800)
        token = jwt.encode(
            {
                'user_id': rows[0]["id"],
                'expiration': exp.isoformat()
            },
            app.config['SECRET_KEY'],
            algorithm='HS256'
        ).decode('utf-8')
        text = render_template('email/reset_password_text.txt',
                               username=rows[0]["username"], token=token)
        if not app.config['TESTING']:
            send_email('Reset Your CTF Password',
                   app.config['MAIL_DEFAULT_SENDER'], [email], text, mail)
    return render_template("forgotpassword.html",
                           message='If there is an account associated with that email, a password reset email has been sent')


@app.route('/resetpassword/<token>', methods=['GET', 'POST'])
def reset_password_user(token):
    try:
        token = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
        user_id = token['user_id']
    except Exception as e:
        sys.stderr.write(str(e))
        user_id = 0
    if not user_id or datetime.strptime(token["expiration"], "%Y-%m-%dT%H:%M:%S.%f") < datetime.utcnow():
        flash('Password reset link expired/invalid')
        return redirect('/forgotpassword')

    if request.method == "GET":
        return render_template('resetpassword.html')

    if not request.form.get("password") or len(request.form.get("password")) < 8:
        return render_template("resetpassword.html",
                               message="New password must be at least 8 characters"), 400
    if not request.form.get("confirmation") or request.form.get("password") != request.form.get("confirmation"):
        return render_template("resetpassword.html",
                               message="Passwords do not match"), 400

    db.execute("UPDATE users SET password = :new WHERE id = :id",
               new=generate_password_hash(request.form.get("password")), id=user_id)
    return redirect("/login")


@app.route("/contests")
@login_required
def contests():
    past = db.execute(
        "SELECT * FROM contests WHERE end < datetime('now') ORDER BY end DESC")
    current = db.execute(
        "SELECT * FROM contests WHERE end > datetime('now') AND start <= datetime('now') ORDER BY end DESC")
    future = db.execute(
        "SELECT * FROM contests WHERE start > datetime('now') ORDER BY start DESC")
    for contest in past:
        cid = contest["id"]
        contest["description"] = read_file('metadata/contests/' + cid + '/description.md')
    for contest in current:
        cid = contest["id"]
        contest["description"] = read_file('metadata/contests/' + cid + '/description.md')
    for contest in future:
        cid = contest["id"]
        contest["description"] = read_file('metadata/contests/' + cid + '/description.md')
    return render_template("contest/contests.html",
                           past=past, current=current, future=future)


@app.route("/contest/<contest_id>")
@login_required
def contest(contest_id):
    # Ensure contest exists
    contest_info = db.execute("SELECT * FROM contests WHERE id=:cid", cid=contest_id)
    if len(contest_info) != 1:
        return render_template("contest/contest_noexist.html"), 404

    # Ensure contest started or user is admin
    start = datetime.strptime(contest_info[0]["start"], "%Y-%m-%d %H:%M:%S")
    if datetime.utcnow() < start and not session["admin"]:
        return redirect("/")

    title = contest_info[0]["name"]

    # Check for scoreboard permission
    scoreboard = contest_info[0]["scoreboard_visible"] or session["admin"]

    user_info = db.execute("SELECT * FROM contest_users WHERE contest_id=:cid AND user_id=:uid",
                           cid=contest_id, uid=session["user_id"])

    if len(user_info) == 0:
        db.execute("INSERT INTO contest_users (contest_id, user_id) VALUES(:cid, :uid)",
                   cid=contest_id, uid=session["user_id"])

    solved_info = db.execute("SELECT problem_id FROM contest_solved WHERE contest_id=:cid AND user_id=:uid",
                             cid=contest_id, uid=session["user_id"])

    solved_data = set()
    for row in solved_info:
        solved_data.add(row["problem_id"])

    data = []

    info = db.execute("SELECT * FROM contest_problems WHERE contest_id=:cid AND draft=0 ORDER BY category ASC, problem_id ASC",
                      cid=contest_id)
    for row in info:
        keys = {
            "name": row["name"],
            "category": row["category"],
            "problem_id": row["problem_id"],
            "solved": 1 if row["problem_id"] in solved_data else 0,
            "point_value": row["point_value"]
        }
        data.insert(len(data), keys)

    return render_template("contest/contest.html", title=title, scoreboard=scoreboard,
                           data=data)


@app.route("/contest/<contest_id>/drafts")
@admin_required
def contest_drafts(contest_id):
    # Ensure contest exists
    contest_info = db.execute("SELECT * FROM contests WHERE id=:cid", cid=contest_id)
    if len(contest_info) != 1:
        return render_template("contest/contest_noexist.html"), 404

    title = contest_info[0]["name"]

    return render_template("contest/draft_problems.html", title=title,
        data=db.execute("SELECT * FROM contest_problems WHERE contest_id=:cid AND draft=1",
                        cid=contest_id))


@app.route("/contest/<contest_id>/problem/<problem_id>", methods=["GET", "POST"])
@login_required
def contest_problem(contest_id, problem_id):
    # Ensure contest and problem exist
    check = db.execute("SELECT * FROM contests WHERE id=:id", id=contest_id)
    if len(check) != 1:
        return render_template("contest/contest_noexist.html"), 404

    check = db.execute("SELECT * FROM contest_problems WHERE contest_id=:cid AND problem_id=:pid",
                       cid=contest_id, pid=problem_id)
    if len(check) != 1:
        return render_template("contest/contest_problem_noexist.html"), 404

    # check problem exists
    check1 = db.execute("SELECT * FROM contest_problems WHERE contest_id=:cid AND problem_id=:pid AND draft=0",
                        cid=contest_id, pid=problem_id)
    if len(check1) != 1 and session["admin"] != 1:
        return render_template("contest/contest_problem_noexist.html"), 404

    check[0]["description"] = read_file(
        'metadata/contests/' + contest_id + '/' + problem_id + '/description.md')
    check[0]["hints"] = read_file(
        'metadata/contests/' + contest_id + '/' + problem_id + '/hints.md')

    if request.method == "GET":
        return render_template("contest/contest_problem.html", data=check[0])

    # Reached via POST

    # Ensure contest hasn't ended
    end = db.execute("SELECT end FROM contests WHERE id=:id", id=contest_id)
    end = datetime.strptime(end[0]["end"], "%Y-%m-%d %H:%M:%S")
    if datetime.utcnow() > end:
        return render_template("contest/contest_problem.html", data=check[0],
                               status="fail", message="This contest has ended.")

    flag = request.form.get("flag")
    if not flag:
        return render_template("contest/contest_problem.html", data=check[0],
                               status="fail", message="Cannot submit an empty flag!")

    # Check if flag is correct
    if flag != check[0]["flag"]:
        db.execute("INSERT INTO submissions(date, user_id, problem_id, contest_id, correct) VALUES(datetime('now'), :uid, :pid, :cid, 0)",
                   uid=session["user_id"], pid=problem_id, cid=contest_id)
        return render_template("contest/contest_problem.html", data=check[0],
                               status="fail", message="Your flag is incorrect.")

    db.execute("INSERT INTO submissions(date, user_id, problem_id, contest_id, correct) VALUES(datetime('now'), :uid, :pid, :cid, 1)",
               uid=session["user_id"], pid=problem_id, cid=contest_id)

    # Check if user has already found this flag
    check1 = db.execute("SELECT * FROM contest_solved WHERE contest_id=:cid AND user_id=:uid AND problem_id=:pid",
                        cid=contest_id, uid=session["user_id"], pid=problem_id)

    # check if user is in the contest
    check2 = db.execute("SELECT * FROM contest_users WHERE contest_id=:cid AND user_id=:uid",
                        cid=contest_id, uid=session["user_id"])
    if len(check2) == 0:
        db.execute("INSERT INTO contest_users(contest_id, user_id) VALUES (:cid, :uid)",
                   cid=contest_id, uid=session["user_id"])

    if len(check1) == 0:
        points = check[0]["point_value"]
        db.execute("INSERT INTO contest_solved(contest_id, user_id, problem_id) VALUES(:cid, :uid, :pid)",
                   cid=contest_id, pid=problem_id, uid=session["user_id"])
        db.execute("UPDATE contest_users SET lastAC=datetime('now'), points=points+:points WHERE contest_id=:cid AND user_id=:uid",
                   cid=contest_id, points=points, uid=session["user_id"])

    return render_template("contest/contest_problem.html",
                           data=check[0], status="success",
                           message="Congratulations! You have solved this problem!")


@app.route("/contest/<contest_id>/problem/<problem_id>/publish")
@admin_required
def publish_contest_problem(contest_id, problem_id):
    # Ensure contest and problem exist
    check = db.execute("SELECT * FROM contests WHERE id=:id", id=contest_id)
    if len(check) != 1:
        return render_template("contest/contest_noexist.html"), 404

    check = db.execute("SELECT * FROM contest_problems WHERE contest_id=:cid AND problem_id=:pid",
                       cid=contest_id, pid=problem_id)

    if len(check) != 1:
        return render_template("contest/contest_problem_noexist.html"), 404

    db.execute("UPDATE contest_problems SET draft=0 WHERE problem_id=:pid AND contest_id=:cid",
               pid=problem_id, cid=contest_id)

    return redirect("/contest/" + contest_id + "/problem/" + problem_id)


@app.route('/contest/<contest_id>/problem/<problem_id>/edit', methods=["GET", "POST"])
@admin_required
def edit_contest_problem(contest_id, problem_id):
    # Ensure contest exists
    data = db.execute("SELECT * FROM contests WHERE id=:cid", cid=contest_id)
    if len(data) != 1:
        return render_template("contest/contest_noexist.html"), 404

    # Ensure problem exists
    data = db.execute("SELECT name FROM contest_problems WHERE contest_id=:cid AND problem_id=:pid",
                      cid=contest_id, pid=problem_id)
    if len(data) != 1:
        return render_template("contest/contest_problem_noexist.html"), 404

    data[0]["description"] = read_file(
        'metadata/contests/' + contest_id + '/' + problem_id + '/description.md')
    data[0]["hints"] = read_file(
        'metadata/contests/' + contest_id + '/' + problem_id + '/hints.md')

    if request.method == "GET":
        return render_template('problem/editproblem.html', data=data[0])

    # Reached via POST

    if not request.form.get("name") or not request.form.get("description"):
        return render_template('problem/editproblem.html', data=data[0])

    new_name = request.form.get("name")
    new_description = request.form.get("description").replace('\r', '')
    new_hint = request.form.get("hints")
    if not new_description:
        new_description = ""
    if not new_hint:
        new_hint = ""

    db.execute("UPDATE contest_problems SET name=:name WHERE contest_id=:cid AND problem_id=:pid",
               name=new_name, cid=contest_id, pid=problem_id)

    file = open('metadata/contests/' + contest_id + '/' + problem_id + '/description.md', 'w')
    file.write(new_description)
    file.close()
    file = open('metadata/contests/' + contest_id + '/' + problem_id + '/hints.md', 'w')
    file.write(new_hint)
    file.close()

    return redirect(request.path[:-5])


@app.route("/contest/<contest_id>/scoreboard")
@login_required
def contest_scoreboard(contest_id):
    # Ensure contest exists
    contest_info = db.execute("SELECT * FROM contests WHERE id=:cid", cid=contest_id)
    if len(contest_info) != 1:
        return render_template("contest/contest_noexist.html"), 404

    # Ensure proper permissions
    if not contest_info[0]["scoreboard_visible"] and not session["admin"]:
        return redirect("/contest/" + contest_id)

    # Render page
    data = db.execute("SELECT user_id, points, lastAC, username FROM contest_users JOIN users on user_id=users.id WHERE contest_users.contest_id=:cid ORDER BY points DESC, lastAC ASC",
                      cid=contest_id)
    return render_template("contest/contestscoreboard.html",
                           title=contest_info[0]["name"], data=data)


@app.route("/contest/<contest_id>/addproblem", methods=["GET", "POST"])
@admin_required
def contest_add_problem(contest_id):
    # Ensure contest exists
    contest_info = db.execute(
        "SELECT * FROM contests WHERE id=:cid", cid=contest_id)
    if len(contest_info) != 1:
        return render_template("contest/contest_noexist.html"), 404

    # Ensure contest hasn't ended
    end = datetime.strptime(contest_info[0]["end"], "%Y-%m-%d %H:%M:%S")
    if datetime.utcnow() > end:
        return render_template("admin/createproblem.html",
                               message="This contest has already ended!"), 403

    if request.method == "GET":
        return render_template("admin/createproblem.html")

    # Reached via POST
    if not request.form.get("id") or not request.form.get("name") or not request.form.get("description") or not request.form.get("point_value") or not request.form.get("category") or not request.form.get("flag"):
        return render_template("admin/createproblem.html",
                               message="You have not entered all required fields"), 400

    # Check if problem ID is valid
    if not verify_text(request.form.get("id")):
        return render_template("admin/createproblem.html",
                               message="Invalid problem ID"), 400

    problem_id = request.form.get("id")
    name = request.form.get("name")
    description = request.form.get("description").replace('\r', '')
    hints = request.form.get("hints")
    point_value = request.form.get("point_value")
    category = request.form.get("category")
    flag = request.form.get("flag")
    draft = 1 if request.form.get("draft") else 0

    # Ensure problem does not already exist
    problem_info = db.execute("SELECT * FROM contest_problems WHERE contest_id=:cid AND (problem_id=:pid OR name=:name)",
                              cid=contest_id, pid=problem_id, name=name)
    if len(problem_info) != 0:
        return render_template("admin/createproblem.html",
                               message="A problem with this name or ID already exists"), 409

    # Check if file exists & upload if it does
    file = request.files["file"]
    if file.filename:
        if not os.path.exists("dl/" + contest_id):
            os.makedirs("dl/" + contest_id)
        filename = problem_id + ".zip"
        filepath = "dl/" + contest_id + "/"
        file.save(filepath + filename)
        description += '<br><a href="/' + filepath + filename + '">' + filename + '</a>'

    # Modify problems table
    db.execute("INSERT INTO contest_problems(contest_id, problem_id, name, point_value, category, flag, draft) VALUES(:cid, :pid, :name, :point_value, :category, :flag, :draft)",
               cid=contest_id, pid=problem_id, name=name, point_value=point_value, category=category, flag=flag, draft=draft)

    os.makedirs('metadata/contests/' + contest_id + '/' + problem_id)
    file = open('metadata/contests/' + contest_id + '/' + problem_id + '/description.md', 'w')
    file.write(description)
    file.close()
    file = open('metadata/contests/' + contest_id + '/' + problem_id + '/hints.md', 'w')
    file.write(hints)
    file.close()

    # Go to contest page on success
    return redirect("/contest/" + contest_id)


@app.route('/contest/<contest_id>/problem/<problem_id>/export', methods=["GET", "POST"])
@admin_required
def export_contest_problem(contest_id, problem_id):
    # Ensure contest exists
    data1 = db.execute("SELECT * FROM contests WHERE id=:cid", cid=contest_id)
    if len(data1) != 1:
        return render_template("contest/contest_noexist.html"), 404

    # Ensure problem exists
    data = db.execute("SELECT * FROM contest_problems WHERE contest_id=:cid AND problem_id=:pid",
                      cid=contest_id, pid=problem_id)
    if len(data) != 1:
        return render_template("contest/contest_problem_noexist.html"), 404

    if request.method == "GET":
        end = datetime.strptime(data1[0]["end"], "%Y-%m-%d %H:%M:%S")
        if datetime.utcnow() < end:
            return render_template('contest/exportproblem.html', data=data[0],
                                   message="Are you sure? The contest hasn't ended yet")

        return render_template('contest/exportproblem.html', data=data[0])

    # Reached via POST

    new_id = contest_id + "-" + problem_id  # this should be safe already

    check = db.execute("SELECT * FROM problems WHERE id=:id", id=new_id)
    if len(check) != 0:
        return render_template('contest/exportproblem.html', data=data[0],
                               message="This problem has already been exported")

    new_name = data1[0]["name"] + " - " + data[0]["name"]

    # Insert into problems databases
    db.execute("BEGIN")
    db.execute("INSERT INTO problems(id, name, point_value, category, flag) VALUES(:id, :name, :pv, :cat, :flag)",
               id=new_id, name=new_name, pv=data[0]["point_value"],
               cat=data[0]["category"], flag=data[0]["flag"])

    solved = db.execute("SELECT user_id, problem_id FROM contest_solved WHERE contest_id=:cid AND problem_id=:pid",
                        cid=contest_id, pid=problem_id)
    for row in solved:
        db.execute("INSERT INTO problem_solved(user_id, problem_id) VALUES(:uid, :pid)",
                   uid=row['user_id'], pid=row['problem_id'])

    db.execute("COMMIT")

    os.makedirs('metadata/problems/' + new_id)
    file = open('metadata/problems/' + new_id + '/description.md', 'w')
    file.write(read_file('metadata/contests/' + contest_id + '/' + problem_id + '/description.md'))
    file.close()
    file = open('metadata/problems/' + new_id + '/hints.md', 'w')
    file.write(read_file('metadata/contests/' + contest_id + '/' + problem_id + '/hints.md'))
    file.close()
    file = open('metadata/problems/' + new_id + '/editorial.md', 'w')
    file.write("")
    file.close()

    return redirect("/problem/" + new_id)


@app.route('/problems')
@login_required
def problems():
    solved_data = db.execute("SELECT problem_id FROM problem_solved WHERE user_id=:uid",
                             uid=session["user_id"])
    solved = set()
    for row in solved_data:
        solved.add(row["problem_id"])

    data = db.execute("SELECT * FROM problems WHERE draft=0 ORDER BY id ASC")

    return render_template('problem/problems.html', data=data, solved=solved)


@app.route('/problems/draft')
@admin_required
def draft_problems():
    return render_template('problem/draft_problems.html',
                           data=db.execute("SELECT * FROM problems WHERE draft=1"))


@app.route('/problem/<problem_id>', methods=["GET", "POST"])
@login_required
def problem(problem_id):
    data = db.execute("SELECT * FROM problems WHERE id=:problem_id",
                      problem_id=problem_id)

    # Ensure problem exists
    if len(data) != 1:
        return render_template("problem/problem_noexist.html"), 404

    check = db.execute("SELECT * FROM problems WHERE id=:problem_id AND draft=0",
                       problem_id=problem_id)

    if len(check) != 1 and session["admin"] != 1:
        return render_template("problem/problem_noexist.html"), 404

    # Retrieve problem description and hints
    data[0]["description"] = read_file(
        'metadata/problems/' + problem_id + '/description.md')
    data[0]["hints"] = read_file(
        'metadata/problems/' + problem_id + '/hints.md')
    data[0]["editorial"] = read_file(
        'metadata/problems/' + problem_id + '/editorial.md')

    if request.method == "GET":
        return render_template('problem/problem.html', data=data[0])

    # Reached via POST
    flag = data[0]["flag"]

    if not request.form.get("flag"):
        return render_template('problem/problem.html', data=data[0], status="fail",
                               message="You did not enter a flag"), 400

    check = request.form.get("flag") == flag
    db.execute("INSERT INTO submissions (date, user_id, problem_id, correct) VALUES (datetime('now'), :user_id, :problem_id, :check)",
               user_id=session["user_id"], problem_id=problem_id, check=check)

    if not check:
        return render_template('problem/problem.html', data=data[0], status="fail",
                               message="The flag you submitted was incorrect")

    db.execute("INSERT INTO problem_solved(user_id, problem_id) VALUES(:uid, :pid)",
               uid=session["user_id"], pid=problem_id)

    return render_template('problem/problem.html', data=data[0], status="success",
                           message="Congratulations! You have solved this problem!")


@app.route('/problem/<problem_id>/publish')
@admin_required
def publish_problem(problem_id):
    data = db.execute("SELECT * FROM problems WHERE id=:problem_id",
                      problem_id=problem_id)

    # Ensure problem exists
    if len(data) != 1:
        return render_template("problem/problem_noexist.html"), 404

    db.execute("UPDATE problems SET draft=0 WHERE id=:problem_id", problem_id=problem_id)

    return redirect("/problem/" + problem_id)


@app.route('/problem/<problem_id>/editorial')
@login_required
def problem_editorial(problem_id):
    data = db.execute("SELECT * FROM problems WHERE id=:problem_id",
                      problem_id=problem_id)

    # Ensure problem exists
    if len(data) == 0:
        return render_template("problem/problem_noexist.html"), 404

    check = db.execute("SELECT * FROM problems WHERE id=:problem_id AND draft=0",
                       problem_id=problem_id)

    if len(check) != 1 and session["admin"] != 1:
        return render_template("problem/problem_noexist.html"), 404

    # Ensure editorial exists
    editorial = read_file('metadata/problems/' + problem_id + '/editorial.md')
    if not editorial:
        return render_template("problem/problem_noeditorial.html"), 404

    return render_template('problem/problemeditorial.html', data=data[0], ed=editorial)


@app.route('/problem/<problem_id>/edit', methods=["GET", "POST"])
@admin_required
def editproblem(problem_id):
    data = db.execute("SELECT * FROM problems WHERE id=:problem_id",
                      problem_id=problem_id)

    # Ensure problem exists
    if len(data) == 0:
        return render_template("problem/problem_noexist.html"), 404

    data[0]['description'] = read_file(
        'metadata/problems/' + problem_id + '/description.md')
    data[0]['hints'] = read_file(
        'metadata/problems/' + problem_id + '/hints.md')

    if request.method == "GET":
        return render_template('problem/editproblem.html', data=data[0])

    # Reached via POST

    if not request.form.get("name") or not request.form.get("description"):
        return render_template('problem/editproblem.html', data=data[0])

    new_name = request.form.get("name")
    new_description = request.form.get("description").replace('\r', '')
    new_hint = request.form.get("hints")
    if not new_hint:
        new_hint = ""

    db.execute("UPDATE problems SET name=:name WHERE id=:problem_id",
               name=new_name, problem_id=problem_id)
    file = open('metadata/problems/' + problem_id + '/description.md', 'w')
    file.write(new_description)
    file.close()
    file = open('metadata/problems/' + problem_id + '/hints.md', 'w')
    file.write(new_hint)
    file.close()
    return redirect("/problem/" + problem_id)


@app.route('/problem/<problem_id>/editeditorial', methods=["GET", "POST"])
@admin_required
def problem_editeditorial(problem_id):
    data = db.execute("SELECT * FROM problems WHERE id=:problem_id",
                      problem_id=problem_id)

    # Ensure problem exists
    if len(data) == 0:
        return render_template("problem/problem_noexist.html"), 404

    data[0]['editorial'] = read_file('metadata/problems/' + problem_id + '/editorial.md')

    if request.method == "GET":
        return render_template('problem/editeditorial.html', data=data[0])

    # Reached via POST

    new_editorial = request.form.get("editorial")
    if not new_editorial:
        new_editorial = ""
    new_editorial = new_editorial.replace('\r', '')

    file = open('metadata/problems/' + problem_id + '/editorial.md', 'w')
    file.write(new_editorial)
    file.close()
    return redirect("/problem/" + problem_id)


@app.route('/problem/<problem_id>/delete')
@admin_required
def delete_problem(problem_id):
    data = db.execute("SELECT * FROM problems WHERE id=:pid", pid=problem_id)

    # Ensure problem exists
    if len(data) == 0:
        return render_template("problem/problem_noexist.html"), 404

    db.execute("BEGIN")
    db.execute("DELETE FROM problems WHERE id=:pid", pid=problem_id)
    db.execute("DELETE FROM problem_solved WHERE problem_id=:pid", pid=problem_id)
    db.execute("COMMIT")
    shutil.rmtree(f"metadata/problems/{problem_id}")

    return redirect("/problems")


@app.route("/admin/submissions")
@admin_required
def admin_submissions():
    submissions = None

    modifier = " WHERE"
    args = []

    if request.args.get("username"):
        modifier += " username=? AND"
        args.insert(len(args), request.args.get("username"))

    if request.args.get("problem_id"):
        modifier += " problem_id=? AND"
        args.insert(len(args), request.args.get("problem_id"))

    if request.args.get("contest_id"):
        modifier += " contest_id=? AND"
        args.insert(len(args), request.args.get("contest_id"))

    if request.args.get("correct"):
        modifier += " correct=? AND"
        args.insert(len(args), request.args.get("correct") == "AC")

    if len(args) == 0:
        submissions = db.execute(
            "SELECT sub_id, date, username, problem_id, contest_id, correct FROM submissions JOIN users ON user_id=users.id")
    else:
        modifier = modifier[:-4]
        submissions = db.execute(
            "SELECT sub_id, date, username, problem_id, contest_id, correct FROM submissions JOIN users ON user_id=users.id" + modifier, *args)

    return render_template("admin/submissions.html", data=submissions)


@app.route("/admin/users")
@admin_required
def admin_users():
    data = db.execute("SELECT * FROM users")
    return render_template("admin/users.html", data=data)


@app.route("/admin/createcontest", methods=["GET", "POST"])
@admin_required
def admin_createcontest():
    if request.method == "GET":
        return render_template("admin/createcontest.html")

    # Reached using POST

    contest_id = request.form.get("contest_id")

    # Ensure contest ID is valid
    if not verify_text(contest_id):
        return render_template("admin/createcontest.html",
                               message="Invalid contest ID"), 400

    contest_name = request.form.get("contest_name")

    # Ensure contest doesn't already exist
    check = db.execute("SELECT * FROM contests WHERE id=:contest_id OR name=:contest_name",
                       contest_id=contest_id, contest_name=contest_name)
    if len(check) != 0:
        return render_template("admin/createcontest.html",
                               message="A contest with that name or ID already exists"), 409

    start = request.form.get("start")
    end = request.form.get("end")

    # Ensure start and end dates are valid
    check_start = datetime.strptime(start, "%Y-%m-%dT%H:%M:%S.%fZ")
    check_end = datetime.strptime(end, "%Y-%m-%dT%H:%M:%S.%fZ")
    if check_end < check_start:
        return render_template("admin/createcontest.html",
                               message="Contest cannot end before it starts!"), 400

    description = request.form.get("description").replace('\r', '')
    scoreboard_visible = bool(request.form.get("scoreboard_visible"))
    if not description:
        return render_template("admin/createcontest.html",
                               message="Description cannot be empty!"), 400

    db.execute("INSERT INTO contests (id, name, start, end, scoreboard_visible) VALUES (:id, :name, datetime(:start), datetime(:end), :scoreboard_visible)",
               id=contest_id, name=contest_name, start=start, end=end,
               scoreboard_visible=scoreboard_visible)

    os.makedirs('metadata/contests/' + contest_id)
    file = open('metadata/contests/' + contest_id + '/description.md', 'w')
    file.write(description)
    file.close()

    return redirect("/contest/" + contest_id)


@app.route("/admin/createproblem", methods=["GET", "POST"])
@admin_required
def createproblem():
    if request.method == "GET":
        return render_template("admin/createproblem.html")

    # Reached via POST

    if not request.form.get("id") or not request.form.get("name") or not request.form.get("description") or not request.form.get("point_value") or not request.form.get("category") or not request.form.get("flag"):
        return render_template("admin/createproblem.html",
                               message="You have not entered all required fields"), 400

    # Check if problem ID is valid
    if not verify_text(request.form.get("id")):
        return render_template("admin/createproblem.html",
                               message="Invalid problem ID"), 400

    problem_id = request.form.get("id")
    name = request.form.get("name")
    description = request.form.get("description").replace('\r', '')
    hints = request.form.get("hints")
    point_value = request.form.get("point_value")
    category = request.form.get("category")
    flag = request.form.get("flag")
    draft = 1 if request.form.get("draft") else 0
    if not description:
        description = ""
    if not hints:
        hints = ""

    # Ensure problem does not already exist
    problem_info = db.execute("SELECT * FROM problems WHERE id=:problem_id OR name=:name",
                              problem_id=problem_id, name=name)
    if len(problem_info) != 0:
        return render_template("admin/createproblem.html",
                               message="A problem with this name or ID already exists"), 409

    # Check if file exists & upload if it does
    file = request.files["file"]
    if file.filename:
        filename = problem_id + ".zip"
        file.save("dl/" + filename)
        description += '<br><a href="/dl/' + filename + '">' + filename + '</a>'

    # Modify problems table
    db.execute("INSERT INTO problems (id, name, point_value, category, flag, draft) VALUES (:id, :name, :point_value, :category, :flag, :draft)",
               id=problem_id, name=name, point_value=point_value, category=category,
               flag=flag, draft=draft)

    os.makedirs('metadata/problems/' + problem_id)
    f = open('metadata/problems/' + problem_id + '/description.md', 'w')
    f.write(description)
    f.close()
    f = open('metadata/problems/' + problem_id + '/hints.md', 'w')
    f.write(hints)
    f.close()
    f = open('metadata/problems/' + problem_id + '/editorial.md', 'w')
    f.write("")
    f.close()

    # Go to problems page on success
    return redirect("/problems")


@app.route("/admin/ban")
@admin_required
def ban():
    user_id = request.args.get("user_id")
    if not user_id:
        return "Must provide user ID"

    user = db.execute("SELECT * FROM users WHERE id=:id", id=user_id)

    if len(user) != 1:
        return "That user doesn't exist!"

    user_id = int(user_id)
    user = user[0]

    if user_id == session["user_id"]:
        return "Cannot ban yourself!"

    if user["admin"] and session["user_id"] != 1:
        return "Only the super-admin can ban admins"

    if user_id == 1:
        return "Cannot ban super-admin"

    db.execute("UPDATE users SET banned=:status WHERE id=:id",
               status=not user["banned"], id=user_id)

    if user["banned"]:
        return "Successfully unbanned " + user["username"]
    else:
        return "Successfully banned " + user["username"]


@app.route("/admin/resetpass")
@admin_required
def reset_password():
    user_id = request.args.get("user_id")
    if not user_id:
        return "Must provide user ID", 400

    password = generate_password()
    db.execute("UPDATE users SET password=:p WHERE id=:id",
               p=generate_password_hash(password), id=user_id)

    return "New password is " + password


@app.route("/admin/createannouncement", methods=["GET", "POST"])
@admin_required
def createannouncement():
    if request.method == "GET":
        return render_template("admin/createannouncement.html")

    # Reached via POST

    if not request.form.get("name") or not request.form.get("description"):
        return render_template("admin/createannouncement.html",
                               message="You have not entered all required fields"), 400

    name = request.form.get("name")
    description = request.form.get("description").replace('\r', '')

    db.execute("INSERT INTO announcements (name, date) VALUES (:name, datetime('now'))",
               name=name)
    aid = db.execute("SELECT * FROM announcements ORDER BY date DESC")[0]["id"]

    f = open('metadata/announcements/' + str(aid) + '.md', 'w')
    f.write(description)
    f.close()

    # Go to problems page on success
    return redirect("/")


@app.route("/admin/deleteannouncement")
@admin_required
def delete_announcement():
    aid = request.args.get("aid")
    if not aid:
        return "Must provide announcement ID", 400

    db.execute("DELETE FROM announcements WHERE id=:id", id=aid)
    os.remove('metadata/announcements/' + aid + '.md')

    return redirect("/")


@app.route("/admin/deletecontest/<contest_id>", methods=["GET", "POST"])
@admin_required
def delete_contest(contest_id):
    # Ensure contest exists
    check = db.execute("SELECT * FROM contests WHERE id=:cid", cid=contest_id)
    if len(check) == 0:
        return render_template("contest/contest_noexist.html")

    if request.method == "GET":
        return render_template("contest/delete_confirm.html", data=check[0])

    # Reached using POST

    db.execute("BEGIN")
    db.execute("DELETE FROM contests WHERE id=:cid", cid=contest_id)
    db.execute("DELETE FROM contest_users WHERE contest_id=:cid", cid=contest_id)
    db.execute("DELETE FROM contest_solved WHERE contest_id=:cid", cid=contest_id)
    db.execute("DELETE FROM contest_problems WHERE contest_id=:cid", cid=contest_id)
    db.execute("COMMIT")

    shutil.rmtree('metadata/contests/' + contest_id)

    return redirect("/contests")


@app.route("/admin/makeadmin")
@admin_required
def makeadmin():
    user_id = request.args.get("user_id")
    if not user_id:
        return "Must provide user ID", 400

    admin_status = db.execute("SELECT admin FROM users WHERE id=:id", id=user_id)

    if len(admin_status) != 1:
        return "That user doesn't exist!"

    user_id = int(user_id)
    admin_status = admin_status[0]["admin"]

    if admin_status and session["user_id"] != 1:
        return "Only the super-admin can revoke admin status"

    if admin_status and user_id == 1:
        return "Cannot revoke super-admin's admin status"

    if admin_status and session["user_id"] == 1:
        db.execute("UPDATE users SET admin=0 WHERE id=:id", id=user_id)
        return "Admin privileges for user with ID " + str(user_id) + " revoked"

    db.execute("UPDATE users SET admin=1 WHERE id=:id", id=user_id)
    return "Admin privileges for user with ID " + str(user_id) + " granted"


@app.route('/admin/editannouncement/<a_id>', methods=["GET", "POST"])
@admin_required
def editannouncement(a_id):
    data = db.execute("SELECT * FROM announcements WHERE id=:a_id", a_id=a_id)

    # Ensure announcement exists
    if len(data) == 0:
        return redirect("/")

    data[0]["description"] = read_file('metadata/announcements/' + a_id + '.md')

    if request.method == "GET":
        return render_template('admin/editannouncement.html', data=data[0])

    # Reached via POST
    new_name = request.form.get("name")
    new_description = request.form.get("description").replace('\r', '')

    if not new_name:
        return render_template('admin/editannouncement.html',
                               data=data[0], message="Name cannot be empty"), 400
    if not new_description:
        return render_template('admin/editannouncement.html',
                               data=data[0], message="Description cannot be empty"), 400

    # Update database
    db.execute("UPDATE announcements SET name=:name WHERE id=:a_id",
               name=new_name, a_id=a_id)

    file = open('metadata/announcements/' + a_id + '.md', 'w')
    file.write(new_description)
    file.close()

    return redirect("/")


@app.route('/admin/editcontest/<contest_id>', methods=["GET", "POST"])
@admin_required
def editcontest(contest_id):
    data = db.execute("SELECT * FROM contests WHERE id=:cid", cid=contest_id)

    # Ensure contest exists
    if len(data) == 0:
        return redirect("/contests")

    data[0]["description"] = read_file(
        'metadata/contests/' + contest_id + '/description.md')

    if request.method == "GET":
        return render_template('admin/editcontest.html', data=data[0])

    # Reached via POST
    new_name = request.form.get("name")
    new_description = request.form.get("description").replace('\r', '')
    start = request.form.get("start")
    end = request.form.get("end")

    if not new_name:
        return render_template('admin/editcontest.html', data=data[0],
                               message="Name cannot be empty"), 400
    if not new_description:
        return render_template('admin/editcontest.html', data=data[0],
                               message="Description cannot be empty"), 400

    # Ensure start and end dates are valid
    check_start = datetime.strptime(start, "%Y-%m-%dT%H:%M:%S.%fZ")
    check_end = datetime.strptime(end, "%Y-%m-%dT%H:%M:%S.%fZ")
    if check_end < check_start:
        return render_template("admin/editcontest.html",
                               message="Contest cannot end before it starts!"), 400

    db.execute("UPDATE contests SET name=:name, start=datetime(:start), end=datetime(:end) WHERE id=:cid",
               name=new_name, start=start, end=end, cid=contest_id)

    file = open('metadata/contests/' + contest_id + '/description.md', 'w')
    file.write(new_description)
    file.close()

    return redirect("/contests")


@app.route("/admin/maintenance")
@admin_required
def maintenance():
    global maintenance_mode
    maintenance_mode = not maintenance_mode
    return "Enabled maintenance mode" if maintenance_mode else "Disabled maintenance mode"


def errorhandler(e):
    if not isinstance(e, HTTPException):
        e = InternalServerError()
    if e.code == 404:
        return render_template("error/404.html"), 404
    if e.code == 500:
        return render_template("error/500.html"), 500
    return render_template("error/generic.html", e=e), e.code


# Listen for errors
for code in default_exceptions:
    app.errorhandler(code)(errorhandler)


@app.route("/teapot")
def teapot():
    return render_template("error/418.html"), 418


@app.after_request
def security_policies(response):
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    return response
