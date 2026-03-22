"""FastAPI backend for RSS-Notion AI Daily Digest."""

import asyncio
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"

# ---------------------------------------------------------------------------
# Job tracker for async pipeline runs
# ---------------------------------------------------------------------------

class JobStatus(BaseModel):
    job_id: str
    status: str  # "pending" | "running" | "completed" | "failed"
    started_at: str
    finished_at: str | None = None
    error: str | None = None

_jobs: dict[str, JobStatus] = {}

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="AI Daily Digest API",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _scan_reports() -> list[dict[str, Any]]:
    """Return a list of available report dates (newest first)."""
    if not OUTPUT_DIR.is_dir():
        return []

    reports: list[dict[str, Any]] = []
    for folder in sorted(OUTPUT_DIR.iterdir(), reverse=True):
        if not folder.is_dir():
            continue
        # Only consider folders that look like a date (YYYY-MM-DD*)
        name = folder.name
        has_pdf = (folder / "report.pdf").exists()
        has_json = (folder / "data.json").exists()
        if has_pdf or has_json:
            reports.append({
                "date": name,
                "has_pdf": has_pdf,
                "has_json": has_json,
            })
    return reports


def _get_date_dir(date: str) -> Path:
    """Resolve and validate a date folder path."""
    # Prevent path traversal
    if ".." in date or "/" in date or "\\" in date:
        raise HTTPException(status_code=400, detail="Invalid date parameter")
    date_dir = OUTPUT_DIR / date
    if not date_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"No report found for {date}")
    return date_dir

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@app.get("/api/reports")
async def list_reports():
    """List available reports."""
    return _scan_reports()


@app.get("/api/reports/{date}")
async def get_report_data(date: str):
    """Return the data.json content for a specific date."""
    date_dir = _get_date_dir(date)
    data_path = date_dir / "data.json"
    if not data_path.exists():
        raise HTTPException(status_code=404, detail=f"No data.json for {date}")
    content = json.loads(data_path.read_text(encoding="utf-8"))
    return content


@app.get("/api/reports/{date}/pdf")
async def get_report_pdf(date: str):
    """Serve the PDF file for download."""
    date_dir = _get_date_dir(date)
    pdf_path = date_dir / "report.pdf"
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail=f"No report.pdf for {date}")
    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=f"digest-{date}.pdf",
    )


@app.post("/api/trigger")
async def trigger_pipeline():
    """Trigger an async pipeline run. Returns a job ID immediately."""
    job_id = uuid.uuid4().hex[:12]
    job = JobStatus(
        job_id=job_id,
        status="pending",
        started_at=datetime.now().isoformat(),
    )
    _jobs[job_id] = job

    asyncio.create_task(_run_pipeline(job_id))
    return {"job_id": job_id, "status": job.status}


@app.get("/api/trigger/{job_id}")
async def get_job_status(job_id: str):
    """Check the status of a triggered pipeline run."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    return job

# ---------------------------------------------------------------------------
# Webhook: Deep Reader trigger
# ---------------------------------------------------------------------------

@app.post("/api/webhook/deep-read")
async def webhook_deep_read(payload: dict | None = None):
    """Webhook endpoint for Notion — triggers Deep Reader.

    Call this when a page's 待深度阅读 checkbox is toggled.
    Notion automation → HTTP request → this endpoint → fetch transcript → write summary.
    """
    job_id = f"dr-{uuid.uuid4().hex[:8]}"
    job = JobStatus(
        job_id=job_id,
        status="pending",
        started_at=datetime.now().isoformat(),
    )
    _jobs[job_id] = job

    asyncio.create_task(_run_deep_reader(job_id))
    return {"job_id": job_id, "status": "processing", "message": "Deep Reader triggered"}


@app.post("/api/webhook/notion")
async def webhook_notion(payload: dict | None = None):
    """Generic Notion webhook — auto-detects action and routes accordingly."""
    # For now, always trigger deep reader
    return await webhook_deep_read(payload)


async def _run_deep_reader(job_id: str) -> None:
    """Execute Deep Reader in the background."""
    job = _jobs[job_id]
    job.status = "running"
    try:
        from dotenv import load_dotenv
        load_dotenv()
        from main import load_config
        from generator.deep_reader import process_deep_read_pages

        config = load_config()
        count = await process_deep_read_pages(config)
        job.status = "completed"
        logger.info(f"Deep Reader webhook done: {count} pages processed")
    except Exception as e:
        logger.exception("Deep Reader webhook failed")
        job.status = "failed"
        job.error = str(e)
    finally:
        job.finished_at = datetime.now().isoformat()


# ---------------------------------------------------------------------------
# Background pipeline runner
# ---------------------------------------------------------------------------

async def _run_pipeline(job_id: str) -> None:
    """Execute the main pipeline in the background."""
    job = _jobs[job_id]
    job.status = "running"
    try:
        # Import here to avoid circular / heavy imports at module level
        from main import run_pipeline, load_config

        config = load_config()
        await run_pipeline(config, skip_email=True)
        job.status = "completed"
    except Exception as e:
        logger.exception("Pipeline run failed")
        job.status = "failed"
        job.error = str(e)
    finally:
        job.finished_at = datetime.now().isoformat()

# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    import uvicorn
    uvicorn.run(
        "api.server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )


if __name__ == "__main__":
    main()
