"""
The "Catalogue Sync" tab on the admin Settings screen: start a SAP item sync, watch it.

Thin, like the settings router next to it - the work is scripts/sap_sync.py and the
supervision is services/sap_sync_runner.py. This module is the HTTP edge: the permission
gate, and turning "one is already running" into a 409 the Settings panel can show.

Gated on `admin`, the same permission as the rest of the Settings screen and for the same
reason. A run reprices ~8,000 products and can hide any that SAP has stopped offering;
that is the owner's decision about the store, not a job like product_management.

Two verbs, because the job outlasts the request:

  POST /sap-sync/run     start a run, answer 202 immediately
  GET  /sap-sync/status  what it is doing, its console output, and the last reports

A synchronous endpoint that returned when the sync did would sit for minutes behind a
gateway timeout with no way to see progress, and a browser that gave up would look
exactly like a failed sync while the run carried on writing.
"""
from fastapi import APIRouter, Depends, HTTPException, status

from app.core.deps import require_permission
from app.models import User
from app.schemas import SapSyncRun
from app.services import sap_sync_runner
from app.services.sap_sync_runner import SapSyncBusy

router = APIRouter(prefix="/sap-sync", tags=["SAP sync"])

_admin = Depends(require_permission("admin"))


@router.get("/status")
def sap_sync_status(include_reports: bool = False, _: User = _admin):
    """Poll target. `include_reports=true` adds the full text of each run report - the
    panel asks for that on load and when a run ends, not on every poll."""
    return sap_sync_runner.status(include_reports=include_reports)


@router.post("/run", status_code=status.HTTP_202_ACCEPTED)
def run_sap_sync(payload: SapSyncRun, current_user: User = _admin):
    """202, not 200: the run has been accepted and is going on after this response.

    The caller gets the same shape GET /status returns, so the panel can render the
    "running" state from the answer to its own click without waiting for a poll.
    """
    try:
        return sap_sync_runner.start(
            payload.catalogue,
            payload.apply,
            actor=current_user.user_name or current_user.email,
        )
    except SapSyncBusy as exc:
        # 409 rather than 400: nothing is wrong with the request, it just cannot happen
        # yet. The Flask panel shows this message as-is.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
