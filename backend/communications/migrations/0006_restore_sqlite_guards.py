import importlib
from django.db import migrations


def restore(apps,schema_editor):
    if schema_editor.connection.vendor=='sqlite':
        guards=importlib.import_module('communications.migrations.0003_history_guards')
        guards.remove(apps,schema_editor)
        guards.create(apps,schema_editor)


class Migration(migrations.Migration):
    dependencies=[('communications','0005_messageevent_estimated_cost_minor')]
    operations=[migrations.RunPython(restore,migrations.RunPython.noop)]
