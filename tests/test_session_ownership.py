"""Владение общей игрой: изолированные клиенты, без запуска процессов."""
import unittest
import threading
from g1r_mcp.session_ownership import SessionOwnership
from g1r_mcp.development import DevelopmentError


class SessionOwnershipTests(unittest.TestCase):
    def test_other_client_cannot_take_control_or_stop(self):
        state = SessionOwnership(); a, b = object(), object()
        process = {"pid": 11, "creation_time": "T1", "executable": "game"}
        first = state.acquire(a, "Проверка ExpApi", process=process)
        self.assertTrue(first["claimed"])
        for operation in (lambda: state.acquire(b, "Другая проверка"), lambda: state.require(b, process), lambda: state.release(b)):
            with self.assertRaises(DevelopmentError) as caught: operation()
            self.assertEqual(caught.exception.code, "game_session_owned")
        state.bind(a, process, "native_loaded")
        with self.assertRaises(DevelopmentError) as caught: state.require(a, {**process, "pid": 12})
        self.assertEqual(caught.exception.code, "session_process_mismatch")
        state.launched(a, "NewPackage")
        self.assertIsNone(state.status()["claim"]["process"])
        state.bind(a, {**process,"pid":12}, "native_loaded")
        self.assertFalse(state.release(a)["game_stopped"])
        self.assertFalse(state.status()["claimed"])
        self.assertTrue(state.acquire(b, "После завершения")["claimed"])

    def test_repeated_acquire_same_owner_keeps_claim(self):
        state = SessionOwnership(); a = object()
        first = state.acquire(a, "Проверка")
        again = state.acquire(a, "Продолжение")
        self.assertEqual(first["claim"]["claim_id"], again["claim"]["claim_id"])
        self.assertFalse(state.status()["disconnect_releases_claim"])
        for reason in ("", "a\n", "x" * 257, None):
            with self.assertRaises(DevelopmentError): state.acquire(a, reason)

    def test_concurrent_clients_only_one_owner(self):
        state = SessionOwnership(); owners = [object(), object()]; barrier = threading.Barrier(2); outcomes = []
        def claim(owner):
            barrier.wait()
            try: state.acquire(owner, "Одновременная заявка"); outcomes.append("claimed")
            except DevelopmentError as error: outcomes.append(error.code)
        threads = [threading.Thread(target=claim, args=(owner,)) for owner in owners]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertCountEqual(outcomes, ["claimed", "game_session_owned"])

    def test_orphan_recovery_requires_exact_claim_and_no_game(self):
        state = SessionOwnership(); owner = object()
        claim = state.acquire(owner, "Старая MCP-сессия")["claim"]["claim_id"]
        with self.assertRaises(DevelopmentError): state.release_abandoned(claim, game_running=True)
        with self.assertRaises(DevelopmentError): state.release_abandoned("other", game_running=False)
        self.assertTrue(state.status()["claimed"])
        self.assertEqual(state.release_abandoned(claim, game_running=False)["recovery"], "process_snapshot_empty")
        self.assertFalse(state.status()["claimed"])
