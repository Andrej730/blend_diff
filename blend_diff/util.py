import struct
from blender_asset_tracer import blendfile
from collections import defaultdict
from typing import Union, NamedTuple


def pack_address(address: int) -> bytes:
    return struct.pack("<Q", address)


TailType = tuple[Union[bytes, int], ...]


class Inverse(NamedTuple):
    address: int
    block_item_index: int
    dna_type_id: bytes
    path: TailType


class BlendFileInverses:
    inverses: defaultdict[int, set[Inverse]]
    """Mapping of addresses found in blend file to it's inverses.
    
    Mapped addresses are not just data-blocks, all pointer fields in the file
    are checked.
    """

    def __init__(self, bf: blendfile.BlendFile):
        self.bf = bf
        self.inverses = defaultdict(set)

    def check_block_field(
        self,
        block: blendfile.BlendFileBlock,
        block_item_index: int,
        field: blendfile.dna.Field,
        tail: TailType = (),
    ) -> None:
        fields = field.dna_type.fields
        is_pointer = field.name.is_pointer
        if not fields and not is_pointer:
            return

        field_name = field.name.name_only
        tail = tail + (field_name,)
        single_item = field.name.array_size == 1
        for array_item_index in range(field.name.array_size):
            # Just to keep tails more readable.
            if single_item:
                tail_ = tail
            else:
                tail_ = tail + (array_item_index,)

            if is_pointer:
                # Ensure to check is_pointer first, as it also has fields.
                value = block.get(tail_, block_item_index=block_item_index)
                if not value:
                    continue
                self.inverses[value].add(Inverse(block.addr_old, block_item_index, block.dna_type_id, tail_))
            elif fields:
                for field_ in fields:
                    self.check_block_field(block, block_item_index, field_, tail_)
            else:
                assert False

    def check_inverses(self):
        """Build inverses mapping and print stats on orphaned data."""
        # Locals for quick access.
        inverses = self.inverses
        bf = self.bf

        blocks = bf.blocks
        inverses.clear()

        for block in blocks:
            for i in range(block.count):
                for field in block.dna_type.fields:
                    self.check_block_field(block, i, field)

        # Store bytes for inverses check.
        tell = bf.fileobj.tell()
        bf.fileobj.seek(0)
        bf_bytes = bf.fileobj.read()
        bf.fileobj.seek(tell)

        # Print orphaned data.
        for block in bf.blocks:
            inverses_ = inverses[block.addr_old]
            if len(inverses_) > 0:
                continue

            # Double check we don't miss anything.
            # Can show 0 occurrences with 0 inverses,
            # when same pointer is used for two file-blocks
            # (have seen it in startup.blend).
            bytes_occurrences = bf_bytes.count(pack_address(block.addr_old))

            print(
                block.dna_type_id,
                block.code,
                hex(block.addr_old),
                pack_address(block.addr_old).hex(),
                len(inverses_),
                inverses_,
                bytes_occurrences,
                len(bf.find_blocks_from_code(block.code)),
            )

        orphaned = [b for b in bf.blocks if len(inverses[b.addr_old]) == 0]
        print(f"Orphaned file-blocks: {len(orphaned)}.")

        # Pointers without file-blocks.
        homeless_addresses = [p for p in inverses if p not in bf.block_from_addr]
        homeless_references = sum(len(inverses[p]) for p in homeless_addresses)
        print(f"Homeless addresses: {len(homeless_addresses)} ({homeless_references} references).")

        # Odd file-block addresses.
        print("File-block addresses validation...")
        used_addresses: dict[int, blendfile.BlendFileBlock] = {}
        for block in bf.blocks:
            address = block.addr_old
            if address <= 0:
                print(f"{block} has an odd address.")
                continue

            # Oddly enough, `REND` and `GLOB` file-blocks share the same address in startup.blend.
            if address in used_addresses:
                print(f"{block} address is already used in block: {used_addresses[address]}.")
                continue

            used_addresses[address] = block
