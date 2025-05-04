import difflib
import argparse
from functools import cache
from pathlib import Path
from blender_asset_tracer import blendfile


def format_diff(lines: list[str]) -> None:
    RED = "\033[91m"
    GREEN = "\033[92m"
    CYAN = "\033[96m"
    RESET = "\033[0m"
    for line in lines:
        if line.startswith("+") and not line.startswith("+++"):
            print(f"{GREEN}{line}{RESET}")
        elif line.startswith("-") and not line.startswith("---"):
            print(f"{RED}{line}{RESET}")
        elif line.startswith("@@"):
            print(f"{CYAN}{line}{RESET}")
        else:
            print(line)


def get_id_data(path: Path) -> list[str]:
    bf_base = blendfile.BlendFile(path)

    @cache
    def is_id_block(bf: blendfile.BlendFile, sdna_index: int) -> bool:
        field = bf.structs[sdna_index]._fields_by_name.get(b"id", None)
        if field is None:
            return False
        return field.dna_type.dna_type_id == b"ID"

    id_file_blocks = [b for b in bf_base.blocks if is_id_block(bf_base, b.sdna_index)]

    # Keep the original order as blocks order is probably stable
    # and if it changes, it has a meaning too.
    id_data: list[tuple[bytes, bytes]] = []
    for block in id_file_blocks:
        name = block.get((b"id", b"name"))
        id_data.append((block.code, name))

    id_data_strings = [str(d) for d in id_data]
    return id_data_strings


def diff_blend(fromfile: Path, tofile: Path) -> None:
    diff = list(
        difflib.unified_diff(
            get_id_data(fromfile),
            get_id_data(tofile),
            fromfile=fromfile.name,
            tofile=tofile.name,
            lineterm="",
        )
    )
    format_diff(diff)


def main() -> None:
    parser = argparse.ArgumentParser(description="Diff two .blend files.")
    parser.add_argument("fromfile", type=Path, help="Path to base .blend file")
    parser.add_argument("tofile", type=Path, help="Path to changed .blend file")
    args = parser.parse_args()

    diff_blend(args.fromfile, args.tofile)


if __name__ == "__main__":
    main()
