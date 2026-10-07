# Fix My Block - database setup

The page (`index.html`) is served by a small Flask app (`server.py`) that stores everything
in Postgres. The student's UI is unchanged; JS changes are marked with `DB WIRING` comments.

## Run locally

```bash
pip install -r requirements.txt
DATABASE_URL=postgresql://... python3 -m flask --app server run   # http://127.0.0.1:5000
```

Production (Render): start command `gunicorn server:app`, env vars `DATABASE_URL` and `SECRET_KEY`
(if `SECRET_KEY` is unset a random one is used, which logs everyone out on each restart).

Set up / update the database: `DATABASE_URL=... python3 seed.py` (safe to re-run).
`schema.sql` is the full schema for a fresh database.

## Demo login

Every seed user has the password **demo1234**. The login form is prefilled with
`maria.gonzalez@example.com` / `demo1234`. Others: `devon.smith@example.com`,
`alicia.chen@example.com`, `carlos.rivera@example.com`, `admin@fixmyblock.nyc`.

## Tables

| Table | What it holds |
|---|---|
| `users` | accounts: email, display_name, role, `xp_points` (level = xp/500 + 1), `points` (spendable), `password_hash` (bcrypt), neighborhood |
| `issues` | reported problems: title, category, neighborhood, location, description, lat/lng, image_url, status, upvotes_count, funding_goal / funding_current, reporter_name (for seed rows without a user) |
| `issue_updates` | the "Progress Updates" timeline for each issue |
| `issue_upvotes` | one row per user upvote (so the thumbs-up remembers you) |
| `donations` | money pledges and tool pledges; donor_name / neighborhood / detail feed the "Recent Donors" list |
| `tools_needed` | the "Tools & Materials Needed" cards |
| `rewards` | the rewards catalog |
| `redemptions` | rewards a user has redeemed |

Ids are text; new rows get a uuid string.

## API

| Method & path | Auth | Purpose |
|---|---|---|
| `GET /` | | the page |
| `GET /api/me` | | `{user}` or `{user: null}` |
| `POST /api/login` | | `{email, password}` |
| `POST /api/signup` | | `{name, email, password, neighborhood}` |
| `POST /api/logout` | | |
| `GET /api/issues` | | all issues with updates; `userUpvoted` for the logged-in user |
| `GET /api/tools` | | tools needed |
| `GET /api/donors` | | 20 most recent donations |
| `GET /api/rewards` | | rewards catalog |
| `GET /api/redemptions` | login | your redeemed rewards |
| `POST /api/issues` | login | report an issue (+100 XP, +50 points) |
| `POST /api/issues/<id>/upvote` | login | toggle upvote (+10 XP, +5 points when adding) |
| `POST /api/donations` | login | `{type:'monetary', amount, issue_id?, donor_name?}` or `{type:'tool_pledge', tool_name, neighborhood, notes?}` (+50 XP, +30 points) |
| `POST /api/redeem` | login | `{reward_id}`; spends points |

Guests can browse everything; reporting, upvoting, donating and redeeming open the login box.
Only the theme/language setting is still kept in the browser (localStorage).
