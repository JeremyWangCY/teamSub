"""Manual checks of public release metadata. No application configuration is read."""
from dataclasses import dataclass
import json
import re
import urllib.error
import urllib.request

REPOSITORY = "JeremyWangCY/teamsSub"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
LATEST_API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
MAX_RESPONSE_BYTES = 1024 * 1024


class UpdateCheckError(RuntimeError):
    pass


def version_tuple(value):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value or "")
    if not match:
        raise ValueError("版本号格式无效")
    return tuple(int(part) for part in match.groups())


@dataclass(frozen=True)
class Release:
    version: str
    url: str

    def newer_than(self, current):
        return version_tuple(self.version) > version_tuple(current)


def check_latest_release():
    request = urllib.request.Request(LATEST_API_URL, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "TeamsSub-update-check",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        message = {404: "尚未找到正式发布版本", 403: "GitHub 请求受限，请稍后重试",
                   429: "GitHub 请求受限，请稍后重试"}.get(exc.code, "GitHub 暂时不可用，请稍后重试")
        raise UpdateCheckError(message) from None
    except (OSError, urllib.error.URLError):
        raise UpdateCheckError("无法连接 GitHub，请检查网络后重试") from None
    try:
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("response too large")
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
            raise ValueError("not a stable release")
        tag = data["tag_name"]
        version_tuple(tag)
        expected = f"{RELEASES_URL}/tag/{tag}"
        if data.get("html_url") != expected:
            raise ValueError("unexpected release URL")
        return Release(tag.removeprefix("v"), expected)
    except (ValueError, KeyError, TypeError):
        raise UpdateCheckError("GitHub 返回的版本信息无效，请稍后重试") from None
