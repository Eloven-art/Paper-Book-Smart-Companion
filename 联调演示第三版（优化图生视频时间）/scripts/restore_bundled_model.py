"""Restore the byte-identical GGUF from its Git LFS parts."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile


def restore(target: Path) -> None:
    manifest_path = target.with_suffix(".gguf.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if target.is_file() and target.stat().st_size == manifest["bytes"]:
        return

    parts = [target.with_name(part["name"]) for part in manifest["parts"]]
    for path, part in zip(parts, manifest["parts"]):
        if not path.is_file() or path.stat().st_size != part["bytes"]:
            raise ValueError(f"{path.name} is missing or incomplete. Run git lfs pull.")

    print("Preparing the bundled Qwen3 4B model (first start only)...", flush=True)
    digest = hashlib.sha256()
    with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".part", delete=False) as output:
        temporary = Path(output.name)
        try:
            for part in parts:
                with part.open("rb") as source:
                    while data := source.read(8 * 1024**2):
                        digest.update(data)
                        output.write(data)
            output.close()
            if temporary.stat().st_size != manifest["bytes"] or digest.hexdigest() != manifest["sha256"]:
                raise ValueError("The restored model failed the SHA-256 check.")
            temporary.replace(target)
        finally:
            output.close()
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    restore(Path(sys.argv[1]))
