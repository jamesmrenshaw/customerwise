"""Small helpers shared by staff and shopper views: forms that accept empty POSTs, and dialogs.

A dialog is a <dialog> fragment loaded by HTMX into #modal. Without JavaScript the same URL
renders it inside a full page, so every action still works.
"""

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render


def posted(request):
    """Form data for a POST (even an empty one), or None for GET, so empty submits still validate."""
    return request.POST if request.method == "POST" else None


def is_htmx(request):
    return request.headers.get("HX-Request") == "true"


def dialog(request, template, context):
    if is_htmx(request):
        return render(request, template, context)
    return render(request, "staff/dialog_page.html", {**context, "dialog_template": template})


def done(request, url, message=None):
    """Finish a form: flash a message and go to url (HX-Redirect for HTMX dialogs)."""
    if message:
        messages.success(request, message)
    if is_htmx(request):
        response = HttpResponse(status=204)
        response["HX-Redirect"] = url
        return response
    return redirect(url)
