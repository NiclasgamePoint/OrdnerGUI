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
        try:
            if self.category == "customers":
                results = self._search_customers()
            else:
                with IndexManager(self.db_path, initialize=False) as manager:
                    if self.category == "folders":
                        results = manager.search_folders_page(
                            self.query, self.filters, self.page, self.page_size
                        )
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

    def _search_customers(self) -> SearchPage:
        if not self.customer_db_path.exists():
            return SearchPage([], 0, 1, self.page_size)
        repository = CustomerRepository(self.customer_db_path, readonly=True)
        try:
            customers = repository.search(self.query, self.result_limit)
        finally:
            repository.close()
        return SearchPage(
            customers[: self.page_size],
            len(customers),
            1,
            self.page_size,
        )
