import glob
import os
import sys

from config import DB_PATH, COLLECTION_NAME, DATA_DIR, INDEX_FLAG, SEEKDB_DEPS

if SEEKDB_DEPS:
    sys.path.insert(0, SEEKDB_DEPS)

from flask import Flask, render_template, request

from seekdb import SeekDB, SearchError, parse_markdown_file

app = Flask(__name__)

_db_instance: SeekDB | None = None


def get_db() -> SeekDB:
    global _db_instance
    if _db_instance is None:
        _db_instance = SeekDB(DB_PATH, COLLECTION_NAME)
    return _db_instance


def needs_index() -> bool:
    if not os.path.exists(INDEX_FLAG):
        return True
    md_files = glob.glob(os.path.join(DATA_DIR, "*.md"))
    if not md_files:
        return False
    flag_mtime = os.path.getmtime(INDEX_FLAG)
    return any(os.path.getmtime(f) > flag_mtime for f in md_files)


def index_markdown_files():
    if not needs_index():
        return
    db = get_db()
    db.clear()
    for md_file in glob.glob(os.path.join(DATA_DIR, "*.md")):
        paragraphs = parse_markdown_file(md_file)
        db.add_paragraphs(paragraphs)
        print(f"[seekdb] indexed {len(paragraphs)} paragraphs from {os.path.basename(md_file)}")
    os.makedirs(DB_PATH, exist_ok=True)
    with open(INDEX_FLAG, "w") as f:
        f.write("ok")
    print(f"[seekdb] total paragraphs in collection: {db.count()}")


@app.route("/")
def index():
    query = request.args.get("q", "").strip()
    results = []
    error = None
    if query:
        db = get_db()
        try:
            results = db.search(query)
        except SearchError as e:
            error = str(e)
    return render_template("index.html", query=query, results=results, error=error)


if __name__ == "__main__":
    index_markdown_files()
    app.run(host="0.0.0.0", port=5000, debug=False)
