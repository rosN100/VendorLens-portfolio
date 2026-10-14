"""Run with `python -m app.storage.demo`; uses only a fixed synthetic payload."""

import argparse
from dataclasses import asdict
import json
import logging
from uuid import UUID

from app.db import get_engine
from app.storage.errors import StorageError
from app.storage.local import LocalFiles
from app.storage.service import FileStore

DEMO_BYTES = b"VendorLens F1 synthetic storage check.\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    save = commands.add_parser("save")
    save.add_argument("--request-id", required=True, type=UUID)
    read = commands.add_parser("read")
    read.add_argument("file_id", type=UUID)
    commands.add_parser("inspect-orphans")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    try:
        store = FileStore(get_engine(), LocalFiles())
        if args.command == "save":
            receipt = store.save(DEMO_BYTES, "f1-demo.txt", args.request_id)
            print(json.dumps(asdict(receipt), default=str, indent=2))
        elif args.command == "read":
            data = store.read(args.file_id)
            # Deliberately do not print arbitrary stored file contents.
            print(json.dumps({"file_id": str(args.file_id), "bytes_read": len(data), "checksum_verified": True}))
        else:
            print(json.dumps({"orphan_candidates": store.inspect_orphans(),
                              "note": "Read-only snapshot; stop writers before interpreting. Nothing deleted."}, indent=2))
    except (StorageError, RuntimeError, ValueError) as error:
        print(json.dumps({"error": getattr(error, "code", "configuration_error"), "message": str(error)}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
