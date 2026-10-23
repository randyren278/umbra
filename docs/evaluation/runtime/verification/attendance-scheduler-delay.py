import sys,time,os,json,signal
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent/'tests'))
import test_umbra as base
from server import Server
server=Server()
page='''<title>Attendance</title><script>
let last=Date.now();
for(const kind of ['keydown','keyup','mousemove','scroll','visibilitychange'])addEventListener(kind,e=>{if(!['keyup','visibilitychange'].includes(kind))last=Date.now();fetch('/event?kind='+kind+'&key='+e.key+'&trusted='+e.isTrusted+'&host='+location.hostname+'&vis='+document.visibilityState)});
setInterval(()=>{if(Date.now()-last>8000)location.replace('/logout?from='+location.hostname)},500);
</script>'''
server.page('/account',page);server.page('/logout','<title>Logged out</title>');server.page('/event','ok')
profile=base.new_profile();sites=base.write_sites([{'name':'Kept','domain':'127.0.0.1','login':server.url('/account'),'cookie':'sess','keepSignedIn':True}]);env={'UMBRA_IDLE':'30'}
started=time.monotonic()
pid=None
paused=False
try:
 for name,host in [('kept','127.0.0.1'),('other','localhost')]:
  r=base.umbra('open',server.url('/account',host),'--tab',name,profile=profile,sites=sites,env=env);assert r.code==0,r.err
 pid=next(pid for pid,name in base.daemons().items() if name==profile)
 first=server.requests('/account')[0].t
 time.sleep(max(0,first+6.5-time.monotonic()))
 os.kill(pid,signal.SIGSTOP);paused=True
 time.sleep(2.8)
 os.kill(pid,signal.SIGCONT);paused=False
 time.sleep(max(0,first+11-time.monotonic()))
 print(json.dumps([{'elapsed':round(r.t-started,3),'path':r.path,'query':r.query} for r in server.log],indent=2))
finally:
 if paused and pid is not None:os.kill(pid,signal.SIGCONT)
 base.umbra('stop',profile=profile);server.close()
