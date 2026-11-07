"""Shared configuration for web and CLI."""
import os

DB_PATH = os.path.expanduser("~/seekdb_research_data")

COLLECTION_NAME = "research_paragraphs"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

INDEX_FLAG = os.path.join(DB_PATH, ".indexed")

SEEKDB_DEPS = os.environ.get("SEEKDB_DEPS", "")
