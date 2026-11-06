"""Convert long-text answers written with the old **bold** / '- ' markup into rich-text HTML."""

from django.db import migrations


def forwards(apps, schema_editor):
    from apps.reports.formatting import from_simple_markup

    Answer = apps.get_model("reports", "Answer")
    for answer in Answer.objects.filter(question__answer_type="long_text").exclude(text=""):
        if answer.text.lstrip().startswith("<"):
            continue  # already HTML
        answer.text = from_simple_markup(answer.text)
        answer.save(update_fields=["text"])


class Migration(migrations.Migration):
    dependencies = [("reports", "0001_initial")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
