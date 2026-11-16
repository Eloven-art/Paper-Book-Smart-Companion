"""Retry resumable downloads and publish only verified files."""
import re
import time

import requests

from server.run_logger import sha256_file as sha256


def valid(path, size=None, digest=None):
    return path.is_file() and (size is None or path.stat().st_size == size) and (
        digest is None or sha256(path) == digest)


def download(url, dest, size=None, digest=None):
    if valid(dest,size,digest):
        return
    pending = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(3):
        try:
            offset = pending.stat().st_size if pending.exists() else 0
            headers = {"Accept-Encoding":"identity"}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            with requests.get(url,headers=headers,stream=True,timeout=(15,90)) as response:
                if offset and response.status_code == 416:
                    if (size or digest) and valid(pending,size,digest):
                        pending.replace(dest)
                        return
                    pending.unlink(missing_ok=True)
                    raise ValueError("Invalid partial file")
                response.raise_for_status()
                expected = size
                if response.status_code == 206:
                    span = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)",response.headers.get("Content-Range",""))
                    if not span or int(span[1]) != offset:
                        raise ValueError("Unexpected Content-Range")
                    expected = expected or int(span[3])
                else:
                    offset = 0
                    expected = expected or int(response.headers.get("Content-Length",0)) or None
                with pending.open("ab" if offset else "wb") as output:
                    for chunk in response.iter_content(1024*1024):
                        output.write(chunk)
            if not valid(pending,expected,digest):
                if expected is None or pending.stat().st_size >= expected:
                    pending.unlink(missing_ok=True)
                raise ValueError(f"File verification failed for {dest.name}")
            pending.replace(dest)
            return
        except (OSError,ValueError,requests.RequestException) as exc:
            if attempt == 2:
                raise
            print(f"{dest.name}: retry {attempt+1}/2 ({type(exc).__name__})",flush=True)
            time.sleep(2**attempt)
