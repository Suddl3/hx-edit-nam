"""Pinned Clear submission boundary; reads file bytes only, never opens a device."""
from native_export_static import read

def clear_contract():
    anchors = {
        # Stock confirmation builds the selected occupied-slot vector then calls
        # the queue method. No per-slot queue submission in this selection loop.
        0x612b90: 'c745d0dcb0cb00',
        0x612c00: '8b0083b80c01000000',
        0x612c0b: '8b8000010000',
        0x612ca2: '8b8f6c0200008d45d050e8ff751c00',
        # thiscall manager is ECX, sole stack argument is uint vector.
        0x7da2d8: '894de88b75088b46142b4610',
        0x7da480: '8b4608',
        0x7da496: '8b0488',
        # Queues worker 7dc6d0 only after constructing its copied request.
        0x7da5da: '8b45e88d4dd8506a175168d0c67d00',
        0x7da5f0: 'e81b21ddff',
        0x7da625: 'c20400',
    }
    for va, value in anchors.items():
        if read(va,len(bytes.fromhex(value))) != bytes.fromhex(value):
            raise ValueError(f'Clear boundary anchor differs: {va:#x}')
    return {'entryRva':0x3da2b0,'abi':'thiscall','returns':'void',
            'arguments':['uint-vector-pointer'],
            'vector':{'vtable':0x8bb0dc,'data':8,'begin':16,'end':20},
            'stockCaller':0x612cac,'worker':0x7dc6d0,
            'runtimeTested':False}

if __name__=='__main__':
    import json
    print(json.dumps(clear_contract(),indent=2))
