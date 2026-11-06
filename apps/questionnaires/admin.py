from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db.models import Count

from . import services
from .models import Question, QuestionnaireVersion, Section


class DraftOnlyInline:
    """Inlines are editable only while the questionnaire is a draft (published versions never change)."""

    def _editable(self, obj):
        return obj is None or obj.is_draft

    def has_add_permission(self, request, obj=None):
        return self._editable(obj) and super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return self._editable(obj) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self._editable(obj) and super().has_delete_permission(request, obj)


class SectionInline(DraftOnlyInline, admin.TabularInline):
    model = Section
    extra = 0
    fields = ["order", "title", "display_style", "question_count"]
    readonly_fields = ["question_count"]
    show_change_link = True
    verbose_name_plural = "Sections (save, then use “Change” to edit a section's questions)"

    @admin.display(description="Questions")
    def question_count(self, obj):
        return obj.questions.count() if obj.pk else "—"


class QuestionInline(admin.StackedInline):
    model = Question
    extra = 0
    fields = [("order", "answer_type", "key"), "prompt", "guidance"]

    def _editable(self, obj):
        return obj is None or obj.version.is_draft

    def has_add_permission(self, request, obj=None):
        return self._editable(obj) and super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return self._editable(obj) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self._editable(obj) and super().has_delete_permission(request, obj)


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    """A section's questions. Reached from its questionnaire version; read-only once published."""

    list_display = ["title", "version", "display_style", "order"]
    list_filter = ["version__project"]
    fields = ["version", "order", "title", "display_style"]
    inlines = [QuestionInline]

    def get_readonly_fields(self, request, obj=None):
        return ["version"] if obj is not None else []

    def _editable(self, obj):
        return obj is None or obj.version.is_draft

    def has_change_permission(self, request, obj=None):
        return self._editable(obj) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self._editable(obj) and super().has_delete_permission(request, obj)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "version":
            kwargs["queryset"] = QuestionnaireVersion.objects.filter(status=QuestionnaireVersion.Status.DRAFT)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(QuestionnaireVersion)
class QuestionnaireVersionAdmin(admin.ModelAdmin):
    list_display = ["project", "number", "status", "section_count", "question_count", "published_at"]
    list_filter = ["status", "project"]
    actions = ["publish", "new_draft"]
    inlines = [SectionInline]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(_sections=Count("sections", distinct=True), _questions=Count("sections__questions"))
        )

    @admin.display(description="Sections", ordering="_sections")
    def section_count(self, obj):
        return obj._sections

    @admin.display(description="Questions", ordering="_questions")
    def question_count(self, obj):
        return obj._questions

    def get_inlines(self, request, obj):
        return [SectionInline] if obj else []

    def get_fields(self, request, obj=None):
        return ["project"] if obj is None else ["project", "number", "status", "published_at", "published_by"]

    def get_readonly_fields(self, request, obj=None):
        return [] if obj is None else ["project", "number", "status", "published_at", "published_by"]

    def save_model(self, request, obj, form, change):
        if not change:
            obj.number = services._next_number(obj.project)
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        # Published versions are kept for the reports that answered them.
        if obj is not None and not obj.is_draft:
            return False
        return super().has_delete_permission(request, obj)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if obj is None:

            class AddForm(form):
                def clean_project(self):
                    project = self.cleaned_data["project"]
                    if project.questionnaire_versions.filter(
                        status=QuestionnaireVersion.Status.DRAFT
                    ).exists():
                        raise ValidationError(
                            "This project already has a draft questionnaire. Edit or publish that one."
                        )
                    return project

            return AddForm
        return form

    @admin.action(description="Publish selected drafts")
    def publish(self, request, queryset):
        for version in queryset:
            try:
                services.publish(version, request.user)
                self.message_user(request, f"Published {version}.", messages.SUCCESS)
            except ValidationError as exc:
                self.message_user(request, f"{version}: " + " ".join(exc.messages), messages.ERROR)

    @admin.action(description="Start a new draft from the selected version")
    def new_draft(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Select exactly one version to copy.", messages.ERROR)
            return
        try:
            draft = services.new_draft_from(queryset.get())
            self.message_user(request, f"Created {draft}.", messages.SUCCESS)
        except ValidationError as exc:
            self.message_user(request, " ".join(exc.messages), messages.ERROR)
