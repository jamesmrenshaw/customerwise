"""Access control used by every view (CW-003, CW-084). See docs/ARCHITECTURE.md.

- Signed-out users are sent to sign in.
- Signed-in users without the right role get a 404, never a 403, so pages for the other
  role aren't revealed.
- Querysets for protected objects are added here as the models arrive
  (assignments_for(user), reports_for(user), evidence_for(user), targets_for(user)).
  Views must fetch objects only through them: get_object_or_404(assignments_for(user), pk=...).
"""

from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.http import Http404


def _role_required(check):
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            user = request.user
            if not user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if not check(user):
                raise Http404
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


staff_required = _role_required(lambda user: user.is_staff)
shopper_required = _role_required(lambda user: user.is_active and user.is_shopper)


def assignments_for(user):
    """Assignments a user may see.

    Staff see all of them. Shoppers see their own open assignments plus those they closed as
    "unable to complete". They lose access when an assignment is reassigned or withdrawn (D07).
    """
    from django.db.models import Q

    from apps.fieldwork.models import Assignment

    qs = Assignment.objects.select_related("target__project", "shopper")
    if user.is_staff:
        return qs
    if not (user.is_authenticated and user.is_active and user.is_shopper):
        return qs.none()
    return qs.filter(shopper=user).filter(
        Q(closed_at__isnull=True) | Q(closed_reason=Assignment.ClosedReason.UNABLE)
    )


def targets_for(user):
    """Targets a user may see. Shoppers only see targets they're assigned to, never others' (MVP-01)."""
    from apps.projects.models import Target

    if user.is_staff:
        return Target.objects.all()
    return Target.objects.filter(pk__in=assignments_for(user).values("target")).distinct()
