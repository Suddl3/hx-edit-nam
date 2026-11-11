"""Pinned artifact contracts for successful preset-contents completion.

Read-only artifact inspection. These anchors establish native ordering and
payload representation; they do not claim a hardware Clear has succeeded.
"""
from native_export_static import read

ANCHORS={
    'engine_did_string_binding':(0x48b680,'6a016818d1ca00b978b5e800e8af5f1100'),
    'engine_will_string_binding':(0x48b6a0,'6a0168f4d0ca00b958b4e800e88f5f1100'),
    'editor_did_string_binding':(0x48be00,'6a016818d1ca00b95cb7e800e82f581100'),
    'editor_will_string_binding':(0x48be20,'6a0168f4d0ca00b93cb6e800e80f581100'),
    'contents_will_before_mutation':(0x88ba78,'8b4f048d8518fbffff6a0050576858b4e80081c1a4000000c645fc00e8e78cfdff'),
    'engine_mutex_then_inline_sf_replacement':(0x88baf0,'8d4f18e8b80ffaff8d8528fbffff508d4f30e8d95de1ff'),
    'engine_mutex_release_before_did':(0x88bb8f,'8d4f18e8e910faff'),
    'completion_result_is_signed_variant':(0x88bb9d,'c785e0faffff04000000899de8faffff'),
    'completion_result_key':(0x88bbda,'6830f3ca00'),
    'contents_did_after_mutation':(0x88bc4f,'8b4f048d8518fbffff6a0050576878b5e80081c1a4000000c645fc03e8108bfdff'),
    'editor_subscribes_contents_pair':(0x9118e8,'6a00683cb6e800538bcfe83928f5ff6a00685cb7e800538bcfe82a28f5ff'),
    'editor_synthetic_success_is_int_zero':(0x91014c,'c745cc04000000c745d4000000008d45ccc645fc09506830f3ca008d4dbce8'),
    'model_notification_forwarding_unfiltered_by_name':(0x61b2dd,'83f80e755f8d45dc508d730868902fcb008bcee8abbbf8ff8bc8e894bcf8ff8d45dcc745fc03000000508d4decc745ec24c1ca00c745f000000000e80369f8ff8d4ddcc645fc05e8f74df8ff568d45ec8bcf50e8cbd6ffff'),
    'editor_accepts_both_contents_names':(0x91a482,'683cb6e8008bcee87278c8ff84c00f8583020000685cb7e8008bcee85e78c8ff84c00f856f020000'),
    'editor_acceptance_requires_current_engine':(0x91a719,'8b450c3b87240100005f0f94c05e5dc2'),
    'editor_forwards_accepted_event_unchanged':(0x9101c4,'8b5d088b751056538d4ffce87ca2000084c074148b4f6485c9740dff77688b550c8b015253ff5004'),
    'model_controller_event_vtable_entry':(0xcd94a4,'20b16100'),
    'base_controller_registers_widget_listener':(0x764143,'8b4e10c645fc0385c974086a0056e86a690500'),
    'widget_listener_storage':(0x7baac0,'558bec8b51548b45088942648b49548b450c8941685dc2'),
}

def audit():
    for name,(va,expected) in ANCHORS.items():
        assert read(va,len(expected)//2).hex()==expected,name
    assert read(0xcad0f4,35).split(b'\0',1)[0]==b'EditBuffer_PresetContentsWillChange'
    assert read(0xcad118,34).split(b'\0',1)[0]==b'EditBuffer_PresetContentsDidChange'
    assert read(0xcaf330,7)==b'result\0'
    return {'passed':len(ANCHORS)+3,'device_access':False,'runtime_validated':False,
            'handshake':'matched PresetContentsWillChange/DidChange, typed result zero, then UI settle'}

if __name__=='__main__':
    import json
    print(json.dumps(audit(),indent=2))
