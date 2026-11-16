#import <Cocoa/Cocoa.h>
#import <WebKit/WebKit.h>

typedef void (*ToneMessage)(const char *);

void hx_nam_dispatch(void (*run)(void)) {
    dispatch_async(dispatch_get_main_queue(), ^{ run(); });
}

NSInteger hx_tone_window(void) {
    NSWindow *window = NSApp.keyWindow ?: NSApp.mainWindow;
    if (window.canBecomeMainWindow) return window.windowNumber;
    for (NSWindow *candidate in NSApp.orderedWindows) {
        if (candidate.isVisible && candidate.canBecomeMainWindow) return candidate.windowNumber;
    }
    return 0;
}

static BOOL IsToneURL(NSURL *url) {
    return [url.scheme isEqual:@"https"] && !url.user && !url.password &&
        (!url.port || url.port.integerValue == 443) &&
        ([url.host isEqual:@"www.tone3000.com"] || [url.host isEqual:@"tone3000.com"]);
}

@interface HXTonePane : NSObject <WKNavigationDelegate, WKScriptMessageHandler>
@property (weak) NSView *host;
@property NSView *view;
@property WKWebView *catalog;
@property WKWebView *card;
@property NSURL *callback;
@property NSURL *catalogPage;
@property NSMutableDictionary *status;
@property NSDictionary *bounds;
@property NSNumber *session;
@property BOOL hasTone;
@property BOOL retry;
@property (assign) ToneMessage message;
@end

@implementation HXTonePane

- (void)emit:(NSString *)kind value:(NSString *)value {
    NSDictionary *event = @{@"type": kind, @"session": self.session, @"value": value ?: @""};
    NSData *data = [NSJSONSerialization dataWithJSONObject:event options:0 error:nil];
    NSString *json = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
    if (json) self.message(json.UTF8String);
}

- (BOOL)open:(NSWindow *)window {
    self.host = window.contentView;
    if (!self.host) return NO;
    self.status = [NSMutableDictionary dictionary];
    self.view = [[NSView alloc] initWithFrame:NSZeroRect];
    self.view.hidden = YES;
    [self.host addSubview:self.view positioned:NSWindowAbove relativeTo:nil];

    WKWebViewConfiguration *catalog = [[WKWebViewConfiguration alloc] init];
    self.catalog = [[WKWebView alloc] initWithFrame:NSZeroRect configuration:catalog];
    self.catalog.navigationDelegate = self;
    [self.view addSubview:self.catalog];

    WKWebViewConfiguration *card = [[WKWebViewConfiguration alloc] init];
    card.websiteDataStore = WKWebsiteDataStore.nonPersistentDataStore;
    [card.userContentController addScriptMessageHandler:self name:@"hxToneCard"];
    self.card = [[WKWebView alloc] initWithFrame:NSZeroRect configuration:card];
    self.card.navigationDelegate = self;
    [self.view addSubview:self.card];
    return YES;
}

- (void)close {
    [self.card.configuration.userContentController removeScriptMessageHandlerForName:@"hxToneCard"];
    [self.catalog stopLoading];
    [self.card stopLoading];
    [self.view removeFromSuperview];
}

- (void)layout {
    CGFloat x = [self.bounds[@"x"] doubleValue], y = [self.bounds[@"y"] doubleValue];
    CGFloat w = [self.bounds[@"w"] doubleValue], h = [self.bounds[@"h"] doubleValue];
    self.view.hidden = ![self.bounds[@"visible"] boolValue] || w < 60 || h < 60;
    if (self.view.hidden) return;
    CGFloat bottom = self.host.isFlipped ? y : NSHeight(self.host.bounds) - y - h;
    self.view.frame = NSMakeRect(x, bottom, w, h);
    CGFloat cardHeight = MIN(h, self.hasTone ? MIN(150, MAX(130, h - 100)) : 48);
    self.card.frame = NSMakeRect(0, 0, w, cardHeight);
    self.catalog.frame = NSMakeRect(0, cardHeight, w, h - cardHeight);
}

- (void)updateCard {
    self.status[@"viewing_source"] = @(self.catalogPage != nil);
    self.status[@"can_retry"] = @(self.retry);
    NSData *data = [NSJSONSerialization dataWithJSONObject:self.status options:0 error:nil];
    NSString *json = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
    [self.card evaluateJavaScript:[NSString stringWithFormat:@"window.hxToneStatus?.(%@)", json] completionHandler:nil];
}

- (void)receive:(NSDictionary *)command {
    NSString *type = command[@"type"];
    if ([type isEqual:@"navigate"]) {
        NSURL *url = [NSURL URLWithString:command[@"url"]];
        if (!IsToneURL(url)) return;
        self.retry = NO;
        self.catalogPage = nil;
        self.callback = [NSURL URLWithString:command[@"callback"]];
        [self.catalog loadRequest:[NSURLRequest requestWithURL:url]];
        [self updateCard];
    } else if ([type isEqual:@"card"]) {
        self.hasTone = [command[@"has_tone"] boolValue];
        [self layout];
        [self.card loadHTMLString:command[@"html"] baseURL:nil];
    } else if ([type isEqual:@"source"]) {
        NSURL *url = [NSURL URLWithString:command[@"url"]];
        if (self.catalogPage || !IsToneURL(url) || !IsToneURL(self.catalog.URL)) return;
        self.catalogPage = self.catalog.URL;
        [self.catalog loadRequest:[NSURLRequest requestWithURL:url]];
        [self updateCard];
    } else if ([type isEqual:@"status"]) {
        self.status = command.mutableCopy;
        [self updateCard];
    } else if ([type isEqual:@"browseError"]) {
        self.status[@"text"] = command[@"text"];
        self.retry = YES;
        [self updateCard];
    } else if ([type isEqual:@"bounds"]) {
        self.bounds = command;
        [self layout];
    }
}

- (void)userContentController:(WKUserContentController *)controller didReceiveScriptMessage:(WKScriptMessage *)event {
    if (event.webView != self.card || !event.frameInfo.mainFrame ||
        ![event.body isKindOfClass:NSString.class]) return;
    NSString *action = event.body;
    if ([action isEqual:@"back"] && self.catalogPage) {
        NSURL *page = self.catalogPage;
        self.catalogPage = nil;
        [self.catalog loadRequest:[NSURLRequest requestWithURL:page]];
        [self updateCard];
    } else if (![action isEqual:@"back"]) {
        [self emit:@"action" value:action];
    }
}

- (void)webView:(WKWebView *)webView decidePolicyForNavigationAction:(WKNavigationAction *)action
    decisionHandler:(void (^)(WKNavigationActionPolicy))decide {
    NSURL *url = action.request.URL;
    if (webView == self.card) {
        decide([url.absoluteString isEqual:@"about:blank"] ? WKNavigationActionPolicyAllow : WKNavigationActionPolicyCancel);
        return;
    }
    BOOL callback = !self.catalogPage && [url.scheme isEqual:@"http"] && !url.user && !url.password &&
        [url.host isEqual:@"127.0.0.1"] && [url.port isEqual:self.callback.port] &&
        [url.path isEqual:self.callback.path];
    if (!IsToneURL(url) && !callback) {
        decide(WKNavigationActionPolicyCancel);
    } else if (!action.targetFrame) {
        decide(WKNavigationActionPolicyCancel);
        [webView loadRequest:action.request];
    } else {
        decide(WKNavigationActionPolicyAllow);
    }
}

- (void)webView:(WKWebView *)webView didFinishNavigation:(WKNavigation *)navigation {
    if (webView == self.card) [self updateCard];
}

- (void)webViewWebContentProcessDidTerminate:(WKWebView *)webView {
    [self emit:@"error" value:@"WebKit closed the TONE3000 panel. Reopen it to try again."];
}

@end

static HXTonePane *pane;

void hx_tone_browser(const char *json, ToneMessage message) {
    @autoreleasepool {
        NSData *data = [NSData dataWithBytes:json length:strlen(json)];
        NSDictionary *command = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
        NSString *type = command[@"type"];
        NSNumber *session = command[@"session"];
        if ([type isEqual:@"open"]) {
            [pane close];
            pane = [[HXTonePane alloc] init];
            pane.session = session;
            pane.message = message;
            NSWindow *window = [NSApp windowWithWindowNumber:[command[@"window"] integerValue]];
            if ([pane open:window]) {
                pane.bounds = command[@"bounds"];
                [pane layout];
                [pane emit:@"ready" value:nil];
            } else {
                [pane emit:@"error" value:@"The HX Edit window is unavailable."];
            }
        } else if ([pane.session isEqual:session]) {
            if ([type isEqual:@"close"]) {
                [pane close];
                pane = nil;
            } else {
                [pane receive:command];
            }
        }
    }
}
