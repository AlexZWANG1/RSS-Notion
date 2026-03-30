"""Webhook server — Notion Automation triggers Deep Reader.

When a page's 待深度阅读 checkbox is toggled in Notion,
Notion Automation sends a POST to this server, which:
  1. Scans all pages with 待深度阅读 = True
  2. YouTube → fetches transcript via youtube-transcript-api
  3. Article → fetches full text via Jina Reader
  4. LLM generates structured deep summary
  5. Writes summary back to Notion page
  6. Unchecks the checkbox

Setup:
  1. python -m api.webhook          (starts server on port 8900)
  2. ngrok http 8900                (exposes public URL)
  3. Notion Automation → POST to https://<ngrok>/webhook/deep-read

Run:  python -m api.webhook
"""

import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

load_dotenv()

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="RSS-Notion Deep Reader Webhook")


def _load_config() -> dict:
    import json
    from pathlib import Path
    config_path = Path(__file__).resolve().parent.parent / "config.json"
    with open(config_path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Webhook endpoints
# ---------------------------------------------------------------------------

@app.post("/webhook/deep-read")
async def deep_read(request: Request):
    """Notion Automation calls this when 待深度阅读 is checked.

    Processes all pending pages and returns result.
    """
    logger.info("🔔 Webhook received — processing deep read pages...")
    try:
        from generator.deep_reader import process_deep_read_pages
        config = _load_config()
        count = await process_deep_read_pages(config)
        logger.info(f"✅ Done — {count} pages processed")
        return JSONResponse({"ok": True, "processed": count})
    except Exception as e:
        logger.exception("Deep read failed")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/health")
async def health():
    return {"status": "ok", "mode": "webhook"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("WEBHOOK_PORT", "8900"))
    logger.info(f"🚀 Deep Reader webhook server starting on port {port}")
    logger.info(f"   POST /webhook/deep-read — Notion Automation endpoint")
    logger.info(f"   GET  /health            — Health check")
    uvicorn.run(app, host="0.0.0.0", port=port)
