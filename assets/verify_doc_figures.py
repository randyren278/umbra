#!/usr/bin/env python3
"""Check generated SVG text bounds in real WebKit using a throwaway local profile."""
import json,sys
from pathlib import Path
sys.dont_write_bytecode=True
root=Path(__file__).resolve().parent.parent;sys.path.insert(0,str(root/'tests'))
import test_umbra as b
from server import Server
paths=sorted((root/'assets/docs').glob('*.svg'))
site=Server();profile=b.new_profile()
source='<!doctype html><title>SVG layout verification</title><style>svg{position:absolute;left:0;top:0;visibility:hidden}</style>'+''.join(p.read_text().replace('<svg ', '<svg data-file="'+p.name+'" ',1) for p in paths)
site.page('/gallery',source)
def call(*args):
 r=b.umbra(*args,'--json',profile=profile,binary=b.UMBRA,env={'UMBRA_UNSEEN':'1'})
 assert r.code==0,(args[0],r.err)
 return json.loads(r.text)
try:
 call('open',site.url('/gallery'),'--until','document.querySelectorAll("svg").length === '+str(len(paths)))
 value=call('eval','''await document.fonts.ready; const bad=[];let texts=0;for(const s of document.querySelectorAll('svg')){const v=s.viewBox.baseVal;for(const e of s.querySelectorAll('text')){texts++;const b=e.getBBox(),limit=+e.dataset.maxWidth;if(e.getComputedTextLength()>limit+1||b.x<-.5||b.y<-.5||b.x+b.width>v.width+.5||b.y+b.height>v.height+.5)bad.push({file:s.dataset.file,text:e.textContent,width:e.getComputedTextLength(),limit,bounds:[b.x,b.y,b.width,b.height],viewBox:[v.width,v.height]});}}return {figures:document.querySelectorAll('svg').length,texts,violations:bad};''')['value']
finally:
 site.close();b.cleanup()
value['cleanup']={'daemonGone':profile not in b.daemons().values(),'profileFilesGone':not any(p.exists() for p in b.profile_files(profile))}
print(json.dumps(value,indent=2))
assert not value['violations'], 'SVG text exceeds its column or viewBox'
assert all(value['cleanup'].values()), 'verification profile was not cleaned up'
