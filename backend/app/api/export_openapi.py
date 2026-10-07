"""Print the OpenAPI document: ``python -m app.api.export_openapi > openapi.json``."""

import json
import sys

from app.api.app import create_app
from app.core.obs import configure_logging


def main() -> None:
    # Logs go to stderr so stdout holds only the JSON document.
    configure_logging(stream=sys.stderr)
    app = create_app(configure_logs=False)
    json.dump(app.openapi(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
