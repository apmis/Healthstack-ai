import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime_lock import acquire_script_lock
from app.services.vector_indexing import (
    SOURCE_COLLECTIONS,
    clear_chunk_collection,
    drop_vector_search_index,
    ensure_chunk_collection_indexes,
    ensure_vector_search_index,
    index_all_sources,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chunk and index patient narrative documents for Atlas Vector Search.")
    parser.add_argument(
        "--collection",
        action="append",
        choices=SOURCE_COLLECTIONS,
        dest="collections",
        help="Limit indexing to one or more specific source collections.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit the number of source documents processed per collection.",
    )
    parser.add_argument(
        "--updated-after",
        type=str,
        default=None,
        help="Only process documents updated on or after this ISO timestamp.",
    )
    parser.add_argument(
        "--ensure-index",
        action="store_true",
        help="Create the supporting MongoDB indexes and Atlas Vector Search index if missing.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read and chunk documents without writing chunk records.",
    )
    parser.add_argument(
        "--reset-chunks",
        action="store_true",
        help="Delete existing chunk documents before indexing.",
    )
    parser.add_argument(
        "--recreate-vector-index",
        action="store_true",
        help="Drop and recreate the Atlas Vector Search index using current dimensions.",
    )
    parser.add_argument(
        "--resume-from-existing",
        action="store_true",
        help="Continue after the latest source document already present in copilot_chunks.",
    )
    return parser.parse_args()


def main() -> None:
    acquire_script_lock(PROJECT_ROOT / ".runtime" / "full_reindex.lock")
    args = parse_args()
    updated_after = datetime.fromisoformat(args.updated_after) if args.updated_after else None
    if args.recreate_vector_index and not args.dry_run:
        print(json.dumps({"vector_index": drop_vector_search_index()}, indent=2))
    if args.ensure_index and not args.dry_run:
        ensure_chunk_collection_indexes()
        ensure_vector_search_index()
    if args.reset_chunks and not args.dry_run:
        print(json.dumps({"deleted_chunks": clear_chunk_collection()}, indent=2))

    stats = index_all_sources(
        collections=args.collections or SOURCE_COLLECTIONS,
        limit_per_collection=args.limit,
        dry_run=args.dry_run,
        updated_after=updated_after,
        resume_from_existing=args.resume_from_existing,
    )
    print(json.dumps(stats, indent=2, default=str))


if __name__ == "__main__":
    main()
