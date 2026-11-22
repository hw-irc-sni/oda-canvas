"""Sync of federation repositories (FTO layout: modules.json + catalog.json) into the registry.

``ONTOLOGY_SOURCES`` lists the repositories, as git (url, ref, secretRef) or a mounted directory. At startup and
every ``ONTOLOGY_SYNC_INTERVAL`` seconds, each module of each repository is registered or updated:

- as ``FederatedKnowledgeSpecification`` (``@baseType`` KnowledgeSpecification), ``published`` (the repository
  governs it), with its status in the repository as ``sourceStatus``;
- its layer is the ``fto:layer`` of its ontology, else the top folder of its file (``foundational``, ``telecom``,
  ``implementation``); files at the repository root are Foundational;
- a module without a file in the repository is registered without content, and a module without an IRI gets the
  placeholder ``urn:tmfc060:federation:<source>:<key>`` until the repository gives it one;
- a module that disappears from the repository becomes ``deprecated`` (never deleted).
"""
import asyncio
import json
import logging
import re
from pathlib import Path

from pyoxigraph import NamedNode, RdfFormat, parse
from sqlalchemy import select

from ..db.models import KnowledgeSpecification as SpecRow
from ..graphstore import module_graph
from . import loaders
from .checks import repository_pass
from .registry import FEDERATED_TYPE, Registry

log = logging.getLogger(__name__)

FTO_LAYER = "https://models.tmforum.org/ontology/fto/layer"
FOLDER_LAYERS = {"foundational": "Foundational", "telecom": "Telecom", "implementation": "Implementation"}
DEPTH_KIND = {"vocabulary": "vocabulary"}


def parse_sources(raw: str) -> list[dict]:
    sources = json.loads(raw or "[]")
    if not isinstance(sources, list):
        raise ValueError("ONTOLOGY_SOURCES must be a JSON list")
    for s in sources:
        if not s.get("name") or not (s.get("git") or s.get("mount")):
            raise ValueError("each ontology source needs a name and a git or mount block")
    return sources


def declared_layer(data: bytes) -> str | None:
    try:
        for triple in parse(data, format=RdfFormat.TURTLE):
            if triple.predicate.value == FTO_LAYER and isinstance(triple.object, NamedNode):
                name = triple.object.value.rsplit("/", 1)[-1].rsplit("#", 1)[-1]
                if name in FOLDER_LAYERS.values():
                    return name
    except (SyntaxError, ValueError) as exc:
        log.warning("could not read fto:layer: %s", exc)
    return None


def folder_layer(relative: str) -> str:
    top = relative.split("/", 1)[0] if "/" in relative else ""
    return FOLDER_LAYERS.get(top.lower(), "Foundational")


def spec_kind(entry: dict) -> str:
    depth = (entry.get("depth") or "").lower()
    if "vocabulary" in depth:
        return "vocabulary"
    if "bridge" in (entry.get("file") or "").lower():
        return "bridge"
    return "ontology"


def owner(entry: dict) -> dict | None:
    if not entry.get("owner"):
        return None
    return {"@type": "RelatedPartyRefOrPartyRoleRef", "role": "owner",
            "partyOrPartyRole": {"@type": "PartyRef", "id": slug(entry["owner"]), "name": entry["owner"]}}


def unchanged(old: dict, new: dict) -> bool:
    ignore = {"lastUpdate"}
    return {k: v for k, v in old.items() if k not in ignore} == {k: v for k, v in new.items() if k not in ignore}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:63] or "party"


async def repository(registry: Registry, source: dict) -> tuple[Path, dict]:
    """The repository's working tree, and the source block that imported modules point back to."""
    if source.get("git"):
        git = source["git"]
        workdir = await loaders.checkout(registry.settings, git["url"], git.get("ref") or "main", git.get("secretRef"))
        return workdir, {"kind": "git", "url": git["url"], "ref": git.get("ref") or "main",
                         **({"secretRef": git["secretRef"]} if git.get("secretRef") else {})}
    root = loaders.mounted_path(registry.settings, source["mount"]["path"])
    return root, {"kind": "mount", "path": str(root)}


def same_source(body: dict, origin: dict) -> bool:
    src = body.get("source", {})
    if origin["kind"] == "git":
        return src.get("kind") == "git" and src.get("url") == origin["url"]
    return src.get("kind") == "mount" and str(src.get("path", "")).startswith(origin["path"] + "/")


async def sync_source(registry: Registry, source: dict) -> dict:
    """Register or update every module of one repository. Returns counts for the log."""
    root, origin = await repository(registry, source)
    modules = json.loads((root / "modules.json").read_text())["modules"]
    stats = {"created": 0, "updated": 0, "metadataOnly": 0, "deprecated": 0, "skipped": 0}
    pending_imports: dict[str, list[str]] = {}
    for key, entry in modules.items():
        # Planned modules may have no IRI yet: give them a placeholder, replaced once the repository has one
        iri = entry.get("iri") or f"urn:tmfc060:federation:{source['name']}:{key}"
        relative = entry.get("file")
        data = None
        if relative:
            path = loaders.safe_join(root, relative)
            data = path.read_bytes() if path.is_file() else None
        if origin["kind"] == "git":
            module_source = {**origin, "path": relative or ""}
        else:
            module_source = {"kind": "mount", "path": str(root / relative) if relative else origin["path"] + "/"}
        layer = (declared_layer(data) if data else None) or folder_layer(relative or "")
        body = {"@type": FEDERATED_TYPE, "id": key, "name": entry.get("label") or key,
                "description": entry.get("purpose"), "iri": iri,
                "prefix": entry.get("prefix"), "layer": layer, "specKind": spec_kind(entry),
                "depth": entry.get("depth"), "source": module_source,
                "describes": [{"id": d} for d in entry.get("describes") or []]}
        if owner(entry):
            body["owner"] = owner(entry)
        body = {k: v for k, v in body.items() if v not in (None, [], "")}
        pending_imports[key] = entry.get("imports") or []
        row = await registry.session.get(SpecRow, key)
        graph = module_graph(key)
        if data is not None:
            registry.graphs.replace(graph, data, "text/turtle")
        else:
            registry.graphs.remove(graph)
            stats["metadataOnly"] += 1
        if row is None:
            if await registry.session.scalar(select(SpecRow).where(SpecRow.iri == iri)):
                log.warning("sync %s: iri %s of %s is already registered; skipped", source["name"], iri, key)
                stats["skipped"] += 1
                continue
            full = {**body, "href": registry.href(key), "lifecycleStatus": "published",
                    "sourceStatus": entry.get("status"), "@baseType": "KnowledgeSpecification",
                    "@schemaLocation": registry.schema_location()}
            registry.save(None, full)
            stats["created"] += 1
            await registry.session.flush()
            await registry.publish("Create", full)
        else:
            full = {**row.body, **body, "lifecycleStatus": "published", "sourceStatus": entry.get("status")}
            if unchanged(row.body, full):
                continue
            registry.save(row, full)
            stats["updated"] += 1
            await registry.publish("AttributeValueChange", full)
    await registry.session.flush()
    # imports: modules.json keys, plus the owl:imports in the content, resolved by iri
    for key, keys in pending_imports.items():
        row = await registry.session.get(SpecRow, key)
        if row is None:
            continue
        body = dict(row.body)
        body["imports"] = [{"id": k} for k in keys]
        await registry.refresh_imports(body)
        body["describes"] = await registry.resolve_refs(row.body.get("describes") or [])
        if not body["describes"]:
            body.pop("describes")
        if not unchanged(row.body, body):
            registry.save(row, body)
    # the reified bridge:mapping records of synced bridge modules become read-only mappings
    from .mappings import Mappings
    mappings = Mappings(registry)
    for key in pending_imports:
        row = await registry.session.get(SpecRow, key)
        if row is not None and row.spec_kind == "bridge":
            stats.setdefault("mappings", 0)
            stats["mappings"] += await mappings.import_from_content(row)
    await registry.session.flush()

    # shapes folders or files listed in the source become specKind shapes modules
    for shape in source.get("shapes") or []:
        sid = shape["id"]
        base = loaders.safe_join(root, shape["path"])
        files = [base] if base.is_file() else sorted(base.rglob("*.ttl"))
        graph = module_graph(sid)
        registry.graphs.clear(graph)
        for path in files:
            registry.graphs.merge(graph, path.read_bytes(), "text/turtle")
        modules[sid] = {"shapes": True}
        shape_source = {**origin, "path": shape["path"]} if origin["kind"] == "git" else {"kind": "mount", "path": str(base)}
        body = {"@type": FEDERATED_TYPE, "id": sid, "name": shape.get("name") or sid,
                "iri": shape.get("iri") or f"urn:tmfc060:federation:{source['name']}:{sid}", "specKind": "shapes",
                "layer": folder_layer(shape["path"]), "source": shape_source}
        row = await registry.session.get(SpecRow, sid)
        if row is None:
            full = {**body, "href": registry.href(sid), "lifecycleStatus": "published", "sourceStatus": "shapes",
                    "@baseType": "KnowledgeSpecification", "@schemaLocation": registry.schema_location()}
            registry.save(None, full)
            stats["created"] += 1
            await registry.session.flush()
            await registry.publish("Create", full)
        elif not unchanged(row.body, {**row.body, **body}):
            registry.save(row, {**row.body, **body})
            stats["updated"] += 1

    # modules of this repository that are gone from modules.json
    for row in (await registry.session.scalars(select(SpecRow))).all():
        if row.body.get("@type") == FEDERATED_TYPE and same_source(row.body, origin) and row.id not in modules \
                and row.lifecycle_status != "deprecated":
            registry.save(row, {**row.body, "lifecycleStatus": "deprecated"})
            stats["deprecated"] += 1
            await registry.publish("StateChange", row.body)
    await registry.session.commit()
    repo = await asyncio.to_thread(repository_pass, root)
    stats["filesParsed"] = repo["filesParsed"]
    stats["repoErrors"] = repo["errors"]
    log.info("ontology source %s synced: %s", source["name"], {k: v for k, v in stats.items() if k != "repoErrors"})
    log.info("ontology source %s: %d files parse, %s", source["name"], repo["filesParsed"],
             f"{len(repo['errors'])} repository error(s): {repo['errors'][:5]}" if repo["errors"] else "catalog intact")
    return stats


async def sync_all(make_registry, sources: list[dict]) -> dict[str, dict]:
    results = {}
    for source in sources:
        try:
            async with make_registry() as registry:
                results[source["name"]] = await sync_source(registry, source)
        except Exception as exc:  # noqa: BLE001 - one bad source must not stop the others or the API
            log.error("ontology source %s failed: %s", source["name"], exc)
            results[source["name"]] = {"error": str(exc)}
    return results


async def run_periodically(make_registry, sources: list[dict], interval: int) -> None:
    while True:
        await sync_all(make_registry, sources)
        await asyncio.sleep(interval)
