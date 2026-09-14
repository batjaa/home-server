#!/usr/bin/env python3
"""Prepare a separate PostgreSQL 18 cluster and persistent Redis snapshot.

The Ansible role performs the subsequent container replacement. On preparation
failure the existing app is restarted; neither original data volume is removed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def run(args, **kwargs):
    result = subprocess.run(args, capture_output=True, **kwargs)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace"))
    return result.stdout


def sql(container, query, database="immich"):
    return run(["docker", "exec", container, "psql", "-X", "-U", "postgres",
                "-d", database, "-At", "-v", "ON_ERROR_STOP=1", "-c", query]).decode().strip()


def inventory(container):
    tables = sql(container, "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").splitlines()
    counts = {table: int(sql(container, 'SELECT count(*) FROM public."' + table.replace('"', '""') + '"')) for table in tables}
    asset_ids = sql(container, "SELECT md5(string_agg(id::text, ',' ORDER BY id)) FROM asset")
    return {"tables": counts, "asset_ids_md5": asset_ids}


def restore(image, destination, backup, env, name):
    existing = subprocess.run(["docker", "inspect", name], capture_output=True)
    if existing.returncode == 0:
        raise RuntimeError("Restore container already exists; inspect it before retrying")
    destination.mkdir(mode=0o700)
    # PostgreSQL drops privileges before creating its versioned PGDATA directory.
    destination.chmod(0o755)
    process_env = os.environ.copy()
    process_env["POSTGRES_PASSWORD"] = env["POSTGRES_PASSWORD"]
    try:
        run(["docker", "run", "-d", "--name", name, "--network", "none",
             "--shm-size", "1g", "-e", "POSTGRES_PASSWORD", "-e", "POSTGRES_DB=postgres",
             "-e", "POSTGRES_INITDB_ARGS=--data-checksums",
             "-v", str(destination) + ":/var/lib/postgresql", image,
             "postgres", "-c", "shared_preload_libraries=vchord.so",
             "-c", "maintenance_work_mem=512MB"], env=process_env)
        for _ in range(90):
            ready = subprocess.run(["docker", "exec", name, "pg_isready", "-h", "127.0.0.1", "-U", "postgres"], capture_output=True)
            if ready.returncode == 0:
                break
            if run(["docker", "inspect", "--format", "{{.State.Running}}", name]).strip() != b"true":
                raise RuntimeError("Target database exited: " + run(["docker", "logs", name]).decode(errors="replace"))
            time.sleep(2)
        else:
            raise RuntimeError("Target database did not become ready")
        with backup.open("rb") as stream:
            run(["docker", "exec", "-i", name, "pg_restore", "-U", "postgres",
                 "-d", "postgres", "--create", "--exit-on-error"], stdin=stream)
        run(["docker", "exec", name, "vacuumdb", "-U", "postgres", "-d", "immich", "--analyze-only"])
        invalid = sql(name, "SELECT count(*) FROM pg_index WHERE NOT indisvalid")
        assert invalid == "0", "Restored database has invalid indexes"
        return inventory(name), sql(name, "SELECT extname || ':' || extversion FROM pg_extension ORDER BY extname")
    finally:
        subprocess.run(["docker", "stop", "-t", "60", name], capture_output=True)
        subprocess.run(["docker", "rm", name], capture_output=True)


def main():
    if sys.argv[1:] == ["version"]:
        print("1.0.0")
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version="1.0.0")
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--target-dir", type=Path, required=True)
    parser.add_argument("--redis-dir", type=Path, required=True)
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    assert not args.target_dir.exists(), "Target directory exists; inspect it before retrying"
    assert not args.redis_dir.exists(), "Redis target exists; inspect it before retrying"
    assert sql("immich-db", "SHOW server_version_num").startswith("14"), "Only the audited PG14 source is supported"
    assert sql("immich-db", "SELECT string_agg(rolname, ',' ORDER BY rolname) FROM pg_roles WHERE rolname !~ '^pg_'") == "postgres", "Additional database roles need explicit migration"
    args.backup_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
    os.umask(0o077)
    definitions = json.loads(run(["docker", "inspect", "immich-db", "immich-server", "immich-redis"]))
    (args.backup_dir / "containers-before.json").write_text(json.dumps(definitions, indent=2))
    env = dict(value.split("=", 1) for value in definitions[0]["Config"]["Env"] if "=" in value)
    assert env["POSTGRES_USER"] == "postgres" and env["POSTGRES_DB"] == "immich"
    app_stopped = False
    redis_stopped = False
    try:
        run(["docker", "stop", "-t", "120", "immich-server"])
        app_stopped = True
        before = inventory("immich-db")
        (args.backup_dir / "inventory-before.json").write_text(json.dumps(before, indent=2))
        with (args.backup_dir / "database.dump").open("wb") as output:
            result = subprocess.run(["docker", "exec", "immich-db", "pg_dump", "-U", "postgres", "-d", "immich", "-Fc", "--create"], stdout=output, stderr=subprocess.PIPE)
        if result.returncode:
            raise RuntimeError(result.stderr.decode())
        assert inventory("immich-db") == before, "Database changed while the app was stopped"
        with (args.backup_dir / "globals.sql").open("wb") as output:
            output.write(run(["docker", "exec", "immich-db", "pg_dumpall", "-U", "postgres", "--globals-only"]))
        digest = hashlib.sha256()
        with (args.backup_dir / "database.dump").open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        (args.backup_dir / "database.sha256").write_text(digest.hexdigest() + "  database.dump\n")
        print("Consistent database dump saved; restoring into a separate PostgreSQL 18 directory", flush=True)
        after, extensions = restore(args.image, args.target_dir, args.backup_dir / "database.dump", env, "immich-db-migration-check")
        assert after == before, "Restored table counts or asset IDs differ"
        (args.backup_dir / "restore-verification.json").write_text(json.dumps({"inventory": after, "extensions": extensions, "invalid_indexes": 0}, indent=2))
        print("All table counts and asset IDs match; vector indexes restored successfully", flush=True)
        redis_size = int(run(["docker", "exec", "immich-redis", "redis-cli", "DBSIZE"]))
        run(["docker", "exec", "immich-redis", "redis-cli", "SAVE"])
        run(["docker", "stop", "-t", "60", "immich-redis"])
        redis_stopped = True
        source = Path(next(m["Source"] for m in definitions[2]["Mounts"] if m["Destination"] == "/data"))
        shutil.copytree(source, args.backup_dir / "redis-data", copy_function=shutil.copy2)
        shutil.copytree(source, args.redis_dir, copy_function=shutil.copy2)
        run(["chown", "-R", "999:1000", str(args.redis_dir)])
        (args.backup_dir / "redis-before.json").write_text(json.dumps({"keys": redis_size, "original_volume": str(source)}))
        (args.backup_dir / "prepared").write_text("Database and Redis are prepared; apply the Immich role to start production.\n")
        (args.target_dir / ".immich-migration-verified").write_text(str(args.backup_dir) + "\n")
    except BaseException as error:
        (args.backup_dir / "error.log").write_text(str(error))
        if redis_stopped:
            subprocess.run(["docker", "start", "immich-redis"], capture_output=True)
        if app_stopped:
            subprocess.run(["docker", "start", "immich-server"], capture_output=True)
        raise RuntimeError("Preparation failed; existing services restarted. Inspect the private backup error.log") from None
    print("Migration prepared; original PG14 directory and Redis volume retained", flush=True)


if __name__ == "__main__":
    main()
