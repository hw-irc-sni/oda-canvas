"""DependentAPIs of data resource bindings (spec.dependencyKind: data-resource, ADR-010) are left to the data
resource operator that creates them: no handler of this operator runs on them, and so no finalizer is added."""

import os
import sys

import kopf

try:
    import dependentApiSimpleOperator as op
except ModuleNotFoundError:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../src")))
    import dependentApiSimpleOperator as op


def test_is_api_dependency():
    assert op.is_api_dependency({"name": "party", "apiType": "openapi"})
    assert op.is_api_dependency({"name": "party", "apiType": "openapi", "dependencyKind": "api"})
    assert not op.is_api_dependency({"name": "kb", "apiType": "mcp", "dependencyKind": "data-resource"})
    assert op.is_api_dependency(None)


def test_every_dependentapi_handler_has_the_filter():
    registry = kopf.get_default_registry()
    handlers = [h for h in registry._changing.get_all_handlers()]
    names = {h.id.split("/")[0] for h in handlers}
    assert {"dependentApiCreate", "dependentApiDelete", "updateDepedentAPIReady"} <= names
    for h in handlers:
        assert h.when is op.is_api_dependency, h.id
