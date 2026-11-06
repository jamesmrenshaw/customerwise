import uuid

from django.db import models


class BaseModel(models.Model):
    """Base for domain models: UUID primary keys, so URLs never expose sequential IDs."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
