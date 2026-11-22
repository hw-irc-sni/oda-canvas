"""TMF Open API resources (TMF639/638/642/628/921): a record for lifting and a searchable text rendering."""
SOURCES = {"tmf639", "tmf638", "tmf642", "tmf628", "tmf921"}
TEXT_FIELDS = ("name", "description", "@type", "category", "state", "status", "lifecycleState", "resourceStatus",
               "operationalState", "administrativeState", "alarmType", "perceivedSeverity", "probableCause",
               "specificProblem", "priority")


def record(resource: dict, source: str) -> dict:
    return {"source": source, "@type": resource.get("@type"), "id": resource.get("id"), "href": resource.get("href"),
            "resource": resource}


def characteristics(resource: dict) -> list[str]:
    for key in ("resourceCharacteristic", "serviceCharacteristic", "characteristic", "intentCharacteristic"):
        for c in resource.get(key) or []:
            if isinstance(c, dict) and c.get("name") is not None:
                yield f"{c['name']}: {c.get('value')}"


def render(resource: dict, source: str) -> str:
    lines = [f"{source.upper()} {resource.get('@type') or 'resource'} {resource.get('id') or ''}".strip()]
    lines += [f"{k}: {resource[k]}" for k in TEXT_FIELDS if resource.get(k) not in (None, "", [])]
    lines += list(characteristics(resource))
    return "\n".join(lines)


def resources_of(params: dict) -> list[dict]:
    """The resources of a batch: params.resources, or the resource of a TMF event (params.event)."""
    if isinstance(params.get("resources"), list):
        return [r for r in params["resources"] if isinstance(r, dict)]
    event = params.get("event")
    if isinstance(event, dict):
        payload = event.get("event") if isinstance(event.get("event"), dict) else {}
        return [v for v in payload.values() if isinstance(v, dict)]
    return []
