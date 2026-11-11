"""Pinned native SF-to-HLX empty-node omission contract; file reads only."""
import struct
from native_export_static import read


def audit_empty_node_omission():
    # Node emitter skips empty property vectors before creating an output key.
    count_branch=read(0x86a408,18)
    if count_branch.hex()!='8b5d088b43182b43148945f00f8442020000':
        raise ValueError('SF empty-node omission changed')
    if 0x86a41a+struct.unpack_from('<i',count_branch,14)[0]!=0x86a65c:
        raise ValueError('SF empty-node target changed')
    # Empty names are separately omitted, but our native decoder rejects them.
    name_branch=read(0x86a41a,12)
    if name_branch.hex()!='8b7d0c803f000f8436020000' or \
            0x86a426+struct.unpack_from('<i',name_branch,8)[0]!=0x86a65c:
        raise ValueError('SF empty-name omission changed')
    if read(0x86a65c,20).hex()!='8b4df464890d00000000595f5e5b8be55dc20c00':
        raise ValueError('SF omission epilogue changed')
    if read(0x86a460,23).hex()!='8b4d108945bc8d45b45057e83079d3ff8bc8e899c8d3ff':
        raise ValueError('SF output node construction changed')
    return {'passed':4,'nodeEmitter':0x86a3e0,'emptyPropertiesSkipped':True,
            'hardwareObservedUnknownNode':False,
            'limit':'Static omission rule, not proof of the contents of any live preset'}
