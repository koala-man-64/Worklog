"""SQLite snapshots, offline restoration, and portable human-readable exports."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import tempfile
import uuid
import zipfile
from collections.abc import Iterable, Iterator
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .migrations import SCHEMA_VERSION, UPGRADABLE_VERSIONS

logger = logging.getLogger("worklog")

REQUIRED_TABLES = frozenset(
    {"work_items", "days", "daily_entries", "notes", "attachments",
     "credentials", "roadmaps", "steps", "proposals", "settings", "events",
     "receipts", "file_references", "search_index", "roadmap_versions"}
)

# Every released schema, sealed: the digest of each shape a version shipped in, over its normalized
# sqlite_master. A digest covers table definitions, constraints, indexes and FTS shadows, never
# contents, and never changes once released; only v1 shipped in more than one shape. Restore accepts
# a database whose version and digest are listed here, and builds nothing from today's code to compare
# it with. backend/tests/schemas holds the DDL of each shape, and test_sealed_schemas.py checks every
# digest against it, so a schema change needs a new version, its upgrade, the same change in
# schema.BASELINE and a new seal.
SEALED_SCHEMAS: dict[int, frozenset[str]] = {
    1: frozenset({
        "c5ae7406cdd63ddfa0cd7f3a18f2cc74f898936ef42c3b72909424c2d2e5a272",  # discussions with roadmap questions and decisions, the last v1 shape
        "23aab55b497a3d6534338da7887cb6a014169ecce1d2e0b91c229f42da8f9d2c",  # discussions, before roadmap questions and decisions
        "cd9f0804a6ceea36a375f52f9a00ad9ee9a593110a954d899e6ead5b8536370e",  # before discussions
        "373ee0760a75eb3784e49fe21d65601c3b134bd8003445f07a962b8567a07f67",  # before agent chats gained provider, model, repos and steps
        "2ffa3142f4cabeb13fcf638c4def1b873dae12ee4b2ed4f4961e69241eba8bf6",  # the shape above, before roadmap nesting
        "f8dbb906b152ee449531b6cac6897802d3fd74fd860385cb992672760bde27bb",  # also before links, agent chats and GitHub references
        "693ab61438716f2e5e834b78aa6ee418260b1d13be8558af771541b181c5d841",  # also before people
        "c73ee4b832defa66818053c6e16406268e70615ea7d5587d7857cb47bbdd3cb6",  # also before work items named a roadmap
        "2f407e161a537b37236a95d1275b94566e9e4bdd35329462c5a7563950f15f11",  # before chat details, nesting and item roadmaps
    }),
    2: frozenset({"b19a19953ba14b1de01989a2c1a1544b6e8b3baeee215c210452280c783ff937"}),  # coordination
    3: frozenset({"05c8e1e8be458c635a470066df5ef3da48cc6332364b190a918d12fd59a2b601"}),  # agent avatars
    4: frozenset({"e3096421a06018257266a5be0850bb1b08c14cb94d54b6295e2489a0839be450"}),  # chat messages
    5: frozenset({"c03c48ed6667dbfe6e552b57cbfa1bafd6eef04add43a084e0ccb3c502f26550"}),  # tickets
    6: frozenset({"8ab1d329e87af88ea067538c3a874ceedb49286096e8d43741482d16c8347f68"}),  # repository remote identity
    7: frozenset({"53fc2362c88e0b72ebc8e23397a51c88932e1e64b9d5c59114c032736542cd5d"}),  # repository provenance
    8: frozenset({"bbf88ec85131d14c25589e950900f6e655e6394356494b92b049410cd02053b7"}),  # one scratchpad per work item
    9: frozenset({"c5e5a29350ff20fc9e70aa3dbe213f594e14acddf4875cd19f5c740f73ecf0fd"}),  # resources
    10: frozenset({"966e4e17996b20bf3a4c30a02cc062e9cf2a8b87ee68a698760e3169897e6e97"}),  # person details
    11: frozenset({"c6661a470078d56e22c00f766d2dcd2d5eba4ab3d2f0f8eb3e3c89c6182ef13d"}),  # search keys and receipt dates
    12: frozenset({"dda4bfa40d402e989a01567cde828e904c48ebc0425f40d088956ff72f9f9b75"}),  # decision state in columns
    13: frozenset({"ed29b8f9cfc5e2b17578ab73f23020bf1a51c5ef0d5971ece2052d08f1ada692"}),  # chat confirmations
    14: frozenset({"134bc42f3ccc11c5c9dabe483ba5a6e1d8d766b21bc00a2b7720a374daf00e13"}),  # task order
    15: frozenset({"7b1a1ec43f20cc2c3cf1d06d4b1956abd5c7c7bee6fcb2754add975fb7ef59fa"}),  # work items can wait
    16: frozenset({"50d1aa8a96cab827efcdc66625b82a7dc0d67f1c4aaaf84df7eb3eb82a00e79d"}),  # what each approved proposal was applied to
    17: frozenset({"0e3f67dde3cbef64846789f21bc50da0600006d9e7119a451061805c3e727cf0"}),  # agent ties
    18: frozenset({"4776ba985558f5e6ddf310cddff70974b259de468bce783eda5b4d68140c1b0a"}),  # the directory each agent registered from
    19: frozenset({"e99601094754d13945ba203ea8645d52b0dbebf709870f1bfa0f246da51246ad"}),  # releases, their contents and checks
    20: frozenset({"c73937fd849b6e20cff7c2a1649ae026a1d429194852e424b547e786d8ef2540"}),  # reminders
    21: frozenset({"83c552ee5c0747fd90c7bffc3c2cf3bd589ee275f3b2901596da081feda61ad2"}),  # each day's reflection revision
    22: frozenset({"785c66e113042d83086be02da5a14f1edf58720a22843babb4a2000829f5a75d"}),  # discussion tags, pins and repository discussions
    23: frozenset({"84d7fb11db635773807292341234aff05dbb191e36c6833dd940276b9ef63546"}),  # CHECKs on tasks, notes, file references and attachments
    # v24 changes data only, linking tracked chats to the repositories their labels name, so it
    # shares v23's shape and seal. A database's version, not its shape, says whether it has the links.
    24: frozenset({"84d7fb11db635773807292341234aff05dbb191e36c6833dd940276b9ef63546"}),  # chats linked to the repositories their labels name
    25: frozenset({"8e46fdc307d7ae883967a50306f40cb91b4e577a2e523db4b25f4938f05ffc7a"}),  # the HTML plan import's table dropped (R1)
    26: frozenset({"1d4c0cd192f8cab75172d61d8aa2f237d3b3b0bfd9b7cfe3ffb930ac9c028882"}),  # work item checklists' tasks dropped (R1)
    27: frozenset({"5acced649fd0da636283d991382405f392e196cf7085e0af2efce4a986b2abdb"}),  # Reminders dropped (R1)
    28: frozenset({"daf3bd059ebdc84494b049c1537a5e1f03436996ba73e4dac48c22a31fce6fbe"}),  # Releases dropped (R1, R6)
}


def _schema_signature(conn: sqlite3.Connection) -> dict[tuple[str, str], str]:
    # Compare definitions, constraints, indexes and FTS shadows; no candidate triggers
    # or substituted virtual-table modules are accepted by offline restore.
    return {(row[0], row[1]): " ".join((row[2] or "").split()) for row in conn.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")}


def _schema_digest(signature: dict[tuple[str, str], str]) -> str:
    encoded = json.dumps(sorted((kind, name, sql) for (kind, name), sql in signature.items()), separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


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
                if _schema_digest(_schema_signature(conn)) not in SEALED_SCHEMAS.get(version, frozenset()):
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


def _metadata(path: Path, *, hashed: bool = True, cache: dict[str, Any] | None = None) -> dict[str, Any]:
    stat = path.stat()
    record: dict[str, Any] = {"name": path.name, "size": stat.st_size,
                              "createdAt": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat()}
    if hashed:
        record["sha256"] = _file_hash(path) if cache is None else _cached_hash(path, cache)
    return record


def _file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


# Each backup's SHA-256 is worked out once and kept beside the backups, for as long as the file has
# the size and modification time it was hashed at (consolidation step 4.2, B-05): the CLI's listing
# read every byte of every backup on each call.
HASH_CACHE = "backup-hashes.json"


def _hash_cache(directory: Path) -> dict[str, Any]:
    try:
        cache = json.loads((directory / HASH_CACHE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return cache if isinstance(cache, dict) else {}


def _save_hash_cache(directory: Path, cache: dict[str, Any]) -> None:
    """Keep the hashes of the files still there, written whole by a rename. A cache that can't be
    written costs only a hash on the next listing."""
    kept = {name: entry for name, entry in cache.items() if (directory / name).is_file()}
    temporary = directory / (".hashes-" + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(kept, sort_keys=True), encoding="utf-8")
        os.replace(temporary, directory / HASH_CACHE)
    except OSError:
        logger.warning("Backup hashes could not be cached; they are worked out again next time")
    finally:
        temporary.unlink(missing_ok=True)


def _cached_hash(path: Path, cache: dict[str, Any]) -> str:
    """The file's SHA-256 from the cache while the file is as it was hashed, or worked out now and
    put in the cache."""
    stat = path.stat()
    stamp = [stat.st_size, stat.st_mtime_ns]
    entry = cache.get(path.name)
    if isinstance(entry, dict) and entry.get("stat") == stamp and isinstance(entry.get("sha256"), str):
        return entry["sha256"]
    digest = _file_hash(path)
    cache[path.name] = {"stat": stamp, "sha256": digest}
    return digest


def list_backups(backup_dir: Path | str, *, hashed: bool = True, verify: bool = False) -> list[dict[str, Any]]:
    """Backups and raw recovery evidence, newest first.

    With hashed=False the listing reads file metadata only: hashing reads every byte of
    every file, which Settings would otherwise pay on each load. The CLI keeps the hashes,
    each worked out once and cached while its file keeps its size and modification time
    (HASH_CACHE). So a cached hash is the one the file had when it was hashed: damage that
    keeps both, or a copy that keeps the modification time, would not show. With verify,
    every file is hashed now, and the cache takes those hashes.
    """
    directory = Path(backup_dir)
    if not directory.exists():
        return []
    files = [*directory.glob("worklog-*.sqlite3"), *directory.glob("worklog-pre-restore-raw-*.zip")]
    cache = ({} if verify else _hash_cache(directory)) if hashed else None
    before = json.dumps(cache, sort_keys=True)
    listed = [_metadata(path, hashed=hashed, cache=cache) for path in sorted(files, reverse=True)
              if path.is_file()]
    if cache is not None and json.dumps(cache, sort_keys=True) != before:
        _save_hash_cache(directory, cache)
    return listed


def backup_database(db_path: Path | str, backup_dir: Path | str,
                    kind: str = "manual") -> dict[str, Any]:
    if kind not in {"manual", "daily", "pre-migration", "pre-restore", "pre-compact"}:
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
    # Compaction prunes its own backups, once a compaction has finished (compaction.BACKUPS_KEPT).
    keep = {"daily": 14, "pre-migration": 3}.get(kind)
    if keep is not None:
        prune_backups(directory, kind, keep)
    # The new backup is hashed for its metadata anyway, so the next listing need not hash it again.
    cache = _hash_cache(directory)
    result = _metadata(target, cache=cache)
    _save_hash_cache(directory, cache)
    return result


def prune_backups(backup_dir: Path | str, kind: str, keep: int) -> None:
    """Delete all but the newest `keep` backups of a kind, by the time in their names."""
    for old in sorted(Path(backup_dir).glob(f"worklog-{kind}-*.sqlite3"), reverse=True)[keep:]:
        old.unlink()


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


def _assert_database_offline(target: Path, doing: str = "restoring") -> None:
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
            raise RuntimeError(f"Database is busy; close all Worklog connections before {doing}") from exc


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
    """An export (see write_export) read back whole, for tests and other small uses. Worklog's own
    callers write it to a file instead."""
    with tempfile.TemporaryDirectory(prefix="worklog-export-copy-") as directory:
        target = Path(directory) / "export.zip"
        write_export(db_path, target)
        return target.read_bytes()


def _chunks(path: Path) -> Iterator[bytes]:
    with path.open("rb") as stream:
        while chunk := stream.read(1 << 20):
            yield chunk


def write_export(db_path: Path | str, target: Path | str) -> None:
    """Export every record and BLOB from one snapshot to target, a ZIP with a per-file hash manifest.

    It is written a record and a BLOB at a time (consolidation step 4.2, REM-18), where the whole
    export used to be built in memory with every BLOB in it twice. A ZIP takes one member at a time,
    so each table's records, and the readable Markdown, are written beside the snapshot first and
    copied in after; each BLOB they name goes into the ZIP as it is met, once. The members and the
    manifest are those the export always had.
    """
    with tempfile.TemporaryDirectory(prefix="worklog-export-") as directory:
        work = Path(directory)
        snapshot = work / "snapshot.sqlite3"
        _snapshot(Path(db_path), snapshot)
        files: dict[str, tuple[int, str]] = {}
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:

            def member(name: str, chunks: Iterable[bytes]) -> None:
                digest, size = hashlib.sha256(), 0
                with archive.open(name, "w", force_zip64=True) as stream:
                    for chunk in chunks:
                        digest.update(chunk)
                        size += len(chunk)
                        stream.write(chunk)
                files[name] = (size, digest.hexdigest())

            markdown_path, records_path = work / "worklog.md", work / "records.json"
            with closing(_readonly(snapshot)) as conn, markdown_path.open("w", encoding="utf-8", newline="\n") as markdown:
                conn.row_factory = sqlite3.Row
                snapshot_version = conn.execute("PRAGMA user_version").fetchone()[0]
                member("schema.json", [json.dumps(
                    [{"type": kind, "name": name, "sql": sql}
                     for (kind, name), sql in _schema_signature(conn).items()],
                    ensure_ascii=False, indent=2).encode("utf-8")])
                # The Markdown's lines, each but the first after a line break, as they were joined.
                lines = 0

                def line(text: str) -> None:
                    nonlocal lines
                    markdown.write(("\n" if lines else "") + text)
                    lines += 1

                for text in ("# Worklog export", "", "All records are in records/*.json. Binary content is in blobs/.", ""):
                    line(text)
                tables = [row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
                for table in tables:
                    line(f"## {table}")
                    line("")
                    count = 0
                    with records_path.open("w", encoding="utf-8", newline="\n") as records:
                        records.write("[")
                        for row in conn.execute(f"SELECT * FROM {_identifier(table)}"):
                            record: dict[str, Any] = {}
                            for key in row.keys():
                                value = row[key]
                                if key == "token_hash":
                                    value = "[redacted; credentials must be reissued after restore]"
                                elif isinstance(value, bytes):
                                    digest = hashlib.sha256(value).hexdigest()
                                    name = f"blobs/{digest}.bin"
                                    if name not in files:
                                        member(name, [value])
                                    value = {"file": name, "sha256": digest, "size": len(value)}
                                record[key] = value
                            # Each record as json.dumps lays out a list of them. Its own line breaks are
                            # "\n" alone: one inside a string is escaped, and splitlines would also
                            # break at a U+2028 a string may hold.
                            text = json.dumps(record, ensure_ascii=False, indent=2)
                            records.write(("," if count else "") + "\n" + "\n".join("  " + part for part in text.split("\n")))
                            count += 1
                            line(f"### {record.get('title') or record.get('name') or record.get('id', 'Record')}")
                            line("")
                            for key, value in record.items():
                                line(f"- {key}: {value}")
                            line("")
                        records.write("\n]" if count else "]")
                    member(f"records/{table}.json", _chunks(records_path))
            member("worklog.md", _chunks(markdown_path))
            manifest = {"format": "worklog-export", "version": 1,
                        "schemaVersion": snapshot_version, "createdAt": datetime.now(UTC).isoformat(),
                        "files": [{"path": name, "size": size, "sha256": digest}
                                  for name, (size, digest) in sorted(files.items())]}
            archive.writestr("manifest.json", json.dumps(manifest, indent=2))
