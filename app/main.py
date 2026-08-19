from __future__ import annotations

import csv
import io
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import APP_NAME, EXTREME_HEAT_WARNING_THRESHOLD_F, HEAT_ADVISORY_THRESHOLD_F, NWS_ZONE_VERSION, NWS_ZONES_URL
from .service import VerificationService, month_bounds_utc
from .zones import ZoneStore

BASE_DIR = Path(__file__).resolve().parent; STATIC_DIR = BASE_DIR / "static"
app = FastAPI(title=APP_NAME, version="1.0.0"); app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
zone_store = ZoneStore(); service = VerificationService(); executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="heat-verify")
jobs: dict[str, dict] = {}; jobs_lock = threading.Lock()

class JobRequest(BaseModel):
    month: str = Field(pattern=r"^\d{4}-\d{2}$")

def update_job(job_id: str, **changes):
    with jobs_lock: jobs[job_id].update(changes)

def run_job(job_id: str, month: str):
    try:
        update_job(job_id, status="running", progress=0.01, message="Starting…")
        def progress(value, message): update_job(job_id, progress=round(float(value), 3), message=message)
        result = service.verify_month(month, progress=progress)
        update_job(job_id, status="done", progress=1.0, message="Verification complete", result=result)
    except Exception as exc:
        update_job(job_id, status="error", message="Verification failed", error=f"{type(exc).__name__}: {exc}")

@app.get("/")
def index(): return FileResponse(STATIC_DIR / "index.html")

@app.get("/api/health")
def health(): return {"status": "ok", "app": APP_NAME}

@app.get("/api/config")
def config():
    return {"wfo": "LIX", "criteria": {"heat_advisory_f": HEAT_ADVISORY_THRESHOLD_F, "extreme_heat_warning_f": EXTREME_HEAT_WARNING_THRESHOLD_F}, "zones": {"version": NWS_ZONE_VERSION, "url": NWS_ZONES_URL}}

@app.get("/api/zones")
def zones():
    try: return JSONResponse(zone_store.geojson())
    except Exception as exc: raise HTTPException(status_code=502, detail=f"Unable to load NWS zones: {exc}") from exc

@app.post("/api/jobs")
def create_job(request: JobRequest):
    try: month_bounds_utc(request.month)
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc
    job_id = uuid.uuid4().hex[:12]
    with jobs_lock:
        jobs[job_id] = {"id": job_id, "month": request.month, "status": "queued", "progress": 0.0, "message": "Queued", "result": None, "error": None}
    executor.submit(run_job, job_id, request.month); return {"id": job_id, "status": "queued"}

@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job: raise HTTPException(status_code=404, detail="Job not found")
        return dict(job)

@app.get("/api/jobs/{job_id}/csv")
def job_csv(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job: raise HTTPException(status_code=404, detail="Job not found")
        if job["status"] != "done" or not job["result"]: raise HTTPException(status_code=409, detail="Job is not complete")
        result = job["result"]
    output = io.StringIO(); fields = ["event_key", "product", "threshold_f", "event_start_utc", "event_end_utc", "ugc", "zone_name", "zone_start_utc", "zone_end_utc", "urma_max_hi_f", "urma_verified", "urma_time_utc", "urma_lat", "urma_lon", "obs_max_hi_f", "obs_verified", "obs_station", "obs_time_utc", "obs_lat", "obs_lon"]
    writer = csv.DictWriter(output, fieldnames=fields); writer.writeheader()
    for event in result["events"]:
        for zone in event["zones"]:
            urma = zone.get("urma") or {}; obs = zone.get("observation") or {}
            writer.writerow({"event_key": event["key"], "product": event["product"], "threshold_f": event["threshold_f"], "event_start_utc": event["start_utc"], "event_end_utc": event["end_utc"], "ugc": zone["ugc"], "zone_name": zone["zone_name"], "zone_start_utc": zone["start_utc"], "zone_end_utc": zone["end_utc"], "urma_max_hi_f": urma.get("heat_index_f"), "urma_verified": urma.get("verified"), "urma_time_utc": urma.get("valid_utc"), "urma_lat": urma.get("lat"), "urma_lon": urma.get("lon"), "obs_max_hi_f": obs.get("heat_index_f"), "obs_verified": obs.get("verified"), "obs_station": obs.get("station"), "obs_time_utc": obs.get("valid_utc"), "obs_lat": obs.get("lat"), "obs_lon": obs.get("lon")})
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="LIX_heat_verification_{result["month"]}.csv"'})
