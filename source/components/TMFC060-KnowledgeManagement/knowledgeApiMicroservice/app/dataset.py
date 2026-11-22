"""Which graphs a knowledge-base query sees: its own graph, plus the ontology modules it conforms to and
everything they import (transitively). Only published modules are included, and deprecated ones so that existing
knowledge-bases keep working; drafts and proposals never reach query answers."""
from dataclasses import dataclass, field

from .graphstore import kb_graph, module_graph

VISIBLE_STATES = frozenset({"published", "deprecated"})


@dataclass(frozen=True)
class ModuleInfo:
    lifecycle_status: str
    imports: tuple[str, ...] = field(default_factory=tuple)


def visible_modules(conforms_to: list[str], modules: dict[str, ModuleInfo]) -> list[str]:
    """The ids of the visible modules: conformsTo plus transitive imports, cycle-safe, in discovery order."""
    seen: list[str] = []
    stack = list(reversed(conforms_to))
    while stack:
        module_id = stack.pop()
        info = modules.get(module_id)
        if module_id in seen or info is None or info.lifecycle_status not in VISIBLE_STATES:
            continue
        seen.append(module_id)
        stack.extend(reversed(info.imports))
    return seen


def query_dataset(kb_id: str, conforms_to: list[str], modules: dict[str, ModuleInfo],
                  kb_graphs: list[str] | None = None) -> list[str]:
    """The graph IRIs that form the default graph of a query on the knowledge-base: its instance data (the KB graph
    and, when given, its per-source graphs) and the visible modules."""
    return list(kb_graphs or [kb_graph(kb_id)]) + [module_graph(m) for m in visible_modules(conforms_to, modules)]
