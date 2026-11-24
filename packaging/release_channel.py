"""Pythia's release lanes (docs/SPEC.md, decision 13), the way droidtop has them (droidtop
build-scripts/release_channel.py): Unstable is every green main build, one permanent pre-release each;
Testing and Stable are fixed tags a person moves to a build already published. Run by CI.

  stamp COMMIT
      Writes pythia/_build.py with this build's number and commit, before anything is built, so the
      installed Pythia knows which build it is (pythia.updater). The number is the count of commits
      reachable from COMMIT: it only grows while main is never rewritten, and a rebuild of the same
      commit gets the same number. Counted through the GitHub API, since CI checks out one commit.

  info ARTIFACTS OUT
      Gathers the installers the platform jobs uploaded (ARTIFACTS, as actions/download-artifact lays
      them out) into OUT under their lane names, and writes OUT/release-info.json: the updater's one
      probe. Its field names and the asset names are a contract with every installed Pythia.

  publish-build OUT
      Publishes OUT to a new pre-release tagged v<version>-dev.<build>: the Unstable lane. A rebuild of
      the same commit replaces its own release, never another.

  publish CHANNEL OUT
      Publishes OUT to the CHANNEL release (testing or stable), a moving tag: the new files are uploaded
      under a staged name while the old ones keep serving, then each is swapped in, release-info.json
      last, so an interrupted publish never leaves the lane empty.
"""

import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

REPO = os.environ.get("GITHUB_REPOSITORY", "mytop-project/pythia")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHANNELS = ("testing", "stable")
STAGED_PREFIX = "next."
INFO = "release-info.json"

# Lane asset name -> (kind the updater asks for, glob under ARTIFACTS). release-info.json is written last.
ASSETS = [
    ("pythia-windows-setup.exe", "windows-installer", "*/Pythia-*-windows-x86_64-setup.exe"),
    ("pythia-macos-arm64.dmg", "macos-dmg-arm64", "*/Pythia-*-macos-arm64.dmg"),
    ("pythia-macos-x86_64.dmg", "macos-dmg-x86_64", "*/Pythia-*-macos-x86_64.dmg"),
    ("pythia-linux-amd64.deb", "linux-deb", "*/pythia_*_amd64.deb"),
    ("pythia-linux-x86_64.rpm", "linux-rpm", "*/pythia-*.x86_64.rpm"),
]

BUILD_TAG_RE = re.compile(r"^v[0-9][0-9.]*-dev\.(\d+)$")


def gh(*args, check=True):
    result = subprocess.run(["gh", *args], capture_output=True, text=True)
    if check and result.returncode != 0:
        sys.exit(f"gh {' '.join(args)} failed:\n{result.stderr}")
    return result


def gh_json(*args):
    return json.loads(gh("api", *args).stdout)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def build_number(commit):
    # With one commit per page, the "last" page number is the commit count.
    out = gh("api", "--include", f"repos/{REPO}/commits?sha={commit}&per_page=1").stdout
    headers = out.split("\r\n\r\n" if "\r\n\r\n" in out else "\n\n", 1)[0]
    last = re.search(r'[?&]page=(\d+)>; rel="last"', headers)
    if last:
        return int(last.group(1))
    body = json.loads(out[len(headers) :].strip())
    if len(body) != 1:
        sys.exit(f"Could not count the commits reachable from {commit}")
    return 1


def base_version():
    import tomllib

    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
        return tomllib.load(f)["project"]["version"]


def stamp(commit):
    code = build_number(commit)
    with open(os.path.join(ROOT, "pythia", "_build.py"), "w", encoding="utf-8", newline="\n") as f:
        f.write(
            '"""Which build this is: written by CI before it builds (packaging/release_channel.py stamp)."""\n\n'
            f"CODE = {code}\n"
            f'COMMIT = "{commit}"\n'
        )
    print(code)


def info(artifacts, out, code_and_commit=None):
    os.makedirs(out, exist_ok=True)
    text = open(os.path.join(ROOT, "pythia", "_build.py"), encoding="utf-8").read()
    code = int(re.search(r"^CODE = (\d+)$", text, re.M).group(1))
    commit = re.search(r'^COMMIT = "([0-9a-f]*)"$', text, re.M).group(1)
    if not code or not commit:
        sys.exit("pythia/_build.py is not stamped; run stamp first")
    assets = {}
    for name, kind, pattern in ASSETS:
        found = glob.glob(os.path.join(artifacts, pattern))
        if len(found) != 1:
            sys.exit(f"Expected one {pattern} under {artifacts}, found {found}")
        shutil.copyfile(found[0], os.path.join(out, name))
        assets[kind] = {"name": name, "sha256": sha256(os.path.join(out, name)), "size": os.path.getsize(found[0])}
    version = base_version()
    document = {
        "formatVersion": 1,
        "versionCode": code,
        "versionName": f"{version}-dev.{code}",
        "version": version,
        "commit": commit,
        "assets": assets,
    }
    with open(os.path.join(out, INFO), "w", encoding="utf-8") as f:
        json.dump(document, f, indent=2)
    print(json.dumps(document, indent=2))


def read_info(out):
    with open(os.path.join(out, INFO), encoding="utf-8") as f:
        document = json.load(f)
    for asset in document["assets"].values():
        if sha256(os.path.join(out, asset["name"])) != asset["sha256"]:
            sys.exit(f"{asset['name']} does not match {INFO}; refusing to publish")
    return document


def files_of(document):
    return [asset["name"] for asset in document["assets"].values()] + [INFO]


def notes_for(document, lane):
    return (
        f"Pythia {document['versionName']} ({lane}), built from commit {document['commit']}. "
        "Standalone installers, each with its own Python and Qt:\n\n"
        "- Windows: pythia-windows-setup.exe\n"
        "- macOS: pythia-macos-arm64.dmg (Apple silicon) or pythia-macos-x86_64.dmg (Intel)\n"
        "- Linux: pythia-linux-amd64.deb (Debian, Ubuntu) or pythia-linux-x86_64.rpm (Fedora and others)\n\n"
        "An installed Pythia updates itself on the lane chosen in its settings."
    )


def release_or_none(tag):
    result = gh("api", f"repos/{REPO}/releases/tags/{tag}", check=False)
    if result.returncode == 0:
        return json.loads(result.stdout)
    if "HTTP 404" in result.stderr:
        return None
    sys.exit(f"Could not read the {tag} release:\n{result.stderr}")


def publish_build(out):
    document = read_info(out)
    tag = f"v{document['versionName']}"
    if release_or_none(tag) is not None:
        gh("release", "delete", tag, "--yes", "--cleanup-tag")
    paths = [os.path.join(out, name) for name in files_of(document)]
    gh(
        "release",
        "create",
        tag,
        *paths,
        "--target",
        document["commit"],
        "--prerelease",
        "--title",
        f"Pythia {tag}",
        "--notes",
        notes_for(document, "unstable"),
    )
    print(f"Published {tag} at {document['commit']}")


def point_tag(channel, commit):
    exists = gh("api", f"repos/{REPO}/git/ref/tags/{channel}", check=False).returncode == 0
    if exists:
        gh("api", "-X", "PATCH", f"repos/{REPO}/git/refs/tags/{channel}", "-f", f"sha={commit}", "-F", "force=true")
    else:
        gh("api", "-X", "POST", f"repos/{REPO}/git/refs", "-f", f"ref=refs/tags/{channel}", "-f", f"sha={commit}")


def publish(channel, out):
    if channel not in CHANNELS:
        sys.exit(f"The lane is one of {', '.join(CHANNELS)}, not {channel}")
    document = read_info(out)
    names = files_of(document)
    title = f"Pythia {channel}"
    notes = notes_for(document, channel)
    release = release_or_none(channel)
    point_tag(channel, document["commit"])
    if release is None:
        flags = ["--prerelease"] if channel == "testing" else []
        gh(
            "release",
            "create",
            channel,
            *[os.path.join(out, n) for n in names],
            "--verify-tag",
            "--title",
            title,
            "--notes",
            notes,
            *flags,
        )
        print(f"Created the {channel} release at {document['commit']}")
        return
    for asset in release["assets"]:
        if asset["name"].startswith(STAGED_PREFIX):
            gh("api", "-X", "DELETE", f"repos/{REPO}/releases/assets/{asset['id']}")
    stage = tempfile.mkdtemp()
    staged = []
    for name in names:
        path = os.path.join(stage, STAGED_PREFIX + name)
        shutil.copyfile(os.path.join(out, name), path)
        staged.append(path)
    gh("release", "upload", channel, *staged)
    assets = {a["name"]: a["id"] for a in gh_json(f"repos/{REPO}/releases/{release['id']}/assets?per_page=100")}
    # release-info.json is in names last: the updater acts on nothing until it is swapped in.
    for name in names:
        if name in assets:
            gh("api", "-X", "DELETE", f"repos/{REPO}/releases/assets/{assets[name]}")
        gh("api", "-X", "PATCH", f"repos/{REPO}/releases/assets/{assets[STAGED_PREFIX + name]}", "-f", f"name={name}")
    # An asset a newer build no longer has (a renamed installer) goes too, so the lane lists only this build.
    for name, asset_id in assets.items():
        if not name.startswith(STAGED_PREFIX) and name not in names:
            gh("api", "-X", "DELETE", f"repos/{REPO}/releases/assets/{asset_id}")
    gh("api", "-X", "PATCH", f"repos/{REPO}/releases/{release['id']}", "-f", f"name={title}", "-f", f"body={notes}")
    print(f"Updated the {channel} release to {document['commit']}")


def main(argv):
    if len(argv) == 2 and argv[0] == "stamp":
        stamp(argv[1])
    elif len(argv) == 3 and argv[0] == "info":
        info(argv[1], argv[2])
    elif len(argv) == 2 and argv[0] == "publish-build":
        publish_build(argv[1])
    elif len(argv) == 3 and argv[0] == "publish":
        publish(argv[1], argv[2])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
