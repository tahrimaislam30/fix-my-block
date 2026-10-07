"""Idempotent migration + seed for Fix My Block.

Usage:  DATABASE_URL=postgres://... python3 seed.py

1. Adds the columns/tables the web app needs (ALTER ... IF NOT EXISTS / CREATE ... IF NOT EXISTS).
2. Backfills the new columns for rows that already existed in the database.
3. Inserts the data that used to be hard-coded in index.html (bronxIssues,
   physicalToolsNeeded, recentDonors, rewardsCatalog) with fixed ids, ON CONFLICT DO NOTHING.
4. Gives every user without a password a bcrypt hash of the demo password 'demo1234'.

Running it again changes nothing.
"""
import json
import os

import bcrypt
import psycopg

DEMO_PASSWORD = "demo1234"

MIGRATIONS = """
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR;
ALTER TABLE users ADD COLUMN IF NOT EXISTS neighborhood VARCHAR;
ALTER TABLE users ADD COLUMN IF NOT EXISTS points INTEGER;

ALTER TABLE issues ADD COLUMN IF NOT EXISTS location VARCHAR;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS reporter_name VARCHAR;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS funding_goal NUMERIC NOT NULL DEFAULT 150;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS funding_current NUMERIC;

ALTER TABLE donations ADD COLUMN IF NOT EXISTS donor_name VARCHAR;
ALTER TABLE donations ADD COLUMN IF NOT EXISTS neighborhood VARCHAR;
ALTER TABLE donations ADD COLUMN IF NOT EXISTS detail VARCHAR;

CREATE TABLE IF NOT EXISTS issue_updates (
    id         VARCHAR PRIMARY KEY,
    issue_id   VARCHAR NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    text       TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS issue_upvotes (
    issue_id   VARCHAR NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    user_id    VARCHAR NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (issue_id, user_id)
);
CREATE TABLE IF NOT EXISTS tools_needed (
    id           VARCHAR PRIMARY KEY,
    item         VARCHAR NOT NULL,
    requested_by VARCHAR,
    neighborhood VARCHAR,
    qty_needed   VARCHAR,
    icon         VARCHAR,
    pledged      INTEGER NOT NULL DEFAULT 0,
    sort_order   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS rewards (
    id          VARCHAR PRIMARY KEY,
    title       VARCHAR NOT NULL,
    cost        INTEGER NOT NULL,
    icon        VARCHAR,
    color       VARCHAR,
    description TEXT,
    sort_order  INTEGER NOT NULL DEFAULT 0
);
ALTER TABLE redemptions ADD COLUMN IF NOT EXISTS reward_id VARCHAR REFERENCES rewards(id) ON DELETE SET NULL;
"""

# Backfills for rows that existed before the app was wired up. Each only touches NULLs.
BACKFILLS = """
-- Spendable points: half of lifetime XP minus points already spent on redemptions.
UPDATE users u SET points = GREATEST(0, u.xp_points / 2 - COALESCE(
    (SELECT SUM(r.xp_cost) FROM redemptions r WHERE r.user_id = u.id), 0))
 WHERE u.points IS NULL;
ALTER TABLE users ALTER COLUMN points SET DEFAULT 0;
ALTER TABLE users ALTER COLUMN points SET NOT NULL;

UPDATE users SET neighborhood = 'Hunts Point' WHERE id = 'u1010000-0000-0000-0000-000000000001' AND neighborhood IS NULL;
UPDATE users SET neighborhood = 'Mott Haven'  WHERE id = 'u1020000-0000-0000-0000-000000000002' AND neighborhood IS NULL;
UPDATE users SET neighborhood = 'Mott Haven'  WHERE id = 'u1030000-0000-0000-0000-000000000003' AND neighborhood IS NULL;
UPDATE users SET neighborhood = 'Soundview'   WHERE id = 'u1040000-0000-0000-0000-000000000004' AND neighborhood IS NULL;

UPDATE issues SET location = 'Food Center Drive'             WHERE id = 'i2010000-0000-0000-0000-000000000001' AND location IS NULL;
UPDATE issues SET location = 'E 138th St near subway entrance' WHERE id = 'i2020000-0000-0000-0000-000000000002' AND location IS NULL;
UPDATE issues SET location = 'Alley off Fordham Rd'          WHERE id = 'i2030000-0000-0000-0000-000000000003' AND location IS NULL;
UPDATE issues SET location = 'Soundview Ave crosswalk'       WHERE id = 'i2040000-0000-0000-0000-000000000004' AND location IS NULL;

-- Money already raised = monetary donations linked to the issue.
UPDATE issues i SET funding_current = COALESCE(
    (SELECT SUM(d.amount_usd) FROM donations d WHERE d.issue_id = i.id AND d.donation_type = 'monetary'), 0)
 WHERE i.funding_current IS NULL;
ALTER TABLE issues ALTER COLUMN funding_current SET DEFAULT 0;
ALTER TABLE issues ALTER COLUMN funding_current SET NOT NULL;

-- Donor display fields for existing donations.
UPDATE donations d SET donor_name = u.display_name FROM users u
 WHERE d.user_id = u.id AND d.donor_name IS NULL;
UPDATE donations d SET neighborhood = COALESCE(
    (SELECT i.neighborhood FROM issues i WHERE i.id = d.issue_id),
    (SELECT u.neighborhood FROM users u WHERE u.id = d.user_id), 'Bronx')
 WHERE d.neighborhood IS NULL;
UPDATE donations SET detail = CASE
    WHEN donation_type = 'monetary' THEN 'Pledged $' || RTRIM(TO_CHAR(amount_usd, 'FM999999990.##'), '.') || ' Grant'
    ELSE 'Pledged: ' || COALESCE(tool_name, 'equipment') END
 WHERE detail IS NULL;

-- Link existing redemptions to catalog rewards where the title matches.
UPDATE redemptions r SET reward_id = w.id FROM rewards w
 WHERE r.reward_id IS NULL AND r.reward_title = w.title;
"""

# ---- data that used to be hard-coded in index.html ----

def iid(n):
    return f"i2{n:02d}0000-0000-0000-0000-{n:012d}"

# bronxIssues (none of them is the same incident as the 4 database issues:
# different neighborhoods/locations, so all 5 are kept).
PAGE_ISSUES = [
    dict(id=iid(5), title="Illegal Trash Dumping near Hunts Point Produce Terminal", neighborhood="Hunts Point",
         location="Hunts Point Ave & Food Center Drive", category="Illegal Dumping", status="Reported",
         reporter="Maria S.", date="2026-08-30", upvotes=24, goal=150, current=90, lat=40.8122, lng=-73.8821,
         image="https://images.unsplash.com/photo-1530587191325-3db32d826c18?w=500&auto=format&fit=crop&q=80",
         description="Large pile of wooden pallets, old tires, and debris blocking sidewalk access near the freight entrance.",
         updates=[("2026-08-30", "Reported by citizen. 311 Service Request #BX-9042 submitted."),
                  ("2026-08-31", "Community members pledged $90 for private carting support.")]),
    dict(id=iid(6), title="Hazardous Pothole on Mott Haven Commercial Corridor", neighborhood="Mott Haven",
         location="E 138th St & Willis Ave", category="Pothole", status="In Progress",
         reporter="Alex Rivera", date="2026-08-28", upvotes=41, goal=200, current=200, lat=40.8091, lng=-73.9228,
         image="https://images.unsplash.com/photo-1515162816999-a0c47dc192f7?w=500&auto=format&fit=crop&q=80",
         description="Deep pothole damaging car tires right near the bus stop. Needs asphalt fill immediately.",
         updates=[("2026-08-28", "Reported with photo evidence."),
                  ("2026-08-29", "NYC DOT Inspection Team assigned to work order.")]),
    dict(id=iid(7), title="Broken Pedestrian Traffic Light on Fordham Road", neighborhood="Fordham",
         location="Fordham Rd & Grand Concourse", category="Streetlight", status="Reported",
         reporter="Devon K.", date="2026-09-01", upvotes=18, goal=100, current=35, lat=40.8623, lng=-73.8974,
         image="https://images.unsplash.com/photo-1541888946425-d0fbb186a5b3?w=500&auto=format&fit=crop&q=80",
         description="Walk signal hanging by loose wire after high winds. Poses hazard for school kids crossing.",
         updates=[("2026-09-01", "Ticket generated. Marked high priority.")]),
    dict(id=iid(8), title="Damaged Benches at Soundview Park Waterfront", neighborhood="Soundview",
         location="Soundview Park Promenade", category="Parks", status="Resolved",
         reporter="Carmen L.", date="2026-08-20", upvotes=56, goal=120, current=120, lat=40.8145, lng=-73.8680,
         image="https://images.unsplash.com/photo-1519331379826-f10be5486c6f?w=500&auto=format&fit=crop&q=80",
         description="Graffiti and broken wooden slats on two park benches near the soccer field.",
         updates=[("2026-08-20", "Issue reported by local runners."),
                  ("2026-08-22", "Fully funded by Bronx Green Grant."),
                  ("2026-08-25", "Fixed & repainted by Parks Department volunteers!")]),
    dict(id=iid(9), title="Cracked Sidewalk Hazard near Riverdale Metro-North", neighborhood="Riverdale",
         location="Riverdale Ave & W 254th St", category="Sidewalk", status="In Progress",
         reporter="James P.", date="2026-08-25", upvotes=15, goal=250, current=180, lat=40.9031, lng=-73.9063,
         image="https://images.unsplash.com/photo-1584467735871-8e85353a8413?w=500&auto=format&fit=crop&q=80",
         description="Tree root uplifting sidewalk concrete tiles by 4 inches. Tripping hazard for seniors.",
         updates=[("2026-08-25", "Reported to Parks & Transportation."),
                  ("2026-08-27", "Forestry team scheduled root trimming.")]),
]

TOOLS = [
    ("t5010000-0000-0000-0000-000000000001", "Heavy Duty Trash Grabbers & Brooms", "Hunts Point Clean Block Crew", "Hunts Point", "10 Pairs", "fa-broom", 6),
    ("t5020000-0000-0000-0000-000000000002", "Graffiti Removal Paint & Brushes", "Mott Haven Youth Association", "Mott Haven", "5 Cans (Gray/Tan)", "fa-paint-roller", 3),
    ("t5030000-0000-0000-0000-000000000003", "High-Visibility Safety Vests", "Fordham Volunteer Patrol", "Fordham", "15 Vests", "fa-vest", 10),
    ("t5040000-0000-0000-0000-000000000004", "Waterfront Lawn Mower & Trimmer", "Soundview Park Stewards", "Soundview", "1 Unit", "fa-scissors", 0),
]

REWARDS = [
    ("w6010000-0000-0000-0000-000000000001", "$5 Local Bronx Bakery Voucher", 150, "fa-cookie-bite", "amber", "Valid at participating bakeries in Hunts Point & Mott Haven."),
    ("w6020000-0000-0000-0000-000000000002", "NYC Parks Tree Planter Ribbon", 200, "fa-ribbon", "emerald", "Official digital badge + commemorative lapel pin."),
    ("w6030000-0000-0000-0000-000000000003", "Bronx Community Champion Certificate", 300, "fa-certificate", "blue", "Signed certificate of appreciation for block stewardship."),
    ("w6040000-0000-0000-0000-000000000004", "1-Day MTA Bus Pass Discount", 400, "fa-bus", "purple", "Valid on all local Bronx bus routes."),
]

# recentDonors: (id, type, donor, detail, neighborhood, amount, tool, age)
DONORS = [
    ("d3040000-0000-0000-0000-000000000004", "tool_pledge", "Maria S.", "Donated 4 Trash Grabbers", "Hunts Point", None, "4 Trash Grabbers", "2 hours"),
    ("d3050000-0000-0000-0000-000000000005", "monetary", "Devon K.", "Pledged $25 Repair Grant", "Fordham", 25, None, "5 hours"),
    ("d3060000-0000-0000-0000-000000000006", "monetary", "Alex Rivera", "Pledged $10 Repair Grant", "Mott Haven", 10, None, "1 day"),
]


def main():
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute(MIGRATIONS)
        # rewards must exist before the redemption backfill links to them
        for i, r in enumerate(REWARDS):
            conn.execute("""INSERT INTO rewards (id, title, cost, icon, color, description, sort_order)
                            VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING""", (*r, i))
        conn.execute(BACKFILLS)

        for i, t in enumerate(TOOLS):
            conn.execute("""INSERT INTO tools_needed (id, item, requested_by, neighborhood, qty_needed, icon, pledged, sort_order)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING""", (*t, i))

        for it in PAGE_ISSUES:
            conn.execute("""INSERT INTO issues (id, user_id, title, category, neighborhood, description, latitude, longitude,
                                image_url, status, upvotes_count, created_at, location, reporter_name, funding_goal, funding_current)
                            VALUES (%s, NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s, (%s || ' 12:00 America/New_York')::timestamptz,
                                    %s, %s, %s, %s)
                            ON CONFLICT (id) DO NOTHING""",
                         (it["id"], it["title"], it["category"], it["neighborhood"], it["description"], it["lat"], it["lng"],
                          it["image"], it["status"], it["upvotes"], it["date"], it["location"], it["reporter"],
                          it["goal"], it["current"]))
            for k, (d, text) in enumerate(it["updates"], start=1):
                uid = f"n7{it['id'][2:4]}{k:04d}-0000-0000-0000-{k:012d}"
                conn.execute("""INSERT INTO issue_updates (id, issue_id, text, created_at)
                                VALUES (%s, %s, %s, (%s || ' 12:00 America/New_York')::timestamptz)
                                ON CONFLICT (id) DO NOTHING""", (uid, it["id"], text, d))

        # One "reported" update for each issue that was already in the database.
        for n in range(1, 5):
            conn.execute("""INSERT INTO issue_updates (id, issue_id, text, created_at)
                            SELECT %s, i.id, 'Reported by ' || COALESCE(u.display_name, 'a Bronx resident') || '. Ticket created.', i.created_at
                              FROM issues i LEFT JOIN users u ON u.id = i.user_id WHERE i.id = %s
                            ON CONFLICT (id) DO NOTHING""",
                         (f"n70{n}0000-0000-0000-0000-{n:012d}", iid(n)))

        for (did, typ, donor, detail, neigh, amt, tool, age) in DONORS:
            conn.execute("""INSERT INTO donations (id, user_id, issue_id, donation_type, tool_name, amount_usd, created_at,
                                                   donor_name, neighborhood, detail)
                            VALUES (%s, NULL, NULL, %s, %s, %s, now() - %s::interval, %s, %s, %s)
                            ON CONFLICT (id) DO NOTHING""", (did, typ, tool, amt, age, donor, neigh, detail))

        # Demo password for every user that has none yet.
        for (uid,) in conn.execute("SELECT id FROM users WHERE password_hash IS NULL").fetchall():
            h = bcrypt.hashpw(DEMO_PASSWORD.encode(), bcrypt.gensalt()).decode()
            conn.execute("UPDATE users SET password_hash = %s WHERE id = %s", (h, uid))

        counts = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                  for t in ["users", "issues", "issue_updates", "issue_upvotes", "donations",
                            "tools_needed", "rewards", "redemptions"]}
        print(json.dumps(counts))


if __name__ == "__main__":
    main()
