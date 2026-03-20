import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime_lock import acquire_script_lock
from app.core.config import get_settings
from app.core.database import get_database
from app.services.vector_indexing import (
    SOURCE_COLLECTIONS,
    ensure_chunk_collection_indexes,
    ensure_vector_search_index,
    index_source_document_by_id,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find source documents with no chunk rows and backfill them directly."
    )
    parser.add_argument(
        "--collection",
        action="append",
        choices=SOURCE_COLLECTIONS,
        dest="collections",
        help="Limit to one or more source collections.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional maximum number of missing source documents to process per collection.",
    )
    parser.add_argument(
        "--write-report",
        type=str,
        default=None,
        help="Optional path to write the missing-source report as JSON.",
    )
    parser.add_argument(
        "--ensure-index",
        action="store_true",
        help="Create the supporting MongoDB indexes and Atlas Vector Search index if missing.",
    )
    return parser.parse_args()


def _missing_source_pipeline(collection_name: str, chunk_collection_name: str, limit: int | None) -> list[dict]:
    pipeline: list[dict] = [
        {
            "$lookup": {
                "from": chunk_collection_name,
                "let": {"docId": {"$toString": "$_id"}},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$source_collection", collection_name]},
                                    {"$eq": ["$source_document_id", "$$docId"]},
                                ]
                            }
                        }
                    },
                    {"$limit": 1},
                ],
                "as": "chunk_match",
            }
        },
        {"$match": {"chunk_match": {"$eq": []}}},
        {
            "$project": {
                "_id": 1,
                "source_document_id": {"$toString": "$_id"},
                "documentname": 1,
                "facility": 1,
                "client": 1,
                "documentdetail": 1,
                "createdAt": 1,
                "updatedAt": 1,
            }
        },
        {"$sort": {"updatedAt": -1, "_id": -1}},
    ]
    if limit:
        pipeline.append({"$limit": limit})
    return pipeline


def _gather_missing_documents(collection_name: str, limit: int | None = None) -> list[dict]:
    settings = get_settings()
    db = get_database()
    pipeline = _missing_source_pipeline(collection_name, settings.vector_chunks_collection, limit)
    return list(db[collection_name].aggregate(pipeline, allowDiskUse=True))


def _report_item(document: dict) -> dict:
    return {
        "source_document_id": document["source_document_id"],
        "documentname": document.get("documentname"),
        "has_facility": document.get("facility") is not None,
        "has_client": document.get("client") is not None,
        "has_documentdetail": document.get("documentdetail") not in (None, {}, []),
        "createdAt": document.get("createdAt"),
        "updatedAt": document.get("updatedAt"),
    }


def main() -> None:
    acquire_script_lock(PROJECT_ROOT / ".runtime" / "full_reindex.lock")
    args = parse_args()
    collections = args.collections or list(SOURCE_COLLECTIONS)

    if args.ensure_index:
        ensure_chunk_collection_indexes()
        ensure_vector_search_index()

    report: dict[str, list[dict]] = {}
    stats: dict[str, dict[str, int]] = {}

    for collection_name in collections:
        missing_documents = _gather_missing_documents(collection_name, limit=args.limit)
        report[collection_name] = [_report_item(document) for document in missing_documents]
        collection_stats = {
            "missing_before": len(missing_documents),
            "documents_seen": 0,
            "documents_indexed": 0,
            "chunks_upserted": 0,
            "chunks_deleted": 0,
            "documents_skipped": 0,
        }
        for document in missing_documents:
            collection_stats["documents_seen"] += 1
            doc_stats = index_source_document_by_id(collection_name, document["_id"])
            for key in ("documents_indexed", "chunks_upserted", "chunks_deleted", "documents_skipped"):
                collection_stats[key] += doc_stats[key]
        collection_stats["missing_after"] = len(_gather_missing_documents(collection_name, limit=None))
        stats[collection_name] = collection_stats

    if args.write_report:
        report_path = Path(args.write_report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    print(json.dumps({"stats": stats, "report_path": args.write_report}, indent=2, default=str))


if __name__ == "__main__":
    main()
