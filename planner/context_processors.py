from planner.models import Company


def company(request):
    if not request.user.is_authenticated:
        return {"company": None}
    return {"company": Company.objects.filter(owner=request.user).first()}
