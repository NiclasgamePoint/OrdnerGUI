"""Typed HTTP adapter models; domain validation remains in papagui_contracts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SourcePathModel(ApiModel):
    source_id: str
    relative_path: str


class ErrorDetailModel(ApiModel):
    code: str
    message: str
    status: int


class ErrorResponse(ApiModel):
    error: ErrorDetailModel


class HealthResponse(ApiModel):
    status: Literal["ok", "degraded"]
    server_version: str


class CapabilitiesModel(ApiModel):
    api_versions: list[str]
    generation_schema_versions: list[int]
    features: list[str]


class SystemInfoResponse(ApiModel):
    server_version: str
    capabilities: CapabilitiesModel
    api_version: str
    contracts_version: str
    service_name: str


class IndexSettingsModel(ApiModel):
    interval_seconds: int = Field(ge=900, le=172_800)
    automatic_runs_enabled: bool
    daily_reconciliation_enabled: bool
    content_indexing_enabled: bool
    minimum_customer_year: int = Field(ge=1900, le=9999)
    max_file_size_mb: int = Field(ge=1)
    max_extracted_characters: int = Field(ge=1)
    content_extensions: str
    excluded_folders: str
    ocr_enabled: bool
    ocr_max_pages: int = Field(ge=1)
    ocr_extended_max_pages: int = Field(ge=1)
    ocr_extension_threshold: int = Field(ge=0)
    ocr_timeout_seconds: int = Field(ge=1)
    pdf_text_timeout_seconds: int = Field(ge=1)
    resource_profile: Literal["gentle", "balanced", "fast"]
    preferred_document_patterns: str
    priority_documents_per_project: int = Field(ge=0)
    newest_years_first: bool
    extraction_timeout_seconds: int = Field(default=90, ge=1, le=900)
    extraction_memory_mb: int = Field(default=768, ge=128, le=2048)
    pdf_max_pages: int = Field(default=200, ge=1, le=2000)
    image_max_pixels: int = Field(default=25000000, ge=1000000, le=100000000)
    extraction_retry_attempts: int = Field(default=3, ge=1, le=10)
    extraction_retry_delay_seconds: int = Field(default=300, ge=1, le=86400)
    extraction_store_max_mb: int = Field(default=1024, ge=16, le=102400)
    extraction_retention_days: int = Field(default=30, ge=1, le=365)
    recognition_documents_per_project_max: int = Field(default=500, ge=1, le=500)
    recognition_pipeline_enabled: bool = True
    recognition_own_names: str = ''


class SettingsResponse(ApiModel):
    settings: IndexSettingsModel


class IndexSettingsPatchModel(ApiModel):
    interval_seconds: int | None = Field(default=None, ge=900, le=172_800)
    automatic_runs_enabled: bool | None = None
    daily_reconciliation_enabled: bool | None = None
    content_indexing_enabled: bool | None = None
    minimum_customer_year: int | None = Field(default=None, ge=1900, le=9999)
    max_file_size_mb: int | None = Field(default=None, ge=1)
    max_extracted_characters: int | None = Field(default=None, ge=1)
    content_extensions: str | None = None
    excluded_folders: str | None = None
    ocr_enabled: bool | None = None
    ocr_max_pages: int | None = Field(default=None, ge=1)
    ocr_extended_max_pages: int | None = Field(default=None, ge=1)
    ocr_extension_threshold: int | None = Field(default=None, ge=0)
    ocr_timeout_seconds: int | None = Field(default=None, ge=1)
    pdf_text_timeout_seconds: int | None = Field(default=None, ge=1)
    resource_profile: Literal["gentle", "balanced", "fast"] | None = None
    preferred_document_patterns: str | None = None
    priority_documents_per_project: int | None = Field(default=None, ge=0)
    newest_years_first: bool | None = None
    extraction_timeout_seconds: int | None = Field(default=None, ge=1, le=900)
    extraction_memory_mb: int | None = Field(default=None, ge=128, le=2048)
    pdf_max_pages: int | None = Field(default=None, ge=1, le=2000)
    image_max_pixels: int | None = Field(default=None, ge=1000000, le=100000000)
    extraction_retry_attempts: int | None = Field(default=None, ge=1, le=10)
    extraction_retry_delay_seconds: int | None = Field(default=None, ge=1, le=86400)
    extraction_store_max_mb: int | None = Field(default=None, ge=16, le=102400)
    extraction_retention_days: int | None = Field(default=None, ge=1, le=365)
    recognition_documents_per_project_max: int | None = Field(default=None, ge=1, le=500)
    recognition_pipeline_enabled: bool | None = None
    recognition_own_names: str | None = None


class SettingsUpdateRequest(ApiModel):
    settings: IndexSettingsPatchModel


class IndexProgressModel(ApiModel):
    processed_items: int
    total_items: int
    failed_items: int
    phase: str
    current_source: SourcePathModel | None
    legacy_current_path: str | None
    catalog_processed_items: int | None = Field(default=None, ge=0)
    evaluated_documents: int | None = Field(default=None, ge=0)


class IndexStatusModel(ApiModel):
    state: Literal["idle", "queued", "running", "completed", "failed", "error", "cancelled"]
    progress: IndexProgressModel
    run_id: str | None
    message: str | None
    started_at: str | None
    updated_at: str | None
    finished_at: str | None


class RetentionFailureModel(ApiModel):
    message: str
    observed_at: str


class RetentionStatusModel(ApiModel):
    state: Literal["ok", "degraded"]
    required_predecessors: Literal[3]
    failures: dict[str, RetentionFailureModel]


class DocumentWorkerStatusModel(ApiModel):
    state: Literal["idle", "running", "cancelling", "completed", "cancelled", "error"]
    worker_limit: int = Field(ge=0)
    active_workers: int = Field(ge=0)
    queued_documents: int = Field(ge=0)
    discovered_documents: int = Field(ge=0)
    processed_documents: int = Field(ge=0)
    reused_documents: int = Field(ge=0)
    extracted_documents: int = Field(ge=0)
    failed_documents: int = Field(ge=0)
    read_documents: int = Field(ge=0)
    verify_content: bool
    elapsed_seconds: float = Field(ge=0, allow_inf_nan=False)


class ServerStatusResponse(ApiModel):
    state: Literal["online", "degraded", "offline", "stopping"]
    index: IndexStatusModel
    server_version: str
    uptime_seconds: int
    observed_at: str | None
    active_index_generation: str | None
    active_customer_generation: str | None
    message: str | None
    source_id: str
    source_available: bool
    settings: IndexSettingsModel
    backups: dict[str, int]
    retention: RetentionStatusModel
    queued_action: str
    resumable: bool
    document_workers: DocumentWorkerStatusModel | None = None


class IndexRunRequest(ApiModel):
    full_rebuild: bool = False


class ActionResponse(ApiModel):
    accepted: bool
    run_id: str | None = None
    queued: bool | None = None
    rebuild: bool | None = None
    restart: bool | None = None


class GenerationComponentModel(ApiModel):
    kind: Literal["index", "customers"]
    generation: str
    created_at: str
    archive: str
    size: int
    sha256: str
    content_type: str


class GenerationComponentsModel(ApiModel):
    index: GenerationComponentModel | None
    customers: GenerationComponentModel | None


class GenerationManifestResponse(ApiModel):
    schema_version: Literal[2]
    created_at: str
    components: GenerationComponentsModel
    legacy_combined: bool


class ContactModel(ApiModel):
    id: str | None = Field(default=None, min_length=1, max_length=128)
    name: str = ""
    role: str = ""
    email: str = ""
    phone: str = ""


class JournalEntryModel(ApiModel):
    id: int | None = None
    customer_id: int | None = None
    entry_number: int = 0
    title: str = ""
    body: str = ""
    created_at: str = ""
    updated_at: str = ""
    revision: int = 0


class CustomerProjectModel(ApiModel):
    id: int | None = None
    customer_id: int | None = None
    project_root_id: int | None = None
    source: SourcePathModel | None = None
    service_type: str = ""
    project_label: str = ""
    project_city: str = ""
    year: int | None = None
    provenance: str = "folder"


class CustomerModel(ApiModel):
    id: int | None = None
    revision: int = 0
    folder_path: str = ""
    folder_paths: list[str] = Field(default_factory=list)
    display_name: str
    entity_type: str = "Unternehmen"
    service_types: list[str] = Field(default_factory=list)
    company: str = ""
    email: str = ""
    phone: str = ""
    street: str = ""
    postal_code: str = ""
    city: str = ""
    contacts: list[ContactModel] = Field(default_factory=list)
    journal_entries: list[JournalEntryModel] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    projects: list[CustomerProjectModel] = Field(default_factory=list)


class CustomerCreateRequest(ApiModel):
    customer: CustomerModel
    idempotency_key: str | None = None


class CustomerUpdateRequest(ApiModel):
    customer: CustomerModel
    expected_revision: int | None = Field(default=None, ge=0)
    idempotency_key: str | None = None


class CustomerResponse(ApiModel):
    customer: CustomerModel


class CustomersResponse(ApiModel):
    customers: list[CustomerModel]
    limit: int
    offset: int


class CustomerConflictResponse(ErrorResponse):
    current: CustomerModel | None


class JournalMutationRequest(ApiModel):
    entry: JournalEntryModel
    expected_revision: int = Field(ge=0)
    idempotency_key: str | None = None


class JournalResponse(ApiModel):
    entries: list[JournalEntryModel]
    revision: int


class JournalMutationResponse(ApiModel):
    entry: JournalEntryModel | None = None
    revision: int
    deleted: bool | None = None
    entry_id: int | None = None


class DeleteCustomerResponse(ApiModel):
    deleted: bool
    customer_id: int


class MutationEnvelopeRequest(ApiModel):
    operation: Literal["create", "update", "delete"]
    target: Literal["customer", "journal"] = "customer"
    idempotency_key: str = Field(min_length=8, max_length=128)
    expected_revision: int | None = Field(default=None, ge=0)
    target_id: int | None = Field(default=None, ge=1)
    customer_id: int | None = Field(default=None, ge=1)
    payload: CustomerModel | JournalEntryModel | None = None


class CatalogFileModel(ApiModel):
    id: int
    source: SourcePathModel
    filename: str
    file_type: str
    file_size: int
    modified_at: str
    domain_folder: str
    time_bucket: str
    project_name: str
    relative_dir: str
    folder_id: int | None
    project_root_id: int | None


class CatalogSearchResponse(ApiModel):
    items: list[CatalogFileModel]
    total: int
    limit: int
    offset: int


class CatalogFacetsResponse(ApiModel):
    domains: list[str]
    years: list[str]
    file_types: list[str]


class CatalogFolderModel(ApiModel):
    id: int
    source: SourcePathModel
    name: str
    parent_id: int | None
    project_root_id: int | None
    file_count: int
    total_size: int
    last_modified: str | None


class CatalogFoldersResponse(ApiModel):
    folders: list[CatalogFolderModel]


class CatalogFolderResponse(ApiModel):
    folder: CatalogFolderModel
    children: list[CatalogFolderModel]


class CatalogProjectRootModel(ApiModel):
    id: int
    source: SourcePathModel
    service_type: str
    year: int
    customer_label: str
    customer_name: str
    city: str
    recognition_key: str


class CatalogProjectRootsResponse(ApiModel):
    project_roots: list[CatalogProjectRootModel]


class CatalogProjectRootResponse(ApiModel):
    project_root: CatalogProjectRootModel


class RecognitionEvidenceModel(ApiModel):
    field_name: str
    value: str
    source: SourcePathModel
    excerpt: str
    rule: str
    confidence: float


class RecognitionCaseModel(ApiModel):
    signature: str
    recognition_key: str
    display_name: str
    project_roots: list[SourcePathModel]
    cities: list[str]
    service_types: list[str]
    years: list[int]
    reason: str
    suggested_customer_ids: list[int]
    evidence: list[RecognitionEvidenceModel]
    status: Literal["pending", "resolved", "rejected", "stale"]


class RecognitionCasesResponse(ApiModel):
    cases: list[RecognitionCaseModel]


class RecognitionRunModel(ApiModel):
    id: int | None
    detected: int
    created: int
    assigned: int
    pending: int
    rejected: int
    error: str
    started_at: str
    finished_at: str


class RecognitionRunsResponse(ApiModel):
    runs: list[RecognitionRunModel]


class RecognitionRunResult(ApiModel):
    detected: int
    created: int
    assigned: int
    pending: int
    rejected: int
    suggestions: int


class RecognitionRunResponse(ApiModel):
    summary: RecognitionRunResult


class RecognitionDecisionRequest(ApiModel):
    action: Literal["accept", "assign", "reject"]
    customer_id: int | None = Field(default=None, ge=1)
    expected_revision: int | None = Field(default=None, ge=0)
    idempotency_key: str | None = None


class RecognitionDecisionModel(ApiModel):
    signature: str
    action: Literal["accept", "assign", "reject"]
    customer_id: int | None
    decided_at: str


class RecognitionDecisionResponse(ApiModel):
    decision: RecognitionDecisionModel
    customer: CustomerModel | None = None


class CustomerSuggestionModel(ApiModel):
    id: int
    customer_id: int
    field_name: str
    value: str
    source: SourcePathModel
    fingerprint: str
    excerpt: str
    rule: str
    confidence: float
    status: Literal["pending", "accepted", "rejected"]
    suggestion_type: Literal["field", "contact", "address"] = "field"
    contact: ContactModel | None = None
    normalized_value: str = ""
    party_role: str = "unknown"
    quality: Literal["strong", "review", "legacy"] = "legacy"
    reasons: list[str] = Field(default_factory=list)
    payload: dict = Field(default_factory=dict)
    evidence: list[dict] = Field(default_factory=list)
    evidence_count: int = 1
    is_conflict: bool = False
    created_at: str
    resolved_at: str


class CustomerSuggestionsResponse(ApiModel):
    suggestions: list[CustomerSuggestionModel]
    revision: int
    total: int = 0
    has_more: bool = False
    recognition: dict = Field(default_factory=dict)


class CustomerRecognitionRequest(ApiModel):
    mode: Literal["reassess", "extract"] = "reassess"


class CustomerRecognitionJobModel(ApiModel):
    id: str
    customer_id: int
    mode: Literal["reassess", "extract"]
    state: Literal["queued", "running", "completed", "cancelled", "error"]
    created_at: str
    finished_at: str = ""
    error: str = ""
    document_workers: DocumentWorkerStatusModel | None = None


class CustomerRecognitionJobResponse(ApiModel):
    job: CustomerRecognitionJobModel


class RecognitionRebuildJobModel(CustomerRecognitionJobModel):
    customer_id: None = None
    mode: Literal["rebuild"] = "rebuild"


class RecognitionRebuildResponse(ApiModel):
    job: RecognitionRebuildJobModel | None = None


class RecognitionBlocklistRequest(ApiModel):
    kind: Literal["email", "phone", "email_domain", "contact_name", "company"]
    value: str = Field(min_length=1, max_length=320)
    reason: str = Field(default="", max_length=500)


class RecognitionBlocklistEntry(RecognitionBlocklistRequest):
    id: int
    normalized_value: str
    created_at: str


class RecognitionBlocklistResponse(ApiModel):
    entries: list[RecognitionBlocklistEntry]


class RecognitionBlocklistStatusResponse(RecognitionBlocklistResponse):
    publication_pending: bool


class RecognitionBlocklistMutationResponse(ApiModel):
    entry: RecognitionBlocklistEntry


class RecognitionBlocklistBatchRequest(ApiModel):
    additions: list[RecognitionBlocklistRequest] = Field(default_factory=list, max_length=500)
    deletions: list[Annotated[int, Field(gt=0, le=(1 << 63) - 1)]] = Field(default_factory=list, max_length=500)


class RecognitionBlocklistBatchResponse(RecognitionBlocklistResponse):
    changed: bool
    published: bool


class CustomerRecognitionStatusResponse(ApiModel):
    customer_id: int
    state: str
    reason: str
    summary: str
    counts: dict = Field(default_factory=dict)
    last_run_at: str = ""
    pipeline_version: str = ""
    catalog_version: str = ""
    migration_conflicts: int = 0
    field_provenance: list[dict] = Field(default_factory=list)
    job: CustomerRecognitionJobModel | None = None


class SuggestionDecisionRequest(ApiModel):
    reason: Literal["", "not_a_value", "wrong_customer", "outdated", "already_present", "other"] = ""
    action: Literal["accept", "reject"]
    expected_revision: int | None = Field(default=None, ge=0)
    idempotency_key: str | None = None


class SuggestionDecisionResponse(ApiModel):
    suggestion: CustomerSuggestionModel
    customer: CustomerModel
