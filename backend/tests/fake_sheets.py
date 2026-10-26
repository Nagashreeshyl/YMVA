"""A stand-in for the Google Sheets API (the requests the Sheets tool makes), keeping an
in-memory grid per tab. Used by the tests and the in-call test harness."""

import json
import re
from urllib.parse import unquote

import httpx


class FakeSheets:
    def __init__(self, tabs: dict[str, list[list[str]]] | None = None) -> None:
        self.tabs = tabs if tabs is not None else {"Sheet1": []}

    def _tab(self, a1: str) -> tuple[str, str]:
        name, _, cells = unquote(a1).partition("!")
        return name.strip("'"), cells

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = unquote(request.url.path)
        if request.headers.get("authorization") != "Bearer fake-token":
            return httpx.Response(401, json={"error": {"message": "no token"}})
        if not re.search(r"/values/", path):  # spreadsheet metadata
            sheets = [{"properties": {"title": t, "sheetId": i}} for i, t in enumerate(self.tabs)]
            return httpx.Response(200, json={"sheets": sheets})
        tab, cells = self._tab(path.split("/values/", 1)[1].removesuffix(":append"))
        if tab not in self.tabs:
            return httpx.Response(400, json={"error": {"message": f"Unable to parse range: {tab}"}})
        rows = self.tabs[tab]
        if request.method == "GET":
            wanted = rows[:1] if cells == "1:1" else rows
            return httpx.Response(200, json={"values": wanted} if wanted else {})
        body = json.loads(request.content)["values"]
        if request.method == "PUT":  # title row
            rows[:1] = [body[0]]
        else:  # append
            rows.append(body[0])
        return httpx.Response(200, json={"updates": {"updatedRows": 1}})
