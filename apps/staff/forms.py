from django import forms
from django.db.models import Q

from apps.accounts.models import User
from apps.projects.models import ClientOrganisation, Project, Target
from apps.questionnaires.models import Question, Section

INPUT = {"class": "input"}


def _input(widget):
    widget.attrs.setdefault("class", "input")
    return widget


class StyledForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if not isinstance(field.widget, forms.CheckboxInput | forms.RadioSelect):
                _input(field.widget)


class StyledModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if not isinstance(field.widget, forms.CheckboxInput | forms.RadioSelect):
                _input(field.widget)


# --- Clients -------------------------------------------------------------------------


class ClientForm(StyledModelForm):
    class Meta:
        model = ClientOrganisation
        fields = ["name", "reference"]
        labels = {"name": "Client name"}

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        clash = ClientOrganisation.objects.filter(name__iexact=name).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("A client with this name already exists.")
        return name


# --- Projects ------------------------------------------------------------------------


class ProjectForm(StyledModelForm):
    brief = forms.CharField(
        label="Shopper brief",
        required=False,
        widget=forms.Textarea(attrs={"rows": 12}),
    )

    class Meta:
        model = Project
        fields = [
            "client",
            "status",
            "notification_email",
            "internal_name",
            "client_facing_name",
            "shopper_facing_name",
        ]
        labels = {"notification_email": "Notification email"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Active clients, plus the project's current client even if it has been deactivated.
        clients = Q(is_active=True)
        if self.instance.pk:
            clients |= Q(pk=self.instance.client_id)
        self.fields["client"].queryset = ClientOrganisation.objects.filter(clients)
        if self.instance.pk and self.instance.latest_brief:
            self.fields["brief"].initial = self.instance.latest_brief.text
        # Required from now on, so every project tells someone when a report is submitted.
        self.fields["notification_email"].required = True

    def clean(self):
        data = super().clean()
        internal = (data.get("internal_name") or "").strip().lower()
        for field in ("client_facing_name", "shopper_facing_name"):
            if internal and (data.get(field) or "").strip().lower() == internal:
                self.add_error(field, "Use a different name from the internal one.")
        client = data.get("client")
        brief = (data.get("brief") or "").lower()
        if client and client.name.lower() in brief:
            self.add_error(
                "brief", f"The brief mentions the client ({client.name}). Shoppers must not see it."
            )
        return data


# --- Targets and assignments -------------------------------------------------------


class TargetForm(StyledModelForm):
    add_anyway = forms.BooleanField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = Target
        fields = ["name", "website"]
        labels = {"name": "Organisation name", "website": "Website"}
        widgets = {"website": forms.TextInput(attrs={"placeholder": "example.com"})}

    def __init__(self, *args, project, **kwargs):
        self.project = project
        self.duplicates = []
        super().__init__(*args, **kwargs)

    def clean(self):
        from apps.projects.services import find_duplicate_targets

        data = super().clean()
        if data.get("name") and not data.get("add_anyway"):
            self.duplicates = list(
                find_duplicate_targets(self.project, data["name"], data.get("website", ""))
            )
            if self.duplicates:
                # Shown as a warning; submitting again with add_anyway=1 goes ahead.
                self.data = self.data.copy()
                self.data["add_anyway"] = "1"
                raise forms.ValidationError("Possible duplicate", code="duplicate")
        return data


def shopper_choices(exclude=None):
    shoppers = User.objects.filter(role=User.Role.SHOPPER, is_active=True).order_by("full_name", "email")
    return shoppers.exclude(pk=exclude.pk) if exclude else shoppers


class AssignForm(StyledForm):
    shopper = forms.ModelChoiceField(queryset=User.objects.none(), empty_label="Choose a shopper…")
    approval_confirmed = forms.BooleanField(
        required=True,
        label="The client has approved this shopper",
        error_messages={"required": "Confirm the client has approved this shopper before assigning."},
    )
    approval_note = forms.CharField(
        required=False,
        label="Approval note",
        widget=forms.TextInput(attrs={"placeholder": "e.g. Approved by email, 15 Oct"}),
    )

    def __init__(self, *args, exclude=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["shopper"].queryset = shopper_choices(exclude)


class ReassignForm(AssignForm):
    note = forms.CharField(required=False, label="Reason", widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["shopper"].label = "New shopper"


class ReasonForm(StyledForm):
    note = forms.CharField(required=False, label="Reason", widget=forms.Textarea(attrs={"rows": 2}))


# --- Questionnaire builder ------------------------------------------------------------


class SectionForm(StyledModelForm):
    class Meta:
        model = Section
        fields = ["title", "display_style"]
        labels = {"title": "Section title", "display_style": "Display style in the report"}

    def clean_display_style(self):
        style = self.cleaned_data["display_style"]
        if style == Section.DisplayStyle.KEY_FACTS:
            other = (
                Section.objects.filter(version=self.instance.version, display_style=style)
                .exclude(pk=self.instance.pk)
                .first()
            )
            if other:
                raise forms.ValidationError(
                    f"Only one section can use Key facts. “{other.title}” already does."
                )
        return style


class QuestionForm(StyledModelForm):
    class Meta:
        model = Question
        fields = ["prompt", "answer_type", "guidance"]
        labels = {"guidance": "Guidance for shoppers (optional)"}
        widgets = {
            "prompt": forms.Textarea(attrs={"rows": 2}),
            "guidance": forms.TextInput(),
        }


QuestionFormSet = forms.modelformset_factory(Question, form=QuestionForm, extra=0)


class PublishForm(StyledForm):
    move_drafts = forms.TypedChoiceField(
        choices=[("1", "Move them to the new version"), ("0", "Leave them on their current version")],
        coerce=lambda v: v == "1",
        initial="1",
        widget=forms.RadioSelect,
        required=False,
    )
