"""Fix My Block - Flask server.

Serves index.html and a small JSON API backed by Postgres (env DATABASE_URL).
Run locally:  DATABASE_URL=... python3 -m flask --app server run
On Render:    gunicorn server:app
"""
import os
import secrets
import uuid
from decimal import Decimal

import bcrypt
import psycopg
from flask import Flask, jsonify, request, send_from_directory, session
from psycopg.rows import dict_row

HERE = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.environ.get("RENDER")),  # https on Render
    MAX_CONTENT_LENGTH=8 * 1024 * 1024,  # photo uploads arrive as base64 data URLs
)

# XP / points awarded per action (same numbers the page shows).
XP_REPORT, PTS_REPORT = 100, 50
XP_UPVOTE, PTS_UPVOTE = 10, 5
XP_DONATE, PTS_DONATE = 50, 30
MAX_IMAGE_CHARS = 3_000_000

NEIGHBORHOOD_COORDS = {
    "Hunts Point": (40.8115, -73.8830),
    "Mott Haven": (40.8090, -73.9220),
    "Fordham": (40.8620, -73.8970),
    "Soundview": (40.8140, -73.8680),
    "Riverdale": (40.9030, -73.9060),
    "Pelham Bay": (40.8500, -73.8200),
}


# ---------------------------------------------------------------- helpers

def db():
    """New connection per request; `with db() as conn:` commits on success."""
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def new_id():
    return str(uuid.uuid4())


def num(v):
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    return v


def iso(ts):
    return ts.isoformat() if ts else None


def err(msg, code=400):
    return jsonify({"error": msg}), code


def body():
    return request.get_json(silent=True) or {}


def user_json(u):
    if not u:
        return None
    return {
        "id": u["id"],
        "email": u["email"],
        "name": u["display_name"],
        "role": u["role"],
        "neighborhood": u["neighborhood"] or "Bronx",
        "xp": u["xp_points"],
        "points": u["points"],
        "level": u["xp_points"] // 500 + 1,
    }


USER_COLS = "id, email, display_name, role, neighborhood, xp_points, points"


def current_user(conn):
    uid = session.get("uid")
    if not uid:
        return None
    return conn.execute(f"SELECT {USER_COLS} FROM users WHERE id = %s", (uid,)).fetchone()


def award(conn, uid, xp, pts):
    return conn.execute(
        f"UPDATE users SET xp_points = xp_points + %s, points = points + %s WHERE id = %s RETURNING {USER_COLS}",
        (xp, pts, uid)).fetchone()


def require_user(conn):
    u = current_user(conn)
    if not u:
        session.pop("uid", None)
    return u


# ---------------------------------------------------------------- page

@app.get("/")
def index():
    return send_from_directory(HERE, "index.html")


# ---------------------------------------------------------------- auth

@app.get("/api/me")
def me():
    with db() as conn:
        return jsonify({"user": user_json(current_user(conn))})


@app.post("/api/login")
def login():
    d = body()
    email = (d.get("email") or "").strip().lower()
    password = d.get("password") or ""
    with db() as conn:
        row = conn.execute(f"SELECT {USER_COLS}, password_hash FROM users WHERE lower(email) = %s",
                           (email,)).fetchone()
    if not row or not row["password_hash"] or not bcrypt.checkpw(password.encode(), row["password_hash"].encode()):
        return err("Wrong email or password.", 401)
    session.clear()
    session["uid"] = row["id"]
    return jsonify({"user": user_json(row)})


@app.post("/api/signup")
def signup():
    d = body()
    name = (d.get("name") or "").strip()[:80]
    email = (d.get("email") or "").strip().lower()[:200]
    password = d.get("password") or ""
    neighborhood = (d.get("neighborhood") or "").strip()[:80] or None
    if not name or "@" not in email:
        return err("Name and a valid email are required.")
    if len(password) < 6:
        return err("Password must be at least 6 characters.")
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    with db() as conn:
        if conn.execute("SELECT 1 FROM users WHERE lower(email) = %s", (email,)).fetchone():
            return err("An account with that email already exists. Please log in.", 409)
        row = conn.execute(
            f"""INSERT INTO users (id, email, display_name, role, xp_points, points, password_hash, neighborhood)
                VALUES (%s, %s, %s, 'resident', 0, 0, %s, %s) RETURNING {USER_COLS}""",
            (new_id(), email, name, pw_hash, neighborhood)).fetchone()
    session.clear()
    session["uid"] = row["id"]
    return jsonify({"user": user_json(row)}), 201


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


# ---------------------------------------------------------------- reads

def load_issues(conn, uid, issue_id=None):
    where, params = ("WHERE i.id = %s", [issue_id]) if issue_id else ("", [])
    rows = conn.execute(f"""
        SELECT i.*, COALESCE(u.display_name, i.reporter_name, 'Bronx Resident') AS reported_by,
               EXISTS (SELECT 1 FROM issue_upvotes v WHERE v.issue_id = i.id AND v.user_id = %s) AS user_upvoted
          FROM issues i LEFT JOIN users u ON u.id = i.user_id
          {where}
         ORDER BY i.created_at DESC""", [uid] + params).fetchall()
    ids = [r["id"] for r in rows]
    updates = {}
    if ids:
        for up in conn.execute("""SELECT issue_id, text, created_at FROM issue_updates
                                   WHERE issue_id = ANY(%s) ORDER BY created_at, id""", (ids,)):
            updates.setdefault(up["issue_id"], []).append({"date": iso(up["created_at"]), "text": up["text"]})
    return [{
        "id": r["id"],
        "title": r["title"],
        "neighborhood": r["neighborhood"] or "Bronx",
        "location": r["location"] or r["neighborhood"] or "",
        "category": r["category"],
        "status": r["status"],
        "reportedBy": r["reported_by"],
        "reportedDate": iso(r["created_at"]),
        "upvotes": r["upvotes_count"],
        "userUpvoted": bool(r["user_upvoted"]),
        "fundingGoal": num(r["funding_goal"]),
        "fundingCurrent": num(r["funding_current"]),
        "lat": num(r["latitude"]),
        "lng": num(r["longitude"]),
        "image": r["image_url"] or "",
        "description": r["description"] or "",
        "updates": updates.get(r["id"], []),
    } for r in rows]


@app.get("/api/issues")
def get_issues():
    with db() as conn:
        return jsonify(load_issues(conn, session.get("uid")))


@app.get("/api/tools")
def get_tools():
    with db() as conn:
        rows = conn.execute("""SELECT id, item, requested_by, neighborhood, qty_needed, icon, pledged
                                 FROM tools_needed ORDER BY sort_order, id""").fetchall()
    return jsonify([{"id": r["id"], "item": r["item"], "requestedBy": r["requested_by"],
                     "neighborhood": r["neighborhood"], "qtyNeeded": r["qty_needed"],
                     "icon": r["icon"], "pledged": r["pledged"]} for r in rows])


@app.get("/api/donors")
def get_donors():
    """Recent donations, newest first (the page's 'Recent Donors' list)."""
    with db() as conn:
        rows = conn.execute("""SELECT d.id, COALESCE(d.donor_name, u.display_name, 'Anonymous Citizen') AS name,
                                      d.detail, d.donation_type, d.amount_usd, d.tool_name,
                                      COALESCE(d.neighborhood, 'Bronx') AS neighborhood, d.created_at
                                 FROM donations d LEFT JOIN users u ON u.id = d.user_id
                                ORDER BY d.created_at DESC LIMIT 20""").fetchall()
    out = []
    for r in rows:
        detail = r["detail"] or (f"Pledged ${num(r['amount_usd'])} Grant" if r["donation_type"] == "monetary"
                                 else f"Pledged: {r['tool_name']}")
        out.append({"id": r["id"], "name": r["name"], "detail": detail,
                    "neighborhood": r["neighborhood"], "createdAt": iso(r["created_at"])})
    return jsonify(out)


@app.get("/api/rewards")
def get_rewards():
    with db() as conn:
        rows = conn.execute("SELECT id, title, cost, icon, color, description FROM rewards ORDER BY sort_order, id").fetchall()
    return jsonify(rows)


@app.get("/api/redemptions")
def get_redemptions():
    """The logged-in user's redeemed rewards."""
    with db() as conn:
        u = require_user(conn)
        if not u:
            return err("Please log in.", 401)
        rows = conn.execute("""SELECT id, reward_id, reward_title, xp_cost, status, created_at
                                 FROM redemptions WHERE user_id = %s ORDER BY created_at DESC""", (u["id"],)).fetchall()
    return jsonify([{**r, "created_at": iso(r["created_at"])} for r in rows])


# ---------------------------------------------------------------- writes (login required)

@app.post("/api/issues")
def create_issue():
    d = body()
    title = (d.get("title") or "").strip()[:200]
    neighborhood = (d.get("neighborhood") or "").strip()[:80]
    category = (d.get("category") or "").strip()[:80]
    location = (d.get("location") or "").strip()[:200]
    description = (d.get("description") or "").strip()[:4000] or "Reported photo evidence uploaded by Bronx resident."
    image = d.get("image") or "https://images.unsplash.com/photo-1530587191325-3db32d826c18?w=500&auto=format&fit=crop&q=80"
    if not title or not neighborhood or not category or not location:
        return err("Title, neighborhood, category and location are required.")
    if not isinstance(image, str) or len(image) > MAX_IMAGE_CHARS or not image.startswith(("https://", "data:image/")):
        return err("Photo is too large or not an image.")
    lat, lng = d.get("lat"), d.get("lng")
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        lat, lng = NEIGHBORHOOD_COORDS.get(neighborhood, (40.8448, -73.8648))
    with db() as conn:
        u = require_user(conn)
        if not u:
            return err("Please log in to report an issue.", 401)
        iid = new_id()
        # The reporter's own upvote starts the count at 1 (as on the original page).
        conn.execute("""INSERT INTO issues (id, user_id, title, category, neighborhood, description, latitude, longitude,
                                            image_url, status, upvotes_count, location, funding_goal, funding_current)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'Reported',1,%s,150,0)""",
                     (iid, u["id"], title, category, neighborhood, description, lat, lng, image, location))
        conn.execute("INSERT INTO issue_upvotes (issue_id, user_id) VALUES (%s, %s)", (iid, u["id"]))
        conn.execute("INSERT INTO issue_updates (id, issue_id, text) VALUES (%s, %s, %s)",
                     (new_id(), iid, f"Reported by {u['display_name']}. Ticket created."))
        user = award(conn, u["id"], XP_REPORT, PTS_REPORT)
        issue = load_issues(conn, u["id"], iid)[0]
    return jsonify({"issue": issue, "user": user_json(user), "xp": XP_REPORT, "points": PTS_REPORT}), 201


@app.post("/api/issues/<issue_id>/upvote")
def toggle_upvote(issue_id):
    """Toggle the logged-in user's upvote. Adding one earns XP; removing does not take it back."""
    with db() as conn:
        u = require_user(conn)
        if not u:
            return err("Please log in to upvote.", 401)
        if not conn.execute("SELECT 1 FROM issues WHERE id = %s", (issue_id,)).fetchone():
            return err("Issue not found.", 404)
        removed = conn.execute("DELETE FROM issue_upvotes WHERE issue_id = %s AND user_id = %s",
                               (issue_id, u["id"])).rowcount
        xp = pts = 0
        if removed:
            conn.execute("UPDATE issues SET upvotes_count = GREATEST(0, upvotes_count - 1) WHERE id = %s", (issue_id,))
            user = u
        else:
            conn.execute("INSERT INTO issue_upvotes (issue_id, user_id) VALUES (%s, %s)", (issue_id, u["id"]))
            conn.execute("UPDATE issues SET upvotes_count = upvotes_count + 1 WHERE id = %s", (issue_id,))
            xp, pts = XP_UPVOTE, PTS_UPVOTE
            user = award(conn, u["id"], xp, pts)
        issue = load_issues(conn, u["id"], issue_id)[0]
    return jsonify({"issue": issue, "upvoted": not removed, "user": user_json(user), "xp": xp, "points": pts})


@app.post("/api/donations")
def create_donation():
    """{type: 'monetary', amount, issue_id?, donor_name?}  or  {type: 'tool_pledge', tool_name, neighborhood, notes?}"""
    d = body()
    kind = d.get("type")
    with db() as conn:
        u = require_user(conn)
        if not u:
            return err("Please log in to donate or pledge.", 401)
        did = new_id()
        issue = None
        if kind == "monetary":
            try:
                amount = Decimal(str(d.get("amount")))
            except Exception:
                return err("Invalid amount.")
            if not (0 < amount <= 10000):
                return err("Invalid amount.")
            donor = (d.get("donor_name") or "").strip()[:80] or u["display_name"]
            issue_id = d.get("issue_id") or None
            if issue_id and not conn.execute("SELECT 1 FROM issues WHERE id = %s", (issue_id,)).fetchone():
                return err("Issue not found.", 404)
            amt = num(amount)
            conn.execute("""INSERT INTO donations (id, user_id, issue_id, donation_type, amount_usd, donor_name, neighborhood, detail)
                            VALUES (%s,%s,%s,'monetary',%s,%s,%s,%s)""",
                         (did, u["id"], issue_id, amount, donor, u["neighborhood"] or "Bronx", f"Pledged ${amt} Grant"))
            if issue_id:
                conn.execute("UPDATE issues SET funding_current = funding_current + %s WHERE id = %s", (amount, issue_id))
                conn.execute("INSERT INTO issue_updates (id, issue_id, text) VALUES (%s,%s,%s)",
                             (new_id(), issue_id, f"Pledge of ${amt} received from {donor}."))
                issue = load_issues(conn, u["id"], issue_id)[0]
        elif kind == "tool_pledge":
            tool = (d.get("tool_name") or "").strip()[:200]
            if not tool:
                return err("Please enter the item name.")
            neighborhood = (d.get("neighborhood") or "").strip()[:80] or u["neighborhood"] or "Bronx"
            notes = (d.get("notes") or "").strip()[:300] or None
            conn.execute("""INSERT INTO donations (id, user_id, donation_type, tool_name, dropoff_location, donor_name, neighborhood, detail)
                            VALUES (%s,%s,'tool_pledge',%s,%s,%s,%s,%s)""",
                         (did, u["id"], tool, notes, u["display_name"], neighborhood, f"Pledged: {tool}"))
            # Pledging one of the listed "tools needed" bumps its pledged count.
            conn.execute("UPDATE tools_needed SET pledged = pledged + 1 WHERE lower(item) = lower(%s)", (tool,))
        else:
            return err("type must be 'monetary' or 'tool_pledge'.")
        user = award(conn, u["id"], XP_DONATE, PTS_DONATE)
    return jsonify({"id": did, "issue": issue, "user": user_json(user), "xp": XP_DONATE, "points": PTS_DONATE}), 201


@app.post("/api/redeem")
def redeem():
    reward_id = body().get("reward_id")
    with db() as conn:
        u = require_user(conn)
        if not u:
            return err("Please log in to redeem rewards.", 401)
        reward = conn.execute("SELECT id, title, cost FROM rewards WHERE id = %s", (reward_id,)).fetchone()
        if not reward:
            return err("Reward not found.", 404)
        # Atomic check-and-spend so points can't go negative.
        user = conn.execute(f"""UPDATE users SET points = points - %s WHERE id = %s AND points >= %s
                                RETURNING {USER_COLS}""", (reward["cost"], u["id"], reward["cost"])).fetchone()
        if not user:
            return err("Not enough points.", 400)
        rid = new_id()
        conn.execute("""INSERT INTO redemptions (id, user_id, reward_title, xp_cost, status, reward_id)
                        VALUES (%s,%s,%s,%s,'claimed',%s)""", (rid, u["id"], reward["title"], reward["cost"], reward["id"]))
    return jsonify({"id": rid, "user": user_json(user)}), 201


if __name__ == "__main__":
    app.run(debug=True)
