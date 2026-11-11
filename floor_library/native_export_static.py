"""Pinned static export anchors; does not load or attach to the executable."""
import hashlib
import json
from pathlib import Path
import struct


EDITOR_PATH = None

def editor_bytes():
    if EDITOR_PATH is None:
        raise ValueError('Configure the installed HX Edit executable before native audits')
    return Path(EDITOR_PATH).read_bytes()

def read(va,size):
    raw=editor_bytes()
    if hashlib.sha256(raw).hexdigest()!='f7faac4996164f9039c9ae3ca8d85cd135c5bceae8a511c8921a1244046cab66':
        raise ValueError('Editor identity changed')
    pe=struct.unpack_from('<I',raw,0x3c)[0]
    count=struct.unpack_from('<H',raw,pe+6)[0]
    optional=struct.unpack_from('<H',raw,pe+20)[0]
    base=struct.unpack_from('<I',raw,pe+24+28)[0]
    rva=va-base
    for n in range(count):
        sec=pe+24+optional+n*40
        vsize,vaddr,rsize,offset=struct.unpack_from('<IIII',raw,sec+8)
        if vaddr<=rva and rva+size<=vaddr+rsize:
            return raw[offset+rva-vaddr:offset+rva-vaddr+size]
    raise ValueError('Read outside mapped file bytes')


def automatic_contract():
    """Pinned stock single-export caller, vector types, string ownership and queue."""
    anchors={
        0x60e831:'8b8f70020000', # manager from IR controller +270
        0x60e83a:'6a00', # native mode false (ordinary WAV)
        0x60e844:'e8f7761e00', # stock single-slot submit
        0x60e79c:'e89f2ef9ff', # construct path string from UTF-8, copy=true
        0x60e82c:'e8ef18f9ff', # string destructor
        0x60e749:'c745a4ac42cb00', # string vector vtable
        0x60e3b6:'c745d4dcb0cb00', # uint vector vtable
        0x7f62cb:'e850b9daff', # copy string into job-owned variant
        0x7f63ca:'e84163dbff', # enqueue job (does not execute worker inline)
        0x7f63ff:'c20c00', # thiscall: two vectors + bool
    }
    for address,hex_bytes in anchors.items():
        if read(address,len(bytes.fromhex(hex_bytes)))!=bytes.fromhex(hex_bytes):
            raise ValueError(f'Automatic export anchor differs: {address:#x}')
    return {'submit':'0x7f5f40','stringConstructor':'0x5a1640','stringDestructor':'0x5a0120',
            'managerOffset':0x270,'requestCopiedBeforeReturn':True,'hardwareTested':False}


def notification_contract():
    """Resolve notification names through pinned static constructors/dispatch."""
    # After the three storage branches and TaskProgress comparison, a no-match
    # jumps directly to the epilogue. ParamChanged is not an IR mutation here.
    for va,code in ((0x60c828,'687cfae6008bcbe8cc54f9ff84c00f84dc020000'),
                    (0x60cb18,'8b4df464890d00000000595f5e5b8be55dc20800')):
        if read(va,len(bytes.fromhex(code)))!=bytes.fromhex(code):
            raise ValueError('Notification no-match return differs')
    result={}
    for ctor,global_va,branch in ((0x4362a0,0xe6fa5c,0x60c42c),
                                 (0x4362c0,0xe6fe5c,0x60c643),
                                 (0x4362e0,0xe6f99c,0x60c6bc),
                                 (0x435b60,0xe6fa7c,0x60c828)):
        code=read(ctor,17)
        if code[:3]!=b'\x6a\x01\x68' or code[7]!=0xb9 or code[12]!=0xe8:
            raise ValueError('Notification constructor differs')
        text_va,stored_global=struct.unpack_from('<I',code,3)[0],struct.unpack_from('<I',code,8)[0]
        call_target=ctor+17+struct.unpack_from('<i',code,13)[0]
        if stored_global!=global_va or call_target!=0x5a1640:
            raise ValueError('Notification initialization differs')
        if read(branch,5)!=b'\x68'+struct.pack('<I',global_va):
            raise ValueError('Notification dispatch differs')
        name=read(text_va,96).split(b'\0')[0].decode('ascii')
        result[name]={'constructor':hex(ctor),'global':hex(global_va),'branch':hex(branch)}
    return result


def setting_contract():
    """Static anchors for the local export lock and read-only map decoder.

    These establish the native layout, not the identity of any runtime event.
    The observer still validates each event before allowing the export fence.
    """
    expected={0xcb5368:'setting',0xdb9c98:'value',0xcb2f98:'onDevice',
              0xcb2fcc:'isUndo',0xcb5370:'resetAll',0xd1d52c:'UILockout',
              0xcad19c:'Device_SettingChanged'}
    for va,text in expected.items():
        if read(va,len(text)+1)!=text.encode()+b'\0':raise ValueError('Setting literal differs')
    # Export worker's bool variants: true and false; same UILockout literal.
    for va,code in ((0x7e2c4d,'c745c00b000000'),(0x7e2c54,'c645c801'),
                    (0x7e2d75,'c745c00b000000'),(0x7e2d7c,'c645c800'),
                    (0x7e2c7a,'682cd5d100'),(0x7e2da2,'682cd5d100'),
                    # Setting producer copies name and value into the dictionary.
                    (0x917243,'686853cb00'),(0x917282,'68989cdb00'),
                    (0x91729e,'68c4b6e800'),
                    # Tree layout used by the native map lookup.
                    (0x5a43cb,'8b7704'),(0x5a43d2,'8d4608'),
                    (0x5a43f0,'8b4628'),(0x5a43f5,'8b462c'),
                    (0x5a4403,'8d4618'),
                    # Independent allocator/insertion evidence: 64-byte nodes,
                    # key/value copies and both child links, not just lookup.
                    (0x5a2ea4,'6a40'),(0x5a2ec1,'8d4e08'),
                    (0x5a2ed2,'8d4e18'),(0x5a2ede,'c7462800000000'),
                    (0x5a2ee7,'c7462c00000000'),
                    (0x5a3e31,'8b7f28'),(0x5a3e36,'8b7f2c'),
                    (0x5a3e73,'897304')):
        if read(va,len(bytes.fromhex(code)))!=bytes.fromhex(code):
            raise ValueError(f'Setting/map anchor differs: {va:#x}')
    ctor=read(0x48ba60,17)
    if ctor[:3]!=b'\x6a\x01\x68' or struct.unpack_from('<I',ctor,3)[0]!=0xcad19c or struct.unpack_from('<I',ctor,8)[0]!=0xe8b6c4:
        raise ValueError('Setting event constructor differs')
    return {'fields':{hex(k):v for k,v in expected.items()},
        'workerLockValues':[True,False], 'producer':'0x917109..0x9172ae',
        'dictionary':{'variantRep':8,'repRoot':4,'nodeBytes':64,'nodeKey':8,'nodeValue':24,'children':[40,44]},
        'runtimePayloadConfirmed':False}


if __name__=='__main__':
    print(json.dumps({'controllerVtable':[hex(x) for x in struct.unpack('<11I',read(0xccff0c,44))],
        'callbackVtable':[hex(x) for x in struct.unpack('<3I',read(0xccff44,12))],
        'secondaryVtable':[hex(x) for x in struct.unpack('<3I',read(0xccff38,12))],
        'exportStrings':{hex(a):read(a,96).split(b'\0')[0].decode('utf8','replace') for a in
            (0xcd013c,0xcd0128,0xcd0150,0xcd0170,0xcd0174,0xcd01a8,0xcd01cc,
             0xcaf330,0xcd0208,0xcd0218,0xd1ba14,0xcb2fb4,0xcd0080,0xd1d52c)}},indent=2))
