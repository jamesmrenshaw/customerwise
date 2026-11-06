def nav_section(request):
    """Which main-nav item is current, from the URL name (staff:project_* -> "projects")."""
    match = getattr(request, "resolver_match", None)
    if not match or match.namespace != "staff":
        return {}
    return {"section": "clients" if match.url_name.startswith("client") else "projects"}
