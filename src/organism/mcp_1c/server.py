"""Q-8.2: MCP server for 1C accounting system.

Standalone HTTP server exposing 1C read-only operations via MCP protocol.
Deployed alongside 1C at the artel's server.

Usage:
    python -m src.organism.mcp_1c.server --port 8090 --mode demo
    python -m src.organism.mcp_1c.server --port 8090 --mode live --odata-url http://1c-server/odata
"""
import argparse
import json
from typing import Any

from aiohttp import web

from src.organism.logging.error_handler import get_logger

_log = get_logger("mcp_1c.server")

# \u2500\u2500 Tool definitions \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_counterparties",
        "description": "Search counterparties (contractors, suppliers) in 1C by name or INN",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (name, part of name, or INN)",
                },
                "limit": {
                    "type": "integer",
                    "default": 10,
                    "description": "Max results",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_fuel_consumption",
        "description": "Get fuel (diesel/gasoline) consumption data for equipment over a date range",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date_from": {
                    "type": "string",
                    "description": "Start date (YYYY-MM-DD)",
                },
                "date_to": {
                    "type": "string",
                    "description": "End date (YYYY-MM-DD)",
                },
                "equipment": {
                    "type": "string",
                    "default": "",
                    "description": "Equipment name filter (optional)",
                },
            },
            "required": ["date_from", "date_to"],
        },
    },
    {
        "name": "get_equipment_registry",
        "description": "List all registered equipment with status, mileage, and maintenance schedule",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "default": "",
                    "description": "Filter by status: active, repair, decommissioned (empty = all)",
                },
            },
        },
    },
    {
        "name": "get_production_data",
        "description": "Get gold production data (extraction volumes) for a date range",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date_from": {
                    "type": "string",
                    "description": "Start date (YYYY-MM-DD)",
                },
                "date_to": {
                    "type": "string",
                    "description": "End date (YYYY-MM-DD)",
                },
                "site": {
                    "type": "string",
                    "default": "",
                    "description": "Mining site filter (optional)",
                },
            },
            "required": ["date_from", "date_to"],
        },
    },
    {
        "name": "get_spare_parts_requests",
        "description": "List pending spare parts requests with status and priority",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "default": "",
                    "description": "Filter: pending, approved, delivered, all (empty = pending)",
                },
            },
        },
    },
]


# \u2500\u2500 Demo data provider \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500


class DemoDataProvider:
    """Returns realistic demo data for a gold mining artel."""

    def search_counterparties(self, query: str, limit: int = 10) -> list[dict]:
        all_items = [
            {
                "name": "\u041e\u041e\u041e \u0422\u043e\u043f\u043b\u0438\u0432\u043d\u044b\u0439 \u0421\u043d\u0430\u0431",
                "inn": "2724567890",
                "type": "\u043f\u043e\u0441\u0442\u0430\u0432\u0449\u0438\u043a",
                "phone": "+7-914-555-01-01",
            },
            {
                "name": "\u0418\u041f \u041f\u0435\u0442\u0440\u043e\u0432 \u0421.\u0412. \u0417\u0430\u043f\u0447\u0430\u0441\u0442\u0438",
                "inn": "272300001234",
                "type": "\u043f\u043e\u0441\u0442\u0430\u0432\u0449\u0438\u043a",
                "phone": "+7-914-555-02-02",
            },
            {
                "name": "\u0410\u041e \u0414\u0430\u043b\u044c\u0437\u043e\u043b\u043e\u0442\u043e",
                "inn": "2701234567",
                "type": "\u043f\u043e\u043a\u0443\u043f\u0430\u0442\u0435\u043b\u044c",
                "phone": "+7-4212-55-55-55",
            },
            {
                "name": "\u041e\u041e\u041e \u0421\u043f\u0435\u0446\u0422\u0435\u0445\u0421\u0435\u0440\u0432\u0438\u0441",
                "inn": "2724111222",
                "type": "\u043f\u043e\u0434\u0440\u044f\u0434\u0447\u0438\u043a",
                "phone": "+7-914-555-03-03",
            },
            {
                "name": "\u041e\u041e\u041e \u0420\u043e\u0441\u043d\u0435\u0444\u0442\u044c-\u0414\u0412",
                "inn": "2700999888",
                "type": "\u043f\u043e\u0441\u0442\u0430\u0432\u0449\u0438\u043a",
                "phone": "+7-4212-99-88-77",
            },
        ]
        q = query.lower()
        found = [c for c in all_items if q in c["name"].lower() or q in c["inn"]]
        return found[:limit] if found else all_items[:limit]

    def get_fuel_consumption(
        self, date_from: str, date_to: str, equipment: str = "",
    ) -> list[dict]:
        records = [
            {
                "date": "2025-08-01",
                "equipment": "\u041a\u0430\u043c\u0410\u0417-65115 \u0410123\u0411\u0412",
                "fuel_type": "\u0434\u0438\u0437\u0435\u043b\u044c",
                "liters": 320,
                "cost_rub": 19200,
            },
            {
                "date": "2025-08-01",
                "equipment": "\u042d\u043a\u0441\u043a\u0430\u0432\u0430\u0442\u043e\u0440 Komatsu PC-300",
                "fuel_type": "\u0434\u0438\u0437\u0435\u043b\u044c",
                "liters": 580,
                "cost_rub": 34800,
            },
            {
                "date": "2025-08-01",
                "equipment": "\u0411\u0443\u043b\u044c\u0434\u043e\u0437\u0435\u0440 \u0422-170",
                "fuel_type": "\u0434\u0438\u0437\u0435\u043b\u044c",
                "liters": 410,
                "cost_rub": 24600,
            },
            {
                "date": "2025-08-02",
                "equipment": "\u041a\u0430\u043c\u0410\u0417-65115 \u0410123\u0411\u0412",
                "fuel_type": "\u0434\u0438\u0437\u0435\u043b\u044c",
                "liters": 305,
                "cost_rub": 18300,
            },
            {
                "date": "2025-08-02",
                "equipment": "\u042d\u043a\u0441\u043a\u0430\u0432\u0430\u0442\u043e\u0440 Komatsu PC-300",
                "fuel_type": "\u0434\u0438\u0437\u0435\u043b\u044c",
                "liters": 620,
                "cost_rub": 37200,
            },
            {
                "date": "2025-08-02",
                "equipment": "\u0411\u0443\u043b\u044c\u0434\u043e\u0437\u0435\u0440 \u0422-170",
                "fuel_type": "\u0434\u0438\u0437\u0435\u043b\u044c",
                "liters": 450,
                "cost_rub": 27000,
            },
        ]
        if equipment:
            eq = equipment.lower()
            records = [r for r in records if eq in r["equipment"].lower()]
        return records

    def get_equipment_registry(self, status: str = "") -> list[dict]:
        items = [
            {
                "name": "\u041a\u0430\u043c\u0410\u0417-65115",
                "reg_number": "\u0410123\u0411\u0412",
                "status": "active",
                "mileage_km": 145000,
                "next_maintenance": "2025-09-15",
                "year": 2019,
            },
            {
                "name": "Komatsu PC-300",
                "reg_number": "EX-007",
                "status": "active",
                "mileage_km": 0,
                "hours": 12400,
                "next_maintenance": "2025-08-20",
                "year": 2020,
            },
            {
                "name": "\u0411\u0443\u043b\u044c\u0434\u043e\u0437\u0435\u0440 \u0422-170",
                "reg_number": "BD-003",
                "status": "active",
                "mileage_km": 0,
                "hours": 8900,
                "next_maintenance": "2025-10-01",
                "year": 2018,
            },
            {
                "name": "\u0413\u0410\u0417\u0435\u043b\u044c NEXT",
                "reg_number": "\u0412456\u0413\u0414",
                "status": "repair",
                "mileage_km": 67000,
                "next_maintenance": "2025-08-10",
                "year": 2021,
            },
            {
                "name": "CAT D6",
                "reg_number": "BD-005",
                "status": "decommissioned",
                "mileage_km": 0,
                "hours": 22000,
                "year": 2012,
            },
        ]
        if status:
            items = [i for i in items if i["status"] == status]
        return items

    def get_production_data(
        self, date_from: str, date_to: str, site: str = "",
    ) -> list[dict]:
        records = [
            {
                "date": "2025-08-01",
                "site": "\u0423\u0447\u0430\u0441\u0442\u043e\u043a \u0421\u0435\u0432\u0435\u0440\u043d\u044b\u0439",
                "gold_grams": 2150,
                "ore_tons": 850,
                "yield_g_per_ton": 2.53,
            },
            {
                "date": "2025-08-02",
                "site": "\u0423\u0447\u0430\u0441\u0442\u043e\u043a \u0421\u0435\u0432\u0435\u0440\u043d\u044b\u0439",
                "gold_grams": 1980,
                "ore_tons": 820,
                "yield_g_per_ton": 2.41,
            },
            {
                "date": "2025-08-01",
                "site": "\u0423\u0447\u0430\u0441\u0442\u043e\u043a \u042e\u0436\u043d\u044b\u0439",
                "gold_grams": 1420,
                "ore_tons": 640,
                "yield_g_per_ton": 2.22,
            },
            {
                "date": "2025-08-02",
                "site": "\u0423\u0447\u0430\u0441\u0442\u043e\u043a \u042e\u0436\u043d\u044b\u0439",
                "gold_grams": 1650,
                "ore_tons": 710,
                "yield_g_per_ton": 2.32,
            },
        ]
        if site:
            s = site.lower()
            records = [r for r in records if s in r["site"].lower()]
        return records

    def get_spare_parts_requests(self, status: str = "") -> list[dict]:
        items = [
            {
                "id": "ZP-001",
                "equipment": "\u041a\u0430\u043c\u0410\u0417-65115",
                "part": "\u0424\u0438\u043b\u044c\u0442\u0440 \u043c\u0430\u0441\u043b\u044f\u043d\u044b\u0439",
                "qty": 5,
                "status": "pending",
                "priority": "normal",
                "requested_date": "2025-08-01",
            },
            {
                "id": "ZP-002",
                "equipment": "Komatsu PC-300",
                "part": "\u0413\u0438\u0434\u0440\u043e\u0446\u0438\u043b\u0438\u043d\u0434\u0440 \u043a\u043e\u0432\u0448\u0430",
                "qty": 1,
                "status": "approved",
                "priority": "high",
                "requested_date": "2025-07-28",
            },
            {
                "id": "ZP-003",
                "equipment": "\u0411\u0443\u043b\u044c\u0434\u043e\u0437\u0435\u0440 \u0422-170",
                "part": "\u0413\u0443\u0441\u0435\u043d\u0438\u0447\u043d\u0430\u044f \u043b\u0435\u043d\u0442\u0430",
                "qty": 2,
                "status": "pending",
                "priority": "high",
                "requested_date": "2025-08-03",
            },
            {
                "id": "ZP-004",
                "equipment": "\u0413\u0410\u0417\u0435\u043b\u044c NEXT",
                "part": "\u0422\u043e\u0440\u043c\u043e\u0437\u043d\u044b\u0435 \u043a\u043e\u043b\u043e\u0434\u043a\u0438",
                "qty": 4,
                "status": "delivered",
                "priority": "normal",
                "requested_date": "2025-07-20",
            },
        ]
        effective = status if status else "pending"
        if effective != "all":
            items = [i for i in items if i["status"] == effective]
        return items


# \u2500\u2500 Live 1C provider (skeleton) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500


class LiveDataProvider:
    """Connects to real 1C via OData HTTP service.

    This is a skeleton -- actual implementation depends on the artel's
    1C configuration and will be done by their 1C developers.
    """

    def __init__(self, odata_url: str, auth_token: str = "") -> None:
        self.odata_url = odata_url
        self.auth_token = auth_token

    def search_counterparties(self, query: str, limit: int = 10) -> list[dict]:
        raise NotImplementedError(
            "Live 1C connection not configured. Contact artel IT team."
        )

    def get_fuel_consumption(
        self, date_from: str, date_to: str, equipment: str = "",
    ) -> list[dict]:
        raise NotImplementedError("Live 1C connection not configured.")

    def get_equipment_registry(self, status: str = "") -> list[dict]:
        raise NotImplementedError("Live 1C connection not configured.")

    def get_production_data(
        self, date_from: str, date_to: str, site: str = "",
    ) -> list[dict]:
        raise NotImplementedError("Live 1C connection not configured.")

    def get_spare_parts_requests(self, status: str = "") -> list[dict]:
        raise NotImplementedError("Live 1C connection not configured.")


# \u2500\u2500 HTTP server \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500


class MCP1CServer:
    """MCP-protocol HTTP handler for 1C data operations."""

    def __init__(self, provider: DemoDataProvider | LiveDataProvider) -> None:
        self.provider = provider
        self._handlers: dict[str, Any] = {
            "search_counterparties": self._h_counterparties,
            "get_fuel_consumption": self._h_fuel,
            "get_equipment_registry": self._h_equipment,
            "get_production_data": self._h_production,
            "get_spare_parts_requests": self._h_spare_parts,
        }

    async def handle_tools_list(self, request: web.Request) -> web.Response:
        return web.json_response({"tools": MCP_TOOLS})

    async def handle_tools_call(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response(
                {"isError": True, "content": [{"type": "text", "text": "Invalid JSON"}]},
                status=400,
            )

        tool_name = body.get("name", "")
        arguments = body.get("arguments", {})

        handler = self._handlers.get(tool_name)
        if not handler:
            return web.json_response(
                {
                    "isError": True,
                    "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                },
                status=404,
            )

        try:
            result = handler(arguments)
            text = json.dumps(result, ensure_ascii=False, indent=2)
            return web.json_response({"content": [{"type": "text", "text": text}]})
        except NotImplementedError as e:
            return web.json_response(
                {"isError": True, "content": [{"type": "text", "text": str(e)}]},
                status=501,
            )
        except Exception as e:
            _log.error(f"Tool call error: {tool_name} \u2014 {e}")
            return web.json_response(
                {"isError": True, "content": [{"type": "text", "text": f"Error: {e}"}]},
                status=500,
            )

    # \u2500\u2500 Handler adapters \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

    def _h_counterparties(self, args: dict) -> list[dict]:
        return self.provider.search_counterparties(
            args.get("query", ""), args.get("limit", 10),
        )

    def _h_fuel(self, args: dict) -> list[dict]:
        return self.provider.get_fuel_consumption(
            args.get("date_from", ""), args.get("date_to", ""),
            args.get("equipment", ""),
        )

    def _h_equipment(self, args: dict) -> list[dict]:
        return self.provider.get_equipment_registry(args.get("status", ""))

    def _h_production(self, args: dict) -> list[dict]:
        return self.provider.get_production_data(
            args.get("date_from", ""), args.get("date_to", ""),
            args.get("site", ""),
        )

    def _h_spare_parts(self, args: dict) -> list[dict]:
        return self.provider.get_spare_parts_requests(args.get("status", ""))


def create_app(mode: str = "demo", odata_url: str = "") -> web.Application:
    """Create the aiohttp application with MCP routes."""
    if mode == "live":
        provider: DemoDataProvider | LiveDataProvider = LiveDataProvider(odata_url)
    else:
        provider = DemoDataProvider()

    server = MCP1CServer(provider)
    app = web.Application()
    app.router.add_post("/tools/list", server.handle_tools_list)
    app.router.add_post("/tools/call", server.handle_tools_call)
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MCP server for 1C")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--mode", choices=["demo", "live"], default="demo")
    parser.add_argument("--odata-url", default="")
    args = parser.parse_args()

    print(f"Starting MCP 1C server on port {args.port} (mode={args.mode})")
    app = create_app(mode=args.mode, odata_url=args.odata_url)
    web.run_app(app, port=args.port, print=lambda msg: _log.info(msg))
