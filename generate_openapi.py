import asyncio
import json
import os

# Mock the environment to allow loading the tool registry
os.environ["DATABASE_URL"] = "postgresql://dummy:dummy@localhost/dummy"
os.environ["ANTHROPIC_API_KEY"] = "dummy"
os.environ["JWT_SECRET_KEY"] = "dummy"

from mcp_server.tools.registry import ToolRegistry
# Need to import intent_tools so they register
import mcp_server.tools.intent_tools

def generate_openapi():
    tools = ToolRegistry.get_all()
    
    paths = {}
    
    for name, tool_def in tools.items():
        # Only tier-1/agent tools, ignore the demo/commerce ones if they are too many, but let's grab all active ones
        
        properties = {}
        required = []
        for param_name, param_meta in tool_def.parameters.items():
            properties[param_name] = {
                "type": param_meta.get("type", "string"),
                "description": param_meta.get("description", "")
            }
            if param_meta.get("required"):
                required.append(param_name)
                
        paths[f"/api/vertex-agent/{name}"] = {
            "post": {
                "summary": name,
                "description": tool_def.description.split('\n')[0],
                "operationId": name,
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": properties,
                                "required": required
                            }
                        }
                    }
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
                        }
                    }
                }
            }
        }

    openapi_spec = {
        "openapi": "3.0.0",
        "info": {
            "title": "Outris TraceFlow MCP - Vertex AI Extension",
            "description": "REST API bridge for Vertex AI Agent Builder to access Outris TraceFlow capabilities.",
            "version": "1.0.0"
        },
        "servers": [
            {
                "url": "https://mcp-server.outris.com",
                "description": "Production Server"
            }
        ],
        "paths": paths,
        "components": {
            "securitySchemes": {
                "BearerAuth": {
                    "type": "http",
                    "scheme": "bearer"
                }
            }
        },
        "security": [
            {
                "BearerAuth": []
            }
        ]
    }
    
    with open("vertex_openapi.json", "w") as f:
        json.dump(openapi_spec, f, indent=2)

if __name__ == "__main__":
    generate_openapi()
