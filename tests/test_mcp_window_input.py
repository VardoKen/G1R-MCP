"""HTTP-владение вводом и общая native-сессия без обращения к Windows или игре."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx2
from mcp.server.transport_security import TransportSecuritySettings

from g1r_mcp.development import CommandResult, DevelopmentService
from g1r_mcp.native_client import NativeClient
from g1r_mcp.server import create_server
from g1r_mcp.window_input import WindowInputError
from tests.test_mcp_observations import SessionNativeFixture


class MCPHTTPWindowInputTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._asyncioRunner.get_loop().slow_callback_duration = 2.0

    async def test_key_owner_strict_arguments_partial_receipt_and_shared_native_session(self):
        native_status = {"ready": True, "session_id": "fixture-session-1", "tick_count": 48}
        steps = [{"op": "status", "params": {}, "result": native_status} for _ in range(4)]
        with tempfile.TemporaryDirectory() as temporary, SessionNativeFixture(steps) as native:
            root = Path(temporary)
            repository, modkit, workspace, game = (root / name for name in
                                                    ("repository", "Modkit", "workspace", "Game"))
            for directory in (repository / "tools", modkit / "Script", workspace, game):
                directory.mkdir(parents=True)
            (repository / "tools" / "game_process_state.ps1").write_text("# fixture", encoding="utf-8")
            source = modkit / "Script" / "Fixture.as"
            source.write_bytes(b"class UFixture {}\r\n")
            original = source.read_bytes()
            identity = {"pid": 123, "creation_time": "2026-11-01T00:01:02.1234567Z",
                        "executable": str(game / "G1R" / "Binaries" / "Win64" / "G1R-Win64-Shipping.exe")}
            process_calls = []

            def execute(arguments, timeout):
                script = Path(arguments[arguments.index("-File") + 1]).name
                self.assertEqual(script, "game_process_state.ps1")
                process_calls.append((list(arguments), timeout))
                return CommandResult(0, json.dumps({"processes": [identity]}))

            service = DevelopmentService(repository_root=repository, workspace_root=workspace,
                                         modkit_root=modkit, game_root=game,
                                         executor=execute, powershell="fixture-powershell")
            self.addCleanup(service.close)
            backend = NativeClient(port=native.port)
            self.addCleanup(backend.close)
            receipt = {"status": "input_queued", "process": identity, "selected_hwnd": 1024,
                       "window_thread_id": 456, "virtual_key": 120,
                       "messages": [{"message": "WM_KEYDOWN", "message_id": 256, "wparam": 120,
                                     "lparam": 1, "queued": True},
                                    {"message": "WM_KEYUP", "message_id": 257, "wparam": 120,
                                     "lparam": 1 | (1 << 30) | (1 << 31), "queued": True}],
                       "handling_confirmed": False, "outcome_unknown": False, "do_not_retry": False}
            partial = copy.deepcopy(receipt)
            partial.update(status="input_queue_failed", outcome_unknown=True, do_not_retry=True,
                           error={"code": "window_message_failed", "message": "fixture keyup failed"})
            partial["messages"][1].update(queued=False, win32_error=5)
            windows_error = WindowInputError("window_message_failed", "fixture keyup failed", partial)

            with patch("g1r_mcp.server.NativeClient", return_value=backend) as native_factory, \
                 patch("g1r_mcp.server.DevelopmentService", return_value=service) as development_factory, \
                 patch("g1r_mcp.development.queue_key", side_effect=[receipt, windows_error, receipt]) as queue:
                server = create_server(modkit, workspace, game_root=game,
                                       native_port=native.port, transport="streamable-http")
                security = TransportSecuritySettings(enable_dns_rebinding_protection=True,
                    allowed_hosts=["127.0.0.1:27124"], allowed_origins=["http://127.0.0.1:27124"])
                app = server.streamable_http_app(host="127.0.0.1", streamable_http_path="/mcp",
                    stateless_http=False, json_response=True, transport_security=security)
                counter = 0

                async def rpc(client, method, params, session=None):
                    nonlocal counter
                    counter += 1
                    headers = {"Accept": "application/json, text/event-stream"}
                    if session is not None:
                        headers["Mcp-Session-Id"] = session
                    response = await client.post("/mcp", headers=headers,
                        json={"jsonrpc": "2.0", "id": counter, "method": method, "params": params})
                    self.assertEqual(response.status_code, 200, response.text)
                    return response.json()["result"]

                async def initialize(client, name):
                    nonlocal counter
                    counter += 1
                    response = await client.post("/mcp", headers={"Accept": "application/json, text/event-stream"},
                        json={"jsonrpc": "2.0", "id": counter, "method": "initialize", "params": {
                            "protocolVersion": "2025-06-18", "capabilities": {},
                            "clientInfo": {"name": name, "version": "1"}}})
                    self.assertEqual(response.status_code, 200, response.text)
                    session = response.headers["mcp-session-id"]
                    notified = await client.post("/mcp", headers={"Mcp-Session-Id": session,
                        "Accept": "application/json, text/event-stream"},
                        json={"jsonrpc": "2.0", "method": "notifications/initialized"})
                    self.assertEqual(notified.status_code, 202, notified.text)
                    return session

                async def tool(client, session, name, arguments=None):
                    result = await rpc(client, "tools/call", {"name": name, "arguments": arguments or {}}, session)
                    self.assertFalse(result.get("isError", False), result)
                    return result["structuredContent"]

                async def rejected(client, session, arguments, code=None):
                    result = await rpc(client, "tools/call", {"name": "g1r_development_key",
                                                            "arguments": arguments}, session)
                    self.assertTrue(result.get("isError", False), result)
                    text = "\n".join(item.get("text", "") for item in result["content"])
                    if code is not None:
                        payload = json.loads(text[text.index("{"):])
                        self.assertEqual(payload["code"], code)
                        return payload
                    return text

                async with app.router.lifespan_context(app):
                    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),
                                                 base_url="http://127.0.0.1:27124") as first, \
                               httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),
                                                 base_url="http://127.0.0.1:27124") as second:
                        owner = await initialize(first, "input-owner")
                        other = await initialize(second, "input-other")
                        self.assertNotEqual(owner, other)
                        self.assertEqual(await tool(first, owner, "g1r_native_status"), native_status)
                        claim = await tool(first, owner, "g1r_session_acquire", {
                            "reason": "Изолированный оконный ввод", "expected_process": identity})
                        self.assertEqual(claim["claim"]["process"], identity)
                        arguments = {"expected_process": identity, "virtual_key": 120, "expected_window": 1024}
                        before = len(process_calls)
                        await rejected(second, other, arguments, "game_session_owned")
                        await rejected(first, owner, {**arguments, "expected_process": {
                            **identity, "creation_time": "2026-11-01T00:01:03.1234567Z"}},
                            "session_process_mismatch")
                        for invalid in ({"virtual_key": 120.5}, {"virtual_key": True}, {"expected_window": 1024.5}):
                            with self.subTest(arguments=invalid):
                                await rejected(first, owner, {**arguments, **invalid})
                        self.assertEqual(len(process_calls), before)
                        queue.assert_not_called()
                        queued = await tool(first, owner, "g1r_development_key", arguments)
                        self.assertEqual(queued, receipt)
                        self.assertEqual([item["message"] for item in queued["messages"]], ["WM_KEYDOWN", "WM_KEYUP"])
                        self.assertFalse(queued["handling_confirmed"])
                        queue.assert_called_once_with(identity, 120, expected_window=1024)
                        failed = await rejected(first, owner, arguments, "window_message_failed")
                        self.assertEqual(failed["receipt"], partial)
                        self.assertTrue(failed["outcome_unknown"])
                        self.assertTrue(failed["do_not_retry"])
                        self.assertEqual(failed["retry"], "only_explicit_new_request")
                        self.assertEqual(queue.call_count, 2)
                        self.assertEqual(await tool(second, other, "g1r_native_status"), native_status)
                        terminated = await first.delete("/mcp", headers={"Mcp-Session-Id": owner,
                                                        "Accept": "application/json, text/event-stream"})
                        self.assertEqual(terminated.status_code, 200, terminated.text)
                        remaining = await tool(second, other, "g1r_session_status")
                        self.assertEqual(remaining["claim"]["claim_id"], claim["claim"]["claim_id"])
                        await rejected(second, other, arguments, "game_session_owned")
                        self.assertEqual(queue.call_count, 2)
                        self.assertEqual(len(process_calls), before + 2)
                        self.assertEqual(await tool(second, other, "g1r_native_status"), native_status)
                        resume = {"reason": "Восстановление HTTP-владения", "expected_process": identity,
                                  "expected_claim_id": claim["claim"]["claim_id"]}
                        for invalid, expected_code in (({"expected_claim_id": "wrong"}, "session_claim_changed"),
                                                       ({"expected_process": None}, "session_process_mismatch")):
                            result = await rpc(second, "tools/call", {"name": "g1r_session_acquire",
                                                                     "arguments": {**resume, **invalid}}, other)
                            self.assertTrue(result.get("isError", False), result)
                            text = "\n".join(item.get("text", "") for item in result["content"])
                            self.assertEqual(json.loads(text[text.index("{"):])["code"], expected_code)
                            self.assertEqual((await tool(second, other, "g1r_session_status"))["claim"], remaining["claim"])
                            self.assertEqual(queue.call_count, 2)
                        resumed = await tool(second, other, "g1r_session_acquire", resume)
                        self.assertTrue(resumed["resumed"])
                        self.assertEqual(resumed["claim"], remaining["claim"])
                        restored_input = await tool(second, other, "g1r_development_key", arguments)
                        self.assertEqual(restored_input, receipt)
                        self.assertEqual(queue.call_count, 3)
                        self.assertEqual(queue.call_args.args, (identity, 120))
                        self.assertEqual(queue.call_args.kwargs, {"expected_window": 1024})
                        revoked = await first.post("/mcp", headers={"Mcp-Session-Id": owner,
                            "Accept": "application/json, text/event-stream"}, json={"jsonrpc": "2.0",
                            "id": 9999, "method": "tools/call", "params": {
                                "name": "g1r_development_key", "arguments": arguments}})
                        self.assertEqual(revoked.status_code, 404, revoked.text)
                        self.assertEqual(queue.call_count, 3)
                        self.assertEqual(await tool(second, other, "g1r_native_status"), native_status)
                        native_factory.assert_called_once_with(port=native.port)
                        development_factory.assert_called_once()
                self.assertEqual(native.connections, 1)
                self.assertEqual([item["op"] for item in native.requests], ["status"] * 4)
                self.assertEqual(len({item["id"] for item in native.requests}), 4)
                self.assertEqual(source.read_bytes(), original)
                self.assertEqual(list(workspace.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
