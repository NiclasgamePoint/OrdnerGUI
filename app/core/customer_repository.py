from __future__ import annotations

from pathlib import Path
import os
import sqlite3

from app.core.customer_models import Contact, Customer


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
        return os.path.normcase(os.path.abspath(os.path.normpath(folder_path)))

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
            CREATE TABLE IF NOT EXISTS contacts (
                id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT ''
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
            """
        )
        self.connection.commit()

    def get_by_folder(self, folder_path: str) -> Customer | None:
        row = self.connection.execute(
            "SELECT * FROM customers WHERE folder_path = ?", (self._folder_key(folder_path),)
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

    def search(self, query: str, limit: int = 25) -> list[Customer]:
        pattern = f"%{query}%"
        rows = self.connection.execute(
            """
            SELECT DISTINCT customers.* FROM customers
            LEFT JOIN contacts ON contacts.customer_id = customers.id
            LEFT JOIN notes ON notes.customer_id = customers.id
            LEFT JOIN customer_tags ON customer_tags.customer_id = customers.id
            LEFT JOIN tags ON tags.id = customer_tags.tag_id
            WHERE customers.display_name LIKE ?
               OR customers.company LIKE ?
               OR contacts.name LIKE ? OR contacts.email LIKE ?
               OR notes.body LIKE ? OR tags.name LIKE ?
            ORDER BY customers.display_name COLLATE NOCASE
            LIMIT ?
            """,
            (pattern, pattern, pattern, pattern, pattern, pattern, limit),
        ).fetchall()
        return [self._hydrate(row) for row in rows]

    def save(self, customer: Customer) -> Customer:
        values = (
            self._folder_key(customer.folder_path), customer.display_name.strip(),
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
        self._replace_contacts(customer_id, customer.contacts)
        self._replace_notes(customer_id, customer.notes)
        self._replace_tags(customer_id, customer.tags)
        self.connection.commit()
        return self.get(customer_id)

    def delete(self, customer_id: int):
        self.connection.execute("DELETE FROM customers WHERE id = ?", (customer_id,))
        self.connection.commit()

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
            city=row["city"], contacts=contacts, notes=notes, tags=tags,
        )

    def close(self):
        self.connection.close()
