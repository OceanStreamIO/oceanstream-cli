import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    AZURE_CONTAINER_NAME = os.getenv("AZURE_CONTAINER_NAME")
    LOCAL_OUTPUT_PATH = os.getenv("LOCAL_OUTPUT_PATH", "data/output")
    RAW_DATA_PATH = os.getenv("RAW_DATA_PATH", "data/raw_data")
    GEOPARQUET_OUTPUT_PATH = os.getenv("GEOPARQUET_OUTPUT_PATH", "data/geoparquet")
    # Semantic mapping configuration (off by default; controlled via env)
    SEMANTIC_ENABLE = os.getenv("SEMANTIC_ENABLE", "false").lower() in {"1", "true", "yes"}
    SEMANTIC_CF_TABLE = os.getenv("SEMANTIC_CF_TABLE", "")
    SEMANTIC_ALIAS_TABLE = os.getenv("SEMANTIC_ALIAS_TABLE", "")
    SEMANTIC_MIN_CONFIDENCE = float(os.getenv("SEMANTIC_MIN_CONFIDENCE", "0.7"))
    # STAC emission (only if semantic enabled); default true so users get discovery metadata out of the box
    SEMANTIC_GENERATE_STAC = os.getenv("SEMANTIC_GENERATE_STAC", "true").lower() in {"1", "true", "yes"}
