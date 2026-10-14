# VendorLens

VendorLens is a product design for evidence-grounded vendor risk review. This repository contains a local pilot of its intake and evidence-processing stages.

**Current state:** create a review case, collect four types of PDF evidence, retain the originals, parse documents, extract claims with source references, and record processing revisions and checkpoints. The product design goes further; policy evaluation, a final recommendation and the reviewer decision flow are not implemented here. This has not been deployed for production vendor decisions.

## Product decisions

The designed review flow is:

`Documents → Claims → Evidence → Policy → Investigation → Human review`

- Keep claims traceable to their source documents and locations.
- Show missing or conflicting evidence as unresolved findings.
- Treat a processing failure separately from a policy finding.
- Require successful checks before a review package can be complete.
- Leave the vendor decision with a human reviewer.

These describe the design direction; they do not imply every stage is implemented in this pilot.

## What is in this repository

- **Case intake:** create cases, accept four required PDF categories, preserve source files, and record intake events.
- **Evidence processing:** parse supported PDFs, classify document types, extract claims and support checks, and persist processing state and evidence revisions.
- **Interface:** a local React application for case intake and viewing processing results.
- **Backend:** a FastAPI service with PostgreSQL storage and migrations.

Scanned-document OCR uses Apple Vision on macOS. Model-assisted extraction requires a Google Gemini API key. When enabled, parsed document content is sent to Google Gemini; use synthetic documents unless you have approval to send the data to that provider.

## Local setup

This is an experimental local pilot. It has no user authentication and is not hardened for internet exposure. Do not use real supplier documents or expose the service publicly.

Requirements: Python 3.12, Node.js 20.19 or newer, PostgreSQL 17, and macOS for scanned-document OCR.

1. Create a PostgreSQL role and database. Choose a local password and use the same value in `.env`:

   ```sh
   psql -d postgres -c "CREATE ROLE vendorlens LOGIN PASSWORD 'replace-with-a-local-password';"
   createdb --owner=vendorlens vendorlens
   ```

2. Copy `.env.example` to `.env`. Set `DATABASE_URL` to include the local password you chose. Leave `GEMINI_API_KEY` blank if you only want to run the intake flow. Never commit `.env`.
3. Install and start the backend:

   ```sh
   cd backend
   python3.12 -m venv .venv
   source .venv/bin/activate
   pip install -e '.[dev]'
   cd ..
   set -a
   source .env
   set +a
   cd backend
   alembic upgrade head
   uvicorn app.main:app --reload
   ```

4. In another terminal, start the frontend:

   ```sh
   cd frontend
   npm ci
   npm run dev
   ```

5. Open the local Vite URL printed in the terminal. The API health check is `/api/health`; interactive API docs are at `http://127.0.0.1:8000/docs`.

To run automated checks, install the development dependencies above, then run `pytest` from `backend` and `npm test` from `frontend`. PostgreSQL integration tests require a separate test database, as described in `backend/tests/conftest.py`.

## Technology

React, TypeScript, Vite, Python, FastAPI, SQLAlchemy, Alembic, PostgreSQL, LangChain, Gemini and LangGraph.

No open-source license is included.
