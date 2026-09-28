"""Verify Cocoa RTFD attachments, links and file URLs; restore the old pasteboard."""
import ctypes as C
from io import BytesIO
from pathlib import Path
import sys
import tempfile
from urllib.parse import unquote,urlsplit

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from linkexpand.macos_clipboard import Cocoa,P,U,Range
from linkexpand.clipboard import copy_rich,copy_video_file
from linkexpand.metadata import Preview,plain_text

def contents(cocoa):
    board=cocoa.pasteboard();items=cocoa.msg(board,'pasteboardItems');records=[]
    for index in range(cocoa.msg(items,'count',result=U) if items else 0):
        item=cocoa.msg(items,'objectAtIndex:',index,types=[U]);types=cocoa.msg(item,'types');record=[]
        for offset in range(cocoa.msg(types,'count',result=U)):
            kind=cocoa.msg(types,'objectAtIndex:',offset,types=[U])
            name=C.string_at(cocoa.msg(kind,'UTF8String')).decode()
            data=cocoa.bytes(cocoa.msg(item,'dataForType:',kind))
            if data is not None:record.append((name,data))
        records.append(record)
    return records

def restore(cocoa,records):
    array=cocoa.msg(cocoa.cls('NSMutableArray'),'array')
    for record in records:
        item=cocoa.msg(cocoa.msg(cocoa.cls('NSPasteboardItem'),'alloc'),'init')
        try:
            for kind,data in record:cocoa.msg(item,'setData:forType:',cocoa.data(data),cocoa.string(kind),result=C.c_bool)
            cocoa.msg(array,'addObject:',item,result=None)
        finally:cocoa.msg(item,'release',result=None)
    board=cocoa.pasteboard();cocoa.msg(board,'clearContents',result=C.c_long)
    if records:assert cocoa.msg(board,'writeObjects:',array,result=C.c_bool)

def run():
    if sys.platform!='darwin':raise RuntimeError('Run this integration on macOS.')
    cocoa=Cocoa()
    with cocoa.pool():
        backup=contents(cocoa);changed=None
        try:
            picture=BytesIO();Image.new('RGB',(960,480),'#327b5c').save(picture,'PNG');png=picture.getvalue()
            preview=Preview('https://example.com/中文?x=1','中文标题 🖼️','摘要、图片和可以点击的链接。','example.com','网站')
            copy_rich({'preview':preview,'cover':png,'png':png},'mac-native-smoke')
            changed=cocoa.msg(cocoa.pasteboard(),'changeCount',result=C.c_long)
            payloads=dict(contents(cocoa)[0])
            assert payloads['public.png']==png
            assert payloads['public.utf8-plain-text'].decode()==plain_text(preview)
            assert preview.url in payloads['public.html'].decode()
            rtfd=cocoa.msg(cocoa.msg(cocoa.cls('NSAttributedString'),'alloc'),'initWithRTFD:documentAttributes:',cocoa.data(payloads['com.apple.flat-rtfd']),None)
            assert rtfd
            try:
                value=C.string_at(cocoa.msg(cocoa.msg(rtfd,'string'),'UTF8String')).decode()
                assert preview.title in value and value.endswith(preview.url)
                attachment=P.in_dll(cocoa.appkit,'NSAttachmentAttributeName').value
                assert cocoa.msg(rtfd,'attribute:atIndex:effectiveRange:',attachment,0,None,types=[P,U,P])
                position=cocoa.msg(rtfd,'length',result=U)-len(preview.url.encode('utf-16-le'))//2
                key=P.in_dll(cocoa.appkit,'NSLinkAttributeName').value
                link=cocoa.msg(rtfd,'attribute:atIndex:effectiveRange:',key,position,None,types=[P,U,P])
                assert link
            finally:cocoa.msg(rtfd,'release',result=None)
            print('PASS: native macOS RTFD embeds image, Unicode text and active link; HTML / PNG fallback.',flush=True)
            with tempfile.TemporaryDirectory() as folder:
                file=Path(folder)/'完整文件 中文.mp4';file.write_bytes(b'ftyp-test')
                copy_video_file(file);changed=cocoa.msg(cocoa.pasteboard(),'changeCount',result=C.c_long)
                payloads=dict(contents(cocoa)[0])
                uri=payloads['public.file-url'].decode().rstrip('\0')
                assert Path(unquote(urlsplit(uri).path)).resolve()==file.resolve()
            print('PASS: native macOS file URL clipboard.',flush=True)
        finally:
            if changed is not None and changed==cocoa.msg(cocoa.pasteboard(),'changeCount',result=C.c_long):restore(cocoa,backup)
    print('PASS: previous pasteboard restored without displaying its contents.')

if __name__=='__main__':run()
