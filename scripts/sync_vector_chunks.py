import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from pprint import pprint

from bson import json_util
from pymongo.errors import OperationFailure

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime_lock import acquire_script_lock
from app.core.config import get_settings
from app.core.database import get_database
from app.services.vector_indexing import (
    SOURCE_COLLECTIONS,
    cleanup_stale_source_chunks,
    delete_source_document_chunks,
    ensure_chunk_collection_indexes,
    ensure_vector_search_index,
    index_source_document,
    index_source_documents,
)


def _state_file_path() -> Path:
    settings = get_settings()
    path = PROJECT_ROOT / settings.vector_sync_state_file
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _utcnow() -> datetime:
    return datetime.utcnow()


def load_sync_state() -> dict:
    path = _state_file_path()
    if not path.exists():
        return {}
    raw_state = json_util.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw_state, dict) and "resume_token" in raw_state:
        state = raw_state
    else:
        # Backward compatibility for the original state file, which stored only
        # the MongoDB resume token document.
        state = {"resume_token": raw_state}
    if state.get("saved_at") is None:
        state["saved_at"] = datetime.utcfromtimestamp(path.stat().st_mtime)
    return state


def load_resume_token():
    return load_sync_state().get("resume_token")


def save_resume_token(token) -> None:
    path = _state_file_path()
    path.write_text(
        json_util.dumps(
            {
                "version": 2,
                "resume_token": token,
                "saved_at": _utcnow(),
            }
        ),
        encoding="utf-8",
    )


def clear_resume_token() -> None:
    path = _state_file_path()
    if path.exists():
        path.unlink()


def handle_change(change: dict) -> dict:
    collection_name = change["ns"]["coll"]
    operation_type = change["operationType"]
    document_key = change["documentKey"]["_id"]

    if collection_name not in SOURCE_COLLECTIONS:
        return {"ignored": collection_name}

    if operation_type == "delete":
        deleted = delete_source_document_chunks(collection_name, str(document_key))
        return {
            "collection": collection_name,
            "operation": operation_type,
            "deleted_chunks": deleted,
        }

    full_document = change.get("fullDocument")
    if not full_document:
        db = get_database()
        full_document = db[collection_name].find_one({"_id": document_key})
        if not full_document:
            return {
                "collection": collection_name,
                "operation": operation_type,
                "documents_skipped": 1,
            }

    stats = index_source_document(collection_name, full_document)
    return {
        "collection": collection_name,
        "operation": operation_type,
        **stats,
    }


def _is_invalid_resume_token_error(exc: OperationFailure) -> bool:
    labels = exc.details.get("errorLabels", []) if exc.details else []
    return exc.code == 280 or "NonResumableChangeStreamError" in labels


def _run_invalid_resume_catchup(state: dict) -> None:
    settings = get_settings()
    if not settings.vector_sync_invalid_resume_catchup:
        print("Invalid resume token catch-up is disabled; starting a fresh stream from now.")
        return

    saved_at = state.get("saved_at")
    if not isinstance(saved_at, datetime):
        print("No usable saved_at timestamp in vector sync state; starting a fresh stream from now.")
        return

    lookback = max(0, settings.vector_sync_catchup_lookback_minutes)
    updated_after = saved_at - timedelta(minutes=lookback)
    print(
        "Stored change-stream resume token expired. "
        f"Running catch-up for documents updated since {updated_after.isoformat()} UTC..."
    )

    catchup_stats = {}
    for collection_name in SOURCE_COLLECTIONS:
        catchup_stats[collection_name] = {
            "updated_documents": index_source_documents(
                collection_name,
                updated_after=updated_after,
            ),
            "stale_chunks": cleanup_stale_source_chunks(collection_name),
        }
    pprint({"invalid_resume_token_catchup": catchup_stats})


def _watch_changes(db, pipeline: list[dict], resume_token) -> None:
    watch_kwargs = {
        "full_document": "updateLookup",
    }
    if resume_token:
        watch_kwargs["resume_after"] = resume_token

    with db.watch(pipeline, **watch_kwargs) as stream:
        print("Watching clinicaldocuments and labresults for vector-sync updates...")
        for change in stream:
            stats = handle_change(change)
            save_resume_token(change["_id"])
            pprint(stats)


def main() -> None:
    acquire_script_lock(PROJECT_ROOT / ".runtime" / "vector_sync.lock")
    db = get_database()
    ensure_chunk_collection_indexes()
    ensure_vector_search_index()

    pipeline = [
        {
            "$match": {
                "ns.coll": {"$in": list(SOURCE_COLLECTIONS)},
                "operationType": {"$in": ["insert", "update", "replace", "delete"]},
            }
        }
    ]

    while True:
        state = load_sync_state()
        resume_token = state.get("resume_token")
        try:
            _watch_changes(db, pipeline, resume_token)
        except OperationFailure as exc:
            if resume_token and _is_invalid_resume_token_error(exc):
                latest_state = load_sync_state() or state
                clear_resume_token()
                _run_invalid_resume_catchup(latest_state)
                continue
            raise


if __name__ == "__main__":
    main()
