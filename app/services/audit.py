from sqlalchemy.orm import Session

from app.db.models import AuditLog


def create_audit_log(
    db: Session,
    *,
    actor_account_id: int | None,
    actor_username: str | None,
    actor_role: str | None,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    target_label: str | None = None,
    details: str | None = None,
) -> AuditLog:
    log_entry = AuditLog(
        actor_account_id=actor_account_id,
        actor_username=actor_username,
        actor_role=actor_role,
        action=action,
        target_type=target_type,
        target_id=target_id,
        target_label=target_label,
        details=details,
    )
    db.add(log_entry)
    return log_entry
