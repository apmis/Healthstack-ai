import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.database import get_database
from app.core.config import get_settings
from runtime_lock import acquire_script_lock
from app.services.vector_indexing import (
    SOURCE_COLLECTIONS,
    ensure_chunk_collection_indexes,
    ensure_vector_search_index,
    index_source_documents,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Keep resuming vector backfill batches until the selected collections are exhausted."
    )
    parser.add_argument(
        "--collection",
        action="append",
        choices=SOURCE_COLLECTIONS,
        dest="collections",
        help="Limit continuous backfill to one or more specific source collections.",
    )
    parser.add_argument(
        "--batch-limit",
        type=int,
        default=1500,
        help="Maximum number of source documents to process per collection in each resumed batch.",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=2.0,
        help="Pause between completed batches.",
    )
    parser.add_argument(
        "--max-batches",
        type=int,
        default=None,
        help="Optional cap on the number of resumed batches to run before exiting.",
    )
    parser.add_argument(
        "--max-failures",
        type=int,
        default=5,
        help="Stop after this many consecutive batch failures. Use 0 to retry forever.",
    )
    parser.add_argument(
        "--retry-backoff-max-seconds",
        type=float,
        default=300.0,
        help="Cap the retry sleep interval after batch failures.",
    )
    parser.add_argument(
        "--ensure-index",
        action="store_true",
        help="Create the supporting MongoDB indexes and Atlas Vector Search index if missing.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the old JSON batch summaries instead of human-readable progress lines.",
    )
    return parser.parse_args()


def _get_coverage(collection_name: str) -> dict[str, int]:
    settings = get_settings()
    db = get_database()
    chunks = db[settings.vector_chunks_collection]
    return {
        "total": db[collection_name].count_documents({}),
        "embedded": len(chunks.distinct("source_document_id", {"source_collection": collection_name})),
        "chunk_count": chunks.count_documents({"source_collection": collection_name}),
    }


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _format_eta(remaining_docs: int, docs_per_second: float) -> str:
    if remaining_docs <= 0:
        return "done"
    if docs_per_second <= 0:
        return "n/a"
    return _format_duration(remaining_docs / docs_per_second)


def _print_initial_progress(collections: list[str], use_json: bool) -> None:
    if use_json:
        return
    print("Continuous backfill starting...")
    for collection_name in collections:
        coverage = _get_coverage(collection_name)
        percentage = (coverage["embedded"] / coverage["total"] * 100) if coverage["total"] else 0.0
        remaining = max(0, coverage["total"] - coverage["embedded"])
        print(
            f"[{collection_name}] embedded {coverage['embedded']:,}/{coverage['total']:,} "
            f"({percentage:.2f}%), remaining {remaining:,}, chunks {coverage['chunk_count']:,}"
        )


def _print_batch_progress(
    collection_name: str,
    batch_number: int,
    stats: dict[str, int],
    batch_elapsed: float,
    total_elapsed: float,
    initial_embedded: int,
    use_json: bool,
) -> None:
    coverage = _get_coverage(collection_name)
    remaining = max(0, coverage["total"] - coverage["embedded"])
    percentage = (coverage["embedded"] / coverage["total"] * 100) if coverage["total"] else 0.0
    batch_rate = stats["documents_indexed"] / batch_elapsed if batch_elapsed > 0 else 0.0
    session_progress = max(0, coverage["embedded"] - initial_embedded)
    overall_rate = session_progress / total_elapsed if total_elapsed > 0 else 0.0

    if use_json:
        print(
            json.dumps(
                {
                    "batch": batch_number,
                    "collection": collection_name,
                    "stats": stats,
                    "coverage": coverage,
                    "percentage": round(percentage, 2),
                    "remaining": remaining,
                    "batch_elapsed_seconds": round(batch_elapsed, 2),
                    "overall_elapsed_seconds": round(total_elapsed, 2),
                    "batch_docs_per_minute": round(batch_rate * 60, 2),
                    "overall_docs_per_minute": round(overall_rate * 60, 2),
                    "eta_seconds": None if overall_rate <= 0 else round(remaining / overall_rate, 2),
                },
                indent=2,
            )
        )
        return

    print(
        f"[{collection_name}] batch {batch_number}: +{stats['documents_indexed']:,} docs, "
        f"+{stats['chunks_upserted']:,} chunks, "
        f"embedded {coverage['embedded']:,}/{coverage['total']:,} ({percentage:.2f}%), "
        f"remaining {remaining:,}, batch {_format_duration(batch_elapsed)}, "
        f"rate {batch_rate * 60:.1f} docs/min, avg {overall_rate * 60:.1f} docs/min, "
        f"eta {_format_eta(remaining, overall_rate)}"
    )


def main() -> None:
    acquire_script_lock(PROJECT_ROOT / ".runtime" / "full_reindex.lock")
    args = parse_args()
    collections = args.collections or list(SOURCE_COLLECTIONS)
    consecutive_failures = 0
    batch_number = 0
    started_at = time.time()
    initial_coverages = {collection_name: _get_coverage(collection_name) for collection_name in collections}

    if args.ensure_index:
        ensure_chunk_collection_indexes()
        ensure_vector_search_index()

    _print_initial_progress(collections, use_json=args.json)

    while True:
        if args.max_batches is not None and batch_number >= args.max_batches:
            break

        try:
            batch_number += 1
            batch_stats: dict[str, dict[str, int]] = {}
            batch_progress = False
            batch_started_at = time.time()

            for collection_name in collections:
                stats = index_source_documents(
                    collection_name,
                    limit=args.batch_limit,
                    resume_from_existing=True,
                )
                batch_stats[collection_name] = stats
                if stats["documents_seen"] > 0:
                    batch_progress = True
                _print_batch_progress(
                    collection_name,
                    batch_number,
                    stats,
                    batch_elapsed=time.time() - batch_started_at,
                    total_elapsed=time.time() - started_at,
                    initial_embedded=initial_coverages[collection_name]["embedded"],
                    use_json=args.json,
                )

            consecutive_failures = 0

            if not batch_progress:
                if not args.json:
                    print("Backfill is caught up for the selected collections.")
                break

            if args.sleep_seconds > 0:
                time.sleep(args.sleep_seconds)
        except KeyboardInterrupt:
            if args.json:
                print(json.dumps({"stopped": "interrupted", "batch": batch_number}, indent=2))
            else:
                print(f"Stopped after batch {batch_number}.")
            raise SystemExit(130)
        except Exception as exc:
            consecutive_failures += 1
            if args.json:
                print(
                    json.dumps(
                        {
                            "batch": batch_number,
                            "error": str(exc),
                            "consecutive_failures": consecutive_failures,
                            "retry_sleep_seconds": min(
                                max(1.0, args.sleep_seconds) * consecutive_failures,
                                args.retry_backoff_max_seconds,
                            ),
                        },
                        indent=2,
                    ),
                    file=sys.stderr,
                )
            else:
                retry_sleep_seconds = min(
                    max(1.0, args.sleep_seconds) * consecutive_failures,
                    args.retry_backoff_max_seconds,
                )
                print(
                    f"Batch {batch_number} failed: {exc} "
                    f"(consecutive failures: {consecutive_failures}/"
                    f"{'infinite' if args.max_failures <= 0 else args.max_failures}, "
                    f"retrying in {_format_duration(retry_sleep_seconds)})",
                    file=sys.stderr,
                )
            if args.max_failures > 0 and consecutive_failures >= args.max_failures:
                raise
            time.sleep(
                min(
                    max(1.0, args.sleep_seconds) * consecutive_failures,
                    args.retry_backoff_max_seconds,
                )
            )


if __name__ == "__main__":
    main()
