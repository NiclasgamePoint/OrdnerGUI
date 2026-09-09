"""Preserve contact identity across complete customer updates from old and new clients."""
from __future__ import annotations

import uuid

from papagui_server.adapters.candidate_schema import record_provenance


def replace_contacts(connection, customer_id, contacts):
    previous = connection.execute(
        "SELECT * FROM contacts WHERE customer_id=? ORDER BY id", (customer_id,)
    ).fetchall()
    by_uid = {row["uid"]: row for row in previous}
    used = set()
    fields = ("name", "role", "email", "phone")
    for contact in contacts:
        if not isinstance(contact, dict):
            continue
        values = tuple(str(contact.get(key, "")).strip() for key in fields)
        uid = contact.get("id")
        match = by_uid.get(uid) if uid else None
        if uid and match is None:
            raise ValueError("Der Ansprechpartner wurde zwischenzeitlich geändert. Bitte neu laden.")
        if not uid:
            exact = [row for row in previous if row["uid"] not in used
                     and tuple(str(row[k]).strip().casefold() for k in fields)
                     == tuple(value.casefold() for value in values)]
            if len(exact) == 1:
                match = exact[0]
            else:
                # An old client may omit IDs while changing an existing contact.
                # Preserve an unambiguous identity; never select the first of two.
                related = [row for row in previous if row["uid"] not in used and values[0]
                           and str(row["name"]).casefold() == values[0].casefold()]
                if len(related) > 1 or len(exact) > 1:
                    raise ValueError("Ansprechpartner sind nicht eindeutig. Bitte neu laden.")
                if related:
                    match = related[0]
        uid = str(match["uid"]) if match else uuid.uuid4().hex
        if uid in used:
            raise ValueError("Ein Ansprechpartner darf nicht doppelt enthalten sein.")
        used.add(uid)
        if match:
            connection.execute(
                "UPDATE contacts SET name=?,role=?,email=?,phone=? WHERE uid=? AND customer_id=?",
                (*values, uid, customer_id),
            )
            changed = [key for key, value in zip(fields, values) if value != match[key]]
        else:
            connection.execute(
                "INSERT INTO contacts(customer_id,uid,name,role,email,phone) VALUES (?,?,?,?,?,?)",
                (customer_id, uid, *values),
            )
            changed = [key for key, value in zip(fields, values) if value]
        record_provenance(connection, customer_id, changed, origin="manual", target_id=uid)
    for row in previous:
        if row["uid"] not in used:
            connection.execute("DELETE FROM contacts WHERE uid=? AND customer_id=?", (row["uid"], customer_id))
