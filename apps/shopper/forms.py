import re

from django import forms
from django.utils import timezone

from apps.fieldwork.models import Activity, Assignment
from apps.projects.models import Project
from apps.staff.forms import StyledForm, StyledModelForm

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"\+?\(?\d[\d\s().-]{8,}\d")
# Dates and times are common in notes and mustn't look like phone numbers.
DATES_AND_TIMES = re.compile(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/.]\d{1,2}[/.]\d{2,4}|\d{1,2}:\d{2}")
PERSONAL_DATA = "Don't record email addresses or phone numbers here. Record people by job title only."


def looks_like_phone_number(text):
    text = DATES_AND_TIMES.sub(" ", text)
    return any(sum(c.isdigit() for c in m.group()) >= 10 for m in PHONE.finditer(text))


def check_no_contact_details(value):
    if EMAIL.search(value or "") or looks_like_phone_number(value or ""):
        raise forms.ValidationError(PERSONAL_DATA)
    return value


class ActivityForm(StyledModelForm):
    class Meta:
        model = Activity
        fields = ["type", "occurred_at", "contact_job_title", "notes"]
        widgets = {
            "occurred_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "contact_job_title": forms.TextInput(attrs={"placeholder": "e.g. Account executive"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }
        help_texts = {
            "contact_job_title": (
                "Job title only. Don't record names, email addresses, phone numbers or social profiles."
            ),
            "occurred_at": "Use a future date to plan an activity.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["occurred_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        if not self.is_bound and not self.instance.pk:
            self.initial["occurred_at"] = timezone.localtime().strftime("%Y-%m-%dT%H:%M")

    def clean_contact_job_title(self):
        return check_no_contact_details(self.cleaned_data["contact_job_title"])

    def clean_notes(self):
        return check_no_contact_details(self.cleaned_data["notes"])


class UnableForm(StyledForm):
    reason = forms.ChoiceField(choices=[("", "Choose a reason…"), *Assignment.UnableReason.choices])
    note = forms.CharField(required=False, label="Note", widget=forms.Textarea(attrs={"rows": 3}))

    def clean_note(self):
        return check_no_contact_details(self.cleaned_data["note"])


class ProjectChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return obj.shopper_facing_name  # never the internal or client-facing name


class ShopperTargetForm(StyledForm):
    project = ProjectChoiceField(queryset=Project.objects.none(), empty_label=None)
    name = forms.CharField(label="Organisation name", max_length=200)
    website = forms.CharField(
        label="Website",
        max_length=300,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "example.com"}),
    )

    def __init__(self, *args, projects, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["project"].queryset = projects
        if projects.count() > 1:
            self.fields["project"].empty_label = "Choose a project…"
