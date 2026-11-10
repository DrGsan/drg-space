"""Bounded audio container recognition for the existing private chat uploads."""

def audio_mime(data):
    head=data[:65536]
    if head.startswith(b'OggS') and (b'OpusHead' in head or b'\x01vorbis' in head):return 'audio/ogg'
    if head.startswith(b'\x1aE\xdf\xa3') and b'webm' in head and b'OpusHead' in head and not any(codec in head for codec in (b'V_VP8',b'V_VP9',b'V_AV1')):return 'audio/webm'
    if len(data)<12 or data[4:8]!=b'ftyp':return None
    handlers=[]
    def boxes(start,end,depth=0):
        if depth>6:return False
        count=0
        while start+8<=end:
            count+=1
            if count>10000:return False
            size=int.from_bytes(data[start:start+4],'big');kind=data[start+4:start+8];header=8
            if size==1:
                if start+16>end:return False
                size=int.from_bytes(data[start+8:start+16],'big');header=16
            if size==0:size=end-start
            if size<header or start+size>end:return False
            if kind in (b'moov',b'trak',b'mdia'):
                if not boxes(start+header,start+size,depth+1):return False
            elif kind==b'hdlr':
                if size<header+12:return False
                handlers.append(data[start+header+8:start+header+12])
            start+=size
        return start==end
    return 'audio/mp4' if boxes(0,len(data)) and b'soun' in handlers and b'vide' not in handlers else None
