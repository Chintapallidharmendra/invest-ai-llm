"""The ADR-026 V1 tool allow-list as OpenAI function schemas (bounded arguments).

These mirror the contract the agent (Story 3.2+) will expose. The spike only needs the
model to choose and fill them; nothing executes.
"""

from typing import Any, Final

_DOC_ID: Final = {"type": "string", "description": "A document ID from the selected set."}
_OPERATIONS: Final = {
    "type": "array",
    "minItems": 1,
    "maxItems": 20,
    "items": {
        "type": "object",
        "properties": {
            "op": {
                "type": "string",
                "enum": [
                    "filter",
                    "sort",
                    "group_by",
                    "aggregate",
                    "select_columns",
                    "add_calculated_column",
                    "pivot",
                    "deduplicate",
                ],
            },
            "params": {"type": "object"},
        },
        "required": ["op"],
    },
}


def _tool(
    name: str, description: str, properties: dict[str, Any], required: list[str]
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


TOOLS: Final[list[dict[str, Any]]] = [
    _tool(
        "list_selected_documents",
        "Names, types, page and sheet counts of the documents selected for this conversation.",
        {},
        [],
    ),
    _tool(
        "search_documents",
        "Hybrid search within the selected documents; returns chunks with page/cell locators.",
        {
            "query": {"type": "string", "minLength": 2, "maxLength": 500},
            "document_ids": {"type": "array", "items": _DOC_ID, "maxItems": 20},
        },
        ["query"],
    ),
    _tool(
        "read_pages",
        "Read a bounded range of pages (at most 10) of one document.",
        {
            "document_id": _DOC_ID,
            "from": {"type": "integer", "minimum": 1},
            "to": {"type": "integer", "minimum": 1},
        },
        ["document_id", "from", "to"],
    ),
    _tool(
        "get_document_table",
        "Return an extracted table of a document, e.g. table_ref 'p.12#t1'.",
        {"document_id": _DOC_ID, "table_ref": {"type": "string", "maxLength": 64}},
        ["document_id", "table_ref"],
    ),
    _tool(
        "start_summary",
        "Start a background summary of one document.",
        {
            "document_id": _DOC_ID,
            "template": {"type": "string", "enum": ["executive", "detailed", "financials"]},
            "focus": {"type": "string", "maxLength": 300},
        },
        ["document_id", "template"],
    ),
    _tool(
        "start_comparison",
        "Start a background comparison of two documents.",
        {
            "document_a": _DOC_ID,
            "document_b": _DOC_ID,
            "focus": {"type": "string", "maxLength": 300},
        },
        ["document_a", "document_b"],
    ),
    _tool(
        "extract_table_to_excel",
        "Create an Excel file from an extracted table.",
        {"document_id": _DOC_ID, "table_ref": {"type": "string", "maxLength": 64}},
        ["document_id", "table_ref"],
    ),
    _tool(
        "describe_workbook",
        "Sheet names, columns, types and row counts of a workbook.",
        {"document_id": _DOC_ID},
        ["document_id"],
    ),
    _tool(
        "query_sheet",
        "Read-only operations on one sheet (no file is created).",
        {
            "document_id": _DOC_ID,
            "sheet": {"type": "string", "maxLength": 100},
            "operations": _OPERATIONS,
        },
        ["document_id", "sheet", "operations"],
    ),
    _tool(
        "preview_operations",
        "Validate operations and preview them on a sample before the user applies them.",
        {"document_id": _DOC_ID, "output_id": {"type": "string"}, "operations": _OPERATIONS},
        ["operations"],
    ),
    _tool(
        "calculate",
        "Exact financial calculation from cited inputs.",
        {
            "metric": {
                "type": "string",
                "enum": ["growth", "cagr", "margin", "ratio", "multiple", "sum", "mean"],
            },
            "inputs": {
                "type": "array",
                "minItems": 1,
                "maxItems": 50,
                "items": {
                    "type": "object",
                    "properties": {
                        "value": {"type": "string"},
                        "unit": {"type": "string"},
                        "scale": {"type": "string"},
                        "period": {"type": "string"},
                        "ref": {"type": "string"},
                    },
                    "required": ["value", "unit"],
                },
            },
            "years": {"type": "number", "exclusiveMinimum": 0},
        },
        ["metric", "inputs"],
    ),
]

BY_NAME: Final = {tool["function"]["name"]: tool for tool in TOOLS}
