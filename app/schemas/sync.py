from pydantic import BaseModel
from typing import List, Dict, Any

class SyncItem(BaseModel):
    table_name: str
    action: str
    data: Dict[str, Any]

class SyncPayload(BaseModel):
    items: List[SyncItem]
