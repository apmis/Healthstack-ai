import http.client
import json
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod

from app.core.config import get_settings


class Reranker(ABC):
    @abstractmethod
    def rerank(self, query: str, documents: list[str], top_k: int) -> list[dict]:
        raise NotImplementedError


class AtlasVoyageReranker(Reranker):
    def __init__(self, api_key: str, base_url: str, model_name: str):
        settings = get_settings()
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        self.timeout_secs = max(30, settings.reranker_request_timeout_secs)
        self.max_retries = max(1, settings.reranker_request_retries)
        self.backoff_secs = max(0.5, settings.reranker_request_backoff_secs)

    def _post_json(self, path: str, payload: dict) -> dict:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_secs) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                error_body = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"Atlas Voyage rerank request failed: {error_body}") from exc
            except (TimeoutError, urllib.error.URLError, http.client.RemoteDisconnected, ConnectionResetError) as exc:
                last_error = exc
                if attempt == self.max_retries:
                    break
                time.sleep(self.backoff_secs * attempt)
        raise RuntimeError("Atlas Voyage rerank request failed after retries.") from last_error

    def rerank(self, query: str, documents: list[str], top_k: int) -> list[dict]:
        if not documents:
            return []
        payload = {
            "query": query,
            "documents": documents,
            "model": self.model_name,
            "top_k": min(max(1, top_k), len(documents)),
        }
        body = self._post_json("/rerank", payload)
        results = body.get("data") or body.get("results") or []
        parsed = []
        for item in results:
            index = item.get("index")
            if index is None:
                continue
            parsed.append(
                {
                    "index": int(index),
                    "relevance_score": float(item.get("relevance_score") or item.get("score") or 0.0),
                }
            )
        parsed.sort(key=lambda item: item["relevance_score"], reverse=True)
        return parsed


def get_reranker() -> Reranker | None:
    settings = get_settings()
    provider = settings.reranker_provider.lower()
    if provider in {"", "none"}:
        return None
    if provider == "atlas_voyage":
        if not settings.voyage_api_key:
            return None
        return AtlasVoyageReranker(
            api_key=settings.voyage_api_key,
            base_url=settings.voyage_api_base,
            model_name=settings.reranker_model,
        )
    raise RuntimeError(f"Unsupported reranker provider: {settings.reranker_provider}")
