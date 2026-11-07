import os
import re
import subprocess
import sys


def _write_private_file(path: str, content: str) -> None:
    """Write credential config atomically with 0600 owner-only permissions."""
    tmp_path = f"{path}.tmp.{os.getpid()}"
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def get_token() -> str:
    print("Getting new access token via gcloud...")
    result = subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to get token: {result.stderr.strip()}")
    return result.stdout.strip()


def update_netrc(token: str) -> None:
    netrc_path = os.path.expanduser("~/.netrc")
    if not os.path.exists(netrc_path):
        print(f"No .netrc found at {netrc_path}")
        return

    print(f"Updating {netrc_path}...")
    with open(netrc_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Replace the password value following the login oauth2accesstoken line
    # for machine us-python.pkg.dev and machine us-go.pkg.dev.
    pattern = r"(machine\s+us-(?:python|go)\.pkg\.dev\s+login\s+oauth2accesstoken\s+password\s+)(\S+)"
    updated_content = re.sub(pattern, lambda m: f"{m.group(1)}{token}", content)

    _write_private_file(netrc_path, updated_content)
    print("Updated .netrc")


def update_npmrc(token: str) -> None:
    npmrc_path = os.path.expanduser("~/.npmrc")
    if not os.path.exists(npmrc_path):
        print(f"No .npmrc found at {npmrc_path}")
        return

    print(f"Updating {npmrc_path}...")
    with open(npmrc_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Example format: //us-npm.pkg.dev/...:_authToken=ya29...
    pattern = r"(_authToken=)(ya29\.\S+)"
    updated_content = re.sub(pattern, lambda m: f"{m.group(1)}{token}", content)

    _write_private_file(npmrc_path, updated_content)
    print("Updated .npmrc")


def main() -> int:
    try:
        token = get_token()
        update_netrc(token)
        update_npmrc(token)
        print("Token refresh complete!")
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
