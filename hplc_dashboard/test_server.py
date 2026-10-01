import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from server import ApiError, Store, make_handler


class FakeClock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.store = Store(":memory:", clock=self.clock)
        self.sid = self.store.add_system({"name": "HPLC-01", "model": "Agilent 1260"})["id"]

    def status(self):
        return self.store.state()["systems"][0]

    def test_new_system_is_free(self):
        self.assertEqual(self.status()["status"], "free")
        self.assertIsNone(self.status()["run"])

    def test_run_lifecycle(self):
        run_id = self.store.start_run(self.sid, {"analyst": "ejb", "duration_minutes": 90})["id"]
        s = self.status()
        self.assertEqual(s["status"], "running")
        self.assertEqual(s["run"]["analyst"], "EJB")
        self.assertEqual(s["run"]["ends_at"] - s["run"]["started_at"], 90 * 60)

        self.clock.t += 91 * 60
        self.assertEqual(self.status()["status"], "done")

        self.store.finish_run(run_id)
        self.assertEqual(self.status()["status"], "free")

    def test_cannot_double_book(self):
        self.store.start_run(self.sid, {"analyst": "AB", "duration_minutes": 10})
        with self.assertRaises(ApiError) as e:
            self.store.start_run(self.sid, {"analyst": "CD", "duration_minutes": 10})
        self.assertEqual(e.exception.status, 409)

    def test_validation(self):
        for bad in ({"analyst": "", "duration_minutes": 10},
                    {"analyst": "TOOLONG", "duration_minutes": 10},
                    {"analyst": "A1", "duration_minutes": 10},
                    {"analyst": "AB", "duration_minutes": 0},
                    {"analyst": "AB", "duration_minutes": "abc"},
                    {"analyst": "AB"}):
            with self.assertRaises(ApiError, msg=bad):
                self.store.start_run(self.sid, bad)

    def test_extend_running_run_adds_to_end_time(self):
        run_id = self.store.start_run(self.sid, {"analyst": "AB", "duration_minutes": 60})["id"]
        self.store.extend_run(run_id, {"minutes": 30})
        r = self.status()["run"]
        self.assertEqual(r["ends_at"] - r["started_at"], 90 * 60)

    def test_extend_overdue_run_counts_from_now(self):
        run_id = self.store.start_run(self.sid, {"analyst": "AB", "duration_minutes": 60})["id"]
        self.clock.t += 120 * 60
        self.store.extend_run(run_id, {"minutes": 30})
        self.assertEqual(self.status()["run"]["ends_at"], self.clock.t + 30 * 60)
        self.assertEqual(self.status()["status"], "running")

    def test_maintenance_blocks_runs(self):
        self.store.update_system(self.sid, {"out_of_service": True})
        self.assertEqual(self.status()["status"], "maintenance")
        with self.assertRaises(ApiError):
            self.store.start_run(self.sid, {"analyst": "AB", "duration_minutes": 10})

    def test_cannot_take_busy_system_out_of_service(self):
        self.store.start_run(self.sid, {"analyst": "AB", "duration_minutes": 10})
        with self.assertRaises(ApiError):
            self.store.update_system(self.sid, {"out_of_service": True})

    def test_duplicate_name_rejected(self):
        with self.assertRaises(ApiError) as e:
            self.store.add_system({"name": "HPLC-01"})
        self.assertEqual(e.exception.status, 409)

    def test_delete_removes_runs(self):
        self.store.start_run(self.sid, {"analyst": "AB", "duration_minutes": 10})
        self.store.delete_system(self.sid)
        self.assertEqual(self.store.state()["systems"], [])
        self.assertEqual(self.store.history(7)["runs"], [])

    def test_utilization(self):
        # 12 h run released early after 6 h, then a 6 h run that is still going 3 h in.
        r1 = self.store.start_run(self.sid, {"analyst": "AB", "duration_minutes": 12 * 60})["id"]
        self.clock.t += 6 * 3600
        self.store.finish_run(r1)
        self.store.start_run(self.sid, {"analyst": "CD", "duration_minutes": 6 * 60})
        self.clock.t += 3 * 3600
        u = self.store.history(1)["utilization"][0]
        self.assertEqual(u["busy_hours"], 9.0)
        self.assertEqual(u["percent"], 37.5)
        self.assertEqual(u["runs"], 2)

    def test_utilization_ignores_time_after_timer_ends(self):
        # A run left unreleased overnight only counts its planned duration.
        r = self.store.start_run(self.sid, {"analyst": "AB", "duration_minutes": 60})["id"]
        self.clock.t += 10 * 3600
        self.store.finish_run(r)
        self.assertEqual(self.store.history(1)["utilization"][0]["busy_hours"], 1.0)


class HttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Store(":memory:")))
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def call(self, method, path, body=None):
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as res:
                return res.status, res.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_end_to_end(self):
        status, body = self.call("POST", "/api/systems", {"name": "HTTP-1"})
        self.assertEqual(status, 200)
        sid = json.loads(body)["id"]
        status, body = self.call("POST", f"/api/systems/{sid}/runs", {"analyst": "XY", "duration_minutes": 5})
        self.assertEqual(status, 200)
        run_id = json.loads(body)["id"]
        state = json.loads(self.call("GET", "/api/state")[1])
        self.assertEqual(next(s for s in state["systems"] if s["id"] == sid)["status"], "running")
        self.assertEqual(self.call("POST", f"/api/runs/{run_id}/finish")[0], 200)
        self.assertEqual(self.call("POST", f"/api/runs/{run_id}/finish")[0], 409)

    def test_errors(self):
        self.assertEqual(self.call("POST", "/api/systems", {})[0], 400)
        self.assertEqual(self.call("POST", "/api/systems/999/runs", {"analyst": "AB", "duration_minutes": 5})[0], 404)
        self.assertEqual(self.call("GET", "/api/nope")[0], 404)
        self.assertEqual(self.call("GET", "/api/history?days=x")[0], 400)

    def test_serves_dashboard_and_blocks_traversal(self):
        status, body = self.call("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"HPLC Availability", body)
        self.assertEqual(self.call("GET", "/../server.py")[0], 404)
        self.assertEqual(self.call("GET", "/%2e%2e/server.py")[0], 404)


if __name__ == "__main__":
    unittest.main()
