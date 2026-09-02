PRAGMA foreign_keys = ON;

CREATE TABLE customers (
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
CREATE TABLE contacts (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT ''
);
CREATE TABLE customer_services (
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    PRIMARY KEY (customer_id, name)
);
CREATE TABLE service_types (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    normalized_name TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE customer_projects (
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
CREATE TABLE customer_data_suggestions (
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
CREATE TABLE customer_folders (
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    folder_path TEXT UNIQUE NOT NULL,
    PRIMARY KEY (customer_id, folder_path)
);
CREATE TABLE notes (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE customer_journal_entries (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    entry_number INTEGER NOT NULL DEFAULT 0,
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE recognition_cases (
    signature TEXT PRIMARY KEY,
    recognition_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE recognition_decisions (
    signature TEXT PRIMARY KEY,
    action TEXT NOT NULL,
    customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
    decided_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO customers(
    id, folder_path, display_name, entity_type, company, email, phone,
    street, postal_code, city
) VALUES (
    1,
    '/source/Beratung/2024/Beispiel GmbH, Berlin',
    'Beispiel GmbH',
    'Unternehmen',
    'Beispiel GmbH',
    'kontakt@beispiel.invalid',
    '+49 30 123456',
    'Musterweg 1',
    '10115',
    'Berlin'
);
INSERT INTO contacts(id, customer_id, name, role, email, phone)
VALUES (1, 1, 'Erika Beispiel', 'Projektleitung', 'erika@beispiel.invalid', '030 654321');
INSERT INTO customer_services(customer_id, name) VALUES (1, 'Beratung');
INSERT INTO service_types(id, name, normalized_name) VALUES (1, 'Beratung', 'beratung');
INSERT INTO customer_projects(
    id, customer_id, service_type_id, folder_path, folder_key,
    project_label, project_city, year
) VALUES (
    1, 1, 1,
    '/source/Beratung/2024/Beispiel GmbH, Berlin',
    '/source/beratung/2024/beispiel gmbh, berlin',
    'Beispiel GmbH', 'Berlin', 2024
);
INSERT INTO customer_folders(customer_id, folder_path)
VALUES (1, '/source/Beratung/2024/Beispiel GmbH, Berlin');
INSERT INTO notes(id, customer_id, body) VALUES (1, 1, 'Historische Notiz');
INSERT INTO customer_journal_entries(id, customer_id, entry_number, title, body)
VALUES (1, 1, 1, 'Erstkontakt', 'Telefonat dokumentiert');
INSERT INTO customer_data_suggestions(
    id, customer_id, project_id, field_name, suggested_value, source_path,
    excerpt, rule, confidence, fingerprint
) VALUES (
    1, 1, 1, 'email', 'neu@beispiel.invalid',
    '/source/Beratung/2024/Beispiel GmbH, Berlin/Angebot.pdf',
    'Kontakt: neu@beispiel.invalid', 'email', 0.9, 'legacy-suggestion-1'
);
INSERT INTO customer_data_suggestions(
    id, customer_id, project_id, field_name, suggested_value, source_path,
    excerpt, rule, confidence, status, suggestion_type, contact_name,
    contact_role, contact_email, contact_phone, fingerprint
) VALUES (
    2, 1, 1, 'contact', 'Max Kontakt',
    '\\NAS01\Papa\Beratung\2024\Beispiel GmbH, Berlin\Kontakt.pdf',
    'Max Kontakt, Projektleitung', 'contact-block', 0.8, 'pending', 'contact',
    'Max Kontakt', 'Projektleitung', 'max@beispiel.invalid', '030 777777',
    'legacy-contact-2'
);
INSERT INTO recognition_cases(signature, recognition_key, payload_json, reason)
VALUES ('legacy-case-1', 'beispiel-gmbh', '{"display_name":"Beispiel GmbH"}', 'similar_name');
INSERT INTO recognition_decisions(signature, action, customer_id)
VALUES ('legacy-decision-1', 'assign', 1);
