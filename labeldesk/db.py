import os

import psycopg
from psycopg.rows import dict_row


def connect():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def init():
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(320029)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id uuid PRIMARY KEY, text text NOT NULL UNIQUE,
                split text NOT NULL CHECK(split IN ('pool','test')),
                gold integer CHECK(gold IN (0,1)), resolved integer CHECK(resolved IN (0,1)),
                revision integer NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS assignments (
                task_id uuid REFERENCES tasks(id), actor text NOT NULL, token uuid NOT NULL,
                expires_at timestamptz NOT NULL, PRIMARY KEY(task_id,actor));
            CREATE TABLE IF NOT EXISTS annotations (
                id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                task_id uuid REFERENCES tasks(id), actor text NOT NULL, label integer NOT NULL CHECK(label IN (0,1)),
                created_at timestamptz NOT NULL DEFAULT now());
            CREATE TABLE IF NOT EXISTS decisions (
                id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                task_id uuid REFERENCES tasks(id), label integer NOT NULL,
                revision integer NOT NULL, created_at timestamptz NOT NULL DEFAULT now());
            CREATE TABLE IF NOT EXISTS runs (
                id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                report jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now());
        """)
