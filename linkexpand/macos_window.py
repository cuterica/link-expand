"""macOS window containing the existing local interface, using system WebKit."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

import objc
from AppKit import (NSApplication, NSWindow, NSMenu, NSMenuItem,
                    NSBitmapImageRep, NSWorkspace, NSEvent, NSPasteboard, NSPasteboardURLReadingFileURLsOnlyKey,
                    NSTextView, NSMakeRect, NSApplicationActivationPolicyRegular,
                    NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
                    NSWindowStyleMaskMiniaturizable, NSWindowStyleMaskResizable,
                    NSBackingStoreBuffered, NSBitmapImageFileTypePNG,
                    NSCommandKeyMask, NSKeyDown)
from Foundation import NSObject, NSURL, NSURLRequest, NSFileManager
from PyObjCTools import AppHelper
from WebKit import WKWebView, WKWebViewConfiguration, WKSnapshotConfiguration

from . import __version__
from .metadata import PreviewError


def main_call(action, timeout=25):
    done=threading.Event();result={}
    def finish(value=None,error=None):
        result.update(value=value,error=error);done.set()
    def run():
        try:action(finish)
        except Exception as error:result.update(error=str(error));done.set()
    AppHelper.callAfter(run)
    if not done.wait(timeout):raise PreviewError('Mac 窗口操作超时，请重试。')
    if result.get('error'):raise PreviewError(str(result['error']))
    return result.get('value')


class Controller(NSObject):
    @objc.python_method
    def prepare(self,server,url,qa=False):
        self.server=server;self.url=url;self.qa=qa;self.closing=False;self.exports={};self.installing=False
        self.application=NSApplication.sharedApplication()
        self.application.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        self.application.setDelegate_(self)
        self.window=NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(NSMakeRect(0,0,1240,950),
            NSWindowStyleMaskTitled|NSWindowStyleMaskClosable|NSWindowStyleMaskMiniaturizable|NSWindowStyleMaskResizable,
            NSBackingStoreBuffered,False)
        self.window.setTitle_('Link Expand '+__version__)
        self.window.setMinSize_((800,600));self.window.setReleasedWhenClosed_(False);self.window.setDelegate_(self)
        self.webview=WKWebView.alloc().initWithFrame_configuration_(self.window.contentView().bounds(),WKWebViewConfiguration.alloc().init())
        self.webview.setAutoresizingMask_(18);self.window.setContentView_(self.webview)
        self.install_menu()
        self.window.center();self.window.makeKeyAndOrderFront_(None)
        self.application.activateIgnoringOtherApps_(True)
        self.webview.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(url)))

    @objc.python_method
    def install_menu(self):
        bar=NSMenu.alloc().init();app_item=NSMenuItem.alloc().init();bar.addItem_(app_item)
        menu=NSMenu.alloc().initWithTitle_('Link Expand');app_item.setSubmenu_(menu)
        quit_item=NSMenuItem.alloc().initWithTitle_action_keyEquivalent_('退出 Link Expand','quit:','q')
        quit_item.setTarget_(self);menu.addItem_(quit_item)
        folder=NSMenuItem.alloc().initWithTitle_action_keyEquivalent_('打开下载文件夹','openDownloads:','')
        folder.setTarget_(self);menu.addItem_(folder)
        setup=NSMenuItem.alloc().initWithTitle_action_keyEquivalent_('安装截图组件（可选）','installBrowser:','')
        setup.setTarget_(self);menu.addItem_(setup)
        edit_item=NSMenuItem.alloc().init();bar.addItem_(edit_item)
        edit=NSMenu.alloc().initWithTitle_('编辑');edit_item.setSubmenu_(edit)
        for title,action,key in [('撤销','undo:','z'),('剪切','cut:','x'),('复制','copy:','c'),('粘贴','paste:','v'),('全选','selectAll:','a')]:
            edit.addItem_(NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title,action,key))
        self.application.setMainMenu_(bar)

    def quit_(self,sender):self.stop()

    def openDownloads_(self,sender):
        from .video_downloads import downloads_directory
        path=downloads_directory();path.mkdir(parents=True,exist_ok=True)
        NSWorkspace.sharedWorkspace().openURL_(NSURL.fileURLWithPath_(str(path)))

    def installBrowser_(self,sender):
        if self.installing:return
        self.installing=True
        def install():
            from .owned_process import run_worker
            message='截图组件已就绪。'
            try:
                self.evaluate("feedback('正在安装独立截图组件，请稍候…')")
                command=[sys.executable,'--browser-install-worker'] if getattr(sys,'frozen',False) else [sys.executable,'-m','linkexpand.setup_browser']
                result=run_worker(command,'',900)
                folder=Path.home()/'Library/Application Support/LinkExpand';folder.mkdir(parents=True,exist_ok=True)
                (folder/'browser-install.log').write_text(result.stdout+result.stderr,encoding='utf-8')
                if result.returncode:message='截图组件安装失败，可以安装 Chrome 后使用截图。'
            except (OSError,subprocess.TimeoutExpired,PreviewError):message='截图组件安装失败，可以安装 Chrome 后使用截图。'
            finally:
                self.installing=False
                if not self.closing:
                    try:self.evaluate('feedback('+__import__('json').dumps(message)+')')
                    except PreviewError:pass
        threading.Thread(target=install,name='LinkExpand-browser-setup',daemon=True).start()

    def windowShouldClose_(self,window):self.stop();return True

    def applicationShouldTerminate_(self,application):self.stop();return 0

    @objc.python_method
    def stop(self):
        if self.closing:return
        self.closing=True
        self.application.stop_(None)
        event=NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
            15,(0,0),0,time.monotonic(),self.window.windowNumber(),None,0,0,0)
        self.application.postEvent_atStart_(event,True)

    @objc.python_method
    def evaluate(self,code):
        return main_call(lambda finish:self.webview.evaluateJavaScript_completionHandler_(code,
            lambda value,error:finish(value,str(error) if error else None)))

    @objc.python_method
    def save(self,name,data=None,source=None):
        folder=Path.home()/'Downloads'/'LinkExpand-exports';folder.mkdir(parents=True,exist_ok=True)
        base=Path(name).stem;suffix=Path(name).suffix
        path=None
        for index in range(1000):
            candidate=folder/(name if index==0 else f'{base}_{index}{suffix}')
            try:descriptor=os.open(candidate,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            except FileExistsError:continue
            path=candidate;break
        if path is None:raise PreviewError('下载文件夹中同名文件过多，请清理后重试。')
        try:
            with os.fdopen(descriptor,'wb') as output:
                if source:
                    with Path(source).open('rb') as input_file:shutil.copyfileobj(input_file,output,1024*1024)
                else:output.write(data)
            self.exports[str(path)]={'size':path.stat().st_size}
            return {'path':str(path),'cancelled':False}
        except OSError:
            path.unlink(missing_ok=True)
            raise PreviewError('文件保存失败，请检查下载文件夹权限。') from None

    @objc.python_method
    def qa_action(self,data):
        if not self.qa:raise PreviewError('测试接口未启用。')
        action=data.get('action')
        if action=='eval':return {'value':self.evaluate(str(data.get('code','')))}
        if action=='key':
            def key(finish):
                name=str(data.get('key','v')).lower()
                codes={'v':9,'c':8,'a':0,'q':12}
                if name not in codes:finish(error='测试按键无效。');return
                self.window.makeKeyAndOrderFront_(None);self.window.makeFirstResponder_(self.webview)
                event=NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
                    NSKeyDown,(0,0),NSCommandKeyMask,time.monotonic(),self.window.windowNumber(),None,name,name,False,codes[name])
                finish({'handled':bool(self.application.mainMenu().performKeyEquivalent_(event))})
            return main_call(key)
        if action=='snapshot':
            def capture(finish):
                def completed(image,error):
                    if error:finish(error=str(error));return
                    path=Path(str(data['path']));path.parent.mkdir(parents=True,exist_ok=True)
                    rep=NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
                    rep.representationUsingType_properties_(NSBitmapImageFileTypePNG,{}).writeToFile_atomically_(str(path),True)
                    finish({'path':str(path),'size':path.stat().st_size})
                self.webview.takeSnapshotWithConfiguration_completionHandler_(WKSnapshotConfiguration.alloc().init(),completed)
            return main_call(capture)
        if action=='native-paste':
            def paste(finish):
                view=NSTextView.alloc().initWithFrame_(NSMakeRect(0,0,640,900));view.setRichText_(True);view.setImportsGraphics_(True)
                view.paste_(None)
                text=str(view.string());storage=view.textStorage();length=storage.length();attachments=[];links=[]
                offset=0
                while offset<length:
                    attributes,span=storage.attributesAtIndex_effectiveRange_(offset,None)
                    if attributes.get('NSAttachment'):attachments.append(offset)
                    if attributes.get('NSLink'):links.append(str(attributes['NSLink']))
                    offset=max(offset+1,span.location+span.length)
                finish({'text':text,'attachments':attachments,'links':links})
            return main_call(paste)
        if action=='file-paste':
            def paste_file(finish):
                urls=NSPasteboard.generalPasteboard().readObjectsForClasses_options_([NSURL],{NSPasteboardURLReadingFileURLsOnlyKey:True})
                if not urls:finish(error='剪贴板没有可接收的文件。');return
                folder=Path(str(data['folder']));folder.mkdir(parents=True,exist_ok=True)
                paths=[]
                for url in urls:
                    source=Path(str(url.path()));target=folder/source.name
                    if target.exists():finish(error='测试接收文件已存在。');return
                    copied,error=NSFileManager.defaultManager().copyItemAtURL_toURL_error_(url,NSURL.fileURLWithPath_(str(target)),None)
                    if not copied:finish(error=str(error));return
                    paths.append(str(target))
                finish({'paths':paths})
            return main_call(paste_file,timeout=90)
        if action=='quit':AppHelper.callAfter(self.stop);return {'ok':True}
        raise PreviewError('未知测试操作。')


def run(server,url,qa=False):
    controller=Controller.alloc().init();controller.prepare(server,url,qa)
    server.app.native_ui=controller
    serving=threading.Thread(target=server.serve_forever,name='LinkExpand-server',daemon=True);serving.start()
    try:controller.application.run()
    finally:
        server.app.native_ui=None
        server.shutdown();serving.join(timeout=3)
        controller.webview.stopLoading();controller.window.orderOut_(None)
