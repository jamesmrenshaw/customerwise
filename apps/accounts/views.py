from django import forms
from django.contrib.auth import login, logout
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from . import services

BACKEND = "django.contrib.auth.backends.ModelBackend"


class EmailForm(forms.Form):
    email = forms.EmailField(
        label="Email address",
        max_length=254,
        widget=forms.EmailInput(attrs={"autocomplete": "email", "autofocus": True, "class": "input"}),
    )


def _safe_next(request):
    nxt = request.POST.get("next") or request.GET.get("next") or ""
    if url_has_allowed_host_and_scheme(
        nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return nxt
    return ""


def _sign_in(request, user):
    login(request, user, backend=BACKEND)
    return redirect(_safe_next(request) or "core:home")


@never_cache
@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect(_safe_next(request) or "core:home")
    form = EmailForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        services.request_login_link(form.cleaned_data["email"])
        return render(request, "accounts/link_sent.html", {"email": form.cleaned_data["email"]})
    return render(request, "accounts/login.html", {"form": form, "next": _safe_next(request)})


@never_cache
@require_http_methods(["GET", "POST"])
def magic_link(request, secret):
    """GET shows a Continue button; only POST uses the link, so email scanners can't burn it."""
    if services.find_login_token(secret) is None:
        return render(request, "accounts/link_invalid.html", status=410)
    if request.method == "POST":
        user = services.use_login_token(secret)
        if user is None:
            return render(request, "accounts/link_invalid.html", status=410)
        return _sign_in(request, user)
    return render(request, "accounts/confirm.html", {"action": "Sign in"})


@never_cache
@require_http_methods(["GET", "POST"])
def accept_invitation(request, secret):
    invitation = services.find_invitation(secret)
    if invitation is None:
        return render(request, "accounts/invitation_invalid.html", status=410)
    if request.method == "POST":
        user = services.accept_invitation(secret)
        if user is None:
            return render(request, "accounts/invitation_invalid.html", status=410)
        return _sign_in(request, user)
    return render(request, "accounts/confirm.html", {"action": "Accept invitation", "invitation": invitation})


@require_POST
def logout_view(request):
    logout(request)
    return redirect("accounts:login")
