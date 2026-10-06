"""
Generate OpenAPI 3.0 specifications for Google Cloud Vertex AI Agent Builder.

Generates:
- vertex_openapi.json (default / full catalog)
- vertex_openapi_full.json
- vertex_openapi_kyb.json
- vertex_openapi_ubo.json
- vertex_openapi_collections.json
- vertex_openapi_fraud.json
- vertex_openapi_compliance.json

Note for Vertex AI Agent Builder:
Agent Builder imports fail if 'components.securitySchemes' is defined in the spec.
Authentication is configured directly in the Vertex Agent Builder console
via Bearer token or API key header, NOT in the OpenAPI document.
"""
import json
import os
from typing import Optional, Dict, Any

# Mock the environment to allow loading the tool registry
os.environ["DATABASE_URL"] = "postgresql://dummy:dummy@localhost/dummy"
os.environ["ANTHROPIC_API_KEY"] = "dummy"
os.environ["JWT_SECRET_KEY"] = "dummy"

from mcp_server.tools.registry import ToolRegistry
import mcp_server.tools.intent_tools
from mcp_server.core.stacks import STACK_DEFINITIONS, get_stack_tools


def build_openapi_spec(stack_name: Optional[str] = None) -> Dict[str, Any]:
    """Build an OpenAPI 3.0 specification for a given stack or all tools."""
    stack_tools = get_stack_tools(stack_name)
    all_tools = ToolRegistry.get_all()

    if stack_tools is not None:
        target_tools = {k: v for k, v in all_tools.items() if k in stack_tools}
        stack_meta = STACK_DEFINITIONS.get(stack_name.lower(), {})
        title_suffix = f" • {stack_meta.get('title', stack_name.upper())}"
        desc = stack_meta.get(
            "description",
            f"REST API bridge for Vertex AI Agent Builder - {stack_name.upper()} stack.",
        )
    else:
        target_tools = all_tools
        title_suffix = ""
        desc = "REST API bridge for Vertex AI Agent Builder to access Outris TraceFlow capabilities."

    paths = {}
    for name, tool_def in target_tools.items():
        properties = {}
        required = []
        for param_name, param_meta in tool_def.parameters.items():
            properties[param_name] = {
                "type": param_meta.get("type", "string"),
                "description": param_meta.get("description", ""),
            }
            if param_meta.get("required"):
                required.append(param_name)

        paths[f"/api/vertex-agent/{name}"] = {
            "post": {
                "summary": name,
                "description": tool_def.description.split("\n")[0],
                "operationId": name,
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": properties,
                                "required": required,
                            }
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Successful operation",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object"
                                }
                            }
                        },
                    },
                    "400": {"description": "Invalid input arguments"},
                    "401": {"description": "Authentication failure"},
                    "402": {"description": "Insufficient credits"},
                    "502": {"description": "Upstream provider error"},
                },
            }
        }

    return {
        "openapi": "3.0.0",
        "info": {
            "title": f"Outris TraceFlow MCP{title_suffix}",
            "description": desc,
            "version": "2.0.0",
        },
        "servers": [
            {
                "url": "https://mcp-server.outris.com",
                "description": "Production Server",
            }
        ],
        "paths": paths,
        "components": {},
    }


def generate_all():
    """Generate all OpenAPI JSON specs."""
    base_dir = os.path.dirname(os.path.abspath(__file__))

    # 1. Full spec (vertex_openapi.json and vertex_openapi_full.json)
    full_spec = build_openapi_spec(None)
    with open(os.path.join(base_dir, "vertex_openapi.json"), "w") as f:
        json.dump(full_spec, f, indent=2)
    with open(os.path.join(base_dir, "vertex_openapi_full.json"), "w") as f:
        json.dump(full_spec, f, indent=2)
    print(f"Generated vertex_openapi.json & vertex_openapi_full.json ({len(full_spec['paths'])} endpoints)")

    # 2. Individual Stacks
    for stack_key in STACK_DEFINITIONS.keys():
        stack_spec = build_openapi_spec(stack_key)
        filename = f"vertex_openapi_{stack_key}.json"
        with open(os.path.join(base_dir, filename), "w") as f:
            json.dump(stack_spec, f, indent=2)
        print(f"Generated {filename} ({len(stack_spec['paths'])} endpoints)")


if __name__ == "__main__":
    generate_all()
