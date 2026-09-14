"""Read complete stable releases from the fixed PapaGUI GitHub repository."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request


REPOSITORY = "NiclasgamePoint/OrdnerGUI"
API = f"https://api.github.com/repos/{REPOSITORY}"
MANIFEST_NAME = "papagui-update.json"
DATA_EPOCH = 1
COMPATIBILITY_FLOOR = "0.4.3"
IMAGE_REPOSITORY = "ghcr.io/niclasgamepoint/ordnergui-server"
ALLOWED_HOSTS = {"api.github.com", "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}


class UpdateError(RuntimeError):
    """An incomplete, incompatible or unverifiable update must not be activated."""


def version(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", value):
        raise UpdateError("Expected a stable three-part version")
    return tuple(int(part) for part in value.split("."))


def checked_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise UpdateError("Release download must use an approved HTTPS host")
    return value


class ReleaseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        checked_url(newurl)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            # A private asset redirects to signed object storage. Never forward
            # repository credentials, even if the first request was authenticated.
            redirected.remove_header("Authorization")
        return redirected


@dataclass(frozen=True)
class Asset:
    name: str
    size: int
    sha256: str
    url: str

    @classmethod
    def parse(cls, payload, release_assets) -> Asset:
        if not isinstance(payload, dict):
            raise UpdateError("Invalid asset metadata")
        name, size, digest = payload.get("name"), payload.get("size"), payload.get("sha256")
        if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]+", name):
            raise UpdateError("Invalid asset filename")
        if type(size) is not int or not 0 < size <= 2_000_000_000:
            raise UpdateError("Invalid asset size")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise UpdateError("Invalid asset digest")
        matches = [item for item in release_assets if item.get("name") == name]
        if len(matches) != 1 or matches[0].get("size") != size or matches[0].get("state") != "uploaded":
            raise UpdateError("Release asset is missing or incomplete")
        item = matches[0]
        if item.get("digest") != "sha256:" + digest:
            raise UpdateError("GitHub and manifest checksums differ")
        url = item.get("url", "")
        if not url.startswith(API + "/releases/assets/"):
            raise UpdateError("Asset does not belong to the configured repository")
        return cls(name, size, digest, checked_url(url))


@dataclass(frozen=True)
class Release:
    version: str
    clients: dict[str, Asset]
    server_image: str

    @classmethod
    def parse(cls, manifest, release) -> Release:
        if not isinstance(manifest, dict) or manifest.get("schema") != 1:
            raise UpdateError("Unsupported update manifest")
        target = manifest.get("version")
        version(target)
        if release.get("draft") is not False or release.get("prerelease") is not False or not release.get("published_at"):
            raise UpdateError("Only published stable releases may update installations")
        if release.get("tag_name") not in (target, "v" + target):
            raise UpdateError("Release tag and update version differ")
        if manifest.get("data_epoch") != DATA_EPOCH or manifest.get("api_versions") != ["2", "1"]:
            raise UpdateError("Release changes the supported API or data format")
        if manifest.get("compatibility_floor") != COMPATIBILITY_FLOOR:
            raise UpdateError("Release drops the supported compatibility baseline")
        clients = manifest.get("clients")
        if not isinstance(clients, dict) or set(clients) != {"windows-x64", "linux-x64", "macos-x64", "macos-arm64"}:
            raise UpdateError("A complete set of client platforms is required")
        image = manifest.get("server_image", "")
        if not re.fullmatch(re.escape(IMAGE_REPOSITORY) + r"@sha256:[0-9a-f]{64}", image):
            raise UpdateError("Server image must be pinned to the PapaGUI registry digest")
        assets = release.get("assets", [])
        return cls(target, {key: Asset.parse(value, assets) for key, value in clients.items()}, image)


class ReleaseFeed:
    def __init__(self, *, token_file: Path | None = None, opener=None):
        configured = os.getenv("PAPAGUI_RELEASE_TOKEN_FILE")
        self.token_file = token_file or (Path(configured) if configured else None)
        self.opener = opener or urllib.request.build_opener(ReleaseRedirect())

    def _open(self, url, *, binary=False):
        checked_url(url)
        headers = {"User-Agent": "PapaGUI-updater", "Accept": "application/octet-stream" if binary else "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.token_file is not None and urllib.parse.urlsplit(url).hostname == "api.github.com":
            token = self.token_file.read_text(encoding="utf-8").strip()
            if not token or "\n" in token or "\r" in token:
                raise UpdateError("Invalid release access token file")
            headers["Authorization"] = "Bearer " + token
        return self.opener.open(urllib.request.Request(url, headers=headers), timeout=15)

    def _json(self, url, *, binary=False):
        with self._open(url, binary=binary) as response:
            data = response.read(1_048_577)
        if len(data) > 1_048_576:
            raise UpdateError("Release metadata is too large")
        return data, json.loads(data)

    def latest(self) -> Release | None:
        try:
            _, release = self._json(API + "/releases/latest")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None  # No public stable release, or no private-repo access.
            raise UpdateError(f"GitHub release lookup failed ({exc.code})") from None
        if not isinstance(release, dict):
            raise UpdateError("Invalid release response")
        matches = [a for a in release.get("assets", []) if a.get("name") == MANIFEST_NAME]
        if not matches:
            return None  # Build is still running. The manifest is uploaded last.
        if len(matches) != 1:
            raise UpdateError("Duplicate update manifests")
        metadata = matches[0]
        digest = metadata.get("digest", "")
        asset = Asset.parse({"name": MANIFEST_NAME, "size": metadata.get("size"), "sha256": digest.removeprefix("sha256:")}, release["assets"])
        data, manifest = self._json(asset.url, binary=True)
        if len(data) != asset.size or hashlib.sha256(data).hexdigest() != asset.sha256:
            raise UpdateError("Manifest checksum verification failed")
        return Release.parse(manifest, release)

    def download(self, asset: Asset, target: Path) -> None:
        digest, size = hashlib.sha256(), 0
        created = False
        try:
            with target.open("xb") as output:
                created = True
                with self._open(asset.url, binary=True) as response:
                    while chunk := response.read(1024 * 1024):
                        size += len(chunk)
                        if size > asset.size:
                            raise UpdateError("Download exceeds declared size")
                        digest.update(chunk)
                        output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if size != asset.size or digest.hexdigest() != asset.sha256:
                raise UpdateError("Download checksum verification failed")
        except BaseException:
            if created:
                target.unlink(missing_ok=True)
            raise
