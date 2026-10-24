"""One-time setup of a hosted PostgreSQL (Supabase) for GoPulse. Reads secrets from the git-ignored .env.deploy; NEVER prints them.

    python scripts/setup_remote_db.py roles      # create/refresh the two least-privilege API roles (idempotent)
    python scripts/setup_remote_db.py verify     # check what each role can and cannot do, and that Supabase's public roles see nothing
    python scripts/setup_remote_db.py urls       # write the runtime connection URLs (transaction pooler) into .env.deploy

Needs in .env.deploy:  SUPABASE_DIRECT_URL (owner),  SUPABASE_POOLER_HOST,  SUPABASE_PROJECT_REF
Writes to .env.deploy: GOPULSE_API_RO_PASSWORD, GOPULSE_API_RW_PASSWORD, RATE_LIMIT_SALT, DATABASE_URL_RO, DATABASE_URL_RW

Why two roles: the dashboard role is read-only (it cannot even write by mistake); the demo-run role can only INSERT run history and
manage the rate-limit table. The owner password is only used here, by the publish job, and by nothing that runs on the web.
"""
from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

import psycopg
from psycopg import sql

ENV = Path(".env.deploy")
ROLES = {"gopulse_api_ro": "GOPULSE_API_RO_PASSWORD", "gopulse_api_rw": "GOPULSE_API_RW_PASSWORD"}


def load() -> dict[str, str]:
    if not ENV.exists():
        sys.exit(f"{ENV} not found")
    out = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def save(updates: dict[str, str]) -> None:
    cur = load()
    cur.update(updates)
    header = "# git-ignored deployment secrets - never commit, never paste into chat\n"
    ENV.write_text(header + "".join(f"{k}={v}\n" for k, v in cur.items()), encoding="utf-8", newline="\n")


def owner(env: dict[str, str]):
    if "SUPABASE_DIRECT_URL" not in env:
        sys.exit("SUPABASE_DIRECT_URL missing in .env.deploy")
    return psycopg.connect(env["SUPABASE_DIRECT_URL"], autocommit=True, connect_timeout=15)


def cmd_roles() -> None:
    env = load()
    new = {}
    for role, key in ROLES.items():
        if key not in env:
            new[key] = secrets.token_urlsafe(24)  # url-safe: no characters that need encoding in a connection URL
    if "RATE_LIMIT_SALT" not in env:
        new["RATE_LIMIT_SALT"] = secrets.token_hex(24)
    if new:
        save(new)
        env = load()
    with owner(env) as c:
        c.execute("CREATE SCHEMA IF NOT EXISTS serving")
        for role, key in ROLES.items():
            exists = c.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [role]).fetchone()
            verb = "ALTER" if exists else "CREATE"
            c.execute(sql.SQL(f"{verb} ROLE {{}} LOGIN PASSWORD {{}} NOSUPERUSER NOCREATEDB NOCREATEROLE").format(
                sql.Identifier(role), sql.Literal(env[key])))
            c.execute(sql.SQL("ALTER ROLE {} SET statement_timeout = '8s'").format(sql.Identifier(role)))
            c.execute(sql.SQL("GRANT USAGE ON SCHEMA serving TO {}").format(sql.Identifier(role)))
            print(f"  {'refreshed' if exists else 'created  '} role {role} (password {'rotated' if exists else 'generated'}, statement_timeout 8s)")
    print("roles ready. Next: publish the data (it applies the table grants), then `verify`.")


def cmd_urls() -> None:
    env = load()
    for k in ("SUPABASE_POOLER_HOST", "SUPABASE_PROJECT_REF", "GOPULSE_API_RO_PASSWORD", "GOPULSE_API_RW_PASSWORD"):
        if k not in env:
            sys.exit(f"{k} missing - run `roles` first")
    host, ref = env["SUPABASE_POOLER_HOST"], env["SUPABASE_PROJECT_REF"]
    # Transaction pooler (port 6543): what a serverless runtime must use. The tenant is part of the user name.
    save({
        "DATABASE_URL_RO": f"postgresql://gopulse_api_ro.{ref}:{env['GOPULSE_API_RO_PASSWORD']}@{host}:6543/postgres",
        "DATABASE_URL_RW": f"postgresql://gopulse_api_rw.{ref}:{env['GOPULSE_API_RW_PASSWORD']}@{host}:6543/postgres",
    })
    print("wrote DATABASE_URL_RO / DATABASE_URL_RW (transaction pooler, port 6543) to .env.deploy")


def expect_denied(conn, stmt: str) -> bool:
    try:
        conn.execute(stmt)
        conn.rollback()
        return False
    except psycopg.errors.InsufficientPrivilege:
        conn.rollback()
        return True


def cmd_verify() -> None:
    env = load()
    bad = 0

    def check(name: str, ok: bool) -> None:
        nonlocal bad
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        bad += not ok

    # 1. Supabase's built-in roles (used by its public REST/GraphQL API) must see nothing in our schema.
    with owner(env) as c:
        leaks = c.execute(
            """SELECT grantee, table_name, privilege_type FROM information_schema.role_table_grants
               WHERE table_schema = 'serving' AND grantee IN ('anon', 'authenticated', 'service_role', 'PUBLIC')""").fetchall()
        check("anon / authenticated / PUBLIC have no privileges on any serving table", not leaks)
        if leaks:
            print("        leaked grants:", leaks[:5])
        n_tables = c.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'serving'").fetchone()[0]
        check(f"serving schema is populated ({n_tables} tables)", n_tables >= 20)
        rls_off = c.execute("SELECT count(*) FROM pg_tables WHERE schemaname = 'serving' AND NOT rowsecurity").fetchone()[0]
        print(f"        note: {rls_off} serving tables have row-level security off; they are not exposed through Supabase's Data API "
              f"(only the `public` schema is by default)")
    # 2. What each API role can do, through the real (pooled) runtime URLs.
    for role, url_key in (("gopulse_api_ro", "DATABASE_URL_RO"), ("gopulse_api_rw", "DATABASE_URL_RW")):
        with psycopg.connect(env[url_key], connect_timeout=15, prepare_threshold=None) as c:
            who = c.execute("SELECT current_user").fetchone()[0]
            check(f"{role}: connects through the transaction pooler as {who.split('.')[0]}", who.startswith(role))
            check(f"{role}: can read the data", c.execute("SELECT count(*) FROM serving.users").fetchone()[0] > 0)
            check(f"{role}: cannot create tables", expect_denied(c, "CREATE TABLE serving.should_fail (a int)"))
            check(f"{role}: cannot update dimension data", expect_denied(c, "UPDATE serving.users SET account_tier = 'x'"))
            check(f"{role}: cannot read other schemas' secrets (auth.users)", expect_denied(c, "SELECT * FROM auth.users LIMIT 1"))
            if role == "gopulse_api_ro":
                check("gopulse_api_ro: cannot insert run history", expect_denied(c, "INSERT INTO serving.demo_hits (client_hash) VALUES ('x')"))
            else:
                check("gopulse_api_rw: cannot delete run history", expect_denied(c, "DELETE FROM serving.pipeline_runs"))
                check("gopulse_api_rw: cannot touch the metrics tables", expect_denied(c, "DELETE FROM serving.mart_kpi_daily"))
    print("\nALL CHECKS PASSED" if not bad else f"\n{bad} CHECK(S) FAILED")
    sys.exit(1 if bad else 0)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["roles", "urls", "verify"])
    {"roles": cmd_roles, "urls": cmd_urls, "verify": cmd_verify}[p.parse_args().command]()


if __name__ == "__main__":
    main()
