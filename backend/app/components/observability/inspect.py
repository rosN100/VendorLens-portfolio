"""Read-only audit lookup: python -m app.components.observability.inspect --file-id UUID."""

import argparse
import json
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.components.observability.queries import find_events
from app.db import get_engine


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file-id", type=UUID)
    parser.add_argument("--correlation-id", type=UUID)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    if args.file_id is None and args.correlation_id is None:
        parser.error("Provide --file-id or --correlation-id.")
    if not 1 <= args.limit <= 100:
        parser.error("--limit must be between 1 and 100.")
    try:
        rows = find_events(get_engine(), file_id=args.file_id, correlation_id=args.correlation_id, limit=args.limit)
    except ValueError as error:
        parser.error(str(error))
    except (RuntimeError, SQLAlchemyError):
        print(json.dumps({"error": "audit_unavailable", "message": "Audit events could not be read."}))
        return 1
    print(json.dumps({"events": rows}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
