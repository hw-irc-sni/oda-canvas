"""The OAS drives the guards: every operation has roles, and routes match the operations they declare."""
from fastapi.routing import APIRoute

from app.auth import ALL_ROLES, load_operations
from app.config import Settings


def test_every_operation_has_known_roles():
    operations = load_operations(Settings().oas_path)
    assert len(operations) == 39  # 47 operations in the OAS minus the 8 listener callbacks
    for op in operations.values():
        assert op.roles and set(op.roles) <= ALL_ROLES, op.operation_id


def routes_with_operations(app):
    """(route, operation_id) for every route guarded by require_operation."""
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for dep in route.dependant.dependencies:
            operation_id = getattr(dep.call, "operation_id", None)
            if operation_id:
                yield route, operation_id


def assert_routes_match_oas(app):
    settings = app.state.settings
    operations = app.state.operations
    for route, operation_id in routes_with_operations(app):
        assert operation_id in operations, f"{route.path}: {operation_id} is not in the OAS"
        op = operations[operation_id]
        assert op.method in route.methods, f"{operation_id}: method {route.methods} != {op.method}"
        assert route.path == settings.base_path + op.path, f"{operation_id}: path {route.path} != {op.path}"


def test_routes_match_the_oas():
    from .test_auth import make_app
    app, _, _ = make_app()
    assert any(True for _ in routes_with_operations(app)), "no guarded route found"
    assert_routes_match_oas(app)
