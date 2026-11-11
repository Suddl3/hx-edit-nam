"""Visual tab ordering only; native selection indexes stay unchanged."""
from pathlib import Path
from xml.etree import ElementTree as ET
from audition_diagnostics import replace_once
from native_export_static import read

HERE=Path(__file__).resolve().parent

def audit_tab_contract():
    # Pinned x86 getter indexes the independent radio-button vector.
    anchors={0x789f33:'8b417c8b4d083b4808',
             0x789f45:'8b0488',
             0x7bc943:'8b4154ba640000003bf20f4ed6',
             0x7bc963:'ff75108990880000008b415489b098000000',
             0x7bc975:'e88694ffff',
             0x7b5e98:'8b978800000085d2',
             0x7b5ead:'8b8798000000',
             0x789be9:'c7065853d100'}
    for va,expected in anchors.items():
        if read(va,len(expected)//2).hex()!=expected:
            raise ValueError('Native tab layout contract changed at '+hex(va))
    return len(anchors)

def patch_tab_view(source):
    if 'createLibraryTabOrder' in source:
        raise ValueError('Tab ordering already patched')
    audit_tab_contract()
    source=replace_once(source,'function installNamView(profiles, library = [])',
        (HERE/'library_tab_order.js').read_text()+'\nfunction installNamView(profiles, library = [])')
    source=replace_once(source,'  function resizeLibrary(width)',
        '  const libraryTabOrder=createLibraryTabOrder(createLibraryTabNativeApi(method,address,()=>libraryRoot,libraryReady));\n  function resizeLibrary(width)')
    return replace_once(source,'    sidebar.refresh();','    sidebar.refresh();\n    libraryTabOrder.refresh();')

def patch_tab_corners(resources):
    path=Path(resources)/'librarian.xml'
    tree=ET.parse(path)
    tabs=tree.getroot().find("panel_widget[@id='livw']/radio_widget[@id='lbtb']")
    buttons=tabs.findall('radio_button_widget')
    if [b.get('id') for b in buttons]!=['1','2','3','4'] or [b.findtext('text/label') for b in buttons]!=['PRESETS','IRs','FAVORITES','NAM']:
        raise ValueError('Unexpected librarian tab identities')
    buttons[2].find('corner_radius').set('tr','yes')
    buttons[3].find('corner_radius').set('tr','no')
    tree.write(path,encoding='utf-8')
