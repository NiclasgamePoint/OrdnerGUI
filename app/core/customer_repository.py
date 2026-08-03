from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import hashlib
import json
import os
import sqlite3
import uuid

from app.core.customer_models import (
    Contact,
    Customer,
    CustomerDataSuggestion,
    CustomerJournalEntry,
    CustomerProject,
    ServiceType,
)
from app.core.customer_recognition_models import (
    ContactScanStats,
    RecognitionCandidate,
    RecognitionStats,
)
from app.core.folder_structure import ProjectRoot, normalize_identity
from app.core.search_models import SearchSort


class CustomerRepository:
    """Persistent customer data, intentionally independent from replaceable search indexes."""

    def __init__(self, database_path: Path, readonly: bool = False):
        self.database_path = database_path
        if readonly:
            self.connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
        else:
            database_path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(str(database_path))
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        if not readonly:
            self.connection.execute("PRAGMA journal_mode = WAL")
            self._initialize()

    @staticmethod
    def _folder_key(folder_path: str) -> str:
        value = folder_path.strip()
        if value.startswith("customer://"):
            return value
        # Resolve platform aliases as well as relative components. This keeps
        # macOS (/var vs /private/var) and Windows (8.3 vs long names) from
        # producing two keys for the same folder.
        return str(Path(value).expanduser().resolve(strict=False))

    @classmethod
    def _folder_lookup_key(cls, folder_path: str) -> str:
        return os.path.normcase(cls._folder_key(folder_path))

    @staticmethod
    def _path_equals_sql(column: str) -> str:
        comparator = "COLLATE NOCASE" if os.name == "nt" else ""
        return f"{column} = ? {comparator}".strip()

    @staticmethod
    def _path_like_sql(column: str) -> str:
        comparator = "COLLATE NOCASE" if os.name == "nt" else ""
        return f"{column} LIKE ? {comparator}".strip()

    def _initialize(self):
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY,
                folder_path TEXT UNIQUE NOT NULL,
                display_name TEXT NOT NULL,
                entity_type TEXT NOT NULL DEFAULT 'Unternehmen',
                company TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '',
                street TEXT NOT NULL DEFAULT '',
                postal_code TEXT NOT NULL DEFAULT '',
                city TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS customer_types (
                id INTEGER PRIMARY KEY,
                name TEXT UNIQUE COLLATE NOCASE NOT NULL
            );
            CREATE TABLE IF NOT EXISTS contacts (
                id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS customer_services (
                customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                PRIMARY KEY (customer_id, name)
            );
            CREATE TABLE IF NOT EXISTS service_types (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                normalized_name TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS customer_projects (
                id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                service_type_id INTEGER NOT NULL REFERENCES service_types(id),
                folder_path TEXT NOT NULL,
                folder_key TEXT UNIQUE NOT NULL,
                project_label TEXT NOT NULL DEFAULT '',
                project_city TEXT NOT NULL DEFAULT '',
                year INTEGER,
                source TEXT NOT NULL DEFAULT 'folder',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS customer_data_suggestions (
                id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                project_id INTEGER REFERENCES customer_projects(id) ON DELETE CASCADE,
                field_name TEXT NOT NULL,
                suggested_value TEXT NOT NULL,
                source_path TEXT NOT NULL DEFAULT '',
                excerpt TEXT NOT NULL DEFAULT '',
                rule TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                suggestion_type TEXT NOT NULL DEFAULT 'field',
                contact_name TEXT NOT NULL DEFAULT '',
                contact_role TEXT NOT NULL DEFAULT '',
                contact_email TEXT NOT NULL DEFAULT '',
                contact_phone TEXT NOT NULL DEFAULT '',
                fingerprint TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS customer_folders (
                customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                folder_path TEXT UNIQUE NOT NULL,
                PRIMARY KEY (customer_id, folder_path)
            );
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS customer_journal_entries (
                id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY,
                name TEXT UNIQUE COLLATE NOCASE NOT NULL
            );
            CREATE TABLE IF NOT EXISTS customer_tags (
                customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                PRIMARY KEY (customer_id, tag_id)
            );
            CREATE TABLE IF NOT EXISTS recognition_cases (
                signature TEXT PRIMARY KEY,
                recognition_key TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS recognition_decisions (
                signature TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
                decided_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS recognition_runs (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                detected INTEGER NOT NULL DEFAULT 0,
                created INTEGER NOT NULL DEFAULT 0,
                assigned INTEGER NOT NULL DEFAULT 0,
                skipped INTEGER NOT NULL DEFAULT 0,
                pending INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '',
                finished_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS automatic_customer_sources (
                folder_path TEXT PRIMARY KEY,
                customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                recognition_key TEXT NOT NULL,
                last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS automatic_field_sources (
                id INTEGER PRIMARY KEY,
                owner_type TEXT NOT NULL CHECK(owner_type IN ('customer', 'contact')),
                owner_id INTEGER NOT NULL,
                field_name TEXT NOT NULL,
                value TEXT NOT NULL,
                normalized_value TEXT NOT NULL,
                source_path TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(owner_type, owner_id, field_name, normalized_value, source_path)
            );
            CREATE TABLE IF NOT EXISTS extracted_value_observations (
                value_type TEXT NOT NULL,
                normalized_value TEXT NOT NULL,
                value TEXT NOT NULL,
                folder_path TEXT NOT NULL,
                source_path TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(value_type, normalized_value, folder_path)
            );
            CREATE TABLE IF NOT EXISTS blacklist_suggestions (
                id INTEGER PRIMARY KEY,
                value_type TEXT NOT NULL,
                normalized_value TEXT NOT NULL,
                value TEXT NOT NULL,
                folder_count INTEGER NOT NULL DEFAULT 0,
                example_sources TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'pending',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(value_type, normalized_value)
            );
            CREATE TABLE IF NOT EXISTS customer_merge_log (
                id INTEGER PRIMARY KEY,
                survivor_id INTEGER NOT NULL,
                absorbed_id INTEGER NOT NULL,
                normalized_name TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT 'same_normalized_name',
                merged_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        self._migrate_data_suggestions()
        self.connection.executemany(
            "INSERT OR IGNORE INTO customer_types (name) VALUES (?)",
            [("Unternehmen",), ("Privatperson",), ("Organisation",)],
        )
        self.connection.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_customer_projects_customer
                ON customer_projects(customer_id);
            CREATE INDEX IF NOT EXISTS idx_customer_projects_service
                ON customer_projects(service_type_id);
            CREATE INDEX IF NOT EXISTS idx_customer_projects_folder_key
                ON customer_projects(folder_key);
            CREATE INDEX IF NOT EXISTS idx_customer_projects_year
                ON customer_projects(year);
            CREATE INDEX IF NOT EXISTS idx_customer_suggestions_customer
                ON customer_data_suggestions(customer_id, status);
            CREATE INDEX IF NOT EXISTS idx_automatic_field_owner
                ON automatic_field_sources(owner_type, owner_id);
            CREATE INDEX IF NOT EXISTS idx_extracted_values
                ON extracted_value_observations(value_type, normalized_value);
            """
        )
        self.connection.execute(
            """
            INSERT OR IGNORE INTO customer_folders (customer_id, folder_path)
            SELECT id, folder_path FROM customers WHERE TRIM(folder_path) != ''
            """
        )
        self._migrate_legacy_projects()
        self.connection.commit()
        self.merge_duplicate_customers_by_name()

    def get_by_folder(
        self,
        folder_path: str,
        include_ancestors: bool = True,
    ) -> Customer | None:
        project = self.find_project_by_folder(folder_path, include_ancestors)
        if project is not None and project.customer_id is not None:
            return self.get(project.customer_id)
        normalized = self._folder_key(folder_path)
        folder_match = self._path_equals_sql("customer_folders.folder_path")
        customer_match = self._path_equals_sql("folder_path")
        ancestor_match = "? LIKE customer_folders.folder_path || ? || '%'"
        if os.name == "nt":
            ancestor_match += " COLLATE NOCASE"
        row = self.connection.execute(
            f"""
            SELECT customers.* FROM customers
            JOIN customer_folders ON customer_folders.customer_id = customers.id
            WHERE {folder_match}
            LIMIT 1
            """,
            (normalized,),
        ).fetchone()
        if row is not None:
            return self._hydrate(row)
        if include_ancestors:
            row = self.connection.execute(
                f"""
                SELECT customers.* FROM customers
                JOIN customer_folders ON customer_folders.customer_id = customers.id
                WHERE {ancestor_match}
                  AND customer_folders.folder_path NOT LIKE 'customer://%'
                ORDER BY LENGTH(customer_folders.folder_path) DESC
                LIMIT 1
                """,
                (normalized, os.sep),
            ).fetchone()
            if row is not None:
                return self._hydrate(row)
        row = self.connection.execute(
            f"SELECT * FROM customers WHERE {customer_match}",
            (normalized,),
        ).fetchone()
        return self._hydrate(row) if row else None

    def get(self, customer_id: int) -> Customer | None:
        row = self.connection.execute(
            "SELECT * FROM customers WHERE id = ?", (customer_id,)
        ).fetchone()
        return self._hydrate(row) if row else None

    def list_customers(self) -> list[Customer]:
        rows = self.connection.execute(
            "SELECT * FROM customers ORDER BY display_name COLLATE NOCASE"
        ).fetchall()
        return [self._hydrate(row) for row in rows]

    def find_by_identity(self, display_name: str, city: str) -> list[Customer]:
        name_key = normalize_identity(display_name)
        city_key = normalize_identity(city)
        return [
            customer
            for customer in self.list_customers()
            if normalize_identity(customer.display_name) == name_key
            and normalize_identity(customer.city) == city_key
        ]

    def find_by_name(self, display_name: str) -> list[Customer]:
        """Return customers with the same stable name, independent of project place."""
        name_key = normalize_identity(display_name)
        if not name_key:
            return []
        return [
            customer
            for customer in self.list_customers()
            if normalize_identity(customer.display_name) == name_key
        ]

    def customer_ids_within_folder(self, folder_path: str) -> list[int]:
        normalized = self._folder_key(folder_path)
        project = self.find_project_by_folder(normalized)
        project_ids = [int(project.customer_id)] if project and project.customer_id else []
        folder_match = self._path_equals_sql("folder_path")
        descendant_match = self._path_like_sql("folder_path")
        rows = self.connection.execute(
            f"""
            SELECT DISTINCT customer_id FROM customer_folders
            WHERE {folder_match} OR {descendant_match}
            """,
            (normalized, f"{normalized}{os.sep}%"),
        ).fetchall()
        return sorted({*project_ids, *(int(row[0]) for row in rows)})

    def list_customer_types(self) -> list[str]:
        rows = self.connection.execute(
            "SELECT name FROM customer_types ORDER BY name COLLATE NOCASE"
        ).fetchall()
        return [str(row[0]) for row in rows]

    def list_journal_entries(self, customer_id: int) -> list[CustomerJournalEntry]:
        rows = self.connection.execute(
            """
            SELECT id, customer_id, body, created_at, updated_at
            FROM customer_journal_entries
            WHERE customer_id=?
            ORDER BY datetime(created_at) DESC, id DESC
            """,
            (customer_id,),
        ).fetchall()
        return [
            CustomerJournalEntry(
                id=int(row["id"]),
                customer_id=int(row["customer_id"]),
                body=str(row["body"] or ""),
                created_at=str(row["created_at"] or ""),
                updated_at=str(row["updated_at"] or ""),
            )
            for row in rows
        ]

    def add_journal_entry(self, customer_id: int, body: str) -> CustomerJournalEntry | None:
        text = str(body or "").strip()
        if not text:
            return None
        exists = self.connection.execute(
            "SELECT 1 FROM customers WHERE id=? LIMIT 1",
            (customer_id,),
        ).fetchone()
        if exists is None:
            raise ValueError("Der ausgewählte Kunde existiert nicht mehr.")
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO customer_journal_entries (customer_id, body)
                VALUES (?, ?)
                """,
                (customer_id, text),
            )
        row = self.connection.execute(
            """
            SELECT id, customer_id, body, created_at, updated_at
            FROM customer_journal_entries
            WHERE id=?
            """,
            (int(cursor.lastrowid),),
        ).fetchone()
        return CustomerJournalEntry(
            id=int(row["id"]),
            customer_id=int(row["customer_id"]),
            body=str(row["body"] or ""),
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
        )

    def upsert_service_type(self, name: str) -> ServiceType:
        cleaned = " ".join(str(name or "").split())
        if not cleaned:
            cleaned = "Unbekannt"
        normalized = normalize_identity(cleaned) or cleaned.casefold()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO service_types (name, normalized_name)
                VALUES (?, ?)
                ON CONFLICT(normalized_name) DO UPDATE SET name=excluded.name
                """,
                (cleaned, normalized),
            )
        row = self.connection.execute(
            "SELECT * FROM service_types WHERE normalized_name = ?",
            (normalized,),
        ).fetchone()
        return self._hydrate_service_type(row)

    def upsert_project_from_root(
        self,
        project_root: ProjectRoot | dict | CustomerProject,
        customer_id: int | None = None,
    ) -> CustomerProject:
        if isinstance(project_root, CustomerProject):
            project = project_root
        else:
            project = self._project_from_root(project_root)
        if customer_id is not None:
            project.customer_id = customer_id
        if project.customer_id is None:
            raise ValueError("Ein Projekt benötigt einen Kunden.")
        service = self.upsert_service_type(project.service_type)
        folder_path = self._folder_key(project.folder_path)
        folder_key = self._folder_lookup_key(folder_path)
        owner = self.find_project_by_folder(folder_path, include_ancestors=False)
        if owner is not None and owner.customer_id != project.customer_id:
            raise ValueError("Der Projektordner ist bereits einem anderen Kunden zugeordnet.")
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO customer_projects
                    (customer_id, service_type_id, folder_path, folder_key,
                     project_label, project_city, year, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(folder_key) DO UPDATE SET
                    customer_id=excluded.customer_id,
                    service_type_id=excluded.service_type_id,
                    folder_path=excluded.folder_path,
                    project_label=excluded.project_label,
                    project_city=excluded.project_city,
                    year=excluded.year,
                    source=excluded.source,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    int(project.customer_id),
                    int(service.id),
                    folder_path,
                    folder_key,
                    project.project_label.strip(),
                    project.project_city.strip(),
                    project.year,
                    project.source or "folder",
                ),
            )
            row = self.connection.execute(
                "SELECT id FROM customer_projects WHERE folder_key = ?",
                (folder_key,),
            ).fetchone()
            project_id = int(row[0])
            self._sync_legacy_project_links(int(project.customer_id))
        return self.get_project(project_id)

    def get_project(self, project_id: int) -> CustomerProject | None:
        row = self.connection.execute(
            """
            SELECT customer_projects.*, service_types.name AS service_type
            FROM customer_projects
            JOIN service_types ON service_types.id = customer_projects.service_type_id
            WHERE customer_projects.id = ?
            """,
            (project_id,),
        ).fetchone()
        return self._hydrate_project(row) if row is not None else None

    def list_projects_for_customer(self, customer_id: int) -> list[CustomerProject]:
        rows = self.connection.execute(
            """
            SELECT customer_projects.*, service_types.name AS service_type
            FROM customer_projects
            JOIN service_types ON service_types.id = customer_projects.service_type_id
            WHERE customer_projects.customer_id = ?
            ORDER BY customer_projects.year DESC, service_types.name COLLATE NOCASE,
                     customer_projects.project_label COLLATE NOCASE
            """,
            (customer_id,),
        ).fetchall()
        return [self._hydrate_project(row) for row in rows]

    def find_project_by_folder(
        self,
        folder_path: str,
        include_ancestors: bool = True,
    ) -> CustomerProject | None:
        normalized = self._folder_key(folder_path)
        folder_key = self._folder_lookup_key(normalized)
        row = self.connection.execute(
            """
            SELECT customer_projects.*, service_types.name AS service_type
            FROM customer_projects
            JOIN service_types ON service_types.id = customer_projects.service_type_id
            WHERE customer_projects.folder_key = ?
            LIMIT 1
            """,
            (folder_key,),
        ).fetchone()
        if row is not None:
            return self._hydrate_project(row)
        if not include_ancestors:
            return None
        candidates = self.connection.execute(
            """
            SELECT customer_projects.*, service_types.name AS service_type
            FROM customer_projects
            JOIN service_types ON service_types.id = customer_projects.service_type_id
            WHERE customer_projects.folder_path NOT LIKE 'customer://%'
            ORDER BY LENGTH(customer_projects.folder_path) DESC
            """
        ).fetchall()
        lookup = self._folder_lookup_key(normalized)
        for candidate in candidates:
            root = self._folder_lookup_key(str(candidate["folder_path"]))
            if lookup.startswith(f"{root}{os.sep}"):
                return self._hydrate_project(candidate)
        return None

    def apply_project_suggestion(
        self,
        customer_id: int,
        project_id: int | None,
        field_name: str,
        suggested_value: str,
        source_path: str = "",
        excerpt: str = "",
        rule: str = "",
        confidence: float = 0.0,
        reopen_rejected: bool = False,
    ) -> CustomerDataSuggestion | None:
        field = field_name.strip()
        value = str(suggested_value or "").strip()
        if not field or not value:
            return None
        fingerprint = self._field_fingerprint(field, value)
        existing = self.connection.execute(
            """
            SELECT * FROM customer_data_suggestions
            WHERE customer_id = ?
              AND suggestion_type = 'field'
              AND fingerprint = ?
            ORDER BY CASE status
                WHEN 'pending' THEN 0 WHEN 'accepted' THEN 1 ELSE 2 END, id DESC
            LIMIT 1
            """,
            (customer_id, fingerprint),
        ).fetchone()
        if existing is not None:
            status = str(existing["status"] or "pending")
            if status == "accepted":
                return None
            if status == "rejected":
                return None
        if existing is not None:
            bounded_confidence = max(0.0, min(1.0, float(confidence)))
            if excerpt or rule or bounded_confidence or existing["status"] == "rejected":
                with self.connection:
                    self.connection.execute(
                        """
                        UPDATE customer_data_suggestions
                        SET source_path = CASE WHEN ? != '' THEN ? ELSE source_path END,
                            excerpt = CASE WHEN ? != '' THEN ? ELSE excerpt END,
                            rule = CASE WHEN ? != '' THEN ? ELSE rule END,
                            confidence = MAX(confidence, ?),
                            status = 'pending'
                        WHERE id = ?
                        """,
                        (
                            source_path, source_path, excerpt, excerpt, rule, rule,
                            bounded_confidence, int(existing["id"]),
                        ),
                    )
                return self.get_data_suggestion(int(existing["id"]))
            return self._hydrate_data_suggestion(existing)
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO customer_data_suggestions
                    (customer_id, project_id, field_name, suggested_value, source_path,
                     excerpt, rule, confidence, fingerprint)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    customer_id, project_id, field, value, source_path,
                    excerpt, rule, max(0.0, min(1.0, float(confidence))),
                    fingerprint,
                ),
            )
            suggestion_id = int(self.connection.execute(
                "SELECT last_insert_rowid()"
            ).fetchone()[0])
        return self.get_data_suggestion(suggestion_id)

    @staticmethod
    def _field_fingerprint(field_name: str, value: str) -> str:
        if field_name == "email":
            normalized = value.strip().casefold()
        elif field_name == "phone":
            normalized = "".join(
                character for character in value if character.isdigit()
            )
        else:
            normalized = normalize_identity(value)
        payload = f"field|{field_name.strip().casefold()}|{normalized}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _contact_fingerprint(contact: Contact) -> str:
        payload = "|".join((
            normalize_identity(contact.name),
            normalize_identity(contact.role),
            contact.email.strip().casefold(),
            "".join(character for character in contact.phone if character.isdigit()),
        ))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def apply_contact_suggestion(
        self,
        customer_id: int,
        project_id: int | None,
        contact: Contact,
        source_path: str = "",
        excerpt: str = "",
        rule: str = "",
        confidence: float = 0.0,
    ) -> CustomerDataSuggestion | None:
        contact = Contact(
            name=contact.name.strip(),
            role=contact.role.strip(),
            email=contact.email.strip().casefold(),
            phone=contact.phone.strip(),
        )
        if not contact.name:
            return None
        fingerprint = self._contact_fingerprint(contact)
        existing = self.connection.execute(
            """
            SELECT * FROM customer_data_suggestions
            WHERE customer_id=? AND suggestion_type='contact' AND fingerprint=?
            ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'accepted' THEN 1 ELSE 2 END,
                     id DESC
            LIMIT 1
            """,
            (customer_id, fingerprint),
        ).fetchone()
        if existing is not None:
            if str(existing["status"]) in {"accepted", "rejected"}:
                return None
            return self._hydrate_data_suggestion(existing)
        words = normalize_identity(contact.name).split()
        if len(words) >= 2:
            pending_rows = self.connection.execute(
                """
                SELECT * FROM customer_data_suggestions
                WHERE customer_id=? AND suggestion_type='contact' AND status='pending'
                ORDER BY id
                """,
                (customer_id,),
            ).fetchall()
            surname_match = next((
                row for row in pending_rows
                if normalize_identity(str(row["contact_name"])) == words[-1]
            ), None)
            if surname_match is not None:
                with self.connection:
                    self.connection.execute(
                        """
                        UPDATE customer_data_suggestions
                        SET suggested_value=?, contact_name=?, contact_role=?,
                            contact_email=?, contact_phone=?, fingerprint=?,
                            source_path=CASE WHEN ?!='' THEN ? ELSE source_path END,
                            excerpt=CASE WHEN ?!='' THEN ? ELSE excerpt END,
                            rule=CASE WHEN ?!='' THEN ? ELSE rule END,
                            confidence=MAX(confidence, ?)
                        WHERE id=?
                        """,
                        (
                            contact.name, contact.name, contact.role, contact.email,
                            contact.phone, fingerprint, source_path, source_path,
                            excerpt, excerpt, rule, rule,
                            max(0.0, min(1.0, float(confidence))),
                            int(surname_match["id"]),
                        ),
                    )
                return self.get_data_suggestion(int(surname_match["id"]))
        matching_contact = self._find_matching_contact(customer_id, contact)
        if matching_contact is not None and all(
            not str(getattr(contact, field) or "").strip()
            or str(matching_contact[field] or "").strip().casefold()
            == str(getattr(contact, field) or "").strip().casefold()
            for field in ("name", "role", "email", "phone")
        ):
            return None
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO customer_data_suggestions
                    (customer_id, project_id, field_name, suggested_value,
                     source_path, excerpt, rule, confidence, suggestion_type,
                     contact_name, contact_role, contact_email, contact_phone,
                     fingerprint)
                VALUES (?, ?, 'contact', ?, ?, ?, ?, ?, 'contact', ?, ?, ?, ?, ?)
                """,
                (
                    customer_id, project_id, contact.name, source_path, excerpt,
                    rule, max(0.0, min(1.0, float(confidence))), contact.name,
                    contact.role, contact.email, contact.phone, fingerprint,
                ),
            )
        return self.get_data_suggestion(int(cursor.lastrowid))

    def _find_matching_contact(
        self, customer_id: int, contact: Contact
    ) -> sqlite3.Row | None:
        rows = self.connection.execute(
            "SELECT id, name, role, email, phone FROM contacts WHERE customer_id=?",
            (customer_id,),
        ).fetchall()
        name_key = normalize_identity(contact.name)
        email_key = contact.email.strip().casefold()
        phone_key = "".join(character for character in contact.phone if character.isdigit())
        return next((
            row for row in rows
            if normalize_identity(str(row["name"])) == name_key
            or (
                email_key and str(row["email"] or "").strip().casefold() == email_key
            )
            or (
                phone_key
                and "".join(
                    character for character in str(row["phone"] or "")
                    if character.isdigit()
                ) == phone_key
            )
        ), None)

    def apply_contact_scan_candidate(
        self,
        customer_id: int,
        candidate: RecognitionCandidate,
    ) -> ContactScanStats:
        """Apply contact-only extraction results without touching customer identity."""
        customer = self.get(customer_id)
        if customer is None:
            raise ValueError("Der ausgewählte Kunde existiert nicht mehr.")
        stats = ContactScanStats(scanned_projects=len(candidate.folder_paths))
        supported = {
            "contact_name", "email", "phone", "street", "postal_code", "city",
        }
        found = {
            (item.field_name, item.normalized_value)
            for item in candidate.evidence
            if item.field_name in supported
        }
        stats.found_fields = len(found)
        contact_emails = {
            contact.email.strip().casefold()
            for contact in candidate.contacts if contact.email.strip()
        }
        contact_phones = {
            "".join(character for character in contact.phone if character.isdigit())
            for contact in candidate.contacts if contact.phone.strip()
        }

        automatic_by_field = {}
        for evidence in candidate.evidence:
            if evidence.field_name not in supported or not evidence.automatic:
                continue
            if (
                evidence.field_name == "email"
                and evidence.value.strip().casefold() in contact_emails
            ) or (
                evidence.field_name == "phone"
                and "".join(
                    character for character in evidence.value if character.isdigit()
                ) in contact_phones
            ):
                continue
            current = automatic_by_field.get(evidence.field_name)
            if current is None or evidence.confidence > current.confidence:
                automatic_by_field[evidence.field_name] = evidence

        with self.connection:
            for field_name, evidence in automatic_by_field.items():
                if field_name == "contact_name":
                    continue
                current_value = str(getattr(customer, field_name) or "").strip()
                if not current_value:
                    self.connection.execute(
                        f"UPDATE customers SET {field_name}=?, "
                        "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (evidence.value, customer_id),
                    )
                    self.connection.execute(
                        """
                        INSERT OR IGNORE INTO automatic_field_sources
                            (owner_type, owner_id, field_name, value, normalized_value,
                             source_path, confidence)
                        VALUES ('customer', ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            customer_id, field_name, evidence.value,
                            evidence.normalized_value, evidence.source_path,
                            evidence.confidence,
                        ),
                    )
                    setattr(customer, field_name, evidence.value)
                    stats.applied_fields += 1

        project_ids = {
            project.folder_path: project.id
            for project in self.list_projects_for_customer(customer_id)
        }
        suggested_keys: set[tuple[str, str]] = set()
        for contact in candidate.contacts:
            matches = [
                item for item in candidate.evidence
                if item.field_name == "contact_name"
                and normalize_identity(item.value) == normalize_identity(contact.name)
            ]
            evidence = max(matches, key=lambda item: item.confidence, default=None)
            source_path = evidence.source_path if evidence else ""
            project_id = next((
                project_id for folder, project_id in project_ids.items()
                if source_path == folder or source_path.startswith(f"{folder}{os.sep}")
            ), None)
            self.apply_contact_suggestion(
                customer_id, project_id, contact, source_path,
                evidence.excerpt if evidence else "",
                evidence.rule if evidence else "Erkannter Ansprechpartner",
                evidence.confidence if evidence else 0.0,
            )
        for evidence in sorted(
            candidate.evidence, key=lambda item: item.confidence, reverse=True
        ):
            if evidence.field_name not in supported:
                continue
            if evidence.field_name == "contact_name":
                continue
            if (
                evidence.field_name == "email"
                and evidence.value.strip().casefold() in contact_emails
            ) or (
                evidence.field_name == "phone"
                and "".join(
                    character for character in evidence.value if character.isdigit()
                ) in contact_phones
            ):
                continue
            suggestion_key = (evidence.field_name, evidence.normalized_value)
            if suggestion_key in suggested_keys:
                continue
            suggested_keys.add(suggestion_key)
            if evidence.field_name == "contact_name":
                existing_name = self.connection.execute(
                    "SELECT 1 FROM contacts WHERE customer_id=? AND name=? COLLATE NOCASE",
                    (customer_id, evidence.value),
                ).fetchone()
                if existing_name is not None:
                    continue
            else:
                current_value = str(getattr(customer, evidence.field_name) or "").strip()
                if normalize_identity(current_value) == normalize_identity(evidence.value):
                    continue
            project_id = next((
                project_id for folder, project_id in project_ids.items()
                if evidence.source_path == folder
                or evidence.source_path.startswith(f"{folder}{os.sep}")
            ), None)
            self.apply_project_suggestion(
                customer_id, project_id, evidence.field_name, evidence.value,
                evidence.source_path, evidence.excerpt, evidence.rule,
                evidence.confidence,
            )
        stats.pending_fields = len(self.list_data_suggestions(customer_id))
        return stats

    def get_data_suggestion(self, suggestion_id: int) -> CustomerDataSuggestion | None:
        row = self.connection.execute(
            "SELECT * FROM customer_data_suggestions WHERE id = ?",
            (suggestion_id,),
        ).fetchone()
        return self._hydrate_data_suggestion(row) if row is not None else None

    def list_data_suggestions(
        self,
        customer_id: int,
        status: str = "pending",
    ) -> list[CustomerDataSuggestion]:
        rows = self.connection.execute(
            """
            SELECT * FROM customer_data_suggestions
            WHERE customer_id = ? AND status = ?
            ORDER BY created_at, id
            """,
            (customer_id, status),
        ).fetchall()
        return [self._hydrate_data_suggestion(row) for row in rows]

    def resolve_data_suggestion(
        self,
        suggestion_id: int,
        accept: bool,
        accepted_value: str | None = None,
    ) -> Customer:
        suggestion = self.get_data_suggestion(suggestion_id)
        if suggestion is None or suggestion.status != "pending":
            raise ValueError("Der Vorschlag ist nicht mehr offen.")
        allowed = {
            "company", "contact", "contact_name", "email", "phone",
            "street", "postal_code", "city", "entity_type",
        }
        if suggestion.field_name not in allowed:
            raise ValueError("Dieses vorgeschlagene Feld wird nicht unterstützt.")
        resolved_value = str(
            accepted_value
            if accepted_value is not None
            else suggestion.suggested_value
        ).strip()
        if suggestion.field_name == "entity_type" and resolved_value not in {
            "Privatperson",
            "Unternehmen",
            "Organisation",
        }:
            raise ValueError("Der ausgewählte Kundentyp ist ungültig.")
        status = "accepted" if accept else "rejected"
        with self.connection:
            if accept:
                if suggestion.is_contact:
                    self._accept_contact_suggestion(
                        int(suggestion.customer_id), suggestion.contact
                    )
                elif suggestion.field_name == "contact_name":
                    self._accept_contact_name(
                        int(suggestion.customer_id), suggestion.suggested_value
                    )
                else:
                    self.connection.execute(
                        f"UPDATE customers SET {suggestion.field_name} = ?, "
                        "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (resolved_value, suggestion.customer_id),
                    )
                    self.connection.execute(
                        "DELETE FROM automatic_field_sources "
                        "WHERE owner_type='customer' AND owner_id=? AND field_name=?",
                        (suggestion.customer_id, suggestion.field_name),
                    )
            self.connection.execute(
                "UPDATE customer_data_suggestions SET status=? WHERE id=?",
                (status, suggestion_id),
            )
        customer = self.get(int(suggestion.customer_id))
        if customer is None:
            raise ValueError("Der zugehörige Kunde existiert nicht mehr.")
        return customer

    def _accept_contact_suggestion(self, customer_id: int, contact: Contact):
        existing = self._find_matching_contact(customer_id, contact)
        if existing is None:
            self.connection.execute(
                """
                INSERT INTO contacts (customer_id, name, role, email, phone)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    customer_id, contact.name.strip(), contact.role.strip(),
                    contact.email.strip(), contact.phone.strip(),
                ),
            )
            return
        updates: dict[str, str] = {}
        for field in ("name", "role", "email", "phone"):
            current = str(existing[field] or "").strip()
            proposed = str(getattr(contact, field) or "").strip()
            if proposed and not current:
                updates[field] = proposed
        if updates:
            assignments = ", ".join(f"{field}=?" for field in updates)
            self.connection.execute(
                f"UPDATE contacts SET {assignments} WHERE id=?",
                (*updates.values(), int(existing["id"])),
            )

    def _project_from_root(
        self,
        project_root: ProjectRoot | dict,
    ) -> CustomerProject:
        if isinstance(project_root, ProjectRoot):
            return CustomerProject(
                service_type=project_root.service_type,
                folder_path=project_root.path,
                project_label=project_root.customer_label,
                project_city=project_root.city,
                year=project_root.year,
                source="folder",
            )
        folder_path = str(project_root.get("path") or project_root.get("folder_path") or "")
        return CustomerProject(
            service_type=str(project_root.get("service_type") or "Unbekannt"),
            folder_path=folder_path,
            project_label=str(
                project_root.get("customer_label")
                or project_root.get("project_label")
                or Path(folder_path).name
            ),
            project_city=str(project_root.get("city") or project_root.get("project_city") or ""),
            year=(
                int(project_root["year"])
                if str(project_root.get("year") or "").strip().isdigit()
                else None
            ),
            source=str(project_root.get("source") or "folder"),
        )

    def _infer_project_from_folder(
        self,
        folder_path: str,
        service_type: str = "",
    ) -> CustomerProject:
        normalized = self._folder_key(folder_path)
        path = Path(normalized)
        service = service_type.strip() or "Unbekannt"
        year = None
        project_city = ""
        project_label = path.name if normalized else ""
        parts = path.parts
        for index, part in enumerate(parts):
            if part.isdigit() and len(part) == 4:
                year = int(part)
                if index > 0 and not service_type.strip():
                    service = parts[index - 1]
                if index + 1 < len(parts):
                    project_label = parts[index + 1]
                break
        if "," in project_label:
            _, city = project_label.split(",", 1)
            project_city = city.strip()
        return CustomerProject(
            service_type=service,
            folder_path=normalized,
            project_label=project_label,
            project_city=project_city,
            year=year,
            source="folder",
        )

    def _candidate_project(
        self,
        candidate: RecognitionCandidate,
        folder_path: str,
        index: int,
    ) -> CustomerProject:
        service = (
            candidate.service_types[min(index, len(candidate.service_types) - 1)]
            if candidate.service_types
            else ""
        )
        project = self._infer_project_from_folder(folder_path, service)
        if project.year is None and candidate.years:
            project.year = candidate.years[min(index, len(candidate.years) - 1)]
        if not project.project_label:
            project.project_label = Path(folder_path).name
        return project

    def _hydrate_service_type(self, row: sqlite3.Row) -> ServiceType:
        return ServiceType(
            id=int(row["id"]),
            name=str(row["name"]),
            normalized_name=str(row["normalized_name"]),
        )

    def _hydrate_project(self, row: sqlite3.Row) -> CustomerProject:
        return CustomerProject(
            id=int(row["id"]),
            customer_id=int(row["customer_id"]),
            service_type_id=int(row["service_type_id"]),
            service_type=str(row["service_type"]),
            folder_path=str(row["folder_path"]),
            folder_key=str(row["folder_key"]),
            project_label=str(row["project_label"] or ""),
            project_city=str(row["project_city"] or ""),
            year=int(row["year"]) if row["year"] is not None else None,
            source=str(row["source"] or "folder"),
        )

    def _hydrate_data_suggestion(self, row: sqlite3.Row) -> CustomerDataSuggestion:
        return CustomerDataSuggestion(
            id=int(row["id"]),
            customer_id=int(row["customer_id"]),
            project_id=int(row["project_id"]) if row["project_id"] is not None else None,
            field_name=str(row["field_name"]),
            suggested_value=str(row["suggested_value"]),
            source_path=str(row["source_path"] or ""),
            excerpt=str(row["excerpt"] or ""),
            rule=str(row["rule"] or ""),
            confidence=float(row["confidence"] or 0.0),
            status=str(row["status"] or "pending"),
            suggestion_type=str(row["suggestion_type"] or "field"),
            contact_name=str(row["contact_name"] or ""),
            contact_role=str(row["contact_role"] or ""),
            contact_email=str(row["contact_email"] or ""),
            contact_phone=str(row["contact_phone"] or ""),
            fingerprint=str(row["fingerprint"] or ""),
        )

    def _migrate_data_suggestions(self):
        columns = {
            str(row["name"])
            for row in self.connection.execute(
                "PRAGMA table_info(customer_data_suggestions)"
            ).fetchall()
        }
        additions = {
            "excerpt": "TEXT NOT NULL DEFAULT ''",
            "rule": "TEXT NOT NULL DEFAULT ''",
            "confidence": "REAL NOT NULL DEFAULT 0",
            "suggestion_type": "TEXT NOT NULL DEFAULT 'field'",
            "contact_name": "TEXT NOT NULL DEFAULT ''",
            "contact_role": "TEXT NOT NULL DEFAULT ''",
            "contact_email": "TEXT NOT NULL DEFAULT ''",
            "contact_phone": "TEXT NOT NULL DEFAULT ''",
            "fingerprint": "TEXT NOT NULL DEFAULT ''",
        }
        for name, definition in additions.items():
            if name not in columns:
                self.connection.execute(
                    f"ALTER TABLE customer_data_suggestions ADD COLUMN {name} {definition}"
                )
        rows = self.connection.execute(
            """
            SELECT id, field_name, suggested_value, suggestion_type,
                   contact_name, contact_role, contact_email, contact_phone,
                   fingerprint
            FROM customer_data_suggestions
            """
        ).fetchall()
        for row in rows:
            field_name = str(row["field_name"] or "")
            if field_name == "contact_name" and str(row["suggestion_type"]) == "field":
                contact = Contact(name=str(row["suggested_value"] or "").strip())
                self.connection.execute(
                    """
                    UPDATE customer_data_suggestions
                    SET field_name='contact', suggestion_type='contact',
                        contact_name=?, fingerprint=?
                    WHERE id=?
                    """,
                    (
                        contact.name, self._contact_fingerprint(contact),
                        int(row["id"]),
                    ),
                )
            elif not str(row["fingerprint"] or ""):
                if str(row["suggestion_type"]) == "contact":
                    contact = Contact(
                        str(row["contact_name"] or ""),
                        str(row["contact_role"] or ""),
                        str(row["contact_email"] or ""),
                        str(row["contact_phone"] or ""),
                    )
                    fingerprint = self._contact_fingerprint(contact)
                else:
                    fingerprint = self._field_fingerprint(
                        field_name, str(row["suggested_value"] or "")
                    )
                self.connection.execute(
                    "UPDATE customer_data_suggestions SET fingerprint=? WHERE id=?",
                    (fingerprint, int(row["id"])),
                )

    def _sync_legacy_project_links(self, customer_id: int):
        projects = self.list_projects_for_customer(customer_id)
        folder_paths = [project.folder_path for project in projects if project.folder_path]
        services = [project.service_type for project in projects if project.service_type]
        if folder_paths:
            primary = folder_paths[0]
            self.connection.execute(
                """
                UPDATE customers
                SET folder_path = CASE
                    WHEN folder_path LIKE 'customer://%' THEN ?
                    ELSE folder_path
                END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (primary, customer_id),
            )
        self._replace_folders(customer_id, folder_paths)
        self._replace_services(customer_id, services)

    def _migrate_legacy_projects(self):
        rows = self.connection.execute(
            "SELECT id, folder_path FROM customers"
        ).fetchall()
        for row in rows:
            customer_id = int(row["id"])
            folders = [
                str(item[0])
                for item in self.connection.execute(
                    "SELECT folder_path FROM customer_folders WHERE customer_id = ?",
                    (customer_id,),
                ).fetchall()
            ]
            if not folders and str(row["folder_path"] or "").strip():
                folders = [str(row["folder_path"])]
            services = [
                str(item[0])
                for item in self.connection.execute(
                    "SELECT name FROM customer_services WHERE customer_id = ?",
                    (customer_id,),
                ).fetchall()
            ]
            for index, folder in enumerate(folders):
                if not folder.strip() or folder.startswith("customer://"):
                    continue
                existing = self.find_project_by_folder(folder, include_ancestors=False)
                if existing is not None:
                    continue
                service = services[min(index, len(services) - 1)] if services else ""
                project = self._infer_project_from_folder(folder, service)
                project.customer_id = customer_id
                try:
                    self.upsert_project_from_root(project)
                except ValueError:
                    continue

    def search(
        self,
        query: str,
        limit: int = 25,
        sort_order: SearchSort = SearchSort.RELEVANCE,
    ) -> list[Customer]:
        from app.core.fuzzy_search import (
            SearchField,
            fuzzy_record_score,
            normalize_search_text,
        )
        rows = self.connection.execute(
            """
            SELECT customers.*,
                   GROUP_CONCAT(DISTINCT customer_projects.project_label) AS project_labels,
                   GROUP_CONCAT(DISTINCT customer_projects.project_city) AS project_cities,
                   GROUP_CONCAT(DISTINCT customer_projects.folder_path) AS project_paths,
                   MAX(customer_projects.year) AS latest_project_year
            FROM customers
            LEFT JOIN customer_projects ON customer_projects.customer_id = customers.id
            GROUP BY customers.id
            """,
        ).fetchall()
        ranked: list[tuple[float, str, int, int]] = []
        for row in rows:
            path_text = normalize_search_text(row["project_paths"])
            identity_values = [
                normalize_search_text(value)
                for value in (row["display_name"], row["city"])
                if str(value or "").strip()
            ]
            if not path_text or not any(value in path_text for value in identity_values):
                continue
            score = fuzzy_record_score(query, [
                SearchField(row["display_name"], 1.12),
                SearchField(row["city"], 1.08),
                SearchField(row["project_labels"], 1.12),
                SearchField(row["project_cities"], 1.08),
            ])
            if score is not None:
                ranked.append((
                    score,
                    str(row["display_name"]).casefold(),
                    int(row["latest_project_year"] or 0),
                    int(row["id"]),
                ))
        if sort_order == SearchSort.DATE:
            ranked.sort(key=lambda item: (-item[2], item[1], item[3]))
        elif sort_order == SearchSort.ALPHABETICAL:
            ranked.sort(key=lambda item: (item[1], item[3]))
        else:
            ranked.sort(key=lambda item: (-item[0], item[1], item[3]))
        return [
            customer
            for _, _, _, customer_id in ranked[:max(0, limit)]
            if (customer := self.get(customer_id)) is not None
        ]

    def add_folder_to_customer(
        self,
        customer_id: int,
        folder_path: str,
        service_type: str = "",
    ) -> Customer:
        """Assign one physical folder to an existing customer."""
        if not folder_path.strip():
            raise ValueError("Bitte einen gültigen Ordner auswählen.")
        normalized_folder = self._folder_key(folder_path)
        target = self.get(customer_id)
        if target is None:
            raise ValueError("Der ausgewählte Kunde wurde nicht gefunden.")

        assigned_customer = self.get_by_folder(normalized_folder)
        if assigned_customer is not None and assigned_customer.id != customer_id:
            raise ValueError(
                f"Der Ordner ist bereits dem Kunden "
                f"„{assigned_customer.display_name}“ zugeordnet."
            )

        cleaned_service = service_type.strip()
        try:
            with self.connection:
                primary_folder = target.folder_path
                if primary_folder.startswith("customer://"):
                    self.connection.execute(
                        """
                        DELETE FROM customer_folders
                        WHERE customer_id = ? AND folder_path = ?
                        """,
                        (customer_id, primary_folder),
                    )
                    self.connection.execute(
                        """
                        UPDATE customers
                        SET folder_path = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (normalized_folder, customer_id),
                    )
                else:
                    self.connection.execute(
                        """
                        UPDATE customers
                        SET updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (customer_id,),
                    )

                self.connection.execute(
                    f"""
                    DELETE FROM customer_folders
                    WHERE customer_id = ? AND {self._path_equals_sql("folder_path")}
                    """,
                    (customer_id, normalized_folder),
                )
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO customer_folders (customer_id, folder_path)
                    VALUES (?, ?)
                    """,
                    (customer_id, normalized_folder),
                )
                service_exists = (
                    self.connection.execute(
                        """
                        SELECT 1 FROM customer_services
                        WHERE customer_id = ? AND name = ? COLLATE NOCASE
                        """,
                        (customer_id, cleaned_service),
                    ).fetchone()
                    if cleaned_service
                    else None
                )
                if cleaned_service and service_exists is None:
                    self.connection.execute(
                        """
                        INSERT INTO customer_services (customer_id, name)
                        VALUES (?, ?)
                        """,
                        (customer_id, cleaned_service),
                    )
        except sqlite3.IntegrityError as error:
            raise ValueError(
                "Der Ordner konnte nicht eindeutig zugeordnet werden."
            ) from error

        project = self._infer_project_from_folder(normalized_folder, cleaned_service)
        project.customer_id = customer_id
        self.upsert_project_from_root(project)
        updated = self.get(customer_id)
        if updated is None:
            raise RuntimeError("Der aktualisierte Kunde konnte nicht geladen werden.")
        return updated

    def apply_recognition_candidate(
        self,
        candidate: RecognitionCandidate,
        customer_id: int | None = None,
    ) -> Customer:
        """Create or safely enrich one customer from an automatic candidate."""
        target = self.get(customer_id) if customer_id is not None else None
        has_complete_address = all((
            candidate.street.strip(),
            candidate.postal_code.strip(),
            candidate.city.strip(),
        ))
        if customer_id is not None and target is None:
            raise ValueError("Der ausgewählte Bestandskunde existiert nicht mehr.")

        existing_owner_ids: set[int] = set()
        for folder_path in candidate.folder_paths:
            existing_owner_ids.update(self.customer_ids_within_folder(folder_path))
            inherited_owner = self.get_by_folder(folder_path)
            if inherited_owner is not None and inherited_owner.id is not None:
                existing_owner_ids.add(inherited_owner.id)
        if target is None and existing_owner_ids:
            raise ValueError(
                "Mindestens ein Ordner ist bereits einem Bestandskunden zugeordnet."
            )
        if customer_id is not None and existing_owner_ids - {customer_id}:
            raise ValueError(
                "Mindestens ein Ordner ist bereits einem anderen Kunden zugeordnet."
            )

        if target is None:
            try:
                target = self.save(Customer(
                    display_name=candidate.display_name,
                    entity_type=candidate.entity_type or "Unternehmen",
                    company=candidate.display_name,
                    city=candidate.city if has_complete_address else "",
                    email=candidate.email,
                    phone=candidate.phone,
                    street=candidate.street if has_complete_address else "",
                    postal_code=(
                        candidate.postal_code if has_complete_address else ""
                    ),
                    contacts=[],
                    service_types=list(candidate.service_types),
                    folder_path=candidate.folder_paths[0] if candidate.folder_paths else "",
                    folder_paths=list(candidate.folder_paths),
                ), commit=False)
            except Exception:
                self.connection.rollback()
                raise
            customer_id = target.id
        if customer_id is None:
            raise RuntimeError("Der automatisch erzeugte Kunde besitzt keine ID.")

        normalized_folders = [self._folder_key(path) for path in candidate.folder_paths]
        pending_field_suggestions: list[tuple[str, str, str]] = []
        try:
            with self.connection:
                row = self.connection.execute(
                    "SELECT * FROM customers WHERE id = ?", (customer_id,)
                ).fetchone()
                if row is None:
                    raise ValueError("Der ausgewählte Kunde existiert nicht mehr.")

                updates = {
                    "company": candidate.display_name,
                    "email": candidate.email,
                    "phone": candidate.phone,
                }
                assignments = []
                values = []
                for column, value in updates.items():
                    cleaned_value = str(value or "").strip()
                    existing_value = str(row[column] or "").strip()
                    if cleaned_value and not existing_value:
                        assignments.append(f"{column} = ?")
                        values.append(cleaned_value)
                    elif (
                        cleaned_value
                        and existing_value
                        and normalize_identity(cleaned_value)
                        != normalize_identity(existing_value)
                    ):
                        pending_field_suggestions.append(
                            (column, cleaned_value, normalized_folders[0] if normalized_folders else "")
                        )
                candidate_address = (
                    candidate.street.strip(),
                    candidate.postal_code.strip(),
                    candidate.city.strip(),
                )
                existing_address = (
                    str(row["street"] or "").strip(),
                    str(row["postal_code"] or "").strip(),
                    str(row["city"] or "").strip(),
                )
                if has_complete_address:
                    if not any(existing_address):
                        for column, value in zip(
                            ("street", "postal_code", "city"),
                            candidate_address,
                        ):
                            assignments.append(f"{column} = ?")
                            values.append(value)
                    elif tuple(
                        normalize_identity(value) for value in candidate_address
                    ) != tuple(
                        normalize_identity(value) for value in existing_address
                    ):
                        for column, value, existing_value in zip(
                            ("street", "postal_code", "city"),
                            candidate_address,
                            existing_address,
                        ):
                            if normalize_identity(value) != normalize_identity(
                                existing_value
                            ):
                                pending_field_suggestions.append((
                                    column,
                                    value,
                                    normalized_folders[0]
                                    if normalized_folders
                                    else "",
                                ))
                if assignments:
                    self.connection.execute(
                        f"UPDATE customers SET {', '.join(assignments)}, "
                        "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (*values, customer_id),
                    )

                primary_folder = str(row["folder_path"] or "")
                primary_is_descendant = bool(
                    normalized_folders
                    and any(
                        self._folder_lookup_key(primary_folder).startswith(
                            f"{self._folder_lookup_key(root)}{os.sep}"
                        )
                        for root in normalized_folders
                    )
                )
                if (
                    primary_folder.startswith("customer://") or primary_is_descendant
                ) and normalized_folders:
                    self.connection.execute(
                        "DELETE FROM customer_folders WHERE customer_id=? AND folder_path=?",
                        (customer_id, primary_folder),
                    )
                    self.connection.execute(
                        "UPDATE customers SET folder_path=? WHERE id=?",
                        (normalized_folders[0], customer_id),
                    )

                for folder, normalized in zip(candidate.folder_paths, normalized_folders):
                    owner = self.get_by_folder(normalized, include_ancestors=False)
                    if owner is not None and owner.id != customer_id:
                        raise ValueError(
                            f"Der Ordner ist bereits „{owner.display_name}“ zugeordnet."
                        )
                    descendant_pattern = f"{normalized}{os.sep}%"
                    self.connection.execute(
                        f"DELETE FROM customer_folders "
                        f"WHERE customer_id=? AND {self._path_like_sql('folder_path')}",
                        (customer_id, descendant_pattern),
                    )
                    self.connection.execute(
                        f"DELETE FROM automatic_customer_sources "
                        f"WHERE customer_id=? AND {self._path_like_sql('folder_path')}",
                        (customer_id, descendant_pattern),
                    )
                    self.connection.execute(
                        f"""
                        DELETE FROM customer_folders
                        WHERE customer_id = ? AND {self._path_equals_sql("folder_path")}
                        """,
                        (customer_id, normalized),
                    )
                    self.connection.execute(
                        "INSERT OR IGNORE INTO customer_folders (customer_id, folder_path) "
                        "VALUES (?, ?)",
                        (customer_id, normalized),
                    )
                    self.connection.execute(
                        """
                        INSERT OR REPLACE INTO automatic_customer_sources
                        (folder_path, customer_id, recognition_key, last_seen)
                        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                        """,
                        (normalized, customer_id, candidate.recognition_key),
                    )

                for service in candidate.service_types:
                    exists = self.connection.execute(
                        "SELECT 1 FROM customer_services "
                        "WHERE customer_id=? AND name=? COLLATE NOCASE",
                        (customer_id, service),
                    ).fetchone()
                    if exists is None and service.strip():
                        self.connection.execute(
                            "INSERT INTO customer_services (customer_id, name) VALUES (?, ?)",
                            (customer_id, service.strip()),
                        )

        except sqlite3.IntegrityError as error:
            raise ValueError("Die automatische Zuordnung ist nicht eindeutig.") from error

        project_ids: list[int] = []
        for index, folder in enumerate(normalized_folders):
            project = self._candidate_project(candidate, folder, index)
            project.customer_id = customer_id
            saved_project = self.upsert_project_from_root(project)
            if saved_project is not None and saved_project.id is not None:
                project_ids.append(int(saved_project.id))
        suggestion_project_id = project_ids[0] if project_ids else None
        for contact in candidate.contacts:
            matches = [
                evidence for evidence in candidate.evidence
                if evidence.field_name == "contact_name"
                and normalize_identity(evidence.value) == normalize_identity(contact.name)
            ]
            evidence = max(matches, key=lambda item: item.confidence, default=None)
            self.apply_contact_suggestion(
                int(customer_id),
                suggestion_project_id,
                contact,
                evidence.source_path if evidence else "",
                evidence.excerpt if evidence else "",
                evidence.rule if evidence else "Erkannter Ansprechpartner",
                evidence.confidence if evidence else 0.0,
            )
        for field, value, source_path in pending_field_suggestions:
            self.apply_project_suggestion(
                customer_id,
                suggestion_project_id,
                field,
                value,
                source_path,
            )
        self._record_candidate_provenance(int(customer_id), candidate)
        self._record_weak_evidence_suggestions(
            int(customer_id), suggestion_project_id, candidate
        )
        self.connection.commit()

        updated = self.get(customer_id)
        if updated is None:
            raise RuntimeError("Der automatisch aktualisierte Kunde konnte nicht geladen werden.")
        return updated

    def _upsert_automatic_contact(
        self,
        customer_id: int,
        contact: Contact,
        candidate: RecognitionCandidate,
    ) -> int:
        name = contact.name.strip()
        if not name:
            return 0
        rows = self.connection.execute(
            "SELECT id, name, role, email, phone FROM contacts WHERE customer_id=?",
            (customer_id,),
        ).fetchall()
        automatic_ids = {
            int(row[0])
            for row in self.connection.execute(
                """
                SELECT owner_id FROM automatic_field_sources
                WHERE owner_type='contact' AND field_name='name'
                  AND owner_id IN (SELECT id FROM contacts WHERE customer_id=?)
                """,
                (customer_id,),
            ).fetchall()
        }
        normalized_name = normalize_identity(name)
        exact = next((
            row for row in rows
            if normalize_identity(str(row["name"])) == normalized_name
        ), None)
        if exact is not None:
            contact_id = int(exact["id"])
            if contact_id not in automatic_ids:
                return 0
            email = str(exact["email"] or "") or contact.email.strip()
            phone = str(exact["phone"] or "") or contact.phone.strip()
            changed = int(email != str(exact["email"] or "")) + int(
                phone != str(exact["phone"] or "")
            )
            if changed:
                self.connection.execute(
                    "UPDATE contacts SET email=?, phone=? WHERE id=?",
                    (email, phone, contact_id),
                )
            self._record_contact_provenance(
                contact_id,
                Contact(name, str(exact["role"] or ""), email, phone),
                candidate,
            )
            return changed

        words = normalized_name.split()
        fallback = next((
            row for row in rows
            if int(row["id"]) in automatic_ids
            and len(normalize_identity(str(row["name"])).split()) == 1
            and len(words) >= 2
            and normalize_identity(str(row["name"])) == words[-1]
        ), None)
        if fallback is not None:
            contact_id = int(fallback["id"])
            email = str(fallback["email"] or "") or contact.email.strip()
            phone = str(fallback["phone"] or "") or contact.phone.strip()
            self.connection.execute(
                "UPDATE contacts SET name=?, email=?, phone=? WHERE id=?",
                (name, email, phone, contact_id),
            )
            self.connection.execute(
                "DELETE FROM automatic_field_sources "
                "WHERE owner_type='contact' AND owner_id=? AND field_name='name'",
                (contact_id,),
            )
            self._record_contact_provenance(
                contact_id,
                Contact(name, str(fallback["role"] or ""), email, phone),
                candidate,
            )
            return 1 + int(bool(contact.email.strip())) + int(bool(contact.phone.strip()))

        cursor = self.connection.execute(
            """
            INSERT INTO contacts (customer_id, name, role, email, phone)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                customer_id, name, contact.role.strip(),
                contact.email.strip(), contact.phone.strip(),
            ),
        )
        self._record_contact_provenance(int(cursor.lastrowid), contact, candidate)
        return sum(bool(value.strip()) for value in (
            contact.name, contact.email, contact.phone,
        ))

    def _accept_contact_name(self, customer_id: int, value: str):
        name = value.strip()
        normalized_name = normalize_identity(name)
        rows = self.connection.execute(
            "SELECT id, name FROM contacts WHERE customer_id=? ORDER BY id",
            (customer_id,),
        ).fetchall()
        if any(normalize_identity(str(row["name"])) == normalized_name for row in rows):
            return
        words = normalized_name.split()
        automatic_ids = {
            int(row[0])
            for row in self.connection.execute(
                """
                SELECT owner_id FROM automatic_field_sources
                WHERE owner_type='contact' AND field_name='name'
                  AND owner_id IN (SELECT id FROM contacts WHERE customer_id=?)
                """,
                (customer_id,),
            ).fetchall()
        }
        fallback = next((
            row for row in rows
            if int(row["id"]) in automatic_ids
            and len(normalize_identity(str(row["name"])).split()) == 1
            and len(words) >= 2
            and normalize_identity(str(row["name"])) == words[-1]
        ), None)
        if fallback is None:
            self.connection.execute(
                "INSERT INTO contacts (customer_id, name) VALUES (?, ?)",
                (customer_id, name),
            )
            return
        contact_id = int(fallback["id"])
        self.connection.execute(
            "UPDATE contacts SET name=? WHERE id=?", (name, contact_id)
        )
        self.connection.execute(
            "DELETE FROM automatic_field_sources "
            "WHERE owner_type='contact' AND owner_id=?",
            (contact_id,),
        )

    def _record_candidate_provenance(
        self, customer_id: int, candidate: RecognitionCandidate
    ):
        allowed = {
            "company", "entity_type", "email", "phone",
            "street", "postal_code", "city",
        }
        for evidence in candidate.evidence:
            if not evidence.automatic or evidence.field_name not in allowed:
                continue
            current = self.connection.execute(
                f"SELECT {evidence.field_name} FROM customers WHERE id=?",
                (customer_id,),
            ).fetchone()
            if current is None or normalize_identity(str(current[0] or "")) != normalize_identity(
                evidence.value
            ):
                continue
            self.connection.execute(
                """
                INSERT OR IGNORE INTO automatic_field_sources
                    (owner_type, owner_id, field_name, value, normalized_value,
                     source_path, confidence)
                VALUES ('customer', ?, ?, ?, ?, ?, ?)
                """,
                (
                    customer_id, evidence.field_name, evidence.value,
                    evidence.normalized_value, evidence.source_path,
                    evidence.confidence,
                ),
            )
        contact_rows = self.connection.execute(
            "SELECT id, name, role, email, phone FROM contacts WHERE customer_id=?",
            (customer_id,),
        ).fetchall()
        for row in contact_rows:
            self._record_contact_provenance(
                int(row["id"]),
                Contact(row["name"], row["role"], row["email"], row["phone"]),
                candidate,
            )

    def _record_contact_provenance(
        self, contact_id: int, contact: Contact, candidate: RecognitionCandidate
    ):
        evidence_fields = {
            "name": "contact_name", "email": "email", "phone": "phone",
        }
        for field_name, evidence_field in evidence_fields.items():
            value = str(getattr(contact, field_name) or "").strip()
            if not value:
                continue
            match = next((
                item for item in candidate.evidence
                if item.automatic
                and item.field_name == evidence_field
                and normalize_identity(item.value) == normalize_identity(value)
            ), None)
            if match is None:
                continue
            self.connection.execute(
                """
                INSERT OR IGNORE INTO automatic_field_sources
                    (owner_type, owner_id, field_name, value, normalized_value,
                     source_path, confidence)
                VALUES ('contact', ?, ?, ?, ?, ?, ?)
                """,
                (
                    contact_id, field_name, value, match.normalized_value,
                    match.source_path, match.confidence,
                ),
            )

    def _record_weak_evidence_suggestions(
        self,
        customer_id: int,
        project_id: int | None,
        candidate: RecognitionCandidate,
    ):
        allowed = {
            "contact_name", "email", "phone", "street", "postal_code", "city",
            "entity_type",
        }
        seen: set[tuple[str, str]] = set()
        for evidence in candidate.evidence:
            key = (evidence.field_name, evidence.normalized_value)
            if evidence.automatic or evidence.field_name not in allowed or key in seen:
                continue
            seen.add(key)
            if evidence.field_name == "contact_name":
                self.apply_contact_suggestion(
                    customer_id,
                    project_id,
                    Contact(name=evidence.value),
                    evidence.source_path,
                    evidence.excerpt,
                    evidence.rule,
                    evidence.confidence,
                )
                continue
            self.apply_project_suggestion(
                customer_id, project_id, evidence.field_name,
                evidence.value, evidence.source_path, evidence.excerpt,
                evidence.rule, evidence.confidence,
            )

    def record_extraction_observations(
        self, candidates: list[RecognitionCandidate], threshold: int = 5
    ):
        for candidate in candidates:
            for evidence in candidate.evidence:
                value_type = {
                    "contact_name": "name",
                    "street": "address",
                }.get(evidence.field_name, evidence.field_name)
                if value_type not in {"name", "email", "phone", "address"}:
                    continue
                for folder_path in candidate.folder_paths:
                    self.connection.execute(
                        """
                        INSERT OR REPLACE INTO extracted_value_observations
                            (value_type, normalized_value, value, folder_path, source_path)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            value_type, evidence.normalized_value, evidence.value,
                            folder_path, evidence.source_path,
                        ),
                    )
        rows = self.connection.execute(
            """
            SELECT value_type, normalized_value, MIN(value) AS value,
                   COUNT(DISTINCT folder_path) AS folder_count,
                   GROUP_CONCAT(DISTINCT source_path) AS sources
            FROM extracted_value_observations
            GROUP BY value_type, normalized_value
            HAVING COUNT(DISTINCT folder_path) >= ?
            """,
            (max(2, int(threshold)),),
        ).fetchall()
        qualifying = {
            (str(row["value_type"]), str(row["normalized_value"])) for row in rows
        }
        for existing in self.connection.execute(
            "SELECT id, value_type, normalized_value FROM blacklist_suggestions "
            "WHERE status='pending'"
        ).fetchall():
            key = (str(existing["value_type"]), str(existing["normalized_value"]))
            if key not in qualifying:
                self.connection.execute(
                    "DELETE FROM blacklist_suggestions WHERE id=?",
                    (int(existing["id"]),),
                )
        for row in rows:
            examples = [
                value for value in str(row["sources"] or "").split(",") if value
            ][:3]
            self.connection.execute(
                """
                INSERT INTO blacklist_suggestions
                    (value_type, normalized_value, value, folder_count, example_sources)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(value_type, normalized_value) DO UPDATE SET
                    value=excluded.value,
                    folder_count=excluded.folder_count,
                    example_sources=excluded.example_sources,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    row["value_type"], row["normalized_value"], row["value"],
                    row["folder_count"], json.dumps(examples, ensure_ascii=False),
                ),
            )
        self.connection.commit()

    def list_blacklist_suggestions(self, status: str = "pending") -> list[dict]:
        rows = self.connection.execute(
            "SELECT * FROM blacklist_suggestions WHERE status=? "
            "ORDER BY folder_count DESC, value COLLATE NOCASE",
            (status,),
        ).fetchall()
        return [
            {
                **dict(row),
                "example_sources": json.loads(str(row["example_sources"] or "[]")),
            }
            for row in rows
        ]

    def set_blacklist_suggestion_status(self, suggestion_id: int, status: str):
        if status not in {"pending", "confirmed", "dismissed"}:
            raise ValueError("Ungültiger Status für Blocklistenvorschlag.")
        self.connection.execute(
            "UPDATE blacklist_suggestions SET status=?, updated_at=CURRENT_TIMESTAMP "
            "WHERE id=?",
            (status, suggestion_id),
        )
        self.connection.commit()

    def cleanup_automatic_blacklisted_values(self, options) -> dict[str, int]:
        from app.services.customer_recognition import RecognitionBlacklist

        blacklist = RecognitionBlacklist(options)
        removed_fields = 0
        removed_contacts = 0
        sources = self.connection.execute(
            "SELECT * FROM automatic_field_sources ORDER BY owner_type, owner_id"
        ).fetchall()
        with self.connection:
            for source in sources:
                field = str(source["field_name"])
                value = str(source["value"])
                if field == "email":
                    blocked = blacklist.email_blocked(value)
                elif field == "phone":
                    blocked = blacklist.phone_blocked(value)
                elif field == "name":
                    blocked = blacklist.name_blocked(value)
                elif field in {"street", "postal_code", "city"}:
                    blocked = blacklist.address_blocked(value)
                else:
                    blocked = False
                if not blocked:
                    continue
                owner_type = str(source["owner_type"])
                owner_id = int(source["owner_id"])
                if owner_type == "customer":
                    row = self.connection.execute(
                        f"SELECT {field} FROM customers WHERE id=?", (owner_id,)
                    ).fetchone()
                    if row is not None and normalize_identity(str(row[0] or "")) == normalize_identity(value):
                        self.connection.execute(
                            f"UPDATE customers SET {field}='', "
                            "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                            (owner_id,),
                        )
                        removed_fields += 1
                elif owner_type == "contact":
                    row = self.connection.execute(
                        f"SELECT {field} FROM contacts WHERE id=?", (owner_id,)
                    ).fetchone()
                    if row is not None and normalize_identity(str(row[0] or "")) == normalize_identity(value):
                        if field == "name":
                            self.connection.execute(
                                "DELETE FROM contacts WHERE id=?", (owner_id,)
                            )
                            removed_contacts += 1
                        else:
                            self.connection.execute(
                                f"UPDATE contacts SET {field}='' WHERE id=?", (owner_id,)
                            )
                            removed_fields += 1
                            empty = self.connection.execute(
                                "SELECT 1 FROM contacts WHERE id=? AND email='' AND phone=''",
                                (owner_id,),
                            ).fetchone()
                            if empty is not None:
                                self.connection.execute(
                                    "DELETE FROM contacts WHERE id=?", (owner_id,)
                                )
                                removed_contacts += 1
                self.connection.execute(
                    "DELETE FROM automatic_field_sources WHERE id=?",
                    (int(source["id"]),),
                )
        return {"fields": removed_fields, "contacts": removed_contacts}

    @staticmethod
    def _normalize_phone(value: str) -> str:
        prefix = "+" if value.strip().startswith("+") else ""
        return prefix + "".join(character for character in value if character.isdigit())

    def get_recognition_decision(self, signature: str) -> dict | None:
        row = self.connection.execute(
            "SELECT action, customer_id FROM recognition_decisions WHERE signature=?",
            (signature,),
        ).fetchone()
        return dict(row) if row is not None else None

    def has_previous_recognition_decision(
        self,
        recognition_key: str,
        current_signature: str,
    ) -> bool:
        row = self.connection.execute(
            """
            SELECT 1 FROM recognition_cases cases
            JOIN recognition_decisions decisions
              ON decisions.signature = cases.signature
            WHERE cases.recognition_key = ? AND cases.signature <> ?
            LIMIT 1
            """,
            (recognition_key, current_signature),
        ).fetchone()
        return row is not None

    def replace_pending_recognition_cases(
        self,
        candidates: list[RecognitionCandidate],
    ):
        with self.connection:
            self.connection.execute(
                "UPDATE recognition_cases SET status='stale', updated_at=CURRENT_TIMESTAMP "
                "WHERE status='pending'"
            )
            for candidate in candidates:
                self.connection.execute(
                    """
                    INSERT INTO recognition_cases
                    (signature, recognition_key, payload_json, reason, status)
                    VALUES (?, ?, ?, ?, 'pending')
                    ON CONFLICT(signature) DO UPDATE SET
                        payload_json=excluded.payload_json,
                        reason=excluded.reason,
                        status='pending',
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        candidate.signature,
                        candidate.recognition_key,
                        json.dumps(candidate.to_dict(), ensure_ascii=False),
                        candidate.reason,
                    ),
                )

    def list_pending_recognition_cases(self) -> list[RecognitionCandidate]:
        rows = self.connection.execute(
            "SELECT payload_json FROM recognition_cases "
            "WHERE status='pending' ORDER BY created_at, signature"
        ).fetchall()
        return [RecognitionCandidate.from_dict(json.loads(row[0])) for row in rows]

    def pending_recognition_count(self) -> int:
        return int(self.connection.execute(
            "SELECT COUNT(*) FROM recognition_cases WHERE status='pending'"
        ).fetchone()[0])

    def save_recognition_decision(
        self,
        signature: str,
        action: str,
        customer_id: int | None = None,
    ):
        with self.connection:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO recognition_decisions
                (signature, action, customer_id, decided_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (signature, action, customer_id),
            )
            self.connection.execute(
                "UPDATE recognition_cases SET status=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE signature=?",
                ("ignored" if action == "ignore" else "resolved", signature),
            )

    def record_recognition_run(self, stats: RecognitionStats):
        values = stats.to_dict()
        with self.connection:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO recognition_runs
                (id, detected, created, assigned, skipped, pending, error, finished_at)
                VALUES (1, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    values["detected"], values["created"], values["assigned"],
                    values["skipped"], values["pending"], values["error"],
                ),
            )

    def last_recognition_run(self) -> dict:
        row = self.connection.execute(
            "SELECT * FROM recognition_runs WHERE id=1"
        ).fetchone()
        return dict(row) if row is not None else {}

    def merge_duplicate_customers_by_name(self) -> dict[int, int]:
        """Merge exact normalized-name duplicates without dropping related data.

        The returned mapping contains ``absorbed_id -> survivor_id`` entries.  A
        merge log keeps the complete absorbed records for auditing and recovery.
        """
        grouped: dict[str, list[Customer]] = {}
        for customer in self.list_customers():
            key = normalize_identity(customer.display_name)
            if key:
                grouped.setdefault(key, []).append(customer)

        duplicate_groups = [group for group in grouped.values() if len(group) > 1]
        if not duplicate_groups:
            return {}

        merged_ids: dict[int, int] = {}

        def split_into_merge_clusters(group: list[Customer]) -> list[list[Customer]]:
            remaining = list(group)
            clusters: list[list[Customer]] = []
            while remaining:
                cluster = [remaining.pop(0)]
                changed = True
                while changed:
                    changed = False
                    for customer in list(remaining):
                        if any(self._is_auto_merge_match(customer, item) for item in cluster):
                            cluster.append(customer)
                            remaining.remove(customer)
                            changed = True
                clusters.append(cluster)
            return clusters

        with self.connection:
            for group in duplicate_groups:
                for cluster in split_into_merge_clusters(group):
                    if len(cluster) < 2:
                        continue
                    survivor = self._merge_customer_group(cluster)
                    if survivor.id is None:
                        continue
                    for customer in cluster:
                        if customer.id is not None and customer.id != survivor.id:
                            merged_ids[int(customer.id)] = int(survivor.id)
        return merged_ids

    def _merge_customer_group(
        self,
        customers: list[Customer],
        preferred_id: int | None = None,
    ) -> Customer:
        if not customers:
            raise ValueError("Es wurden keine Kunden zum Zusammenführen übergeben.")

        survivor = next(
            (customer for customer in customers if customer.id == preferred_id),
            None,
        )
        if survivor is None:
            survivor = max(customers, key=self._customer_data_score)
        if survivor.id is None:
            raise ValueError("Ein gespeicherter Kunde besitzt keine ID.")

        ordered = [survivor] + sorted(
            (customer for customer in customers if customer.id != survivor.id),
            key=lambda customer: customer.id or 0,
        )
        merged = self._combine_customer_data(ordered[0], ordered[1:])
        merged.id = int(survivor.id)
        absorbed = [
            customer for customer in customers
            if customer.id is not None and customer.id != survivor.id
        ]
        if not absorbed:
            return merged

        survivor_id = int(survivor.id)
        absorbed_ids = [int(customer.id) for customer in absorbed if customer.id is not None]
        all_ids = [survivor_id, *absorbed_ids]
        placeholders = ",".join("?" for _ in all_ids)

        for absorbed_id in absorbed_ids:
            self.connection.execute(
                "UPDATE automatic_customer_sources SET customer_id=? WHERE customer_id=?",
                (survivor_id, absorbed_id),
            )
            self.connection.execute(
                "UPDATE customer_projects SET customer_id=?, updated_at=CURRENT_TIMESTAMP WHERE customer_id=?",
                (survivor_id, absorbed_id),
            )
            self.connection.execute(
                "UPDATE recognition_decisions SET customer_id=? WHERE customer_id=?",
                (survivor_id, absorbed_id),
            )
            self.connection.execute(
                "UPDATE customer_journal_entries SET customer_id=? WHERE customer_id=?",
                (survivor_id, absorbed_id),
            )

        for table in (
            "contacts",
            "customer_services",
            "customer_folders",
            "notes",
            "customer_tags",
        ):
            self.connection.execute(
                f"DELETE FROM {table} WHERE customer_id IN ({placeholders})",
                all_ids,
            )
        self.connection.execute(
            f"DELETE FROM customers WHERE id IN ({','.join('?' for _ in absorbed_ids)})",
            absorbed_ids,
        )
        self.connection.execute(
            """
            UPDATE customers SET
                folder_path=?, display_name=?, entity_type=?, company=?, email=?,
                phone=?, street=?, postal_code=?, city=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                merged.folder_path,
                merged.display_name,
                merged.entity_type,
                merged.company,
                merged.email,
                merged.phone,
                merged.street,
                merged.postal_code,
                merged.city,
                survivor_id,
            ),
        )
        self._replace_contacts(survivor_id, merged.contacts)
        self._replace_services(survivor_id, merged.service_types)
        self._replace_folders(survivor_id, merged.folder_paths or [merged.folder_path])
        self._replace_notes(survivor_id, merged.notes)
        self._replace_tags(survivor_id, merged.tags)
        for index, folder in enumerate(merged.folder_paths):
            if not folder.strip() or folder.startswith("customer://"):
                continue
            service = (
                merged.service_types[min(index, len(merged.service_types) - 1)]
                if merged.service_types
                else ""
            )
            project = self._infer_project_from_folder(folder, service)
            project.customer_id = survivor_id
            try:
                self.upsert_project_from_root(project)
            except ValueError:
                continue
        self._sync_legacy_project_links(survivor_id)

        name_key = normalize_identity(merged.display_name)
        for customer in absorbed:
            self.connection.execute(
                """
                INSERT INTO customer_merge_log
                    (survivor_id, absorbed_id, normalized_name, snapshot_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    survivor_id,
                    int(customer.id),
                    name_key,
                    json.dumps(asdict(customer), ensure_ascii=False, sort_keys=True),
                ),
            )
        return merged

    @staticmethod
    def _customer_data_score(customer: Customer) -> tuple[int, int]:
        populated_fields = sum(bool(str(value or "").strip()) for value in (
            customer.company,
            customer.email,
            customer.phone,
            customer.street,
            customer.postal_code,
            customer.city,
        ))
        related_data = (
            len(customer.folder_paths)
            + len(customer.service_types)
            + len(customer.contacts)
            + len(customer.notes)
            + len(customer.tags)
        )
        # A lower ID wins a tie, keeping migrations deterministic.
        return populated_fields + related_data, -(customer.id or 0)

    @classmethod
    def _is_auto_merge_match(cls, left: Customer, right: Customer) -> bool:
        """Return True only for high-confidence automatic merge candidates."""
        if normalize_identity(left.display_name) != normalize_identity(right.display_name):
            return False

        left_city = normalize_identity(left.city)
        right_city = normalize_identity(right.city)
        if left_city and right_city and left_city != right_city:
            return False

        signals = 0
        left_company = normalize_identity(left.company)
        right_company = normalize_identity(right.company)
        if left_company and left_company == right_company:
            signals += 1
        if left.email.strip() and left.email.strip().casefold() == right.email.strip().casefold():
            signals += 1
        left_phone = cls._normalize_phone(left.phone)
        right_phone = cls._normalize_phone(right.phone)
        if left_phone and left_phone == right_phone:
            signals += 1
        left_address = normalize_identity(f"{left.street} {left.postal_code}")
        right_address = normalize_identity(f"{right.street} {right.postal_code}")
        if left_address and left_address == right_address:
            signals += 1

        if left_city and right_city:
            return True
        if left_city or right_city:
            return signals >= 1
        return signals >= 2

    @classmethod
    def _combine_customer_data(
        cls,
        primary: Customer,
        others: list[Customer],
    ) -> Customer:
        sources = [primary, *others]

        def first_value(field: str) -> str:
            return next(
                (
                    str(getattr(source, field) or "").strip()
                    for source in sources
                    if str(getattr(source, field) or "").strip()
                ),
                "",
            )

        def unique_strings(values, key_function):
            unique = {}
            for value in values:
                cleaned = str(value or "").strip()
                if cleaned:
                    unique.setdefault(key_function(cleaned), cleaned)
            return list(unique.values())

        folders = unique_strings(
            (folder for source in sources for folder in source.folder_paths),
            cls._folder_lookup_key,
        )
        physical_folders = [
            folder for folder in folders if not folder.startswith("customer://")
        ]
        if physical_folders:
            folders = physical_folders

        preferred_folder = str(primary.folder_path or "").strip()
        if preferred_folder.startswith("customer://") and physical_folders:
            preferred_folder = physical_folders[0]
        elif not preferred_folder:
            preferred_folder = folders[0] if folders else f"customer://{uuid.uuid4().hex}"

        contacts_by_key: dict[tuple[str, str, str], Contact] = {}
        for source in sources:
            for contact in source.contacts:
                key = (
                    normalize_identity(contact.name),
                    contact.email.strip().casefold(),
                    cls._normalize_phone(contact.phone),
                )
                if contact.name.strip():
                    contacts_by_key.setdefault(key, contact)

        return Customer(
            id=primary.id,
            folder_path=preferred_folder,
            folder_paths=folders,
            display_name=first_value("display_name"),
            entity_type=first_value("entity_type") or "Unternehmen",
            service_types=unique_strings(
                (value for source in sources for value in source.service_types),
                str.casefold,
            ),
            company=first_value("company"),
            email=first_value("email"),
            phone=first_value("phone"),
            street=first_value("street"),
            postal_code=first_value("postal_code"),
            city=first_value("city"),
            contacts=list(contacts_by_key.values()),
            notes=unique_strings(
                (value for source in sources for value in source.notes),
                lambda value: " ".join(value.casefold().split()),
            ),
            tags=unique_strings(
                (value for source in sources for value in source.tags),
                str.casefold,
            ),
        )

    def save(self, customer: Customer, commit: bool = True) -> Customer:
        same_name = self.find_by_name(customer.display_name)
        if customer.id is None and same_name:
            merge_target = next(
                (
                    existing
                    for existing in same_name
                    if self._is_auto_merge_match(existing, customer)
                ),
                None,
            )
            if merge_target is not None:
                customer = self._combine_customer_data(merge_target, [customer])
                customer.id = merge_target.id

        folder_paths = customer.folder_paths or ([customer.folder_path] if customer.folder_path else [])
        normalized_paths = []
        seen_paths = set()
        for folder in folder_paths:
            normalized = self._folder_key(folder)
            lookup_key = self._folder_lookup_key(normalized)
            if lookup_key not in seen_paths:
                normalized_paths.append(normalized)
                seen_paths.add(lookup_key)

        primary_folder = customer.folder_path.strip()
        if primary_folder:
            primary_folder = self._folder_key(primary_folder)
        elif normalized_paths:
            primary_folder = normalized_paths[0]
        else:
            primary_folder = f"customer://{uuid.uuid4().hex}"

        values = (
            primary_folder, customer.display_name.strip(),
            customer.entity_type, customer.company.strip(), customer.email.strip(),
            customer.phone.strip(), customer.street.strip(), customer.postal_code.strip(),
            customer.city.strip(),
        )
        if customer.id is None:
            cursor = self.connection.execute(
                """
                INSERT INTO customers
                    (folder_path, display_name, entity_type, company, email, phone,
                     street, postal_code, city)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            customer_id = int(cursor.lastrowid)
        else:
            customer_id = customer.id
            self.connection.execute(
                "DELETE FROM automatic_field_sources "
                "WHERE owner_type='contact' AND owner_id IN "
                "(SELECT id FROM contacts WHERE customer_id=?)",
                (customer_id,),
            )
            self.connection.execute(
                "DELETE FROM automatic_field_sources "
                "WHERE owner_type='customer' AND owner_id=?",
                (customer_id,),
            )
            self.connection.execute(
                """
                UPDATE customers SET
                    folder_path=?, display_name=?, entity_type=?, company=?, email=?,
                    phone=?, street=?, postal_code=?, city=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (*values, customer_id),
            )
        self.connection.execute(
            "INSERT OR IGNORE INTO customer_types (name) VALUES (?)",
            (customer.entity_type.strip() or "Unternehmen",),
        )
        self._replace_contacts(customer_id, customer.contacts)
        self._replace_services(customer_id, customer.service_types)
        self._replace_folders(customer_id, normalized_paths or [primary_folder])
        if commit:
            for index, folder in enumerate(normalized_paths):
                if folder.startswith("customer://"):
                    continue
                service = (
                    customer.service_types[min(index, len(customer.service_types) - 1)]
                    if customer.service_types
                    else ""
                )
                project = self._infer_project_from_folder(folder, service)
                project.customer_id = customer_id
                self.upsert_project_from_root(project)
        self._replace_notes(customer_id, customer.notes)
        self._replace_tags(customer_id, customer.tags)
        same_name = self.find_by_name(customer.display_name)
        current = self.get(customer_id)
        if len(same_name) > 1 and current is not None:
            merge_candidates = [
                existing
                for existing in same_name
                if self._is_auto_merge_match(existing, current)
            ]
            if len(merge_candidates) > 1:
                customer_id = int(
                    self._merge_customer_group(
                        merge_candidates,
                        preferred_id=customer_id,
                    ).id
                )
        if commit:
            self.connection.commit()
        return self.get(customer_id)

    def delete(self, customer_id: int):
        self.connection.execute("DELETE FROM customers WHERE id = ?", (customer_id,))
        self.connection.commit()

    def clear_all_customer_data(self):
        """Remove all customer-owned data while keeping the database schema."""
        with self.connection:
            for table in (
                "customer_data_suggestions",
                "recognition_decisions",
                "recognition_cases",
                "recognition_runs",
                "automatic_customer_sources",
                "automatic_field_sources",
                "extracted_value_observations",
                "blacklist_suggestions",
                "customer_merge_log",
                "customer_journal_entries",
                "customer_tags",
                "tags",
                "notes",
                "contacts",
                "customer_services",
                "customer_folders",
                "customer_projects",
                "service_types",
                "customers",
                "customer_types",
            ):
                self.connection.execute(f"DELETE FROM {table}")
            self.connection.executemany(
                "INSERT OR IGNORE INTO customer_types (name) VALUES (?)",
                [("Unternehmen",), ("Privatperson",), ("Organisation",)],
            )

    def _replace_contacts(self, customer_id: int, contacts: list[Contact]):
        self.connection.execute("DELETE FROM contacts WHERE customer_id = ?", (customer_id,))
        self.connection.executemany(
            """
            INSERT INTO contacts (customer_id, name, role, email, phone)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (customer_id, item.name.strip(), item.role.strip(), item.email.strip(), item.phone.strip())
                for item in contacts if item.name.strip()
            ],
        )

    def _replace_services(self, customer_id: int, service_types: list[str]):
        self.connection.execute("DELETE FROM customer_services WHERE customer_id = ?", (customer_id,))
        unique = {}
        for service in service_types:
            cleaned = service.strip()
            if cleaned:
                unique.setdefault(cleaned.casefold(), cleaned)
        self.connection.executemany(
            "INSERT INTO customer_services (customer_id, name) VALUES (?, ?)",
            [(customer_id, item) for item in sorted(unique.values(), key=str.casefold)],
        )

    def _replace_folders(self, customer_id: int, folder_paths: list[str]):
        self.connection.execute("DELETE FROM customer_folders WHERE customer_id = ?", (customer_id,))
        self.connection.executemany(
            "INSERT OR IGNORE INTO customer_folders (customer_id, folder_path) VALUES (?, ?)",
            [(customer_id, folder) for folder in folder_paths if folder.strip()],
        )

    def _replace_notes(self, customer_id: int, notes: list[str]):
        self.connection.execute("DELETE FROM notes WHERE customer_id = ?", (customer_id,))
        self.connection.executemany(
            "INSERT INTO notes (customer_id, body) VALUES (?, ?)",
            [(customer_id, note.strip()) for note in notes if note.strip()],
        )

    def _replace_tags(self, customer_id: int, tags: list[str]):
        self.connection.execute("DELETE FROM customer_tags WHERE customer_id = ?", (customer_id,))
        unique = {}
        for tag in tags:
            cleaned = tag.strip()
            if cleaned:
                unique.setdefault(cleaned.casefold(), cleaned)
        normalized = sorted(unique.values(), key=str.casefold)
        for tag in normalized:
            self.connection.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag,))
            tag_id = self.connection.execute(
                "SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (tag,)
            ).fetchone()[0]
            self.connection.execute(
                "INSERT INTO customer_tags (customer_id, tag_id) VALUES (?, ?)",
                (customer_id, tag_id),
            )

    def _hydrate(self, row: sqlite3.Row) -> Customer:
        customer_id = int(row["id"])
        contacts = [
            Contact(item["name"], item["role"], item["email"], item["phone"])
            for item in self.connection.execute(
                "SELECT name, role, email, phone FROM contacts WHERE customer_id=? ORDER BY id",
                (customer_id,),
            )
        ]
        notes = [
            str(item[0])
            for item in self.connection.execute(
                "SELECT body FROM notes WHERE customer_id=? ORDER BY id", (customer_id,)
            )
        ]
        service_types = [
            str(item[0])
            for item in self.connection.execute(
                """
                SELECT DISTINCT service_types.name
                FROM customer_projects
                JOIN service_types ON service_types.id = customer_projects.service_type_id
                WHERE customer_projects.customer_id=?
                ORDER BY service_types.name COLLATE NOCASE
                """,
                (customer_id,),
            )
        ]
        if not service_types:
            service_types = [
                str(item[0])
                for item in self.connection.execute(
                    "SELECT name FROM customer_services WHERE customer_id=? ORDER BY name COLLATE NOCASE",
                    (customer_id,),
                )
            ]
        folder_paths = [
            str(item[0])
            for item in self.connection.execute(
                """
                SELECT folder_path FROM customer_projects
                WHERE customer_id=?
                ORDER BY year DESC, folder_path COLLATE NOCASE
                """,
                (customer_id,),
            )
        ]
        if not folder_paths:
            folder_paths = [
                str(item[0])
                for item in self.connection.execute(
                    "SELECT folder_path FROM customer_folders WHERE customer_id=? ORDER BY folder_path COLLATE NOCASE",
                    (customer_id,),
                )
            ]
        tags = [
            str(item[0])
            for item in self.connection.execute(
                """
                SELECT tags.name FROM tags JOIN customer_tags ON tags.id=customer_tags.tag_id
                WHERE customer_tags.customer_id=? ORDER BY tags.name COLLATE NOCASE
                """,
                (customer_id,),
            )
        ]
        return Customer(
            id=customer_id,
            folder_path=row["folder_path"], display_name=row["display_name"],
            entity_type=row["entity_type"], company=row["company"], email=row["email"],
            phone=row["phone"], street=row["street"], postal_code=row["postal_code"],
            city=row["city"], contacts=contacts, service_types=service_types,
            folder_paths=folder_paths, notes=notes, tags=tags,
        )

    def close(self):
        self.connection.close()
