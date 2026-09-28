"""Cocoa pasteboard support without adding a Python Objective-C dependency."""

import ctypes as C
from functools import lru_cache
from pathlib import Path
from contextlib import contextmanager

from .metadata import PreviewError,plain_text
from .sharing import rich_html

P=C.c_void_p
U=C.c_ulong

class Range(C.Structure):
    _fields_=[('location',U),('length',U)]

class Rect(C.Structure):
    _fields_=[('x',C.c_double),('y',C.c_double),('width',C.c_double),('height',C.c_double)]


class Cocoa:
    def __init__(self):
        self.foundation=C.CDLL('/System/Library/Frameworks/Foundation.framework/Foundation')
        self.appkit=C.CDLL('/System/Library/Frameworks/AppKit.framework/AppKit')
        self.objc=C.CDLL('/usr/lib/libobjc.A.dylib')
        self.objc.objc_getClass.argtypes=[C.c_char_p];self.objc.objc_getClass.restype=P
        self.objc.sel_registerName.argtypes=[C.c_char_p];self.objc.sel_registerName.restype=P
        self.address=C.cast(self.objc.objc_msgSend,P).value

    def cls(self,name):
        value=self.objc.objc_getClass(name.encode())
        if not value:raise PreviewError('无法加载 macOS 剪贴板组件。')
        return value

    @lru_cache(maxsize=64)
    def function(self,result,types):
        return C.CFUNCTYPE(result,P,P,*types)(self.address)

    def msg(self,obj,selector,*args,types=None,result=P):
        return self.function(result,tuple(types or [P]*len(args)))(obj,self.objc.sel_registerName(selector.encode()),*args)

    def string(self,value):
        return self.msg(self.cls('NSString'),'stringWithUTF8String:',value.encode('utf-8'),types=[C.c_char_p])

    def data(self,value):
        return self.msg(self.cls('NSData'),'dataWithBytes:length:',value,len(value),types=[C.c_char_p,U])

    def bytes(self,value):
        if not value:return None
        size=self.msg(value,'length',result=U)
        return C.string_at(self.msg(value,'bytes'),size)

    @contextmanager
    def pool(self):
        pool=self.msg(self.msg(self.cls('NSAutoreleasePool'),'alloc'),'init')
        try:yield
        finally:self.msg(pool,'drain',result=None)

    def pasteboard(self):return self.msg(self.cls('NSPasteboard'),'generalPasteboard')

    def set_payloads(self,payloads):
        item=self.msg(self.msg(self.cls('NSPasteboardItem'),'alloc'),'init')
        try:
            for kind,payload in payloads:
                if not self.msg(item,'setData:forType:',self.data(payload),self.string(kind),result=C.c_bool):
                    raise PreviewError('无法准备 macOS 图文剪贴板。')
            board=self.pasteboard();self.msg(board,'clearContents',result=C.c_long)
            array=self.msg(self.cls('NSArray'),'arrayWithObject:',item)
            if not self.msg(board,'writeObjects:',array,result=C.c_bool):raise PreviewError('macOS 图文复制失败，请重试。')
        finally:self.msg(item,'release',result=None)

    def copy_file(self,path):
        url=self.msg(self.cls('NSURL'),'fileURLWithPath:',self.string(str(path.resolve())))
        array=self.msg(self.cls('NSArray'),'arrayWithObject:',url)
        board=self.pasteboard();self.msg(board,'clearContents',result=C.c_long)
        if not self.msg(board,'writeObjects:',array,result=C.c_bool):raise PreviewError('macOS 文件复制失败，请重试。')

    def rich_payloads(self,record,image_path):
        preview=record['preview'];cover=record['cover'] or record['png'];text=plain_text(preview)
        styled=self.msg(self.msg(self.cls('NSMutableAttributedString'),'alloc'),'initWithString:',self.string(''))
        try:
            if cover:
                attachment=self.msg(self.msg(self.cls('NSTextAttachment'),'alloc'),'initWithData:ofType:',self.data(cover),self.string('public.png'))
                try:
                    self.msg(attachment,'setBounds:',Rect(0,0,320,160),types=[Rect],result=None)
                    image=self.msg(self.cls('NSAttributedString'),'attributedStringWithAttachment:',attachment)
                    self.msg(styled,'appendAttributedString:',image,result=None)
                finally:self.msg(attachment,'release',result=None)
            content=('\n' if cover else '')+text
            words=self.msg(self.msg(self.cls('NSAttributedString'),'alloc'),'initWithString:',self.string(content))
            try:self.msg(styled,'appendAttributedString:',words,result=None)
            finally:self.msg(words,'release',result=None)
            length=self.msg(styled,'length',result=U);url_length=len(preview.url.encode('utf-16-le'))//2
            link_key=P.in_dll(self.appkit,'NSLinkAttributeName').value
            self.msg(styled,'addAttribute:value:range:',link_key,self.string(preview.url),Range(length-url_length,url_length),types=[P,P,Range],result=None)
            attributes=self.msg(self.cls('NSDictionary'),'dictionary')
            rtfd=self.bytes(self.msg(styled,'RTFDFromRange:documentAttributes:',Range(0,length),attributes,types=[Range,P]))
            if not rtfd:raise PreviewError('无法生成带图片和链接的 macOS 富文本。')
            html=rich_html(preview,cover)
            # One item offers RTFD (embedded image), HTML, PNG and Unicode text.
            payloads=[('com.apple.flat-rtfd',rtfd),('public.html',html.encode()),('public.utf8-plain-text',text.encode())]
            if cover:payloads.append(('public.png',cover))
            return payloads
        finally:self.msg(styled,'release',result=None)


def copy_file(path):
    cocoa=Cocoa()
    with cocoa.pool():cocoa.copy_file(path)


def copy_rich(record,image_path):
    cocoa=Cocoa()
    with cocoa.pool():cocoa.set_payloads(cocoa.rich_payloads(record,image_path))
