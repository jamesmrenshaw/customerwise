from django.db import connection
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


@require_GET
@never_cache
def healthz(request):
    """Railway health check: the app is up and can reach the database."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return HttpResponse("ok", content_type="text/plain")


@require_GET
def home(request):
    if request.user.is_authenticated and request.user.is_staff:
        return redirect("staff:projects")
    if request.user.is_authenticated and request.user.is_shopper:
        return redirect("shopper:assignments")
    return render(request, "core/home.html")
