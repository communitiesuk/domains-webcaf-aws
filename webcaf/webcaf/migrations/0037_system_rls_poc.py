from django.db import migrations

TENANT_EXPRESSION = """
current_setting('webcaf.access_scope', true) = 'tenant'
AND organisation_id = NULLIF(current_setting('webcaf.organisation_id', true), '')::bigint
"""

ADMIN_EXPRESSION = """
current_setting('webcaf.access_scope', true) = 'admin'
AND EXISTS (
    SELECT 1
    FROM public.auth_user AS rls_user
    WHERE rls_user.id = NULLIF(current_setting('webcaf.user_id', true), '')::integer
      AND rls_user.is_active
      AND rls_user.is_staff
)
"""


def create_policy_sql(table: str, policy_prefix: str) -> str:
    return f"""
CREATE POLICY {policy_prefix}_tenant ON public.{table}
    AS PERMISSIVE FOR ALL TO webcaf_app
    USING ({TENANT_EXPRESSION})
    WITH CHECK ({TENANT_EXPRESSION});

CREATE POLICY {policy_prefix}_admin ON public.{table}
    AS PERMISSIVE FOR ALL TO webcaf_app
    USING ({ADMIN_EXPRESSION})
    WITH CHECK ({ADMIN_EXPRESSION});

CREATE POLICY {policy_prefix}_owner ON public.{table}
    AS PERMISSIVE FOR ALL TO webcaf_owner
    USING (true)
    WITH CHECK (true);

ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY;
"""


def drop_policy_sql(table: str, policy_prefix: str) -> str:
    return f"""
DROP POLICY {policy_prefix}_tenant ON public.{table};
DROP POLICY {policy_prefix}_admin ON public.{table};
DROP POLICY {policy_prefix}_owner ON public.{table};
ALTER TABLE public.{table} NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("webcaf", "0036_default_framework_caf40"),
    ]

    operations = [
        migrations.RunSQL(
            sql=create_policy_sql("webcaf_system", "webcaf_system_rls"),
            reverse_sql=drop_policy_sql("webcaf_system", "webcaf_system_rls"),
        ),
        migrations.RunSQL(
            sql=create_policy_sql("webcaf_historicalsystem", "webcaf_historicalsystem_rls"),
            reverse_sql=drop_policy_sql("webcaf_historicalsystem", "webcaf_historicalsystem_rls"),
        ),
    ]
