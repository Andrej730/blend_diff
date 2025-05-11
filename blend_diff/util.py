import struct
from blender_asset_tracer import blendfile
from collections import defaultdict
from typing import Union, NamedTuple


def pack_address(address: int) -> bytes:
    return struct.pack("<Q", address)


TailType = tuple[Union[bytes, int], ...]


class Inverse(NamedTuple):
    address: int
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
        if is_pointer:
            value = block.get(tail, block_item_index=block_item_index)
            if not value:
                return
            self.inverses[value].add(Inverse(block.addr_old, block.dna_type_id, tail))
        elif fields:
            for array_item_index in range(field.name.array_size):
                tail_ = tail + (array_item_index,)
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
