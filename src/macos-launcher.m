#import <Cocoa/Cocoa.h>

@interface NAMLauncher : NSObject <NSApplicationDelegate>
@property (strong) NSTask *task;
@end

@implementation NAMLauncher
- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    NSURL *resources = NSBundle.mainBundle.resourceURL;
    NSTask *task = [[NSTask alloc] init];
    task.executableURL = [resources URLByAppendingPathComponent:@"runtime/bin/python3.13"];
    task.currentDirectoryURL = resources;
    NSMutableArray<NSString *> *arguments = [NSMutableArray arrayWithArray:@[
        @"-E", @"-s", @"-B", [resources URLByAppendingPathComponent:@"start-hx-edit-nam-macos.py"].path
    ]];
    for (NSString *argument in [NSProcessInfo.processInfo.arguments subarrayWithRange:
             NSMakeRange(1, NSProcessInfo.processInfo.arguments.count - 1)]) {
        if (![argument hasPrefix:@"-psn_"]) [arguments addObject:argument];
    }
    task.arguments = arguments;
    task.terminationHandler = ^(NSTask *finished) {
        dispatch_async(dispatch_get_main_queue(), ^{
            [NSApp terminate:nil];
        });
    };
    NSError *error;
    if (![task launchAndReturnError:&error]) {
        NSAlert *alert = [NSAlert alertWithError:error];
        [alert runModal];
        [NSApp terminate:nil];
        return;
    }
    self.task = task;
}

- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)sender {
    if (!self.task.running) return NSTerminateNow;
    [self.task interrupt];
    return NSTerminateCancel;
}
@end

int main(void) {
    @autoreleasepool {
        NSApplication *application = NSApplication.sharedApplication;
        [application setActivationPolicy:NSApplicationActivationPolicyAccessory];
        NAMLauncher *delegate = [[NAMLauncher alloc] init];
        application.delegate = delegate;
        [application run];
    }
    return 0;
}
