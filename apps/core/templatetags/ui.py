from django import template
from django.utils import timezone
from django.utils.html import format_html

register = template.Library()


@register.filter
def stamp(value):
    """A date and time as <time datetime="…">6 Nov 2026, 16:05</time>, in UK time."""
    if not value:
        return ""
    local = timezone.localtime(value)
    return format_html(
        '<time datetime="{}">{} {} {}, {}</time>',
        local.isoformat(timespec="minutes"),
        local.day,
        local.strftime("%b"),
        local.year,
        local.strftime("%H:%M"),
    )
