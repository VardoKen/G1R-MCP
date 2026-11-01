"""Владение общей игрой: изолированные клиенты, без запуска процессов."""
import unittest
import threading
from g1r_mcp.session_ownership import SessionOwnership
from g1r_mcp.development import DevelopmentError


class SessionOwnershipTests(unittest.TestCase):
    def test_explicit_resumption_keeps_claim_and_moves_control_to_new_owner(self):
        state = SessionOwnership(); old, new = object(), object()
        process = {"pid": 11, "creation_time": "T1", "executable": "game"}
        claim = state.acquire(old, "Исходная проверка", process=process, package="Main")
        state.bind(old, process, "native_loaded")
        original = state.status()["claim"]
        resumed = state.acquire(new, "Продолжение проверки", process=dict(process),
                                package="MustNotReplace", expected_claim_id=claim["claim"]["claim_id"])
        self.assertTrue(resumed["resumed"])
        self.assertEqual(resumed["claim"], original)
        state.require(new, process)
        for operation in (lambda: state.require(old, process), lambda: state.release(old),
                          lambda: state.acquire(old, "Без явного восстановления")):
            with self.assertRaises(DevelopmentError) as caught: operation()
            self.assertEqual(caught.exception.code, "game_session_owned")
        self.assertEqual(state.status()["claim"], original)

    def test_resumption_mismatched_claim_or_process_never_changes_owner_or_record(self):
        state = SessionOwnership(); old, new = object(), object()
        process = {"pid": 11, "creation_time": "T1", "executable": "game"}
        original = state.acquire(old, "Исходная проверка", process=process, package="Main")["claim"]
        claim_id = original["claim_id"]
        cases = [("wrong", process, "session_claim_changed"),
                 (claim_id, None, "session_process_mismatch")]
        cases += [(claim_id, {**process, key: value}, "session_process_mismatch")
                  for key, value in (("pid", 12), ("creation_time", "T2"), ("executable", "other"))]
        for expected, identity, code in cases:
            with self.subTest(claim=expected, process=identity), self.assertRaises(DevelopmentError) as caught:
                state.acquire(new, "Восстановление", process=identity, expected_claim_id=expected)
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(state.status()["claim"], original)
            state.require(old, process)
            with self.assertRaises(DevelopmentError): state.require(new, process)
        empty = SessionOwnership()
        with self.assertRaises(DevelopmentError) as caught:
            empty.acquire(new, "Восстановление", process=process, expected_claim_id=claim_id)
        self.assertEqual(caught.exception.code, "session_claim_changed")
        self.assertFalse(empty.status()["claimed"])

    def test_resumption_of_unbound_claim_preserves_pending_launch(self):
        state = SessionOwnership(); old, new = object(), object()
        claim = state.acquire(old, "Запуск", package="Main")["claim"]
        state.launched(old, "Main")
        original = state.status()["claim"]
        with self.assertRaises(DevelopmentError) as caught:
            state.acquire(new, "Восстановление", process={"pid": 11}, expected_claim_id=claim["claim_id"])
        self.assertEqual(caught.exception.code, "session_process_mismatch")
        self.assertEqual(state.status()["claim"], original)
        resumed = state.acquire(new, "Восстановление", expected_claim_id=claim["claim_id"])
        self.assertTrue(resumed["resumed"])
        self.assertEqual(resumed["claim"], original)
        state.require(new)

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
