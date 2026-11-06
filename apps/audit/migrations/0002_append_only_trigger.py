"""Make audit_auditevent append-only: the database rejects any UPDATE or DELETE (CW-085)."""

from django.db import migrations

FORWARD = """
CREATE FUNCTION audit_reject_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Audit events are append-only and cannot be %', lower(TG_OP);
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_auditevent_append_only
    BEFORE UPDATE OR DELETE ON audit_auditevent
    FOR EACH ROW EXECUTE FUNCTION audit_reject_change();

CREATE TRIGGER audit_auditevent_no_truncate
    BEFORE TRUNCATE ON audit_auditevent
    FOR EACH STATEMENT EXECUTE FUNCTION audit_reject_change();
"""

REVERSE = """
DROP TRIGGER IF EXISTS audit_auditevent_no_truncate ON audit_auditevent;
DROP TRIGGER IF EXISTS audit_auditevent_append_only ON audit_auditevent;
DROP FUNCTION IF EXISTS audit_reject_change();
"""


class Migration(migrations.Migration):
    dependencies = [("audit", "0001_initial")]

    operations = [migrations.RunSQL(FORWARD, REVERSE)]
