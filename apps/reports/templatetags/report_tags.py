from django import template

from apps.reports.formatting import render_html

register = template.Library()


@register.filter
def formatted(text):
    return render_html(text)
