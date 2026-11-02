"""SQLite snapshots, offline restoration, and portable human-readable exports."""

from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import tempfile
import uuid
import zipfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .migrations import SCHEMA_VERSION, UPGRADABLE_VERSIONS

REQUIRED_TABLES = frozenset(
    {"work_items", "days", "daily_entries", "notes", "tasks", "attachments",
     "credentials", "roadmaps", "steps", "proposals", "settings", "events",
     "receipts", "file_references", "search_index", "roadmap_versions", "plan_imports"}
)

# Sealed, normalized sqlite_master signatures for the nine supported v1 shapes.
# They must not change when current feature schemas evolve. Hashes cover all
# table definitions, constraints, indexes and FTS shadows, not database contents.
V1_SCHEMA_DIGESTS = frozenset({
    # Current main before coordination: discussions plus roadmap questions/decisions.
    "c5ae7406cdd63ddfa0cd7f3a18f2cc74f898936ef42c3b72909424c2d2e5a272",
    "23aab55b497a3d6534338da7887cb6a014169ecce1d2e0b91c229f42da8f9d2c",
    "cd9f0804a6ceea36a375f52f9a00ad9ee9a593110a954d899e6ead5b8536370e",
    "373ee0760a75eb3784e49fe21d65601c3b134bd8003445f07a962b8567a07f67",
    "2ffa3142f4cabeb13fcf638c4def1b873dae12ee4b2ed4f4961e69241eba8bf6",
    "f8dbb906b152ee449531b6cac6897802d3fd74fd860385cb992672760bde27bb",
    "693ab61438716f2e5e834b78aa6ee418260b1d13be8558af771541b181c5d841",
    "c73ee4b832defa66818053c6e16406268e70615ea7d5587d7857cb47bbdd3cb6",
    "2f407e161a537b37236a95d1275b94566e9e4bdd35329462c5a7563950f15f11",
})


def _schema_signature(conn: sqlite3.Connection) -> dict[tuple[str, str], str]:
    # Compare definitions, constraints, indexes and FTS shadows; no candidate triggers
    # or substituted virtual-table modules are accepted by offline restore.
    return {(row[0], row[1]): " ".join((row[2] or "").split()) for row in conn.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")}


def _schema_digest(signature: dict[tuple[str, str], str]) -> str:
    encoded = json.dumps(sorted((kind, name, sql) for (kind, name), sql in signature.items()), separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _expected_schema(*, discussions: bool = True, coordination: bool = True,
                     avatars: bool = True, chat_messages: bool = True,
                     tickets: bool = True, remote_identity: bool = True) -> dict[tuple[str, str], str]:
    from worklog.db import SCHEMA
    from worklog.plans import SCHEMA as PLAN_SCHEMA
    from worklog.proposals import SCHEMA as PROPOSAL_SCHEMA

    with closing(sqlite3.connect(":memory:")) as conn:
        conn.executescript(PLAN_SCHEMA + PROPOSAL_SCHEMA + SCHEMA)
        if discussions:
            from worklog.discussions import AGENT_AVATAR_COLUMN
            from worklog.discussions import SCHEMA as DISCUSSION_SCHEMA

            conn.executescript(DISCUSSION_SCHEMA if avatars else DISCUSSION_SCHEMA.replace(AGENT_AVATAR_COLUMN, ""))
        if coordination:
            from worklog.migrations import COORDINATION_SCHEMA, add_remote_identity

            conn.executescript(COORDINATION_SCHEMA)
            if remote_identity:
                add_remote_identity(conn)
        if chat_messages:
            from worklog.chat_messages import SCHEMA as CHAT_MESSAGE_SCHEMA

            conn.executescript(CHAT_MESSAGE_SCHEMA)
        if tickets:
            from worklog.tickets import SCHEMA as TICKET_SCHEMA

            conn.executescript(TICKET_SCHEMA)
        return _schema_signature(conn)


def _compatible_schemas() -> tuple[dict[tuple[str, str], str], ...]:
    from worklog.db import AGENT_CHAT_COLUMNS

    latest = _expected_schema()
    # Every v1 shape predates agent avatars (v3), messages to chats (v4) and tickets (v5).
    discussion_v1 = _expected_schema(coordination=False, avatars=False, chat_messages=False, tickets=False)
    question_names = {"roadmap_questions", "roadmap_questions_roadmap", "roadmap_decisions", "roadmap_decisions_roadmap"}
    # Questions and decisions shipped on main after discussions, still at v1.
    before_questions = {k: v for k, v in discussion_v1.items() if k[1] not in question_names}
    before_discussions = {k: v for k, v in _expected_schema(discussions=False, coordination=False, chat_messages=False, tickets=False).items() if k[1] not in question_names}
    # `current` is the last shape before agent chats gained provider, model, repos and step
    # assignments; every earlier shape below derives from it.
    current = {k: v for k, v in before_discussions.items() if k[1] not in {"chat_steps", "chat_steps_step"}}
    current[("table", "agent_chats")] = before_discussions[("table", "agent_chats")].replace(AGENT_CHAT_COLUMNS, "")
    lcg_names = {
        "web_links",
        "web_links_owner",
        "agent_chats",
        "agent_chats_owner",
        "github_refs",
        "github_refs_owner",
    }
    people_names = {
        "people",
        "person_links",
        "person_links_target",
        "person_links_person",
    }
    without_lcg = {k: v for k, v in current.items() if k[1] not in lcg_names}
    without_people = {
        k: v for k, v in without_lcg.items() if k[1] not in people_names
    }
    legacy_roadmap = dict(without_people)
    legacy_roadmap[("table", "work_items")] = without_people[("table", "work_items")].replace(
        ", roadmap_id TEXT REFERENCES roadmaps(id)", ""
    )
    current_without_roadmap = dict(current)
    current_without_roadmap[("table", "work_items")] = current[("table", "work_items")].replace(
        ", roadmap_id TEXT REFERENCES roadmaps(id)", ""
    )
    # Every earlier shape predates roadmap nesting, so none has roadmaps.parent_id.
    earlier = [
        {**schema, ("table", "roadmaps"): schema[("table", "roadmaps")].replace(
            ", parent_id TEXT REFERENCES roadmaps(id)", "")}
        for schema in (current, without_lcg, without_people, legacy_roadmap, current_without_roadmap)
    ]
    return (latest, discussion_v1, before_questions, before_discussions, current, *earlier)


class InstanceLock:
    """An OS lock held for the lifetime of a managed server or offline operation.

    Never delete the lock file: unlinking it would allow a second lock inode on Unix.
    The operating system releases ownership after a crash.
    """

    def __init__(self, data_dir: Path | str, name: str = ".server.lock") -> None:
        self.path = Path(data_dir) / name
        self._file: Any = None

    def __enter__(self) -> InstanceLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a+b")
        self._file.seek(0, os.SEEK_END)
        if self._file.tell() == 0:
            self._file.write(b"0")
            self._file.flush()
        self._file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
        except OSError as exc:
            self._file.close()
            self._file = None
            raise RuntimeError("Worklog is running or another offline operation owns this data directory") from exc
        return self

    def __exit__(self, *_: Any) -> None:
        if self._file is not None:
            self._file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]
            self._file.close()
            self._file = None


def _readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)


def validate_database(path: Path | str, *, require_current: bool = True) -> None:
    """Read-only validation; never initialize or modify an untrusted candidate."""
    path = Path(path)
    if not path.is_file():
        raise ValueError("Database file does not exist")
    try:
        with closing(_readonly(path)) as conn:
            if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError("Database integrity check failed")
            if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError("Database foreign key check failed")
            if require_current:
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                if version not in UPGRADABLE_VERSIONS | {SCHEMA_VERSION}:
                    raise ValueError(f"Unsupported database schema version {version}; expected 1 to {SCHEMA_VERSION}")
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                missing = REQUIRED_TABLES - tables
                if missing:
                    raise ValueError("Database missing expected tables: " + ", ".join(sorted(missing)))
                signature = _schema_signature(conn)
                accepted = (signature == _expected_schema() if version == SCHEMA_VERSION
                            else signature == _expected_schema(remote_identity=False) if version == 5
                            else signature == _expected_schema(tickets=False, remote_identity=False) if version == 4
                            else signature == _expected_schema(chat_messages=False, tickets=False, remote_identity=False) if version == 3
                            else signature == _expected_schema(avatars=False, chat_messages=False, tickets=False, remote_identity=False) if version == 2
                            else _schema_digest(signature) in V1_SCHEMA_DIGESTS)
                if not accepted:
                    raise ValueError("Database schema definitions do not match this Worklog version")
                settings = {row[0]: row[1] for row in conn.execute(
                    "SELECT key,value FROM settings WHERE key IN ('dataId','timezone','backlogRevision')")}
                try:
                    data_id = json.loads(settings["dataId"])
                    timezone = json.loads(settings["timezone"])
                    if not isinstance(data_id, str) or not isinstance(timezone, str):
                        raise ValueError()
                    uuid.UUID(data_id)
                    ZoneInfo(timezone)
                    revision = json.loads(settings["backlogRevision"])
                    if type(revision) is not int or revision < 1:
                        raise ValueError()
                except (KeyError, ValueError, TypeError) as exc:
                    raise ValueError("Database is missing valid required application settings") from exc
    except sqlite3.Error as exc:
        raise ValueError(f"Invalid SQLite database: {exc}") from exc


def _snapshot(source: Path, destination: Path) -> None:
    # SQLite's backup API reads a consistent snapshot even when the source uses WAL.
    with closing(_readonly(source)) as src, closing(sqlite3.connect(destination)) as dest:
        src.backup(dest)
    validate_database(destination, require_current=False)
    with destination.open("r+b") as stream:
        os.fsync(stream.fileno())


def _metadata(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {"name": path.name, "size": stat.st_size,
            "createdAt": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
            "sha256": _file_hash(path)}


def _file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def list_backups(backup_dir: Path | str) -> list[dict[str, Any]]:
    directory = Path(backup_dir)
    if not directory.exists():
        return []
    files = [*directory.glob("worklog-*.sqlite3"), *directory.glob("worklog-pre-restore-raw-*.zip")]
    return [_metadata(path) for path in sorted(files, reverse=True)
            if path.is_file()]


def backup_database(db_path: Path | str, backup_dir: Path | str,
                    kind: str = "manual") -> dict[str, Any]:
    if kind not in {"manual", "daily", "pre-migration", "pre-restore"}:
        raise ValueError("Unknown backup kind")
    source, directory = Path(db_path), Path(backup_dir)
    if not source.is_file():
        raise ValueError("Database file does not exist")
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = directory / f"worklog-{kind}-{stamp}-{uuid.uuid4().hex[:8]}.sqlite3"
    descriptor, temporary = tempfile.mkstemp(prefix=".snapshot-", suffix=".sqlite3", dir=directory)
    os.close(descriptor)
    stage = Path(temporary)
    try:
        _snapshot(source, stage)
        os.replace(stage, target)
    finally:
        stage.unlink(missing_ok=True)
    keep = {"daily": 14, "pre-migration": 3}.get(kind)
    if keep is not None:
        for old in sorted(directory.glob(f"worklog-{kind}-*.sqlite3"), reverse=True)[keep:]:
            old.unlink()
    return _metadata(target)


def automatic_backup(db_path: Path | str, backup_dir: Path | str) -> dict[str, Any] | None:
    """Make one snapshot per configured calendar day; manual snapshots are unpruned."""
    lock = InstanceLock(backup_dir, ".daily.lock")
    try:
        lock.__enter__()
    except RuntimeError:
        # A concurrent request/watcher is already handling this calendar check.
        return None
    try:
        return _daily_backup(db_path, backup_dir)
    finally:
        lock.__exit__()


def _daily_backup(db_path: Path | str, backup_dir: Path | str) -> dict[str, Any] | None:
    directory = Path(backup_dir)
    with closing(_readonly(Path(db_path))) as conn:
        settings = dict(conn.execute("SELECT key,value FROM settings WHERE key IN ('timezone','dataId')"))
    timezone = json.loads(settings.get("timezone", '"America/Chicago"'))
    data_id = json.loads(settings["dataId"])
    day = datetime.now(ZoneInfo(timezone)).date().isoformat()
    marker = directory / "daily-backup-state.json"
    try:
        prior = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prior = {}
    if not isinstance(prior, dict):
        prior = {}
    if (prior.get("date") == day and prior.get("timezone") == timezone
            and prior.get("dataId") == data_id
            and Path(str(prior.get("name", ""))).name == prior.get("name")
            and (directory / prior["name"]).is_file()):
        return None
    result = backup_database(db_path, directory, "daily")
    temporary = directory / (".daily-" + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps({"date": day, "timezone": timezone, "dataId": data_id,
                                         "name": result["name"]}), encoding="utf-8")
        os.replace(temporary, marker)
    finally:
        temporary.unlink(missing_ok=True)
    return result


def _offline_evidence(target: Path, backup_dir: Path) -> dict[str, Any] | None:
    """Preserve bytes of a stopped, possibly unreadable database and its sidecars.

    This is forensic evidence, not a claim of a valid SQLite backup. Call only while
    holding the server's offline lock; normal live backups always use SQLite API.
    """
    paths = [path for suffix in ("", "-wal", "-shm", "-journal")
             if (path := target.with_name(target.name + suffix)).exists()]
    marker = target.parent / ".restore-in-progress.json"
    if marker.exists():
        paths.append(marker)
    paths.extend(path for path in target.parent.glob(".restore-old-*") if path.is_file())
    if not paths:
        return None
    signatures = {path: (path.stat().st_size, path.stat().st_mtime_ns) for path in paths}
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = backup_dir / f"worklog-pre-restore-raw-{stamp}-{uuid.uuid4().hex[:8]}.zip"
    descriptor, temporary = tempfile.mkstemp(prefix=".raw-", suffix=".zip", dir=backup_dir)
    os.close(descriptor)
    stage = Path(temporary)
    try:
        manifest: list[dict[str, Any]] = []
        with zipfile.ZipFile(stage, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in paths:
                digest = hashlib.sha256()
                size = 0
                with path.open("rb") as source, archive.open(path.name, "w", force_zip64=True) as output:
                    while chunk := source.read(1024 * 1024):
                        digest.update(chunk)
                        size += len(chunk)
                        output.write(chunk)
                manifest.append({"path": path.name, "size": size, "sha256": digest.hexdigest()})
            archive.writestr("manifest.json", json.dumps(
                {"format": "worklog-offline-evidence", "version": 1, "files": manifest}, indent=2))
        if any((path.stat().st_size, path.stat().st_mtime_ns) != signatures[path] for path in paths):
            raise RuntimeError("Database files changed during offline evidence capture; close all connections")
        with stage.open("r+b") as stream:
            os.fsync(stream.fileno())
        os.replace(stage, destination)
        return {**_metadata(destination), "files": manifest}
    finally:
        stage.unlink(missing_ok=True)


def _corrupt_error(exc: sqlite3.Error) -> bool:
    code = getattr(exc, "sqlite_errorcode", 0) & 0xFF
    if code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED} or any(
            text in str(exc).lower() for text in ("locked", "busy")):
        raise RuntimeError("Database is busy; close all Worklog connections before restoring") from exc
    return code in {sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB} or getattr(
        exc, "sqlite_errorcode", 0) == sqlite3.SQLITE_READONLY_ROLLBACK


def _assert_database_offline(target: Path) -> None:
    """Probe SQLite's documented main-file lock region without reading its schema.

    The 512-byte region beginning at PENDING_BYTE covers pending, reserved and
    shared locks. An exclusive OS lock conflicts with even an idle WAL reader.
    Release before Windows rename; the managed instance lock remains held.
    See https://github.com/sqlite/sqlite/blob/master/src/os.h (PENDING_BYTE).
    """
    if not target.exists():
        return
    with target.open("r+b") as stream:
        stream.seek(0x40000000)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 512)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 512)
            else:
                import fcntl

                fcntl.lockf(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB, 512, 0x40000000)  # type: ignore[attr-defined]
                fcntl.lockf(stream.fileno(), fcntl.LOCK_UN, 512, 0x40000000)  # type: ignore[attr-defined]
        except OSError as exc:
            raise RuntimeError("Database is busy; close all Worklog connections before restoring") from exc


def _write_restore_marker(path: Path, data: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".restore-marker-", dir=path.parent)
    stage = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(stage, path)
    finally:
        stage.unlink(missing_ok=True)


def _original_preserved(target: Path, evidence: dict[str, Any] | None) -> bool:
    files = {entry["path"]: entry for entry in evidence["files"]} if evidence else {}
    for suffix in ("", "-wal", "-shm", "-journal"):
        path = target.with_name(target.name + suffix)
        expected = files.get(path.name)
        if expected:
            if not path.is_file() or _file_hash(path) != expected["sha256"]:
                return False
        elif path.exists():
            return False
    return True


def restore_database(db_path: Path | str, candidate_path: Path | str,
                     backup_dir: Path | str) -> dict[str, Any]:
    target, candidate = Path(db_path).resolve(), Path(candidate_path).resolve()
    if target == candidate:
        raise ValueError("Restore candidate must differ from the active database")
    with InstanceLock(target.parent):
        validate_database(candidate)
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".restore-", suffix=".sqlite3", dir=target.parent)
        os.close(descriptor)
        stage = Path(temporary)
        before = None
        raw_before = None
        quarantined: list[tuple[Path, Path]] = []
        replaced = False
        marker = target.parent / ".restore-in-progress.json"
        previous_marker = marker.read_bytes() if marker.exists() else None
        marker_owned = False
        new_data_id = str(uuid.uuid4())
        try:
            _snapshot(candidate, stage)
            with closing(sqlite3.connect(stage)) as conn:
                from .migrations import invalidate_restored_presence, upgrade

                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("BEGIN IMMEDIATE")
                upgrade(conn)
                invalidate_restored_presence(conn, datetime.now(UTC).isoformat())
                conn.execute("UPDATE credentials SET revoked=1")
                conn.execute("UPDATE settings SET value=? WHERE key='dataId'", (json.dumps(new_data_id),))
                conn.commit()
            validate_database(stage)
            _assert_database_offline(target)
            raw_before = _offline_evidence(target, Path(backup_dir))
            if target.exists():
                try:
                    with target.open("rb") as original:
                        sqlite_header = original.read(16) == b"SQLite format 3\x00"
                    if sqlite_header:
                        before = backup_database(target, backup_dir, "pre-restore")
                    else:
                        before = raw_before
                except sqlite3.Error as exc:
                    if not _corrupt_error(exc):
                        raise
                    before = raw_before
                except ValueError:
                    # A failed integrity/FK check requires raw forensic preservation.
                    before = raw_before
            with stage.open("r+b") as stream:
                os.fsync(stream.fileno())
            planned = []
            for suffix in ("-wal", "-shm", "-journal"):
                sidecar = target.with_name(target.name + suffix)
                if sidecar.exists():
                    quarantine = target.with_name(".restore-old-" + uuid.uuid4().hex + suffix)
                    planned.append((sidecar, quarantine))
            transaction = {"version": 1, "phase": "prepared", "dataId": new_data_id,
                           "target": target.name, "replacementSha256": _file_hash(stage),
                           "offlineEvidence": raw_before["name"] if raw_before else None,
                           "sidecars": [{"original": original.name, "quarantine": saved.name}
                                        for original, saved in planned]}
            _write_restore_marker(marker, json.dumps(transaction, indent=2).encode("utf-8"))
            marker_owned = True
            for sidecar, quarantine in planned:
                os.replace(sidecar, quarantine)
                quarantined.append((sidecar, quarantine))
            os.replace(stage, target)
            replaced = True
            validate_database(target)
            for _, quarantine in quarantined:
                quarantine.unlink(missing_ok=True)
            marker.unlink()
            marker_owned = False
        except BaseException:
            if marker_owned and not replaced:
                # If a process dies here, the durable marker remains. A new startup
                # refuses to open this data; another explicit offline restore may
                # use a verified candidate and preserve the previous evidence.
                for sidecar, quarantine in reversed(quarantined):
                    os.replace(quarantine, sidecar)
                if _original_preserved(target, raw_before):
                    if previous_marker is None:
                        marker.unlink()
                    else:
                        _write_restore_marker(marker, previous_marker)
            raise
        finally:
            stage.unlink(missing_ok=True)
        return {"restored": target.name, "credentialsRevoked": True,
                "dataId": new_data_id, "preRestoreBackup": before, "offlineEvidence": raw_before}


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def export_database(db_path: Path | str) -> bytes:
    """Export every record and BLOB from one snapshot, with a per-file hash manifest."""
    with tempfile.TemporaryDirectory(prefix="worklog-export-") as directory:
        snapshot = Path(directory) / "snapshot.sqlite3"
        _snapshot(Path(db_path), snapshot)
        files: dict[str, bytes] = {}
        markdown = ["# Worklog export", "", "All records are in records/*.json. Binary content is in blobs/.", ""]
        with closing(_readonly(snapshot)) as conn:
            conn.row_factory = sqlite3.Row
            snapshot_version = conn.execute("PRAGMA user_version").fetchone()[0]
            files["schema.json"] = json.dumps(
                [{"type": kind, "name": name, "sql": sql}
                 for (kind, name), sql in _schema_signature(conn).items()],
                ensure_ascii=False, indent=2).encode("utf-8")
            tables = [row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            for table in tables:
                records = []
                for row in conn.execute(f"SELECT * FROM {_identifier(table)}"):
                    record: dict[str, Any] = {}
                    for key in row.keys():
                        value = row[key]
                        if key == "token_hash":
                            value = "[redacted; credentials must be reissued after restore]"
                        elif isinstance(value, bytes):
                            digest = hashlib.sha256(value).hexdigest()
                            # Original HTML is an explicit downloadable archive member;
                            # use a content-derived name rather than an imported filename.
                            name = (f"imports/{digest}.html" if table == "plan_imports" and key == "original"
                                    else f"blobs/{digest}.bin")
                            files[name] = value
                            value = {"file": name, "sha256": digest, "size": len(value)}
                        record[key] = value
                    records.append(record)
                files[f"records/{table}.json"] = json.dumps(records, ensure_ascii=False, indent=2).encode("utf-8")
                markdown += [f"## {table}", ""]
                for record in records:
                    title = record.get("title") or record.get("name") or record.get("id", "Record")
                    markdown += [f"### {title}", ""]
                    for key, value in record.items():
                        markdown += [f"- {key}: {value}"]
                    markdown.append("")
        files["worklog.md"] = "\n".join(markdown).encode("utf-8")
        manifest = {"format": "worklog-export", "version": 1,
                    "schemaVersion": snapshot_version, "createdAt": datetime.now(UTC).isoformat(),
                    "files": [{"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                              for name, data in sorted(files.items())]}
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(files.items()):
                archive.writestr(name, data)
            archive.writestr("manifest.json", json.dumps(manifest, indent=2))
        return output.getvalue()
