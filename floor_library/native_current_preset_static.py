"""Read-only, bounded byte contracts for the local SF edit-buffer reader.

No editor process is opened or attached. read() rejects every binary except the
pinned HX Edit 3.82 artifact. These anchors establish layout, not runtime safety.
"""
import struct
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from native_export_static import read, editor_bytes

def imports():
    raw=editor_bytes()
    pe=struct.unpack_from('<I',raw,60)[0]
    base=struct.unpack_from('<I',raw,pe+52)[0]
    section=pe+24+struct.unpack_from('<H',raw,pe+20)[0]
    sections=[struct.unpack_from('<IIII',raw,section+i*40+8) for i in range(struct.unpack_from('<H',raw,pe+6)[0])]
    def offset(rva):
        return next(o+rva-v for vs,v,z,o in sections if v<=rva<v+max(vs,z))
    def string(rva):
        return raw[offset(rva):].split(b'\0',1)[0].decode('ascii')
    pos=offset(struct.unpack_from('<I',raw,pe+24+104)[0]);result={}
    while any(struct.unpack_from('<IIIII',raw,pos)):
        original,_,_,name,first=struct.unpack_from('<IIIII',raw,pos);i=0
        while True:
            item=struct.unpack_from('<I',raw,offset(original or first)+i*4)[0]
            if not item:break
            if item<0x80000000:result[base+first+i*4]=(string(name),string(item+2))
            i+=1
        pos+=20
    return result

ANCHORS = {
    'engine_lock_and_inline_root': (0x8be040, '568bf18d4e18e865eaf6ff8d46305ec3'),
    'editor_lock': (0x904d69, '8d9f34010000c745fcffffffff8bcbe8337df2ff'),
    'editor_engine': (0x904dbb, '8b8f24010000'),
    'global_and_two_dsp_trees': (0x86a224, '8d7144837904060f45f0eb0b8d715483f80174038d714c85f60f847301'),
    'tree_root_and_left_child': (0x86a243, '8b760485f60f84680100008b464485c074'),
    'node_property_array': (0x86a33b, '8d46185350c645fc00e897000000'),
    'tree_right_child': (0x86a36f, '8b4e488bc6c745d80000'),
    'property_array_bounds_and_pair': (0x867319, '8b561832c98b5e148bc22bc3c745fcffffffff39450873528b760c85f674618b450803c33bc2736d8b34868b4d0c56e8c3f9'),
    'global_array_bounds': (0x86abc9, '8b41302b412c8945dc'),
    'global_array_buffer': (0x86acab, '8b702485f6'),
    'variant8_is_float32': (0x5a5213, '83fb08753a391e75598bcee80dd3ffffd95d08f30f1045080f2f470877448b'),
    'mutex_uses_handle_not_memory_flag': (0x82cab0, '8b41048b0085c074096aff50ff150883c900c3'),
    'mutex_release': (0x82cc80, '8b41048b0085c0740750ff153883c900c3'),
    'engine_release_same_mutex': (0x8f4260, '83c118e9188af3ff'),
    'editor_release_same_mutex': (0x904ef1, '8bcbe8887df2ff'),
    'mutex_constructor': (0x82bd30, '568bf16a04c7064cdcd200e8f6911e0083c4048946046a006a006a00ff153c83c9008b4e0489018bc65ec3'),
    'editor_mutex_constructor': (0x8fc70f, '8d8834010000c6803001000000e80ff6f2ff'),
    'engine_mutex_constructor': (0x878a1f, '8d4f18c645fc01c6471400e80133fbff'),
    'editor_vtable_identity': (0xd63ed8, 'f0aa9100'),
}

def audit():
    for name, (va, expected) in ANCHORS.items():
        assert read(va, len(expected)//2).hex() == expected, name
    imported=imports()
    assert imported[0xc98308] == ('KERNEL32.dll','WaitForSingleObject')
    assert imported[0xc98338] == ('KERNEL32.dll','ReleaseMutex')
    assert imported[0xc9833c] == ('KERNEL32.dll','CreateMutexW')
    return {'passed': len(ANCHORS), 'device_access': False,
            'runtime_validated': False, 'requires': 'UI lifecycle/readiness plus synchronization'}

if __name__ == '__main__':
    import json
    print(json.dumps(audit(), indent=2))
