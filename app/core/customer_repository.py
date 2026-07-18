from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import json
import os
import sqlite3
import uuid

from app.core.customer_models import (
    Contact,
    Customer,
    CustomerDataSuggestion,
    CustomerProject,
    ServiceType,
)
from app.core.customer_recognition_models import RecognitionCandidate, RecognitionStats
from app.core.folder_structure import ProjectRoot, normalize_identity


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
        return os.path.abspath(os.path.normpath(value))

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
                status TEXT NOT NULL DEFAULT 'pending',
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
    ) -> CustomerDataSuggestion | None:
        field = field_name.strip()
        value = str(suggested_value or "").strip()
        if not field or not value:
            return None
        existing = self.connection.execute(
            """
            SELECT * FROM customer_data_suggestions
            WHERE customer_id = ?
              AND COALESCE(project_id, 0) = COALESCE(?, 0)
              AND field_name = ?
              AND suggested_value = ?
              AND status = 'pending'
            LIMIT 1
            """,
            (customer_id, project_id, field, value),
        ).fetchone()
        if existing is not None:
            return self._hydrate_data_suggestion(existing)
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO customer_data_suggestions
                    (customer_id, project_id, field_name, suggested_value, source_path)
                VALUES (?, ?, ?, ?, ?)
                """,
                (customer_id, project_id, field, value, source_path),
            )
            suggestion_id = int(self.connection.execute(
                "SELECT last_insert_rowid()"
            ).fetchone()[0])
        return self.get_data_suggestion(suggestion_id)

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
        if project.project_city == "" and candidate.city:
            project.project_city = candidate.city
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
            status=str(row["status"] or "pending"),
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

    def search(self, query: str, limit: int = 25) -> list[Customer]:
        pattern = f"%{query}%"
        rows = self.connection.execute(
            """
            SELECT DISTINCT customers.* FROM customers
            LEFT JOIN contacts ON contacts.customer_id = customers.id
            LEFT JOIN notes ON notes.customer_id = customers.id
            LEFT JOIN customer_tags ON customer_tags.customer_id = customers.id
            LEFT JOIN tags ON tags.id = customer_tags.tag_id
            LEFT JOIN customer_projects ON customer_projects.customer_id = customers.id
            LEFT JOIN service_types ON service_types.id = customer_projects.service_type_id
            WHERE customers.display_name LIKE ?
               OR customers.company LIKE ?
               OR contacts.name LIKE ? OR contacts.email LIKE ?
               OR notes.body LIKE ? OR tags.name LIKE ?
               OR service_types.name LIKE ?
               OR customer_projects.project_label LIKE ?
               OR customer_projects.project_city LIKE ?
               OR customer_projects.folder_path LIKE ?
            ORDER BY customers.display_name COLLATE NOCASE
            LIMIT ?
            """,
            (
                pattern,
                pattern,
                pattern,
                pattern,
                pattern,
                pattern,
                pattern,
                pattern,
                pattern,
                pattern,
                limit,
            ),
        ).fetchall()
        return [self._hydrate(row) for row in rows]

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
                    city=candidate.city,
                    email=candidate.email,
                    phone=candidate.phone,
                    street=candidate.street,
                    postal_code=candidate.postal_code,
                    contacts=list(candidate.contacts),
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
                    "city": candidate.city,
                    "email": candidate.email,
                    "phone": candidate.phone,
                    "street": candidate.street,
                    "postal_code": candidate.postal_code,
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

                existing_contacts = self.connection.execute(
                    "SELECT name, email, phone FROM contacts WHERE customer_id=?",
                    (customer_id,),
                ).fetchall()
                contact_keys = {
                    (
                        normalize_identity(item["name"]),
                        str(item["email"] or "").casefold(),
                        self._normalize_phone(str(item["phone"] or "")),
                    )
                    for item in existing_contacts
                }
                for contact in candidate.contacts:
                    key = (
                        normalize_identity(contact.name),
                        contact.email.casefold(),
                        self._normalize_phone(contact.phone),
                    )
                    if contact.name.strip() and key not in contact_keys:
                        self.connection.execute(
                            """
                            INSERT INTO contacts (customer_id, name, role, email, phone)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                customer_id,
                                contact.name.strip(),
                                contact.role.strip(),
                                contact.email.strip(),
                                contact.phone.strip(),
                            ),
                        )
                        contact_keys.add(key)
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
        for field, value, source_path in pending_field_suggestions:
            self.apply_project_suggestion(
                customer_id,
                suggestion_project_id,
                field,
                value,
                source_path,
            )

        updated = self.get(customer_id)
        if updated is None:
            raise RuntimeError("Der automatisch aktualisierte Kunde konnte nicht geladen werden.")
        return updated

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
        with self.connection:
            for group in duplicate_groups:
                survivor = self._merge_customer_group(group)
                if survivor.id is None:
                    continue
                for customer in group:
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
            customer = self._combine_customer_data(same_name[0], [customer])
            customer.id = same_name[0].id

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
        if len(same_name) > 1:
            customer_id = int(
                self._merge_customer_group(same_name, preferred_id=customer_id).id
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
                "customer_merge_log",
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
