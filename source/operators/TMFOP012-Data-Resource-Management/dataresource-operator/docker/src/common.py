"""Helpers shared by the handlers and the provider mirror."""

import datetime

from settings import OWNED_PROVIDER, Settings


def is_owned(spec, **_) -> bool:
    """True for the DataResourceConfigs this operator owns (``spec.provider: odaComponent``)."""
    return (spec or {}).get("provider") == OWNED_PROVIDER


def role_of(spec) -> str:
    return (spec or {}).get("role") or "consumer"


def is_owned_provider(spec, **_) -> bool:
    """True for an owned DataResourceConfig with ``role: provider``."""
    return is_owned(spec) and role_of(spec) == "provider"


def component_of(meta, settings: Settings) -> str | None:
    """The owning ODA Component: the componentName label, else a Component ownerReference."""
    labels = (meta or {}).get("labels") or {}
    if labels.get(settings.componentname_label):
        return labels[settings.componentname_label]
    for ref in (meta or {}).get("ownerReferences") or []:
        if ref.get("kind") == "Component":
            return ref.get("name")
    return None


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def condition(ctype: str, status: str, reason: str, message: str, generation, previous=None) -> dict:
    """A Kubernetes condition; ``lastTransitionTime`` is kept when the status did not change."""
    for c in previous or []:
        if c.get("type") == ctype and c.get("status") == status and c.get("lastTransitionTime"):
            transition = c["lastTransitionTime"]
            break
    else:
        transition = now()
    return {"type": ctype, "status": status, "reason": reason, "message": message,
            "lastTransitionTime": transition, "observedGeneration": generation}
