from sqlalchemy.orm import Session
from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime, timezone, timedelta
import logging
import uuid
from uuid import UUID
from typing import Optional, Dict, Any, Union
import json
from app.models.audit_log import AuditLog
# On ne définit pas SessionLocal ici si on veut éviter les imports circulaires, 
# mais on garde l'accès pour la fonction de nettoyage automatique.
from app.db.session import SessionLocal 

logger = logging.getLogger(__name__)

class AuditService:
    """Service d'audit expert - Gère la persistence des logs et la sécurité des types"""
    
    ACTION_CATEGORIES = {
        "user": "users", "tenant": "system", "product": "inventory",
        "inventory": "inventory", "sale": "sales", "purchase": "purchases",
        "client": "clients", "payment": "financial", "refund": "financial",
        "stock_movement": "inventory", "category": "inventory", "supplier": "purchases",
        "report": "data", "audit": "security", "permission": "security",
        "role": "security", "pharmacy": "system", "caisse": "financial",
    }

    def __init__(self):
        # Cache des colonnes du modèle pour éviter les 'invalid keyword argument'
        try:
            self._model_columns = {c.key for c in inspect(AuditLog).mapper.column_attrs}
        except Exception:
            # Fallback au cas où l'inspect échoue au démarrage
            self._model_columns = set()

    def log_action(
        self,
        db: Session,
        tenant_id: Union[str, UUID],
        user_id: Union[str, UUID, None],
        action: str,
        cible: str,
        description: str,
        ip: str = None,
        user_agent: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        entity_id: Optional[Union[str, UUID]] = None,
        severity: str = "info",
        **kwargs
    ) -> Optional[AuditLog]:
        try:
            t_id = self._normalize_uuid(tenant_id)
            u_id = self._normalize_uuid(user_id) if user_id else None
            e_id = self._normalize_uuid(entity_id) if entity_id else None
            
            log_payload = {
                "id": uuid.uuid4(),
                "tenant_id": t_id,
                "user_id": u_id,
                "action": action,
                "action_type": action.upper(),
                "action_category": self.ACTION_CATEGORIES.get(cible.lower(), "system"),
                "entity_type": cible,
                "entity_id": e_id,
                "description": description,
                "details": self._serialize_complex(details),
                "ip_address": ip,
                "user_agent": user_agent,
                "severity": severity.lower(),
                "created_at": datetime.now(timezone.utc)
            }

            # Injection sécurisée des kwargs
            for key, value in kwargs.items():
                if key in self._model_columns and key not in log_payload:
                    log_payload[key] = value
                elif key == 'status' and 'status_code' in self._model_columns:
                    log_payload['status_code'] = value

            audit_log = AuditLog(**log_payload)
            db.add(audit_log)
            db.flush() 
            
            self._log_to_stdout(log_payload)
            return audit_log

        except Exception as e:
            logger.critical(f"CRITICAL AUDIT FAILURE: {str(e)}")
            self._log_fallback(action, cible, description, tenant_id)
            return None

    def _normalize_uuid(self, value: Any) -> UUID:
        if isinstance(value, UUID): return value
        try: return UUID(str(value))
        except: return UUID("00000000-0000-0000-0000-000000000000")

    def _serialize_complex(self, data: Any) -> Dict:
        if not data: return {}
        if isinstance(data, str): 
            try: return json.loads(data)
            except: return {"info": data}
        try:
            return json.loads(
                json.dumps(data, default=lambda o: str(o) if isinstance(o, (datetime, UUID)) else None)
            )
        except: return {"error": "Serialization failed"}

    def _log_to_stdout(self, data: Dict):
        level = data.get('severity', 'info').upper()
        msg = f"AUDIT [{level}] {data['action_type']} | {data['entity_type']} | {data['description']}"
        if level in ['ERROR', 'CRITICAL']:
            logger.error(msg)
        else:
            logger.info(msg)

    def _log_fallback(self, action, cible, desc, t_id):
        logger.warning(f"!!! FALLBACK AUDIT !!! -> {action} ON {cible} | Tenant: {t_id} | Desc: {desc}")
    
    def prune_old_logs(self, db: Session, days: int = 30) -> int:
        """Supprime les logs obsolètes"""
        try:
            threshold_date = datetime.now(timezone.utc) - timedelta(days=days)
            deleted_count = db.query(AuditLog).filter(
                AuditLog.created_at < threshold_date
            ).delete(synchronize_session=False)
            
            db.commit()
            if deleted_count > 0:
                logger.info(f"NETTOYAGE : {deleted_count} logs d'audit supprimés (plus de {days} jours).")
            return deleted_count
        except Exception as e:
            db.rollback()
            logger.error(f"Erreur nettoyage audits: {str(e)}")
            return 0

# --- Instance Unique ---
audit_service = AuditService()

# --- Fonctions à appeler depuis l'extérieur ---

def log_action(db: Session, **kwargs):
    return audit_service.log_action(db, **kwargs)

def run_auto_prune():
    """Fonction utilitaire pour le nettoyage (à appeler dans main.py ou via un cron)"""
    db = SessionLocal()
    try:
        audit_service.prune_old_logs(db, days=30)
    finally:
        db.close()