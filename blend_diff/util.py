import struct
from functools import cache
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
        self.homeless_addresses: list[int] = []

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
            # Skip `raw_data` structs by 0 index. 
            # Use index as `raw_data` struct was introduced relatively recently,
            # but 0 index was used for it for awhile.
            # In theory we can overlook some DrawDataLists in older Blender versions,
            # but it's better than just being completely unsafe.
            # https://projects.blender.org/blender/blender/issues/99875
            if block.sdna_index == 0:
                continue
            for i in range(block.count):
                for field in block.dna_type.fields:
                    self.check_block_field(block, i, field)

        # Store bytes for inverses check.
        tell = bf.fileobj.tell()
        bf.fileobj.seek(0)
        bf_bytes = bf.fileobj.read()
        bf.fileobj.seek(tell)

        # Print orphaned data.
        print("Orphaned data:")
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
        if not orphaned:
            print("... no orphaned data.")
        print(f"Orphaned file-blocks: {len(orphaned)}.")

        # Pointers without file-blocks.
        homeless_addresses = [p for p in inverses if p not in bf.block_from_addr]
        self.homeless_addresses = homeless_addresses
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


class BlendPatch:
    @staticmethod
    def nullify_homeless_addresses(bf_inverses: BlendFileInverses) -> None:
        """Nullify all addresses that don't point to some file-block.

        In theory since they do not point anywhere,
        we might be able to remove them to reduce the diff
        and get away with it.

        It can be dangerous if Blender is checking if pointers are not null
        or comparing them between each other to figure the identities.

        But no idea if Blender actually does that
        and no idea how safe this operation actually is.

        Though I've tested patching simple files (400 references)
        and they reopen without issues, so they don't break
        (at least not right away).

        Even if it's unsafe, it's still can be useful to nullify files before diff.
        """
        inverses = bf_inverses.inverses
        bf = bf_inverses.bf

        i = 0
        for addr in bf_inverses.homeless_addresses:
            inverses_ = inverses[addr]
            for inverse_ in inverses_:
                block = bf.block_from_addr[inverse_.address]
                block.set(inverse_.path, block_item_index=inverse_.block_item_index, value=0)
                i += 1
        print(f"{i} homeless address references nullified.")

    @staticmethod
    def nullify_session_uids(bf: blendfile.BlendFile) -> None:
        """Nullify 'session_uid' field for all ID file-blocks.

        'session_uid' is reset every time you open blend-file,
        creating a diff noise between files.

        Warning. Use for diff-checks only.
        Nullifying all session uids, or setting them all
        to some other number, e.g. '1', is crashing Blender.
        """
        is_id_block_ = cache(is_id_block)
        i = 0
        for b in bf.blocks:
            if not is_id_block_(bf, b.sdna_index):
                continue
            i += 1
            b.set((b"id", b"session_uid"), 0)
        print(f"{i} ID file-blocks have session_uid nullified.")


def is_id_block(bf: blendfile.BlendFile, sdna_index: int) -> bool:
    field = bf.structs[sdna_index]._fields_by_name.get(b"id", None)
    if field is None:
        return False
    return field.dna_type.dna_type_id == b"ID"
