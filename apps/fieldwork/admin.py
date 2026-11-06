from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse

from apps.accounts.models import User

from . import services
from .models import Assignment


def active_shoppers():
    return User.objects.filter(role=User.Role.SHOPPER, is_active=True).order_by("full_name", "email")


class AssignmentAddForm(forms.ModelForm):
    approval_confirmed_offplatform = forms.BooleanField(
        required=True,
        label="Client approval confirmed",
        help_text="Tick to confirm the client approved this shopper for this project (off-platform).",
    )

    class Meta:
        model = Assignment
        fields = ["target", "shopper", "approval_confirmed_offplatform", "approval_note"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["shopper"].queryset = active_shoppers()
        self.fields["target"].queryset = self.fields["target"].queryset.filter(archived_at__isnull=True)

    def clean_target(self):
        # The database also enforces this, but Django's form validation skips that constraint
        # because it refers to closed_at, which isn't on the form.
        target = self.cleaned_data["target"]
        if target.assignments.open().exists():
            raise ValidationError(f"{target} already has an open assignment. Reassign it instead.")
        return target


class ReassignForm(forms.Form):
    new_shopper = forms.ModelChoiceField(queryset=User.objects.none(), label="New shopper")
    approval_confirmed = forms.BooleanField(
        required=True, label="Client approval confirmed for the new shopper (off-platform)"
    )
    approval_note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    note = forms.CharField(
        required=False, label="Reason for reassigning", widget=forms.Textarea(attrs={"rows": 2})
    )

    def __init__(self, *args, current_shopper=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = active_shoppers()
        if current_shopper:
            qs = qs.exclude(pk=current_shopper.pk)
        self.fields["new_shopper"].queryset = qs


class WithdrawForm(forms.Form):
    note = forms.CharField(label="Reason", required=False, widget=forms.Textarea(attrs={"rows": 3}))


@admin.register(Assignment)
class AssignmentAdmin(admin.ModelAdmin):
    list_display = ["target", "project", "shopper", "stage", "state", "approval_confirmed_offplatform"]
    list_filter = ["stage", "closed_reason", "target__project"]
    search_fields = ["target__name", "shopper__email", "shopper__full_name"]
    list_select_related = ["target__project", "shopper"]
    change_form_template = "admin/fieldwork/assignment/change_form.html"
    readonly_fields = [
        "target",
        "shopper",
        "stage",
        "returned",
        "unable_reason",
        "unable_note",
        "closed_at",
        "closed_reason",
        "closed_note",
        "approval_confirmed_offplatform",
        "assigned_by",
        "last_activity_at",
        "created_at",
    ]

    @admin.display(description="Project", ordering="target__project")
    def project(self, obj):
        return obj.target.project

    @admin.display(description="Open?")
    def state(self, obj):
        return "Open" if obj.is_open else f"Closed ({obj.get_closed_reason_display().lower()})"

    def get_form(self, request, obj=None, **kwargs):
        if obj is None:
            kwargs["form"] = AssignmentAddForm
        return super().get_form(request, obj, **kwargs)

    def get_readonly_fields(self, request, obj=None):
        return [] if obj is None else self.readonly_fields

    def get_fields(self, request, obj=None):
        if obj is None:
            return ["target", "shopper", "approval_confirmed_offplatform", "approval_note"]
        return [*self.readonly_fields, "approval_note"]

    def save_model(self, request, obj, form, change):
        if not change:
            obj.assigned_by = request.user
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        # Withdraw or reassign instead, so history is kept (D07).
        return False

    def get_urls(self):
        custom = [
            path(
                "<uuid:pk>/reassign/",
                self.admin_site.admin_view(self.reassign_view),
                name="fieldwork_assignment_reassign",
            ),
            path(
                "<uuid:pk>/withdraw/",
                self.admin_site.admin_view(self.withdraw_view),
                name="fieldwork_assignment_withdraw",
            ),
        ]
        return custom + super().get_urls()

    def _action_view(self, request, pk, form_class, title, perform, form_kwargs=None):
        assignment = get_object_or_404(Assignment.objects.select_related("target", "shopper"), pk=pk)
        if not self.has_change_permission(request, assignment):
            raise PermissionDenied
        change_url = reverse("admin:fieldwork_assignment_change", args=[assignment.pk])
        if not assignment.is_open:
            self.message_user(request, "This assignment is already closed.", messages.ERROR)
            return redirect(change_url)
        form = form_class(request.POST or None, **(form_kwargs or {}))
        if request.method == "POST" and form.is_valid():
            try:
                result, message = perform(assignment, form.cleaned_data)
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                self.message_user(request, message, messages.SUCCESS)
                return redirect(reverse("admin:fieldwork_assignment_change", args=[result.pk]))
        context = {
            **self.admin_site.each_context(request),
            "title": title,
            "assignment": assignment,
            "form": form,
            "opts": self.model._meta,
            "change_url": change_url,
        }
        return TemplateResponse(request, "admin/fieldwork/assignment/action_form.html", context)

    def reassign_view(self, request, pk):
        def perform(assignment, data):
            new = services.reassign(
                assignment,
                data["new_shopper"],
                by=request.user,
                approval_confirmed=data["approval_confirmed"],
                approval_note=data["approval_note"],
                note=data["note"],
            )
            return new, f"Reassigned {assignment.target} to {new.shopper}. The previous assignment is kept."

        assignment = get_object_or_404(Assignment, pk=pk)
        return self._action_view(
            request,
            pk,
            ReassignForm,
            "Reassign target",
            perform,
            {"current_shopper": assignment.shopper},
        )

    def withdraw_view(self, request, pk):
        def perform(assignment, data):
            services.withdraw(assignment, note=data["note"])
            return assignment, f"Withdrew {assignment.shopper} from {assignment.target}."

        return self._action_view(request, pk, WithdrawForm, "Withdraw shopper", perform)
