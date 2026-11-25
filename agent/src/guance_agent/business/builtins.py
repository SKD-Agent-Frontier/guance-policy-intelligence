"""Built-in research capabilities and request-scoped registry snapshots."""
from . import register, registered_modules
from .compare import PolicyComparisonModule, SchemeComparisonModule
from .extract import ExtractModule
from .organize import OrganizeModule

register(PolicyComparisonModule())
register(SchemeComparisonModule())
register(OrganizeModule())
_extract = ExtractModule()


def research_capabilities():
    return {"extract": _extract, **registered_modules()}
