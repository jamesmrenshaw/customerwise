from .models import AuditEvent


def record(actor, action, obj=None, *, project=None, **data):
    """Write an audit event. `data` must be JSON-serialisable and must not contain secrets."""
    return AuditEvent.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        object_type=obj._meta.label_lower if obj is not None else "",
        object_id=str(obj.pk) if obj is not None else "",
        project=project,
        data=data,
    )
