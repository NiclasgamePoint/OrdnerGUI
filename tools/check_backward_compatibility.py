"""Exercise old-client/new-server and new-client/old-server over real HTTP."""

from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]


def check_openapi(old, new):
    """Protect baseline endpoints, response status codes and required inputs."""
    for path, methods in old["paths"].items():
        if path not in new["paths"]:
            raise ValueError(f"Removed API endpoint: {path}")
        for method, details in methods.items():
            candidate = new["paths"][path].get(method)
            if candidate is None:
                raise ValueError(f"Removed API method: {method} {path}")
            if not set(details.get("responses", {})) <= set(candidate.get("responses", {})):
                raise ValueError(f"Removed API response: {method} {path}")
            old_required = {(p["name"], p["in"]) for p in details.get("parameters", []) if p.get("required")}
            new_required = {(p["name"], p["in"]) for p in candidate.get("parameters", []) if p.get("required")}
            if not new_required <= old_required:
                raise ValueError(f"New required API parameter: {method} {path}")
            for parameter in details.get("parameters", []):
                matches = [p for p in candidate.get("parameters", []) if (p["name"], p["in"]) == (parameter["name"], parameter["in"])]
                if len(matches) != 1 or matches[0].get("schema") != parameter.get("schema"):
                    raise ValueError(f"Changed API parameter: {method} {path}")
            if candidate.get("requestBody") != details.get("requestBody"):
                raise ValueError(f"Changed request body: {method} {path}; compatibility review required")
            if candidate.get("security") != details.get("security"):
                raise ValueError(f"Changed authentication requirements: {method} {path}")
            for code, response in details.get("responses", {}).items():
                if candidate["responses"][code].get("content") != response.get("content"):
                    raise ValueError(f"Changed response contract: {method} {path} {code}")
    for name, schema in old.get("components", {}).get("schemas", {}).items():
        candidate = new.get("components", {}).get("schemas", {}).get(name)
        if candidate is None:
            raise ValueError(f"Removed API schema: {name}")
        if not set(candidate.get("required", [])) <= set(schema.get("required", [])):
            raise ValueError(f"New required schema field: {name}")
        for field, old_property in schema.get("properties", {}).items():
            if candidate.get("properties", {}).get(field) != old_property:
                raise ValueError(f"Changed baseline field: {name}.{field}; compatibility review required")


def check(baseline: str):
    with TemporaryDirectory(prefix="papagui-compatibility-") as temporary:
        old = Path(temporary)
        data = subprocess.check_output(["git", "archive", baseline, "packages/contracts", "packages/client", "packages/server", "tests/server/openapi-v2.json"], cwd=ROOT)
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            archive.extractall(old, filter="data")
        check_openapi(json.loads((old / "tests/server/openapi-v2.json").read_text()), json.loads((ROOT / "tests/server/openapi-v2.json").read_text()))
        for client_root, server_root in ((old, ROOT), (ROOT, old)):
            sources = [str(client_root / "packages" / part / "src") for part in ("contracts", "client")]
            code = (
                f"import sys;sys.path[:0]={sources!r};"
                "import papagui_client,papagui_contracts;"
                f"assert papagui_client.__file__.startswith({str(client_root)!r});"
                "import pytest;"
                "raise SystemExit(pytest.main(['tests/system/test_wire_interop.py','-q','-o','pythonpath=']))"
            )
            env = os.environ.copy()
            env["PAPAGUI_TEST_SERVER_ROOT"] = str(server_root)
            print(f"Testing client {client_root.name} against server {server_root.name}", flush=True)
            subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, check=True, timeout=300)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="0.4.3")
    check(parser.parse_args().baseline)
