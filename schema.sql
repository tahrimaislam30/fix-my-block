-- Fix My Block - full database schema (Postgres).
-- Safe to run on an empty database. For the existing database, seed.py applies
-- the same changes as idempotent ALTERs and backfills.
-- All ids are text (VARCHAR); new rows get a uuid string from the server.

CREATE TABLE IF NOT EXISTS users (
    id            VARCHAR PRIMARY KEY,
    email         VARCHAR NOT NULL UNIQUE,
    display_name  VARCHAR NOT NULL,
    role          VARCHAR NOT NULL DEFAULT 'resident'
                  CHECK (role IN ('resident', 'block_captain', 'admin')),
    xp_points     INTEGER NOT NULL DEFAULT 0,      -- lifetime XP (level = xp/500 + 1)
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- added for the web app:
    password_hash VARCHAR,                          -- bcrypt
    neighborhood  VARCHAR,
    points        INTEGER NOT NULL DEFAULT 0        -- spendable reward points
);

CREATE TABLE IF NOT EXISTS issues (
    id              VARCHAR PRIMARY KEY,
    user_id         VARCHAR REFERENCES users(id) ON DELETE SET NULL,
    title           VARCHAR NOT NULL,
    category        VARCHAR NOT NULL,
    neighborhood    VARCHAR,
    description     TEXT,
    latitude        NUMERIC,
    longitude       NUMERIC,
    image_url       TEXT,
    status          VARCHAR NOT NULL DEFAULT 'Reported'
                    CHECK (status IN ('Reported', 'In Progress', 'Resolved')),
    upvotes_count   INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- added for the web app:
    location        VARCHAR,                        -- cross streets
    reporter_name   VARCHAR,                        -- display name when user_id is NULL (page seed data)
    funding_goal    NUMERIC NOT NULL DEFAULT 150,
    funding_current NUMERIC NOT NULL DEFAULT 0
);

-- Progress updates timeline shown on each issue.
CREATE TABLE IF NOT EXISTS issue_updates (
    id         VARCHAR PRIMARY KEY,
    issue_id   VARCHAR NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    text       TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per (issue, user) upvote; issues.upvotes_count is the displayed total.
CREATE TABLE IF NOT EXISTS issue_upvotes (
    issue_id   VARCHAR NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    user_id    VARCHAR NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (issue_id, user_id)
);

CREATE TABLE IF NOT EXISTS donations (
    id               VARCHAR PRIMARY KEY,
    user_id          VARCHAR REFERENCES users(id) ON DELETE SET NULL,
    issue_id         VARCHAR REFERENCES issues(id) ON DELETE SET NULL,
    donation_type    VARCHAR NOT NULL CHECK (donation_type IN ('tool_pledge', 'monetary')),
    tool_name        VARCHAR,
    dropoff_location VARCHAR,
    amount_usd       NUMERIC,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- added for the web app ("Recent Donors" list):
    donor_name       VARCHAR,
    neighborhood     VARCHAR,
    detail           VARCHAR                        -- e.g. 'Pledged $10 Grant', 'Pledged: 2 brooms'
);

-- "Tools & Materials Needed" cards on the Donate tab.
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

-- Rewards catalog on the Rewards & XP tab.
CREATE TABLE IF NOT EXISTS rewards (
    id          VARCHAR PRIMARY KEY,
    title       VARCHAR NOT NULL,
    cost        INTEGER NOT NULL,
    icon        VARCHAR,
    color       VARCHAR,
    description TEXT,
    sort_order  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS redemptions (
    id           VARCHAR PRIMARY KEY,
    user_id      VARCHAR REFERENCES users(id) ON DELETE CASCADE,
    reward_title VARCHAR NOT NULL,
    xp_cost      INTEGER NOT NULL,                  -- points spent
    status       VARCHAR NOT NULL DEFAULT 'claimed',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- added for the web app:
    reward_id    VARCHAR REFERENCES rewards(id) ON DELETE SET NULL
);
