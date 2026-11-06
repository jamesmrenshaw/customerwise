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
