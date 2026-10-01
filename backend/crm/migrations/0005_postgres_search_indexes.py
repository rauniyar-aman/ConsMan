from django.db import migrations

def create_indexes(apps,schema_editor):
    if schema_editor.connection.vendor=='postgresql':
        schema_editor.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
        schema_editor.execute('CREATE INDEX IF NOT EXISTS person_name_trgm ON crm_person USING gin (upper(full_name) gin_trgm_ops)')
        schema_editor.execute('CREATE INDEX IF NOT EXISTS contact_value_trgm ON crm_contactmethod USING gin (upper(normalized_value) gin_trgm_ops)')

def drop_indexes(apps,schema_editor):
    if schema_editor.connection.vendor=='postgresql':
        schema_editor.execute('DROP INDEX IF EXISTS person_name_trgm')
        schema_editor.execute('DROP INDEX IF EXISTS contact_value_trgm')

class Migration(migrations.Migration):
    dependencies=[('crm','0004_person_normalized_name')]
    operations=[migrations.RunPython(create_indexes,drop_indexes)]
