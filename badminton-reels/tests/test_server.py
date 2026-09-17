"""The web editor's HTTP API, driven the way the browser drives it."""

import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from badminton_reels.errors import ProjectError
from badminton_reels.project import Project, source_dict
from badminton_reels.server import _parse_range, create_server
from badminton_reels.workspace import Workspace
from badminton_reels.probe import VideoInfo


def fake_info(path, duration=60.0):
    return VideoInfo(path=path, duration=duration, width=1920, height=1080, fps=30.0,
                     codec="h264", has_audio=True)


def fake_project(directory, name="match.mp4", duration=60.0):
    """A project with plausible analysis data and no real video behind it."""
    source = os.path.join(directory, name)
    with open(source, "wb") as handle:
        handle.write(b"\0" * 4096)
    project = Project(source=source_dict(fake_info(source, duration)))
    rate = 10.0
    count = int(duration * rate)
    project.analysis = {
        "impact_times": [round(5.0 + 0.7 * i, 2) for i in range(12)]
                        + [round(30.0 + 0.6 * i, 2) for i in range(9)],
        "track": {
            "rate": rate,
            "cx": [round(0.3 + 0.4 * (i / count), 3) for i in range(count)],
            "cy": [0.5] * count,
        },
        "curve": {"rate": 4.0, "score": [0.6] * int(duration * 4), "enter": 0.4, "exit": 0.2},
        "roi": [0.1, 0.2, 0.8, 0.6],
        "impacts": 21,
    }
    project.add_clip(4.0, 14.0, label="Rally 1")
    project.add_clip(28.0, 40.0, label="Rally 2")
    project.render = {"layout": "follow", "pan": "smooth", "zoom": 1.0, "width": 1080, "height": 1920,
                      "fps": 30.0, "crf": 20, "speed": 1.0, "fade": 0.12}
    return project


class ServerCase(unittest.TestCase):
    """Boots a real server on a free port for each test class."""

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.mkdtemp(prefix="bdr-api-")
        cls.media = os.path.join(cls.directory, "media-in")
        os.makedirs(cls.media, exist_ok=True)
        cls.server, cls.url = create_server(
            port=0,
            workspace_root=os.path.join(cls.directory, "ws"),
            media=[cls.media],
            token="test-token",
        )
        cls.base = "http://127.0.0.1:%d" % cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={"poll_interval": 0.05})
        cls.thread.daemon = True
        cls.thread.start()
        cls.workspace = cls.server.editor.workspace

    @classmethod
    def tearDownClass(cls):
        cls.server.editor.jobs.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        shutil.rmtree(cls.directory, ignore_errors=True)

    # -- request helpers --------------------------------------------------
    def request(self, path, *, method="GET", body=None, token="test-token", headers=None, raw=False):
        url = self.base + path
        data = None
        request_headers = dict(headers or {})
        if token:
            request_headers["X-BDR-Token"] = token
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, method=method, headers=request_headers)
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = response.read()
            if raw:
                return response, payload
            return json.loads(payload.decode("utf-8")) if payload else {}

    def expect_error(self, path, *, method="GET", body=None, token="test-token", headers=None):
        try:
            self.request(path, method=method, body=body, token=token, headers=headers)
        except urllib.error.HTTPError as error:
            detail = {}
            try:
                detail = json.loads(error.read().decode("utf-8"))
            except Exception:  # noqa: BLE001
                pass
            return error.code, detail.get("error", "")
        self.fail("expected an error from %s %s" % (method, path))

    def make_project(self, name="match.mp4", duration=60.0):
        project = fake_project(self.media, name=name, duration=duration)
        project_id = self.workspace.new_project_id(project.source_path)
        self.workspace.save(project, project_id)
        return project_id


class TestAccessControl(ServerCase):
    def test_state_needs_the_token(self):
        code, message = self.expect_error("/api/state", token=None)
        self.assertEqual(code, 401)
        self.assertIn("token", message)
        self.assertEqual(self.expect_error("/api/state", token="wrong")[0], 401)

    def test_token_may_come_from_the_query_string(self):
        """Needed for <video> and <img>, which cannot set headers."""
        payload = self.request("/api/state?t=test-token", token=None)
        self.assertIn("workspace", payload)

    def test_non_loopback_host_is_refused(self):
        code, message = self.expect_error("/api/state", headers={"Host": "evil.example.com"})
        self.assertEqual(code, 403)
        self.assertIn("localhost", message)

    def test_the_page_itself_needs_no_token(self):
        response, body = self.request("/", token=None, raw=True)
        self.assertEqual(response.status, 200)
        self.assertIn(b"badminton-reels", body)
        self.assertIn("text/html", response.headers["Content-Type"])

    def test_static_assets_are_served(self):
        for path, kind in (("/static/app.js", "javascript"), ("/static/app.css", "css")):
            response, body = self.request(path, token=None, raw=True)
            self.assertEqual(response.status, 200)
            self.assertIn(kind, response.headers["Content-Type"])
            self.assertGreater(len(body), 1000)

    def test_unknown_asset_and_endpoint(self):
        self.assertEqual(self.expect_error("/static/../server.py", token=None)[0], 404)
        self.assertEqual(self.expect_error("/api/nope")[0], 404)

    def test_wrong_method_is_reported(self):
        code, message = self.expect_error("/api/state", method="DELETE")
        self.assertEqual(code, 405)
        self.assertIn("GET", message)


class TestState(ServerCase):
    def test_state_lists_media_and_defaults(self):
        with open(os.path.join(self.media, "sample.mp4"), "wb") as handle:
            handle.write(b"\0" * 32)
        payload = self.request("/api/state")
        self.assertIn("sample.mp4", [item["name"] for item in payload["media"]])
        self.assertIn("layout", payload["defaults"]["render"])
        self.assertIn("min_duration", payload["defaults"]["detect"])
        self.assertGreater(payload["min_clip_duration"], 0)

    def test_analyse_rejects_a_source_outside_the_media_roots(self):
        code, message = self.expect_error(
            "/api/analyze", method="POST", body={"path": "/etc/hosts"},
        )
        self.assertEqual(code, 400)
        self.assertIn("outside", message)

    def test_analyse_rejects_a_missing_file(self):
        code, _ = self.expect_error(
            "/api/analyze", method="POST", body={"path": os.path.join(self.media, "nope.mp4")},
        )
        self.assertEqual(code, 400)

    def test_upload_lands_in_the_media_folder(self):
        data = b"not really a video" * 16
        request = urllib.request.Request(
            self.base + "/api/upload?name=phone%20clip.MOV",
            data=data, method="POST", headers={"X-BDR-Token": "test-token"},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertTrue(payload["path"].startswith(self.workspace.media_dir))
        self.assertEqual(payload["name"], "phone clip.MOV")
        with open(payload["path"], "rb") as handle:
            self.assertEqual(handle.read(), data)

    def test_jobs_endpoint_starts_empty_and_lists(self):
        self.assertIsInstance(self.request("/api/jobs")["jobs"], list)
        self.assertEqual(self.expect_error("/api/jobs/deadbeef")[0], 404)


class TestProjectEditing(ServerCase):
    def setUp(self):
        self.project_id = self.make_project(name="edit-%d.mp4" % time.time_ns())
        self.path = "/api/projects/%s" % self.project_id

    def project(self):
        return self.request(self.path)

    def test_payload_describes_the_project(self):
        payload = self.project()
        self.assertEqual(payload["id"], self.project_id)
        self.assertEqual(len(payload["clips"]), 2)
        self.assertEqual(payload["outputs"], [])
        self.assertFalse(payload["has_proxy"])
        self.assertIn("track", payload["analysis"])

    def test_bad_project_id_is_rejected_not_traversed(self):
        self.assertEqual(self.expect_error("/api/projects/..")[0], 404)
        self.assertEqual(self.expect_error("/api/projects/%2e%2e%2fetc")[0], 404)

    def test_trimming_a_clip_persists_and_rescores(self):
        before = self.project()["clips"][0]
        payload = self.request(self.path, method="PUT", body={
            "clips": [{"id": before["id"], "start": 6.0, "end": 11.0}],
        })
        after = payload["clips"][0]
        self.assertAlmostEqual(after["start"], 6.0)
        self.assertAlmostEqual(after["end"], 11.0)
        # Impacts and score follow the new span.
        self.assertNotEqual(after["shots"], before["shots"])
        self.assertGreater(after["score"], 0)
        # And it is on disk, not just in the reply.
        self.assertAlmostEqual(Project.load(self.workspace.project_file(self.project_id)).clips[0].start, 6.0)

    def test_keep_label_and_overrides_round_trip(self):
        clip_id = self.project()["clips"][1]["id"]
        payload = self.request(self.path, method="PUT", body={"clips": [{
            "id": clip_id, "keep": False, "label": "Best point",
            "layout": "stack", "zoom": 1.25, "speed": 0.5, "pan": "none",
        }]})
        clip = [c for c in payload["clips"] if c["id"] == clip_id][0]
        self.assertFalse(clip["keep"])
        self.assertEqual(clip["label"], "Best point")
        self.assertEqual(clip["layout"], "stack")
        self.assertAlmostEqual(clip["zoom"], 1.25)
        self.assertAlmostEqual(clip["speed"], 0.5)
        self.assertEqual(clip["pan"], "none")
        # Clearing an override falls back to the project default.
        payload = self.request(self.path, method="PUT", body={"clips": [{"id": clip_id, "layout": None}]})
        self.assertNotIn("layout", [c for c in payload["clips"] if c["id"] == clip_id][0])

    def test_render_defaults_are_validated_on_the_way_in(self):
        payload = self.request(self.path, method="PUT", body={
            "render": {"layout": "fit", "zoom": 0.8, "fps": 60},
        })
        self.assertEqual(payload["render"]["layout"], "fit")
        self.assertAlmostEqual(payload["render"]["zoom"], 0.8)
        self.assertAlmostEqual(payload["render"]["fps"], 60.0)
        # Unknown keys are dropped rather than stored and later confusing ffmpeg.
        payload = self.request(self.path, method="PUT", body={"render": {"nonsense": 5}})
        self.assertNotIn("nonsense", payload["render"])

    def test_an_impossible_range_is_refused(self):
        clip_id = self.project()["clips"][0]["id"]
        code, message = self.expect_error(self.path, method="PUT", body={
            "clips": [{"id": clip_id, "start": 20.0, "end": 20.05}],
        })
        self.assertEqual(code, 400)
        self.assertIn("at least", message)

    def test_out_of_range_overrides_are_refused(self):
        clip_id = self.project()["clips"][0]["id"]
        self.assertEqual(self.expect_error(self.path, method="PUT", body={
            "clips": [{"id": clip_id, "zoom": 99}]})[0], 400)

    def test_editing_an_unknown_clip_is_reported(self):
        code, message = self.expect_error(self.path, method="PUT", body={"clips": [{"id": "r99"}]})
        self.assertEqual(code, 400)
        self.assertIn("r99", message)
        self.assertEqual(self.expect_error(self.path, method="PUT", body={"clips": [{}]})[0], 400)

    def test_malformed_json_is_reported(self):
        request = urllib.request.Request(
            self.base + self.path, data=b"{not json", method="PUT",
            headers={"X-BDR-Token": "test-token", "Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=20)
        self.assertEqual(caught.exception.code, 400)

    def test_adding_a_clip_sorts_scores_and_persists(self):
        payload = self.request(self.path + "/clips", method="POST",
                               body={"start": 45.0, "end": 52.0, "label": "Added"})
        self.assertEqual(payload["clip"]["label"], "Added")
        starts = [clip["start"] for clip in payload["project"]["clips"]]
        self.assertEqual(starts, sorted(starts))
        self.assertEqual(len(payload["project"]["clips"]), 3)
        self.assertEqual(len({clip["id"] for clip in payload["project"]["clips"]}), 3)

    def test_adding_an_empty_clip_is_refused(self):
        self.assertEqual(self.expect_error(self.path + "/clips", method="POST",
                                          body={"start": 5.0, "end": 5.1})[0], 400)

    def test_splitting_a_clip(self):
        clip_id = self.project()["clips"][0]["id"]
        payload = self.request(self.path + "/clips/%s/split" % clip_id, method="POST", body={"at": 9.0})
        self.assertAlmostEqual(payload["head"]["end"], 9.0)
        self.assertAlmostEqual(payload["tail"]["start"], 9.0)
        self.assertEqual(len(payload["project"]["clips"]), 3)
        self.assertGreater(payload["tail"]["score"], 0)
        # Splitting outside the clip is refused.
        self.assertEqual(self.expect_error(self.path + "/clips/%s/split" % clip_id,
                                          method="POST", body={"at": 50.0})[0], 400)

    def test_deleting_a_clip(self):
        clip_id = self.project()["clips"][0]["id"]
        payload = self.request(self.path + "/clips/%s" % clip_id, method="DELETE")
        self.assertEqual(len(payload["project"]["clips"]), 1)
        self.assertEqual(self.expect_error(self.path + "/clips/%s" % clip_id, method="DELETE")[0], 400)

    def test_renumbering(self):
        clip_id = self.project()["clips"][0]["id"]
        self.request(self.path, method="PUT", body={"clips": [{"id": clip_id, "keep": False}]})
        payload = self.request(self.path, method="PUT", body={"renumber": True})
        labels = [clip["label"] for clip in payload["clips"]]
        self.assertIn("Rally 1", labels)
        self.assertTrue(any("dropped" in label for label in labels))

    def test_deleting_a_project_removes_its_folder(self):
        project_id = self.make_project(name="doomed-%d.mp4" % time.time_ns())
        self.request("/api/projects/%s" % project_id, method="DELETE")
        self.assertFalse(os.path.exists(self.workspace.project_dir(project_id)))
        self.assertEqual(self.expect_error("/api/projects/%s" % project_id)[0], 400)


class TestPlanEndpoint(ServerCase):
    def setUp(self):
        self.project_id = self.make_project(name="plan-%d.mp4" % time.time_ns())
        self.path = "/api/projects/%s" % self.project_id
        self.clip_id = self.request(self.path)["clips"][0]["id"]

    def plan(self, **query):
        suffix = "&".join("%s=%s" % item for item in query.items())
        return self.request(f"{self.path}/clips/{self.clip_id}/plan" + (f"?{suffix}" if suffix else ""))

    def test_plan_matches_the_renderer(self):
        plan = self.plan()
        self.assertEqual(plan["layout"], "follow")
        self.assertEqual(plan["canvas"], {"width": 1080, "height": 1920})
        self.assertEqual(plan["source"], {"width": 1920, "height": 1080})
        pane = plan["panes"][0]
        self.assertEqual(len(pane["times"]), len(pane["xs"]))
        self.assertEqual(len(pane["times"]), len(pane["ys"]))
        self.assertEqual(pane["times"], sorted(pane["times"]))
        self.assertTrue(pane["tracking"])
        for x, y in zip(pane["xs"], pane["ys"]):
            self.assertGreaterEqual(x, 0)
            self.assertLessEqual(x + pane["crop_w"], plan["source"]["width"])
            self.assertLessEqual(y + pane["crop_h"], plan["source"]["height"])

    def test_layout_and_zoom_can_be_previewed_without_saving(self):
        stack = self.plan(layout="stack")
        self.assertEqual(len(stack["panes"]), 2)
        self.assertTrue(stack["blur_background"])
        tight = self.plan(zoom="1.5")
        self.assertLess(tight["panes"][0]["crop_w"], self.plan()["panes"][0]["crop_w"])
        held = self.plan(pan="none")
        self.assertFalse(held["panes"][0]["tracking"])
        # The project itself is untouched by a preview.
        self.assertEqual(self.request(self.path)["render"]["layout"], "follow")

    def test_plan_for_an_unknown_clip(self):
        self.assertEqual(self.expect_error(f"{self.path}/clips/r99/plan")[0], 400)


class TestMediaServing(ServerCase):
    def setUp(self):
        self.project_id = self.make_project(name="serve-%d.mp4" % time.time_ns())
        self.path = "/api/projects/%s" % self.project_id
        # Stand in for the proxy the analysis pass would have written.
        self.payload = bytes(range(256)) * 40
        proxy = os.path.join(self.workspace.project_dir(self.project_id), "proxy.mp4")
        with open(proxy, "wb") as handle:
            handle.write(self.payload)
        project = self.workspace.load(self.project_id)
        project.analysis["proxy"] = "proxy.mp4"
        self.workspace.save(project, self.project_id)

    def test_preview_is_served_whole(self):
        response, body = self.request(self.path + "/preview", raw=True)
        self.assertEqual(response.status, 200)
        self.assertEqual(body, self.payload)
        self.assertEqual(response.headers["Content-Type"], "video/mp4")
        self.assertEqual(response.headers["Accept-Ranges"], "bytes")

    def test_preview_honours_byte_ranges(self):
        request = urllib.request.Request(
            self.base + self.path + "/preview",
            headers={"X-BDR-Token": "test-token", "Range": "bytes=100-199"},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.headers["Content-Range"],
                             "bytes 100-199/%d" % len(self.payload))
            self.assertEqual(response.read(), self.payload[100:200])

    def test_unsatisfiable_range(self):
        request = urllib.request.Request(
            self.base + self.path + "/preview",
            headers={"X-BDR-Token": "test-token", "Range": "bytes=999999-"},
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=20)
        self.assertEqual(caught.exception.code, 416)

    def test_output_names_are_checked(self):
        for name in ("..%2Fproject.json", "notes.txt", "..%2F..%2Fetc%2Fhosts"):
            with self.subTest(name=name):
                self.assertIn(self.expect_error(self.path + "/outputs/" + name)[0], (400, 404))

    def test_outputs_are_listed_and_downloadable(self):
        out_dir = self.workspace.output_dir(self.project_id)
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "01-rally-1.mp4"), "wb") as handle:
            handle.write(b"clip")
        with open(os.path.join(out_dir, "reel.mp4"), "wb") as handle:
            handle.write(b"reel")
        outputs = self.request(self.path)["outputs"]
        # Reels first, they are what gets posted.
        self.assertEqual(outputs[0]["name"], "reel.mp4")
        self.assertEqual({entry["name"] for entry in outputs}, {"reel.mp4", "01-rally-1.mp4"})
        response, body = self.request(self.path + "/outputs/reel.mp4?download=1", raw=True)
        self.assertEqual(body, b"reel")
        self.assertIn("attachment", response.headers["Content-Disposition"])


class TestRangeParsing(unittest.TestCase):
    def test_forms_browsers_send(self):
        self.assertEqual(_parse_range("bytes=0-99", 1000), (0, 99))
        self.assertEqual(_parse_range("bytes=500-", 1000), (500, 999))
        self.assertEqual(_parse_range("bytes=-100", 1000), (900, 999))
        self.assertEqual(_parse_range("bytes=0-99999", 1000), (0, 999))

    def test_nonsense_is_rejected(self):
        for header in ("bytes=1000-", "bytes=50-10", "bytes=-", "items=0-5", "", None):
            self.assertIsNone(_parse_range(header, 1000))
        self.assertIsNone(_parse_range("bytes=0-10", 0))


class TestWorkspaceSafety(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="bdr-ws-")
        self.workspace = Workspace(os.path.join(self.directory, "ws"))

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_project_ids_cannot_escape(self):
        for bad in ("..", "../etc", "a/b", "", ".hidden/..", "x" * 200):
            with self.subTest(bad=bad), self.assertRaises(ProjectError):
                self.workspace.project_dir(bad)

    def test_project_ids_are_derived_and_unique(self):
        first = self.workspace.new_project_id("/tmp/My Match 2024.MOV")
        self.assertTrue(first.startswith("my-match-2024-"))
        os.makedirs(self.workspace.project_dir(first), exist_ok=True)
        self.assertNotEqual(self.workspace.new_project_id("/tmp/My Match 2024.MOV"), first)

    def test_media_outside_the_roots_is_refused(self):
        outside = os.path.join(self.directory, "elsewhere.mp4")
        with open(outside, "wb") as handle:
            handle.write(b"x")
        with self.assertRaises(ProjectError):
            self.workspace.resolve_media(outside)
        inside = os.path.join(self.workspace.media_dir, "ok.mp4")
        shutil.copy(outside, inside)
        self.assertEqual(self.workspace.resolve_media(inside), os.path.realpath(inside))

    def test_extra_media_roots_are_allowed(self):
        extra = os.path.join(self.directory, "extra")
        os.makedirs(extra, exist_ok=True)
        path = os.path.join(extra, "clip.mp4")
        with open(path, "wb") as handle:
            handle.write(b"x")
        workspace = Workspace(os.path.join(self.directory, "ws2"), extra_media=(extra,))
        self.assertEqual(workspace.resolve_media(path), os.path.realpath(path))

    def test_uploads_are_renamed_safely(self):
        data = b"video bytes"
        for name in ("../../etc/passwd", "weird*name?.mov", ""):
            with self.subTest(name=name):
                path = self.workspace.import_upload(name, __import__("io").BytesIO(data), len(data))
                self.assertEqual(os.path.dirname(path), self.workspace.media_dir)
                self.assertNotIn("..", os.path.basename(path))

    def test_a_short_upload_leaves_nothing_behind(self):
        import io

        before = set(os.listdir(self.workspace.media_dir))
        with self.assertRaises(ProjectError):
            self.workspace.import_upload("truncated.mp4", io.BytesIO(b"abc"), 500)
        self.assertEqual(set(os.listdir(self.workspace.media_dir)), before)

    def test_output_names_are_restricted(self):
        for bad in ("../project.json", "notes.txt", "", ".secret.mp4"):
            with self.subTest(bad=bad), self.assertRaises(ProjectError):
                self.workspace.output_file("anything", bad)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(
    __import__("badminton_reels.ffutil", fromlist=["have_binaries"]).have_binaries(),
    "ffmpeg and ffprobe are required",
)
class TestJobsEndToEnd(ServerCase):
    """Analyse and render through the API, the way the browser does."""

    def wait_for(self, job_id, timeout=420):
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = self.request("/api/jobs/%s" % job_id)
            if job["status"] in ("done", "error"):
                return job
            time.sleep(0.4)
        self.fail("job %s did not finish" % job_id)

    def test_analyse_then_render_through_the_api(self):
        import sys

        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import make_fixture

        source = make_fixture.build(os.path.join(self.media, "api-match.mp4"), duration=28.0)
        rallies = make_fixture.rallies_within(28.0)

        started = self.request("/api/analyze", method="POST", body={
            "path": source, "proxy": True, "settings": {"sensitivity": 1.0},
        })
        job = self.wait_for(started["id"])
        self.assertEqual(job["status"], "done", job.get("error"))
        project_id = job["result"]["project_id"]
        self.assertEqual(job["result"]["clips"], len(rallies))

        payload = self.request("/api/projects/%s" % project_id)
        self.assertTrue(payload["has_proxy"])
        self.assertGreater(len(payload["analysis"]["track"]["cx"]), 100)

        # The proxy is a real, seekable video the browser can scrub.
        response, body = self.request("/api/projects/%s/preview" % project_id, raw=True)
        self.assertEqual(response.headers["Accept-Ranges"], "bytes")
        self.assertGreater(len(body), 10000)

        # A thumbnail is generated on demand.
        clip_id = payload["clips"][0]["id"]
        response, image = self.request(
            "/api/projects/%s/clips/%s/thumb" % (project_id, clip_id), raw=True)
        self.assertEqual(response.headers["Content-Type"], "image/jpeg")
        self.assertEqual(image[:2], b"\xff\xd8")

        # Render one clip and check the file that lands.
        started = self.request("/api/projects/%s/render" % project_id, method="POST", body={
            "mode": "clips", "only": [clip_id], "labels": True,
        })
        job = self.wait_for(started["id"])
        self.assertEqual(job["status"], "done", job.get("error"))
        self.assertEqual(len(job["result"]["files"]), 1)

        outputs = self.request("/api/projects/%s" % project_id)["outputs"]
        self.assertEqual([entry["name"] for entry in outputs], job["result"]["files"])
        rendered = self.workspace.output_file(project_id, outputs[0]["name"])
        probe_out = __import__("subprocess").run(
            [os.environ.get("BDR_FFPROBE", "ffprobe"), "-v", "error", "-select_streams", "v",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", rendered],
            capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(probe_out.rstrip(","), "1080,1920")

    def test_re_detection_uses_the_stored_signals(self):
        import sys

        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import make_fixture

        source = make_fixture.build(os.path.join(self.media, "redetect.mp4"), duration=28.0)
        job = self.wait_for(self.request("/api/analyze", method="POST", body={"path": source})["id"])
        project_id = job["result"]["project_id"]
        before = len(self.request("/api/projects/%s" % project_id)["clips"])

        # Demanding many impacts should leave fewer rallies, and must not
        # decode the video again - so it finishes in a fraction of a second.
        started = self.request("/api/projects/%s/detect" % project_id, method="POST",
                               body={"detect": {"min_shots": 30}})
        done = self.wait_for(started["id"], timeout=30)
        self.assertEqual(done["status"], "done", done.get("error"))
        self.assertLess(done["elapsed"], 5.0)
        self.assertLess(len(self.request("/api/projects/%s" % project_id)["clips"]), before)
