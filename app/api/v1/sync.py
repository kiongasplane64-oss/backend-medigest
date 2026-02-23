from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_current_user
from app.schemas.sync import SyncPayload
from app.services.sync_service import process_sync

router = APIRouter(prefix="/sync", tags=["Sync"])

@router.post("/")
def sync_data(
    payload: SyncPayload,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    process_sync(db, user.tenant_id, payload.items)
    return {"message": "Synchronisation reçue"}
