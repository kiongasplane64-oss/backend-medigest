from sqlalchemy.orm import Session
from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime, timezone, timedelta
import logging
import uuid
from uuid import UUID
from typing import Optional, Dict, Any, Union, List
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
        "pharmacy": "system"  # Ajout de la catégorie pharmacy
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
        """Journalise une action dans la table d'audit"""
        try:
            # Normalisation des UUIDs
            t_id = self._normalize_uuid(tenant_id)
            u_id = self._normalize_uuid(user_id) if user_id else None
            e_id = self._normalize_uuid(entity_id) if entity_id else None
            
            # Déterminer la catégorie d'action
            action_category = self.ACTION_CATEGORIES.get(cible.lower(), "system")
            
            log_payload = {
                "id": uuid.uuid4(),
                "tenant_id": t_id,
                "user_id": u_id,
                "action": action,
                "action_type": action.upper(),
                "action_category": action_category,
                "entity_type": cible,
                "entity_id": e_id,
                "description": description,
                "details": self._serialize_complex(details),
                "ip_address": ip,
                "user_agent": user_agent,
                "severity": severity.lower(),
                "created_at": datetime.now(timezone.utc)
            }

            # Injection sécurisée des kwargs supplémentaires
            for key, value in kwargs.items():
                if key in self._model_columns and key not in log_payload:
                    log_payload[key] = value
                elif key == 'status' and 'status_code' in self._model_columns:
                    log_payload['status_code'] = value
                elif key == 'pharmacy_id':  # Gestion spécifique de pharmacy_id
                    log_payload['pharmacy_id'] = self._normalize_uuid(value) if value else None

            # Création et sauvegarde du log
            audit_log = AuditLog(**log_payload)
            db.add(audit_log)
            db.flush() 
            
            # Log dans la console
            self._log_to_stdout(log_payload)
            return audit_log

        except Exception as e:
            logger.critical(f"CRITICAL AUDIT FAILURE: {str(e)}")
            self._log_fallback(action, cible, description, tenant_id)
            return None

    def _normalize_uuid(self, value: Any) -> Optional[UUID]:
        """Normalise une valeur en UUID"""
        if value is None:
            return None
        if isinstance(value, UUID): 
            return value
        try: 
            return UUID(str(value))
        except (ValueError, AttributeError): 
            # Retourner None au lieu d'un UUID vide pour éviter les erreurs de contrainte
            return None

    def _serialize_complex(self, data: Any) -> Optional[Dict]:
        """Sérialise les données complexes en JSON"""
        if not data:
            return {}
        if isinstance(data, str): 
            try: 
                return json.loads(data)
            except json.JSONDecodeError: 
                return {"info": data}
        try:
            # Fonction de sérialisation personnalisée
            def json_serializer(obj):
                if isinstance(obj, (datetime, UUID)):
                    return str(obj)
                if hasattr(obj, 'dict'):  # Pour les modèles Pydantic
                    return obj.dict()
                if hasattr(obj, '__dict__'):  # Pour les objets simples
                    return str(obj)
                return None
                
            return json.loads(
                json.dumps(data, default=json_serializer)
            )
        except Exception as e:
            logger.warning(f"Erreur de sérialisation: {e}")
            return {"error": "Serialization failed", "original_type": str(type(data))}

    def _log_to_stdout(self, data: Dict):
        """Log dans la console avec formatage"""
        level = data.get('severity', 'info').upper()
        action_type = data.get('action_type', 'UNKNOWN')
        entity_type = data.get('entity_type', 'unknown')
        description = data.get('description', '')
        
        msg = f"AUDIT [{level}] {action_type} | {entity_type} | {description}"
        if level in ['ERROR', 'CRITICAL']:
            logger.error(msg)
        else:
            logger.info(msg)

    def _log_fallback(self, action, cible, desc, t_id):
        """Fallback en cas d'échec critique"""
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

    def get_logs_by_tenant(
        self, 
        db: Session, 
        tenant_id: Union[str, UUID], 
        skip: int = 0, 
        limit: int = 100,
        entity_type: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> List[AuditLog]:
        """Récupère les logs d'audit pour un tenant avec filtres optionnels"""
        try:
            t_id = self._normalize_uuid(tenant_id)
            query = db.query(AuditLog).filter(AuditLog.tenant_id == t_id)
            
            if entity_type:
                query = query.filter(AuditLog.entity_type == entity_type)
            
            if start_date:
                query = query.filter(AuditLog.created_at >= start_date)
            
            if end_date:
                query = query.filter(AuditLog.created_at <= end_date)
            
            return query.order_by(AuditLog.created_at.desc()).offset(skip).limit(limit).all()
            
        except Exception as e:
            logger.error(f"Erreur lors de la récupération des logs: {e}")
            return []

# --- Instance Unique ---
audit_service = AuditService()

# --- Fonctions à appeler depuis l'extérieur ---

def log_action(db: Session, **kwargs):
    """Wrapper pour la fonction de log"""
    return audit_service.log_action(db, **kwargs)

def run_auto_prune():
    """Fonction utilitaire pour le nettoyage (à appeler dans main.py ou via un cron)"""
    db = SessionLocal()
    try:
        audit_service.prune_old_logs(db, days=30)
    finally:
        db.close()