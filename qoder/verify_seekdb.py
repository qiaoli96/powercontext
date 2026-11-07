#!/usr/bin/env python3
"""CLI verification: query seekdb and print results to prove data comes from pyseekdb."""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import DB_PATH, COLLECTION_NAME, DATA_DIR, INDEX_FLAG, SEEKDB_DEPS

if SEEKDB_DEPS:
    sys.path.insert(0, SEEKDB_DEPS)

from seekdb import SeekDB, SearchError, parse_markdown_file


def ensure_indexed(db: SeekDB):
    if os.path.exists(INDEX_FLAG):
        return
    db.clear()
    for md_file in glob.glob(os.path.join(DATA_DIR, "*.md")):
        paragraphs = parse_markdown_file(md_file)
        db.add_paragraphs(paragraphs)
        print(f"[index] {len(paragraphs)} paragraphs from {os.path.basename(md_file)}")
    os.makedirs(DB_PATH, exist_ok=True)
    with open(INDEX_FLAG, "w") as f:
        f.write("ok")


def run_query(db: SeekDB, query: str, limit: int = 10):
    print(f"\nQuery: {query}")
    print("-" * 60)
    try:
        results = db.search(query, limit=limit)
    except SearchError as e:
        print(f"ERROR: {e}")
        return
    print(f"Results: {len(results)} hits")

    if not results:
        print("(no matches)")
        return

    for i, r in enumerate(results, 1):
        print(f"\n[{i}] file={r.file}  section={r.section}")
        print(f"    snippet: {r.snippet}")


def main():
    print("=== SeekDB (pyseekdb) Full-Text Search Verification ===")
    print(f"Database path: {DB_PATH}")
    print(f"Collection: {COLLECTION_NAME}")
    print()

    db = SeekDB(DB_PATH, COLLECTION_NAME)
    ensure_indexed(db)

    print(f"Paragraphs in store: {db.count()}")

    run_query(db, "Transformer 预训练")
    run_query(db, "量子计算 超导")

    db.close()
    print("\n=== All results retrieved from OceanBase SeekDB via pyseekdb ===")


if __name__ == "__main__":
    main()
