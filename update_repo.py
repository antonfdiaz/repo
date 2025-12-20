from __future__ import annotations
import argparse
import bz2
import hashlib
import io
import subprocess
import tarfile
from pathlib import Path

def extract_control_fields(deb_path: Path) -> list[tuple[str, str]]:
    listing = subprocess.check_output(["ar", "t", str(deb_path)], text=True).splitlines()
    control_member = next((name for name in listing if name.startswith("control.tar.")), None)
    if not control_member:
        raise RuntimeError(f"No control.tar.* found in {deb_path.name}")

    control_blob = subprocess.check_output(["ar", "p", str(deb_path), control_member])
    mode = {
        "control.tar.gz": "r:gz",
        "control.tar.xz": "r:xz",
        "control.tar.bz2": "r:bz2",
    }.get(control_member, "r:*")

    with tarfile.open(fileobj=io.BytesIO(control_blob), mode=mode) as tar:
        member = next((m for m in tar.getmembers() if m.isfile() and m.name.split("/")[-1] == "control"), None)
        if member is None:
            raise RuntimeError(f"control file not found inside {deb_path.name}")
        control_text = tar.extractfile(member).read().decode("utf-8", errors="replace")

    entries: list[tuple[str, str]] = []
    last: list[str] | None = None
    for raw_line in control_text.splitlines():
        if not raw_line:
            continue
        if raw_line[0].isspace() and last is not None:
            last[1] += "\n" + raw_line
            continue
        if ":" not in raw_line:
            continue
        key, val = raw_line.split(":", 1)
        last = [key.strip(), val.strip()]
        entries.append(tuple(last))
    return entries

def format_entry(entries: list[tuple[str, str]], filename: str, size: int, md5hex: str) -> str:
    filtered = [(k, v) for k, v in entries if k not in {"Filename", "Size", "MD5sum"}]
    filtered.append(("Filename", filename))
    filtered.append(("Size", str(size)))
    filtered.append(("MD5sum", md5hex))

    lines: list[str] = []
    for key, val in filtered:
        if "\n" in val:
            first, *rest = val.split("\n")
            lines.append(f"{key}: {first}")
            lines.extend(rest)
        else:
            lines.append(f"{key}: {val}")
    return "\n".join(lines) + "\n\n"


def build_packages(deb_dir: Path) -> str:
    entries: list[str] = []
    for deb in sorted(p for p in deb_dir.iterdir() if p.suffix == ".deb"):
        control = extract_control_fields(deb)
        data = deb.read_bytes()
        md5hex = hashlib.md5(data).hexdigest()
        size = len(data)
        rel = f"debs/{deb.name}"
        entries.append(format_entry(control, rel, size, md5hex))
    return "".join(entries)

def update_release(release_path: Path, packages_content: str, packages_bz2: Path) -> None:
    release_text = release_path.read_text(encoding="utf-8")
    new_md5_packages = hashlib.md5(packages_content.encode("utf-8")).hexdigest()
    new_md5_bz2 = hashlib.md5(packages_bz2.read_bytes()).hexdigest()
    size_packages = len(packages_content.encode("utf-8"))
    size_bz2 = packages_bz2.stat().st_size

    lines = release_text.splitlines()
    header_lines: list[str] = []
    for line in lines:
        header_lines.append(line)
        if line.strip() == "MD5Sum:":
            break
    else:
        header_lines.append("MD5Sum:")

    prefix = "\n".join(header_lines[: header_lines.index("MD5Sum:")]) + "\nMD5Sum:\n"
    md5_block = (
        f" {new_md5_packages} {size_packages} Packages\n"
        f" {new_md5_bz2} {size_bz2} Packages.bz2\n"
    )
    release_path.write_text(prefix + md5_block, encoding="utf-8")

def main() -> None:
    parser = argparse.ArgumentParser(description="Regenerate Packages and refresh Release hashes")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Repo root (default: cwd)")
    args = parser.parse_args()

    root = args.root.resolve()
    deb_dir = root / "debs"
    if not deb_dir.is_dir():
        raise SystemExit(f"Missing debs directory at {deb_dir}")

    packages_content = build_packages(deb_dir)
    packages_path = root / "Packages"
    packages_bz2_path = root / "Packages.bz2"

    packages_path.write_text(packages_content, encoding="utf-8")
    packages_bz2_path.write_bytes(bz2.compress(packages_content.encode("utf-8")))

    release_path = root / "Release"
    if not release_path.is_file():
        raise SystemExit(f"Release file not found at {release_path}")
    update_release(release_path, packages_content, packages_bz2_path)

    print("Updated Packages, Packages.bz2, and Release MD5Sum entries.")
    print(f"Packages entries: {packages_content.count('Package: ')}")
    print(f"Packages size: {len(packages_content.encode('utf-8'))}")
    print(f"Packages.bz2 size: {packages_bz2_path.stat().st_size}")

if __name__ == "__main__":
    main()
