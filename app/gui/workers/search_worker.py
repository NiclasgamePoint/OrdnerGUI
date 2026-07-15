from pathlib import Path

from PySide6.QtCore import QThread, Signal

from app.core.customer_repository import CustomerRepository
from app.core.index_manager import IndexManager
from app.core.search_models import SearchFilters, SearchPage


class SearchWorker(QThread):
    """Run one paginated search with thread-local database connections."""

    completed = Signal(int, str, object, str)

    def __init__(
        self,
        db_path: Path,
        generation: int,
        category: str,
        query: str,
        result_limit: int,
        filters: SearchFilters,
        page: int,
        page_size: int,
        customer_db_path: Path,
    ):
        super().__init__()
        self.db_path = db_path
        self.generation = generation
        self.category = category
        self.query = query
        self.result_limit = result_limit
        self.filters = filters
        self.page = page
        self.page_size = page_size
        self.customer_db_path = customer_db_path

    def run(self):
        manager = None
        try:
            manager = IndexManager(self.db_path, initialize=False)
            if self.category == "folders":
                results = manager.search_folders_page(
                    self.query, self.filters, self.page, self.page_size
                )
                results = self._merge_customer_results(manager, results)
            elif self.category == "files":
                results = manager.search_files_page(
                    self.query, self.filters, self.page, self.page_size
                )
            elif self.category == "text":
                results = manager.search_text_page(
                    self.query,
                    self.filters,
                    self.page,
                    self.page_size,
                    maximum=self.result_limit,
                    should_cancel=self.isInterruptionRequested,
                )
            else:
                raise ValueError(f"Unbekannte Suchkategorie: {self.category}")
            self.completed.emit(self.generation, self.category, results, "")
        except Exception as exc:
            self.completed.emit(self.generation, self.category, [], str(exc))
        finally:
            if manager is not None:
                manager.close()

    def _merge_customer_results(self, manager: IndexManager, page: SearchPage) -> SearchPage:
        if self.page != 1 or not self.customer_db_path.exists():
            return page
        repository = CustomerRepository(self.customer_db_path, readonly=True)
        try:
            known_paths = {item["folder_path"] for item in page.items}
            customer_items = []
            for customer in repository.search(self.query, self.page_size):
                if customer.folder_path in known_paths:
                    continue
                entry = manager.get_folder_search_entry(customer.folder_path, self.filters)
                if entry is not None:
                    entry["folder_name"] = customer.display_name
                    entry["customer_match"] = True
                    customer_items.append(entry)
                    known_paths.add(customer.folder_path)
        finally:
            repository.close()
        return SearchPage(
            (customer_items + page.items)[: self.page_size],
            page.total + len(customer_items),
            page.page,
            page.page_size,
        )
