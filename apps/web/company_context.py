from apps.organization.scope import build_theme, emblem_for


def company_scope(request):
    """Template variables for the company switch, colours and emblem. Everything was already worked out by the
    CompanyScopeMiddleware; this only passes it on, so it costs no queries."""
    company = getattr(request, "work_company", None)
    return {
        "company_separate": getattr(request, "company_view_separate", False),
        "work_company": company,
        "work_companies": getattr(request, "work_companies", []),
        "company_theme": build_theme(company) if company is not None else None,
        "company_emblem": emblem_for(company) if company is not None else "",
    }
