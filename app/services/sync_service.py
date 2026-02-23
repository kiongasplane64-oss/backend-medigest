# app/services/sync_service.py

from typing import List, Dict, Any
from datetime import datetime

from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.models.sync_log import SyncLog


def process_sync(
    db: Session,
    tenant_id: str,
    items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Traite les données de synchronisation envoyées par un client mobile.

    Chaque item doit contenir :
        - table_name : nom de la table cible
        - action : CREATE | UPDATE | DELETE
        - data : données à appliquer
    """

    processed = 0
    errors = []

    try:
        for item in items:
            table_name = item.get("table_name")
            action = item.get("action")
            data = item.get("data", {})

            # ---- Validation minimale ----
            if not table_name or not action:
                errors.append(
                    {
                        "item": item,
                        "error": "table_name ou action manquant",
                    }
                )
                continue

            # ---- Enregistrement du log de synchronisation ----
            log = SyncLog(
                tenant_id=tenant_id,
                table_name=table_name,
                action=action,
                data=data,
                created_at=datetime.utcnow(),
            )

            db.add(log)

            # --------------------------------------------------
            # FUTURE : Application réelle sur les tables métier
            # --------------------------------------------------
            if action.upper() == "CREATE":
                _handle_create(db, table_name, data)

            elif action.upper() == "UPDATE":
                _handle_update(db, table_name, data)

            elif action.upper() == "DELETE":
                _handle_delete(db, table_name, data)

            else:
                errors.append(
                    {
                        "item": item,
                        "error": f"Action inconnue: {action}",
                    }
                )
                continue

            processed += 1

        db.commit()

    except SQLAlchemyError as e:
        db.rollback()
        return {
            "status": "error",
            "message": str(e),
            "processed": processed,
        }

    return {
        "status": "success",
        "processed": processed,
        "errors": errors,
        "synced_at": datetime.utcnow().isoformat(),
    }


# ==========================================================
# HANDLERS CRUD (BASE EXTENSIBLE)
# ==========================================================

def _handle_create(db: Session, table_name: str, data: Dict[str, Any]):
    """
    Applique une création.
    À connecter plus tard avec tes modèles métiers.
    """
    # Exemple :
    # if table_name == "products":
    #     from app.models.product import Product
    #     obj = Product(**data)
    #     db.add(obj)
    pass


def _handle_update(db: Session, table_name: str, data: Dict[str, Any]):
    """
    Applique une mise à jour.
    """
    # Exemple :
    # if table_name == "products":
    #     from app.models.product import Product
    #     obj = db.query(Product).get(data["id"])
    #     for key, value in data.items():
    #         setattr(obj, key, value)
    pass


def _handle_delete(db: Session, table_name: str, data: Dict[str, Any]):
    """
    Applique une suppression.
    """
    # Exemple :
    # if table_name == "products":
    #     from app.models.product import Product
    #     obj = db.query(Product).get(data["id"])
    #     if obj:
    #         db.delete(obj)
    pass
