from django import forms
from django.contrib import admin, messages
from django.db.models import Count
from django.utils import timezone

from . import services
from .models import BriefVersion, ClientOrganisation, Project, Target


@admin.register(ClientOrganisation)
class ClientOrganisationAdmin(admin.ModelAdmin):
    list_display = ["name", "reference", "is_active", "project_count"]
    list_filter = ["is_active"]
    search_fields = ["name", "reference"]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_projects=Count("projects"))

    @admin.display(description="Projects", ordering="_projects")
    def project_count(self, obj):
        return obj._projects


class ProjectForm(forms.ModelForm):
    brief = forms.CharField(
        label="Shopper brief",
        required=False,
        widget=forms.Textarea(attrs={"rows": 14, "cols": 100}),
        help_text=(
            "What shoppers see. Saving a change creates a new version; shoppers must acknowledge it "
            "before they can submit a report. Never name the client here."
        ),
    )

    class Meta:
        model = Project
        fields = [
            "client",
            "internal_name",
            "client_facing_name",
            "shopper_facing_name",
            "status",
            "ended_at",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.latest_brief:
            self.fields["brief"].initial = self.instance.latest_brief.text

    def clean(self):
        data = super().clean()
        internal = (data.get("internal_name") or "").strip().lower()
        for field in ("client_facing_name", "shopper_facing_name"):
            if internal and (data.get(field) or "").strip().lower() == internal:
                self.add_error(field, "Use a different name from the internal one, so it can't leak.")
        client = data.get("client")
        brief = (data.get("brief") or "").lower()
        if client and client.name.lower() in brief:
            self.add_error(
                "brief", f"The brief mentions the client ({client.name}); shoppers must not see it."
            )
        return data


class BriefVersionInline(admin.TabularInline):
    model = BriefVersion
    extra = 0
    can_delete = False
    fields = ["number", "created_at", "created_by", "acknowledged_by"]
    readonly_fields = fields
    verbose_name_plural = "Brief history"

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="Acknowledged by")
    def acknowledged_by(self, obj):
        names = [str(a.user) for a in obj.acknowledgements.select_related("user")]
        return ", ".join(names) or "—"


class TargetInline(admin.TabularInline):
    model = Target
    extra = 0
    fields = ["name", "website", "added_by_shopper", "archived_at"]
    readonly_fields = ["added_by_shopper"]
    show_change_link = True


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    form = ProjectForm
    list_display = ["internal_name", "client", "shopper_facing_name", "status", "created_at"]
    list_filter = ["status", "client"]
    search_fields = ["internal_name", "client_facing_name", "shopper_facing_name", "client__name"]
    fieldsets = [
        (None, {"fields": ["client", "status", "ended_at"]}),
        ("Names", {"fields": ["internal_name", "client_facing_name", "shopper_facing_name"]}),
        ("Brief", {"fields": ["brief"]}),
    ]
    inlines = [TargetInline, BriefVersionInline]

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        version = services.update_brief(obj, form.cleaned_data.get("brief"), request.user)
        if version and version.number > 1:
            self.message_user(
                request,
                f"Brief saved as version {version.number}. Shoppers will be asked to acknowledge the change.",
                messages.INFO,
            )

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for obj in instances:
            if isinstance(obj, Target) and obj.pk is None:
                obj.added_by = request.user
        formset.save()
        if formset.model is Target:
            for obj in formset.new_objects:
                _warn_duplicates(self, request, obj)


def _warn_duplicates(model_admin, request, target):
    dupes = services.find_duplicate_targets(target.project, target.name, target.website, exclude=target)
    if dupes:
        names = ", ".join(t.name for t in dupes)
        model_admin.message_user(
            request,
            f"Possible duplicate: “{target.name}” matches existing target(s) {names} on this project.",
            messages.WARNING,
        )


@admin.register(Target)
class TargetAdmin(admin.ModelAdmin):
    list_display = ["name", "project", "domain", "added_by_shopper", "current_shopper", "archived_at"]
    list_filter = ["project", "added_by_shopper", ("archived_at", admin.EmptyFieldListFilter)]
    search_fields = ["name", "website", "project__internal_name"]
    fields = ["project", "name", "website", "domain", "added_by", "added_by_shopper", "archived_at"]
    readonly_fields = ["domain", "added_by", "added_by_shopper"]
    actions = ["archive"]

    @admin.display(description="Current shopper")
    def current_shopper(self, obj):
        assignment = obj.assignments.open().select_related("shopper").first()
        return assignment.shopper if assignment else "—"

    def save_model(self, request, obj, form, change):
        if not change:
            obj.added_by = request.user
        super().save_model(request, obj, form, change)
        if not change:
            _warn_duplicates(self, request, obj)

    @admin.action(description="Archive selected targets (history is kept)")
    def archive(self, request, queryset):
        count = queryset.filter(archived_at__isnull=True).update(archived_at=timezone.now())
        self.message_user(request, f"Archived {count} target(s).", messages.SUCCESS)

    def has_delete_permission(self, request, obj=None):
        # Archive instead, so fieldwork history is never lost (MVP-01).
        return False


@admin.register(BriefVersion)
class BriefVersionAdmin(admin.ModelAdmin):
    list_display = ["project", "number", "created_at", "created_by"]
    list_filter = ["project"]
    fields = ["project", "number", "text", "created_at", "created_by"]
    readonly_fields = fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
