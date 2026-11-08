"""Optional shared-session metadata without starting or stopping processes."""
import unittest
import threading
from g1r_mcp.session_ownership import SessionOwnership
from g1r_mcp.development import DevelopmentError


class SessionOwnershipTests(unittest.TestCase):
    def test_explicit_resumption_keeps_metadata_without_restricting_clients(self):
        state = SessionOwnership(); old, new = object(), object()
        process = {"pid": 11, "creation_time": "T1", "executable": "game"}
        claim = state.acquire(old, "Initial inspection", process=process, package="Main")
        state.bind(old, process, "native_loaded")
        original = state.status()["claim"]
        resumed = state.acquire(new, "Continued inspection", process=dict(process),
                                package="MustNotReplace", expected_claim_id=claim["claim"]["claim_id"])
        self.assertTrue(resumed["resumed"])
        self.assertEqual(resumed["claim"], original)
        state.require(new, process)
        state.require(old, process)
        self.assertEqual(state.acquire(old, "Another inspection")["claim"], original)
        released = state.release(old)
        self.assertEqual(released["previous_claim"], original)
        self.assertFalse(released["game_stopped"])
        self.assertFalse(state.status()["claimed"])

    def test_stale_claim_and_process_identity_do_not_block_new_metadata(self):
        state = SessionOwnership(); old, new = object(), object()
        process = {"pid": 11, "creation_time": "T1", "executable": "game"}
        original = state.acquire(old, "Initial inspection", process=process, package="Main")["claim"]
        claim_id = original["claim_id"]
        cases = [("wrong", process), (claim_id, None)]
        cases += [(claim_id, {**process, key: value})
                  for key, value in (("pid", 12), ("creation_time", "T2"), ("executable", "other"))]
        expected_process = process
        for expected, identity in cases:
            with self.subTest(claim=expected, process=identity):
                resumed = state.acquire(new, "Recovery", process=identity, expected_claim_id=expected)
                self.assertTrue(resumed["resumed"])
                if identity is not None:
                    expected_process = identity
                self.assertEqual(resumed["claim"]["process"], expected_process)
                self.assertEqual(resumed["claim"]["claim_id"], claim_id)
                state.require(old, process)
                state.require(new, identity)
        empty = SessionOwnership()
        recovered = empty.acquire(new, "Recovery", process=process, expected_claim_id=claim_id)
        self.assertTrue(recovered["claimed"])
        self.assertTrue(recovered["resumed"])
        self.assertEqual(recovered["claim"]["process"], process)

    def test_resumption_can_update_unbound_launch_metadata(self):
        state = SessionOwnership(); old, new = object(), object()
        claim = state.acquire(old, "Launch", package="Main")["claim"]
        state.launched(old, "Main")
        process = {"pid": 11}
        resumed = state.acquire(new, "Recovery", process=process, expected_claim_id=claim["claim_id"])
        self.assertTrue(resumed["resumed"])
        self.assertEqual(resumed["claim"]["process"], process)
        self.assertEqual(resumed["claim"]["state"], "launch_requested")
        self.assertEqual(resumed["claim"]["relative_package"], "Main")
        original = state.status()["claim"]
        self.assertEqual(state.acquire(new, "Recovery", expected_claim_id="stale")["claim"], original)
        state.require(new)
        state.require(old)

    def test_control_does_not_require_metadata_and_any_client_can_update_or_release(self):
        state = SessionOwnership(); a, b = object(), object()
        process = {"pid": 11, "creation_time": "T1", "executable": "game"}
        state.require(a, process)
        self.assertFalse(state.status()["claimed"])
        state.bind(a, process, "native_loaded")
        self.assertEqual(state.status()["claim"]["process"], process)
        self.assertEqual(state.status()["scope"], "informational")
        self.assertFalse(state.status()["control_required"])
        self.assertFalse(state.status()["ownership_enforced"])
        replacement = {**process, "pid": 12}
        self.assertEqual(state.acquire(b, "Another inspection", process=replacement)["claim"]["process"], replacement)
        state.require(a, process)
        state.bind(a, process, "native_loaded")
        self.assertEqual(state.status()["claim"]["process"], process)
        self.assertTrue(state.release(b)["released"])
        self.assertFalse(state.release(a)["released"])
        state.launched(b, "NewPackage")
        self.assertIsNone(state.status()["claim"]["process"])
        state.bind(a, replacement, "native_loaded")
        self.assertEqual(state.status()["claim"]["process"], replacement)
        self.assertFalse(state.release(b)["game_stopped"])

    def test_repeated_acquire_keeps_metadata_without_text_length_or_content_limits(self):
        state = SessionOwnership(); a = object()
        first = state.acquire(a, "x" * 100001)
        for reason in ("", "a\n", "Continued inspection"):
            again = state.acquire(a, reason)
            self.assertEqual(first["claim"]["claim_id"], again["claim"]["claim_id"])
        self.assertFalse(state.status()["disconnect_releases_claim"])
        for reason in (None, 1):
            with self.assertRaises(DevelopmentError) as caught:
                state.acquire(a, reason)
            self.assertEqual(caught.exception.code, "invalid_session_reason")
        self.assertEqual(state.status()["claim"], first["claim"])

    def test_concurrent_clients_share_one_informational_record_without_refusals(self):
        state = SessionOwnership(); owners = [object(), object()]; barrier = threading.Barrier(2); outcomes = []
        def claim(owner):
            barrier.wait()
            outcomes.append(state.acquire(owner, "Concurrent inspection")["claim"]["claim_id"])
        threads = [threading.Thread(target=claim, args=(owner,)) for owner in owners]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(len(outcomes), 2)
        self.assertEqual(len(set(outcomes)), 1)
        for owner in owners:
            state.require(owner)

    def test_recovery_clears_metadata_with_stale_claim_id_and_running_game(self):
        state = SessionOwnership(); owner = object()
        original = state.acquire(owner, "Previous session")["claim"]
        released = state.release_abandoned("stale", game_running=True)
        self.assertTrue(released["released"])
        self.assertEqual(released["previous_claim"], original)
        self.assertFalse(released["game_stopped"])
        self.assertEqual(released["recovery"], "informational_claim_release")
        self.assertFalse(state.status()["claimed"])
        self.assertFalse(state.release_abandoned(original["claim_id"], game_running=False)["released"])
