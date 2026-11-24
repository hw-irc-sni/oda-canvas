"""The DataResource category (ADR-011) in the MCP tools and prompts. The Resource Inventory call is mocked."""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import resource_inventory_mcp_server as server  # noqa: E402


def run_tool(monkeypatch, **kwargs):
    calls = []

    async def fake_get_resource(**params):
        calls.append(params)
        return [{"id": "km.knowledge-vectors", "category": "DataResource"}]

    monkeypatch.setattr(server, "get_resource", fake_get_resource)
    result = asyncio.run(server.resource_get(**kwargs))
    return result, calls[0]


def test_category_reaches_the_filter(monkeypatch):
    result, params = run_tool(monkeypatch, category="DataResource")
    assert params["filter"] == {"category": "DataResource"}
    assert result[0]["category"] == "DataResource"


def test_category_merges_with_other_filters(monkeypatch):
    _, params = run_tool(monkeypatch, category="DataResource", filter={"resourceStatus": "available"})
    assert params["filter"] == {"resourceStatus": "available", "category": "DataResource"}


def test_without_category_the_filter_is_unchanged(monkeypatch):
    _, params = run_tool(monkeypatch, filter={"category": "API"})
    assert params["filter"] == {"category": "API"}
    _, params = run_tool(monkeypatch)
    assert params["filter"] is None


def test_tool_description_explains_the_two_levels():
    doc = server.resource_get.__doc__
    for text in ("DataResource", "store, id <dataresourceconfig>.<item>", "knowledgeSpecification", "providedBy",
                 "isPartOf", "contains", "dependsOn", "componentId", "Data Registry"):
        assert text in doc, text


def test_schema_has_a_data_resource_example():
    schema = asyncio.run(server.resource_schema())
    assert "DataResource" in schema["schema"]["properties"]["category"]["description"]
    example = next(e for e in schema["examples"] if e.get("category") == "DataResource")
    assert {r["relationshipType"] for r in example["resourceRelationship"]} == {"providedBy", "isPartOf"}


def test_prompts():
    found = server.find_knowledge_base_prompt("network incidents")
    assert "network incidents" in found and 'category "DataResource"' in found
    assert "kind = \"knowledge\"" in found and "providedBy" in found and "componentId" in found
    assert "DataResource" in server.list_resources_prompt(category="DataResource")
    assert "DataResource" in server.search_resources_by_type_prompt("DataResource")
    assert "Data Registry" in server.get_usage_help_prompt()
