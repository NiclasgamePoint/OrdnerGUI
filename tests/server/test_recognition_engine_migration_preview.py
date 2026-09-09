"""Migration previews exercise only databases invented under pytest's tmp_path."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

import pytest

from papagui_server.adapters.customer_schema import initialize_customer_schema

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("recognition_migration_preview", ROOT / "tools/recognition_migration_preview.py")
preview = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preview)

SENTINEL = "SYNTHETIC_PRIVATE_MARKER_XYZ"


@pytest.fixture
def legacy_database(tmp_path):
    path = tmp_path / (SENTINEL + ".sqlite")
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    initialize_customer_schema(connection)
    connection.execute("INSERT INTO customers(id,folder_path,display_name,entity_type,company,email,phone,street,postal_code,city) VALUES (1,'customer://synthetic-preview',?,'Privatperson','Preview Company','manual@example.org','030 12345678','Musterstraße 12','10115','Berlin')", (SENTINEL,))
    connection.execute("INSERT INTO contacts(customer_id,uid,name,role,email,phone) VALUES (1,'invented-uid','Mira Muster','Planung','mira@example.org','030 12345678')")
    connection.execute("INSERT INTO customer_projects(customer_id,source_id,relative_path,folder_path,folder_key,project_label,service_type,project_city,year) VALUES (1,'synthetic','Service/2026/Project','source://synthetic/Service/2026/Project','source://synthetic/service/2026/project','Invented Project','Service','Bonn',2026)")
    connection.execute("INSERT INTO customer_services(customer_id,name) VALUES (1,'Synthetic Service')")
    connection.execute("INSERT INTO customer_folders(customer_id,folder_path) VALUES (1,'source://synthetic/Service/2026/Project')")
    connection.execute("INSERT INTO notes(customer_id,body) VALUES (1,?)", (SENTINEL,))
    connection.execute("INSERT INTO recognition_cases(signature,recognition_key,display_name,project_roots_json,status) VALUES ('synthetic-case','synthetic-key','Synthetic Case','[]','rejected')")
    connection.execute("INSERT INTO recognition_decisions(signature,action,customer_id) VALUES ('synthetic-case','reject',1)")
    for table in ("candidate_aliases", "candidate_evidence", "candidate_decisions", "customer_field_provenance"):
        connection.execute(f"DROP TABLE {table}")
    connection.execute("DROP INDEX idx_contacts_uid")
    connection.execute("ALTER TABLE contacts DROP COLUMN uid")
    connection.execute("DROP TABLE customer_document_suggestions")
    connection.execute("""CREATE TABLE customer_document_suggestions (
        id INTEGER PRIMARY KEY,customer_id INTEGER NOT NULL,kind TEXT NOT NULL,value TEXT NOT NULL,
        source_path TEXT NOT NULL,excerpt TEXT NOT NULL DEFAULT '',fingerprint TEXT UNIQUE NOT NULL,
        confidence REAL NOT NULL,status TEXT NOT NULL DEFAULT 'pending',created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    values=[("phone","+49 30 12345678","accepted"),("phone","030 12345678","rejected"),
            ("phone","0049 30 12345678","pending"),("email","same@example.org","rejected"),
            ("email","same@example.org","pending"),("phone","09.09.2026","pending"),
            ("email","fresh@example.org","pending")]
    for index,(kind,value,status) in enumerate(values):
        connection.execute("INSERT INTO customer_document_suggestions(customer_id,kind,value,source_path,excerpt,fingerprint,confidence,status) VALUES (1,? ,?,'source://synthetic/old.txt',?,?,0,?)", (kind,value,SENTINEL,f"invented-{index}",status))
    connection.commit()
    connection.close()
    return path


def test_preview_preserves_source_and_all_protected_synthetic_values(legacy_database):
    original_bytes=legacy_database.read_bytes()
    original_mtime=legacy_database.stat().st_mtime_ns
    result=preview.preview_migration(legacy_database)
    assert result["technical_preservation_passed"] is True
    assert result["semantic_quality_accepted"] is False
    assert result["original_database_modified"] is False
    assert result["counts"]["customers_total"]==1
    assert result["counts"]["open_before"]==4
    assert result["counts"]["open_after"]==1
    assert result["counts"]["invalid"]==1
    assert result["counts"]["superseded"]==3
    assert result["counts"]["migration_conflicts"]==1
    assert result["counts"]["decision_records_before"]==4
    assert result["counts"]["decision_records_preserved"]==4
    assert all(value==0 for key,value in result["counts"].items() if key.startswith("changed_"))
    assert all(result["checks"].values())
    assert legacy_database.read_bytes()==original_bytes
    assert legacy_database.stat().st_mtime_ns==original_mtime
    assert result["checksums"]["source_before"]==result["checksums"]["source_after"]
    assert result["checksums"]["backup_before"]!=result["checksums"]["backup_after"]
    encoded=json.dumps(result)
    for forbidden in (SENTINEL,"manual@example.org","Mira Muster","source://synthetic","Musterstraße",str(legacy_database)):
        assert forbidden not in encoded


def test_original_sqlite_connection_is_explicitly_read_only(legacy_database,monkeypatch):
    actual_connect=preview.sqlite3.connect
    connections=[]
    def traced(database,*args,**kwargs):
        connections.append((str(database),kwargs.get("uri",False)))
        return actual_connect(database,*args,**kwargs)
    monkeypatch.setattr(preview.sqlite3,"connect",traced)
    preview.preview_migration(legacy_database)
    originals=[entry for entry in connections if SENTINEL in entry[0]]
    assert originals==[(legacy_database.as_uri()+"?mode=ro",True)]


def test_preview_detects_changed_customer_value_in_copy(legacy_database,monkeypatch):
    actual_migrate=preview.migrate_candidates
    def faulty(connection):
        actual_migrate(connection)
        connection.execute("UPDATE customers SET email='wrong@example.org'")
    monkeypatch.setattr(preview,"migrate_candidates",faulty)
    original=legacy_database.read_bytes()
    result=preview.preview_migration(legacy_database)
    assert result["technical_preservation_passed"] is False
    assert result["checks"]["customer_fields_preserved"] is False
    assert result["counts"]["changed_customer_values"]==1
    assert legacy_database.read_bytes()==original


def test_preview_detects_missing_old_decision_in_copy(legacy_database,monkeypatch):
    actual_migrate=preview.migrate_candidates
    def faulty(connection):
        actual_migrate(connection)
        connection.execute("DELETE FROM recognition_decisions")
    monkeypatch.setattr(preview,"migrate_candidates",faulty)
    result=preview.preview_migration(legacy_database)
    assert result["checks"]["all_existing_decisions_preserved"] is False
    assert result["counts"]["decision_records_preserved"]==3
    assert result["technical_preservation_passed"] is False


def test_failure_does_not_modify_original_or_disclose_error_values(legacy_database,monkeypatch,capsys):
    original=legacy_database.read_bytes()
    def failure(connection):
        connection.execute("UPDATE customers SET phone='changed only in temporary copy'")
        raise sqlite3.DatabaseError(SENTINEL)
    monkeypatch.setattr(preview,"migrate_candidates",failure)
    monkeypatch.setattr(sys,"argv",["recognition_migration_preview.py","--database",str(legacy_database)])
    assert preview.main()==2
    captured=capsys.readouterr()
    assert captured.out==""
    assert json.loads(captured.err)=={"error":"migration_preview_failed"}
    assert SENTINEL not in captured.err
    assert legacy_database.read_bytes()==original


def test_corrupt_database_error_is_anonymous(tmp_path,monkeypatch,capsys):
    path=tmp_path/"synthetic-corrupt.sqlite"
    path.write_text(SENTINEL)
    monkeypatch.setattr(sys,"argv",["recognition_migration_preview.py","--database",str(path)])
    assert preview.main()==2
    output=capsys.readouterr()
    assert json.loads(output.err)=={"error":"migration_preview_failed"}
    assert SENTINEL not in output.err and str(path) not in output.err


def test_foreign_key_integrity_failure_is_anonymous(legacy_database):
    connection=sqlite3.connect(legacy_database)
    connection.execute("INSERT INTO contacts(customer_id,name) VALUES (999,?)",(SENTINEL,))
    connection.commit()
    connection.close()
    with pytest.raises(preview.PreviewError,match="^integrity_check_failed$"):
        preview.preview_migration(legacy_database)


@pytest.mark.parametrize("alias",["same","hardlink","wal","symlink"])
def test_output_cannot_overwrite_source_or_sidecars(legacy_database,tmp_path,monkeypatch,capsys,alias):
    if alias=="same":
        output=legacy_database
    elif alias=="hardlink":
        output=tmp_path/"report-hardlink.json"
        output.hardlink_to(legacy_database)
    elif alias=="symlink":
        output=tmp_path/"report-symlink.json"
        output.symlink_to(legacy_database)
    else:
        output=Path(str(legacy_database)+"-wal")
    original=legacy_database.read_bytes()
    monkeypatch.setattr(sys,"argv",["recognition_migration_preview.py","--database",str(legacy_database),"--output",str(output)])
    assert preview.main()==2
    assert json.loads(capsys.readouterr().err)=={"error":"output_overlaps_source"}
    assert legacy_database.read_bytes()==original


def test_cli_json_file_matches_anonymous_stdout(legacy_database,tmp_path,monkeypatch,capsys):
    output=tmp_path/"anonymous-report.json"
    monkeypatch.setattr(sys,"argv",["recognition_migration_preview.py","--database",str(legacy_database),"--output",str(output)])
    assert preview.main()==0
    stdout=capsys.readouterr().out
    assert output.read_text()==stdout
    assert json.loads(stdout)["technical_preservation_passed"] is True
    assert SENTINEL not in stdout


def test_database_argument_has_no_implicit_default(monkeypatch,capsys):
    monkeypatch.setattr(sys,"argv",["recognition_migration_preview.py"])
    with pytest.raises(SystemExit) as error:
        preview.main()
    assert error.value.code==2
    assert "--database" in capsys.readouterr().err


def test_missing_database_does_not_create_a_new_file(tmp_path):
    path=tmp_path/"does-not-exist.sqlite"
    with pytest.raises(preview.PreviewError,match="^database_unavailable$"):
        preview.preview_migration(path)
    assert not path.exists()


def test_output_cannot_overwrite_resolved_symlink_database_sidecar(legacy_database,tmp_path,monkeypatch,capsys):
    alias=tmp_path/"database-alias.sqlite"
    alias.symlink_to(legacy_database)
    output=Path(str(legacy_database)+"-wal")
    monkeypatch.setattr(sys,"argv",["recognition_migration_preview.py","--database",str(alias),"--output",str(output)])
    assert preview.main()==2
    assert json.loads(capsys.readouterr().err)=={"error":"output_overlaps_source"}
    assert not output.exists()


def test_open_before_does_not_recount_already_imported_archived_rows():
    connection=sqlite3.connect(":memory:")
    connection.row_factory=sqlite3.Row
    connection.executescript("""
        CREATE TABLE customer_document_suggestions(status TEXT,fingerprint TEXT,canonical_id INTEGER,lifecycle TEXT);
        CREATE TABLE customer_data_suggestions(status TEXT,fingerprint TEXT);
        INSERT INTO customer_document_suggestions VALUES ('accepted','already-imported',NULL,'active');
        INSERT INTO customer_document_suggestions VALUES ('pending','still-open',NULL,'active');
        INSERT INTO customer_document_suggestions VALUES ('pending','obsolete',NULL,'stale');
        INSERT INTO customer_data_suggestions VALUES ('pending','already-imported');
        INSERT INTO customer_data_suggestions VALUES ('pending','still-open');
        INSERT INTO customer_data_suggestions VALUES ('pending','not-yet-imported');
    """)
    assert preview._open_before(connection)==2
    connection.close()


def test_wal_commits_are_included_without_changing_original_database(tmp_path):
    path=tmp_path/"synthetic-wal.sqlite"
    writer=sqlite3.connect(path)
    writer.row_factory=sqlite3.Row
    initialize_customer_schema(writer)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute("INSERT INTO customers(folder_path,display_name) VALUES ('customer://wal-preview','Synthetic WAL Customer')")
    writer.commit()
    before=path.read_bytes()
    wal=Path(str(path)+"-wal")
    wal_before=wal.read_bytes()
    try:
        result=preview.preview_migration(path)
        assert result["counts"]["customers_total"]==1
        assert result["technical_preservation_passed"] is True
        assert path.read_bytes()==before and wal.read_bytes()==wal_before
    finally:
        writer.close()
