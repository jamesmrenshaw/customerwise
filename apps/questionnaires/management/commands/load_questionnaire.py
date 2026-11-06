from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.projects.models import Project
from apps.questionnaires import services

PILOT = Path(__file__).resolve().parents[2] / "seed" / "pilot.json"


class Command(BaseCommand):
    help = "Create a draft questionnaire for a project from a JSON file (default: the D06 pilot seed)."

    def add_arguments(self, parser):
        parser.add_argument("project", help="Project ID, or its exact internal name")
        parser.add_argument("--file", default=str(PILOT))
        parser.add_argument("--publish", action="store_true", help="Publish the draft straight away")

    def handle(self, *args, project, file, publish, **options):
        try:
            proj = Project.objects.get(pk=project)
        except (Project.DoesNotExist, ValidationError, ValueError):
            proj = Project.objects.filter(internal_name=project).first()
        if proj is None:
            raise CommandError(f"No project found for {project!r}.")
        try:
            draft = services.create_draft_from_data(proj, services.load_json(file))
            if publish:
                services.publish(draft)
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages)) from exc
        questions = sum(s.questions.count() for s in draft.sections.all())
        self.stdout.write(
            self.style.SUCCESS(
                f"Created {draft} with {draft.sections.count()} sections and {questions} questions."
            )
        )
