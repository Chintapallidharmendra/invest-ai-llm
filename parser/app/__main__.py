"""``python -m app``: serve on ``PARSER_PORT`` (default 8080)."""

import os

from app.server import serve

if __name__ == "__main__":
    serve(port=int(os.environ.get("PARSER_PORT", "8080")))
