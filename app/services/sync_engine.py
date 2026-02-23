from datetime import datetime
from typing import Dict, Any, List, Optional

from sqlalchemy.orm import Session

from app.models.sync_log import SyncLog


class SyncEngine:
    """
    Moteur central de synchronisation mobile.
    """

    def __init__(self, db: Session):
        self.db = db

    # =====================================================
    # MOBILE → SERVEUR
    # =====================================================
    def push_changes(
        self,
        tenant_id: str,
        device_id: str,
        changes: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Le mobile envoie ses modifications locales.
        """

        if not changes:
            return {"pushed": 0}

        pushed = 0

        for change in changes:
            log = SyncLog(
                tenant_id=tenant_id,
                device_id=device_id,
                table_name=change["table"],
                action=change["action"],
                data=change["data"],
            )

            self.db.add(log)

            # appliquer modification métier
            self._apply_change(change)

            pushed += 1

        self.db.commit()

        return {"pushed": pushed}

    # =====================================================
    # SERVEUR → MOBILE (DELTA SYNC)
    # =====================================================
    def pull_changes(
        self,
        tenant_id: str,
        last_sync_at: Optional[datetime],
    ) -> Dict[str, Any]:
        """
        Renvoie uniquement les changements depuis
        la dernière synchronisation.
        """

        query = self.db.query(SyncLog).filter(
            SyncLog.tenant_id == tenant_id
        )

        if last_sync_at:
            query = query.filter(SyncLog.created_at > last_sync_at)

        logs = query.order_by(SyncLog.created_at.asc()).all()

        return {
            "changes": [
                {
                    "table": log.table_name,
                    "action": log.action,
                    "data": log.data,
                    "timestamp": log.created_at.isoformat(),
                }
                for log in logs
            ],
            "server_time": datetime.utcnow().isoformat(),
        }

    # =====================================================
    # FULL SYNC (PUSH + PULL)
    # =====================================================
    def full_sync(
        self,
        tenant_id: str,
        device_id: str,
        last_sync: Optional[str],
        changes: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Synchronisation complète bidirectionnelle.
        """

        last_sync_dt = (
            datetime.fromisoformat(last_sync)
            if last_sync
            else None
        )

        pushed = self.push_changes(
            tenant_id=tenant_id,
            device_id=device_id,
            changes=changes,
        )

        pulled = self.pull_changes(
            tenant_id=tenant_id,
            last_sync_at=last_sync_dt,
        )

        return {
            "pushed": pushed,
            "pulled": pulled,
        }

    # =====================================================
    # APPLICATION DES MODIFICATIONS
    # =====================================================
    def _apply_change(self, change: Dict[str, Any]):
        """
        Applique CREATE / UPDATE / DELETE
        sur les tables métier.
        """

        table = change["table"]
        action = change["action"].upper()
        data = change["data"]

        # Exemple : produits
        if table == "products":
            from app.models.product import Product

            if action == "CREATE":
                self.db.add(Product(**data))

            elif action == "UPDATE":
                obj = self.db.query(Product).get(data["id"])
                if obj:
                    for k, v in data.items():
                        setattr(obj, k, v)

            elif action == "DELETE":
                obj = self.db.query(Product).get(data["id"])
                if obj:
                    self.db.delete(obj)
