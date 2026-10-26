import io
import subprocess
import sys
import tarfile

from common.config import RAW, ROOT

DATA_BRANCH = "data-gbfs"


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True).stdout


if __name__ == "__main__":
    try:
        git("fetch", "origin", DATA_BRANCH)
        archive = git("archive", "FETCH_HEAD", "gbfs_status")
    except subprocess.CalledProcessError as e:
        print(f"error: {e.stderr.decode().strip()}", file=sys.stderr)
        sys.exit(1)
    RAW.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        members = [m for m in tar.getmembers() if m.isfile() and m.name.endswith(".parquet")]
        tar.extractall(RAW, members=members, filter="data")
    print(f"{len(members)} snapshots -> {RAW / 'gbfs_status'}")
