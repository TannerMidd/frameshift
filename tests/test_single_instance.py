"""The single-instance probe accepts Frameshift, not merely any HTTP 200."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import instance_already_running


class Response:
    def __init__(self, payload, *, status=200, headers=None):
        self.status = status
        self.headers = headers or {}
        self._body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, amount=-1):
        return self._body if amount < 0 else self._body[:amount]


product_headers = {
    "X-Frameshift-Product": "Frameshift",
    "X-Frameshift-Version": "2.10.0",
}
valid_state = {"journal_rebuild": {"phase": "ready"}, "galaxy_mode": "live"}


def probe(response):
    with patch("app.urllib.request.urlopen", return_value=response):
        return instance_already_running(8666)


assert probe(Response(valid_state, headers=product_headers))
assert not probe(Response(valid_state)), "a foreign HTTP server was accepted"
assert not probe(Response(valid_state, headers={**product_headers,
                                                "X-Frameshift-Product": "Other"}))
assert not probe(Response({"status": "ok"}, headers=product_headers))
assert not probe(Response(b"not json", headers=product_headers))
assert not probe(Response(valid_state, status=503, headers=product_headers))

print("single-instance probe OK: product/version/state identity required")
