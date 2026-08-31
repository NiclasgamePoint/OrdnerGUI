from __future__ import annotations

from app.core.customer_models import Customer
from app.core.customer_repository import CustomerConflictError, CustomerRepository
from tests.base.test_case import PapaGuiTestCase


class CustomerRevisionTests(PapaGuiTestCase):
    def test_stale_customer_update_is_rejected_without_overwrite(self):
        repository = CustomerRepository(self.temp_path / "customers.db")
        created = repository.save(Customer(display_name="Kunde C"))
        first = repository.get(int(created.id))
        stale = repository.get(int(created.id))
        first.display_name = "Name von Client A"
        saved = repository.save(first, expected_revision=first.revision)
        self.assertGreater(saved.revision, first.revision)

        stale.display_name = "Name von Client B"
        with self.assertRaises(CustomerConflictError) as conflict:
            repository.save(stale, expected_revision=stale.revision)
        self.assertEqual(conflict.exception.current.display_name, "Name von Client A")
        self.assertEqual(repository.get(int(created.id)).display_name, "Name von Client A")
        repository.close()
