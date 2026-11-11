"""Order preview cleanup before helper teardown; retain all ownership guards."""
from audition_diagnostics import replace_once


def patch_preview_cleanup(source):
    source = replace_once(source, "        if payload['type'] == 'toneReply':", '''        if payload['type'] == 'toneCancel':
            if payload.get('session') == self.session and self.browser:
                self.browser.actions.put('finish')
            return
        if payload['type'] == 'toneReply':''')
    source = replace_once(source, '''            if browser:
                browser.close()
                self.browser = None
            if not self.detached.is_set():''', '''            # Keep the helper/parameter view alive until native cleanup replies.
            if not self.detached.is_set():''')
    return replace_once(source, '''            # Device cleanup and close acknowledgement have finished.''', '''            if browser:
                try:
                    browser.close()
                except (OSError, ValueError):
                    notice = notice or ('The browser helper did not close normally. Device cleanup was already requested.', 0x40)
                finally:
                    self.browser = None
            # Device cleanup and close acknowledgement have finished.''')


def patch_browser_escape(source):
    if 'protected override bool ProcessCmdKey' in source:
        raise ValueError('Browser Escape overlay already applied or upstream changed')
    source = replace_once(source, '        timer.Tick += (sender, args) => LayoutPane();', '''        browser.PreviewKeyDown += MarkPreviewEscape;
        details.PreviewKeyDown += MarkPreviewEscape;
        browser.KeyDown += HandlePreviewEscape;
        details.KeyDown += HandlePreviewEscape;
        timer.Tick += (sender, args) => LayoutPane();''')
    return replace_once(source, '    protected override CreateParams CreateParams', '''    bool finishRequested;
    void RequestFinish()
    {
        if (finishRequested || IsDisposed) return;
        finishRequested = true;
        SendAction("finish");
    }

    void MarkPreviewEscape(object sender, PreviewKeyDownEventArgs args)
    {
        if (args.KeyData == Keys.Escape) args.IsInputKey = true;
    }

    void HandlePreviewEscape(object sender, KeyEventArgs args)
    {
        if (args.KeyData != Keys.Escape) return;
        // WebView2 owns keyboard focus; Form.ProcessCmdKey does not see this path.
        args.Handled = true;
        args.SuppressKeyPress = true;
        BeginInvoke(new Action(RequestFinish));
    }

    protected override bool ProcessCmdKey(ref Message message, Keys keys)
    {
        if (keys == Keys.Escape)
        {
            // Request cleanup; do not destroy the helper before its acknowledgement.
            RequestFinish();
            return true;
        }
        return base.ProcessCmdKey(ref message, keys);
    }

    protected override CreateParams CreateParams''')


def patch_cleanup_trace(source):
    source = replace_once(source, '      const event = args[0].add(4).readU32();', '''      const event = args[0].add(4).readU32();
      if (active.workflow !== 'library' && event === 0x100 && args[0].add(8).readU32() === 27) {
        // A main-window Escape must not tear down the selected native view first.
        args[0].add(4).writeU32(0); // WM_NULL; consume only this preview's Escape.
        if (!active.cancelRequested) {
          active.cancelRequested = true;
          trace('close-requested', {reason: 'host-escape'});
          send({type: 'toneCancel', session: active.id});
        }
        return;
      }''')
    source = replace_once(source,
        "    if (!playing(transfer)) return 'preview-target-or-identity-changed';", '''    if (!transfer) return 'no-preview-transfer';
    if (!active || !active.valid) return 'preview-target-invalidated';
    if (!atPosition()) return 'preview-position-changed';
    if (!atTarget()) return 'preview-block-changed';
    if (active.undoAfter === undefined) return 'preview-not-confirmed';
    if (namView.modelSlot() !== transfer.slot) return 'preview-model-unavailable-or-changed';
    if (!namView.previewName(transfer)) return 'preview-slot-identity-changed';''')
    source = replace_once(source,
        '        active.valid = false;\n        active.panel = NULL;',
        "        trace('target-invalidated', {reason: 'controller-teardown'});\n        active.valid = false;\n        active.panel = NULL;")
    source = replace_once(source,
        '''        active.valid = false;
      }
    },
    onLeave() {
      if (this.ours''', '''        trace('target-invalidated', {reason: event});
        active.valid = false;
      }
    },
    onLeave() {
      if (this.ours''')
    source = replace_once(source,
        '''        active.valid = false;
      }
    },
  });

  function reply''', '''        trace('target-invalidated', {reason: 'edit-buffer-target-mismatch'});
        active.valid = false;
      }
    },
  });

  function reply''')
    return source
