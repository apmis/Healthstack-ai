import hashlib
import http.client
import json
import math
import re
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod

from app.core.config import get_settings


class EmbeddingProvider(ABC):
    dimensions: int
    provider_name: str
    model_name: str

    @abstractmethod
    def embed_documents(self, documents: list[list[str]]) -> list[list[list[float]]]:
        raise NotImplementedError

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        raise NotImplementedError

    def embed_document_chunks(self, chunks: list[str]) -> list[list[float]]:
        return self.embed_documents([chunks])[0]


class HashEmbeddingProvider(EmbeddingProvider):
    def __init__(self, dimensions: int, model_name: str = "hash-bow-v1"):
        self.dimensions = dimensions
        self.provider_name = "hash"
        self.model_name = model_name

    def _terms(self, text: str) -> list[str]:
        terms = re.findall(r"[a-zA-Z0-9]+", text.lower())
        if len(terms) < 2:
            return terms
        bigrams = [f"{terms[index]}_{terms[index + 1]}" for index in range(len(terms) - 1)]
        return terms + bigrams

    def _hash_term(self, term: str) -> tuple[int, float]:
        digest = hashlib.blake2b(term.encode("utf-8"), digest_size=16).digest()
        bucket = int.from_bytes(digest[:4], "big") % self.dimensions
        sign = -1.0 if digest[4] & 1 else 1.0
        return bucket, sign

    def _normalize(self, vector: list[float]) -> list[float]:
        magnitude = math.sqrt(sum(value * value for value in vector))
        if magnitude == 0:
            return vector
        return [value / magnitude for value in vector]

    def _embed_text(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for term in self._terms(text):
            bucket, sign = self._hash_term(term)
            vector[bucket] += sign
        return self._normalize(vector)

    def embed_documents(self, documents: list[list[str]]) -> list[list[list[float]]]:
        return [[self._embed_text(text) for text in document] for document in documents]

    def embed_query(self, text: str) -> list[float]:
        return self._embed_text(text)


class OpenAIEmbeddingProvider(EmbeddingProvider):
    endpoint = "https://api.openai.com/v1/embeddings"

    def __init__(self, api_key: str, model_name: str, dimensions: int):
        self.api_key = api_key
        self.model_name = model_name
        self.dimensions = dimensions
        self.provider_name = "openai"

    def _request_embeddings(self, texts: list[str]) -> list[list[float]]:
        payload = {
            "input": texts,
            "model": self.model_name,
            "dimensions": self.dimensions,
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI embedding request failed: {error_body}") from exc

        data = body.get("data", [])
        if len(data) != len(texts):
            raise RuntimeError("Embedding response count did not match request count.")
        ordered = sorted(data, key=lambda item: item.get("index", 0))
        return [item["embedding"] for item in ordered]

    def embed_documents(self, documents: list[list[str]]) -> list[list[list[float]]]:
        lengths = [len(document) for document in documents]
        flat_texts = [text for document in documents for text in document]
        if not flat_texts:
            return [[] for _ in documents]
        flat_embeddings = self._request_embeddings(flat_texts)
        output: list[list[list[float]]] = []
        cursor = 0
        for length in lengths:
            output.append(flat_embeddings[cursor : cursor + length])
            cursor += length
        return output

    def embed_query(self, text: str) -> list[float]:
        return self._request_embeddings([text])[0]


class AtlasVoyageContextEmbeddingProvider(EmbeddingProvider):
    def __init__(self, api_key: str, base_url: str, model_name: str, dimensions: int):
        settings = get_settings()
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        self.dimensions = dimensions
        self.provider_name = "atlas_voyage"
        self.timeout_secs = max(30, settings.embedding_request_timeout_secs)
        self.max_retries = max(1, settings.embedding_request_retries)
        self.backoff_secs = max(0.5, settings.embedding_request_backoff_secs)

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
                raise RuntimeError(f"Atlas Voyage embedding request failed: {error_body}") from exc
            except (TimeoutError, urllib.error.URLError, http.client.RemoteDisconnected, ConnectionResetError) as exc:
                last_error = exc
                if attempt == self.max_retries:
                    break
                time.sleep(self.backoff_secs * attempt)
        raise RuntimeError(
            "Atlas Voyage embedding request failed after retries due to a transient network timeout."
        ) from last_error

    def _parse_contextualized_response(self, body: dict, expected_documents: int, expected_chunks: list[int]) -> list[list[list[float]]]:
        data = body.get("data", [])
        ordered_documents = sorted(data, key=lambda item: item.get("index", 0))
        if len(ordered_documents) != expected_documents:
            raise RuntimeError("Contextualized embedding response count did not match request count.")

        parsed: list[list[list[float]]] = []
        for document_index, (document_result, expected_count) in enumerate(zip(ordered_documents, expected_chunks, strict=True)):
            embedding_rows = sorted(document_result.get("data", []), key=lambda item: item.get("index", 0))
            if len(embedding_rows) != expected_count:
                raise RuntimeError(
                    f"Contextualized embedding response for document {document_index} "
                    f"returned {len(embedding_rows)} vectors, expected {expected_count}."
                )
            parsed.append([row["embedding"] for row in embedding_rows])
        return parsed

    def embed_documents(self, documents: list[list[str]]) -> list[list[list[float]]]:
        if not documents:
            return []
        payload = {
            "inputs": documents,
            "model": self.model_name,
            "input_type": "document",
            "output_dimension": self.dimensions,
            "output_dtype": "float",
        }
        body = self._post_json("/contextualizedembeddings", payload)
        expected_chunks = [len(document) for document in documents]
        return self._parse_contextualized_response(body, len(documents), expected_chunks)

    def embed_query(self, text: str) -> list[float]:
        payload = {
            "inputs": [[text]],
            "model": self.model_name,
            "input_type": "query",
            "output_dimension": self.dimensions,
            "output_dtype": "float",
        }
        body = self._post_json("/contextualizedembeddings", payload)
        documents = self._parse_contextualized_response(body, 1, [1])
        return documents[0][0]


def get_embedding_provider() -> EmbeddingProvider:
    settings = get_settings()
    provider = settings.embedding_provider.lower()
    if provider == "hash":
        return HashEmbeddingProvider(
            dimensions=settings.embedding_dimensions,
            model_name=settings.embedding_model,
        )
    if provider == "openai":
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required when EMBEDDING_PROVIDER=openai.")
        return OpenAIEmbeddingProvider(
            api_key=settings.openai_api_key,
            model_name=settings.openai_embedding_model,
            dimensions=settings.embedding_dimensions,
        )
    if provider == "atlas_voyage":
        if not settings.voyage_api_key:
            raise RuntimeError("VOYAGE_API_KEY is required when EMBEDDING_PROVIDER=atlas_voyage.")
        return AtlasVoyageContextEmbeddingProvider(
            api_key=settings.voyage_api_key,
            base_url=settings.voyage_api_base,
            model_name=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
        )
    raise RuntimeError(f"Unsupported embedding provider: {settings.embedding_provider}")
