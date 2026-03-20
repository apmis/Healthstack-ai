import json
import sys
from pathlib import Path
from pprint import pprint

from bson import json_util

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime_lock import acquire_script_lock
from app.core.config import get_settings
from app.core.database import get_database
from app.services.vector_indexing import (
    SOURCE_COLLECTIONS,
    delete_source_document_chunks,
    ensure_chunk_collection_indexes,
    ensure_vector_search_index,
    index_source_document,
)


def _state_file_path() -> Path:
    settings = get_settings()
    path = PROJECT_ROOT / settings.vector_sync_state_file
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_resume_token():
    path = _state_file_path()
    if not path.exists():
        return None
    return json_util.loads(path.read_text(encoding="utf-8"))


def save_resume_token(token) -> None:
    path = _state_file_path()
    path.write_text(json_util.dumps(token), encoding="utf-8")


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

    resume_token = load_resume_token()
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


if __name__ == "__main__":
    main()
