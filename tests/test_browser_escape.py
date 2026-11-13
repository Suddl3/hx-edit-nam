"""Focused-document Escape routes to cleanup without loading a remote page."""
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from tone_browser import ROOT


def test_actual_webview_escape_script_only_handles_plain_initial_escape(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Local Node runtime unavailable; no download')
    source = (ROOT / 'tone_browser.cs').read_text(encoding='utf-8')
    match = re.search(r'internal const string EscapeScript = @"(.*?)";', source, re.S)
    assert match, 'Production Escape script was not found'
    script = match.group(1).replace('""', '"')
    harness = r'''
const vm = require('node:vm');
const assert = require('node:assert/strict');
let handler;
const messages = [];
vm.runInNewContext(SCRIPT, {
  document: {addEventListener(name, callback, capture) {
    assert.equal(name, 'keydown'); assert.equal(capture, true); handler = callback;
  }},
  window: {chrome: {webview: {postMessage(message) { messages.push(message); }}}}
}, {timeout: 1000});
function key(values) {
  let prevented = 0, stopped = 0;
  handler({key:'Escape', repeat:false, altKey:false, ctrlKey:false, metaKey:false,
    preventDefault() { prevented++; }, stopImmediatePropagation() { stopped++; }, ...values});
  return [prevented, stopped];
}
for (const event of [{key:'Enter'}, {key:'a'}, {repeat:true}, {altKey:true}, {ctrlKey:true}, {metaKey:true}]) {
  assert.deepEqual(key(event), [0, 0]);
}
assert.equal(messages.length, 0);
assert.deepEqual(key({}), [1, 1]);
assert.deepEqual(messages, ['hx-unbound-preview-escape']);
console.log('Actual WebView Escape script passed without a browser or network');
'''.replace('SCRIPT', json.dumps(script))
    path = tmp_path / 'escape.cjs'
    path.write_text(harness, encoding='utf-8')
    result = subprocess.run([node, str(path)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Actual WebView Escape script passed' in result.stdout
