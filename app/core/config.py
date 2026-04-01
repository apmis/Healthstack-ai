from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "HS Copilot"
    api_host: str = "127.0.0.1"
    api_port: int = 8010
    cors_allow_all: bool = True
    cors_allow_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    cors_allow_origin_regex: str = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"
    mongodb: str
    mongodb_db: str = "healthstackv2"
    jwt_secret: str = "/bPj3Lp34XPy8ceLi/pwBW8ymvc="
    jwt_algorithm: str = "HS256"
    default_notes_limit: int = 5
    max_patient_results: int = 10
    vector_chunks_collection: str = "copilot_chunks"
    vector_index_name: str = "patient_note_chunks"
    embedding_provider: str = "hash"
    embedding_model: str = "hash-bow-v1"
    embedding_dimensions: int = 256
    voyage_api_key: str | None = None
    voyage_api_base: str = "https://ai.mongodb.com/v1"
    openai_api_key: str | None = None
    openai_embedding_model: str = "text-embedding-3-small"
    openrouter_api_key: str | None = None
    openrouter_api_base: str = "https://openrouter.ai/api/v1"
    openrouter_http_referer: str = "http://localhost:8010"
    openrouter_app_title: str = "HS Copilot"
    llm_provider: str = "none"
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.2
    llm_request_timeout_secs: int = 120
    llm_request_retries: int = 3
    llm_request_backoff_secs: float = 2.0
    reranker_provider: str = "atlas_voyage"
    reranker_model: str = "rerank-2.5"
    reranker_candidate_limit: int = 24
    reranker_request_timeout_secs: int = 90
    reranker_request_retries: int = 3
    reranker_request_backoff_secs: float = 2.0
    embedding_request_timeout_secs: int = 180
    embedding_request_retries: int = 4
    embedding_request_backoff_secs: float = 3.0
    chunk_size_tokens: int = 180
    chunk_overlap_tokens: int = 0
    index_batch_size: int = 50
    index_batch_token_budget: int = 80000
    vector_num_candidates_factor: int = 20
    vector_sync_state_file: str = ".runtime/vector_sync_state.json"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_allow_origins_list(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.cors_allow_origins.split(",")
            if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
