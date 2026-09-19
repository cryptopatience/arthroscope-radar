import base64
import json
import unittest
from unittest.mock import Mock

from radar.cloud_saved import CloudStore, CloudSaveError


def response(code, body=None):
    result = Mock(status_code=code)
    result.json.return_value = body
    return result


def listing(values, sha="old"):
    return response(200, {"sha": sha, "content": base64.b64encode(json.dumps(values).encode()).decode()})


class CloudSavedTests(unittest.TestCase):
    def setUp(self):
        self.store = CloudStore(token="test-token")
        self.idea = {"id": "one", "title": "First", "savedAt": "2026-09-19", "obsidianPath": "private-local-path"}
        self.other = {"id": "two", "title": "Second", "savedAt": "2026-09-19"}

    def test_concurrent_save_reloads_and_preserves_other_device(self):
        self.store.request = Mock(side_effect=[response(200, {"private": True}), listing([]), response(409),
            response(200, {"private": True}), listing([self.other], "new"), response(200)])
        saved = self.store.update(self.idea)
        self.assertEqual([i["id"] for i in saved], ["one", "two"])
        payload = self.store.request.call_args.kwargs["json"]
        self.assertEqual(payload["sha"], "new")
        self.assertNotIn("obsidianPath", base64.b64decode(payload["content"]).decode())

    def test_public_repository_rejected_before_write(self):
        self.store.request = Mock(return_value=response(200, {"private": False}))
        with self.assertRaises(CloudSaveError):
            self.store.update(self.idea)
        self.assertEqual(self.store.request.call_count, 1)

    def test_write_failure_not_reported_as_success(self):
        self.store.request = Mock(side_effect=[response(200, {"private": True}), listing([]), response(403)])
        with self.assertRaises(CloudSaveError):
            self.store.update(self.idea)

    def test_remove_only_clicked_item(self):
        self.store.request = Mock(side_effect=[response(200, {"private": True}), listing([self.idea, self.other]), response(200)])
        self.assertEqual(self.store.update(self.idea, remove=True), [self.other])


if __name__ == "__main__":
    unittest.main()
