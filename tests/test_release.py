import asyncio, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from controller import Controller, Incident
from brains import ScriptedBrain
import guardrails
MD = '''---
project: t
service: web
guardrails: {max_replicas: 3, max_memory: 256m, allowed_actions: [scale]}
probe: {recover_ms: 500, threshold_ms: 800}
verify: {consecutive: 5, timeout_s: 0}
---
# Web
'''
COMP = 'services:\n  web:\n    image: x\n    deploy:\n      replicas: 1\n      resources:\n        limits: {memory: 128m}\n'
passed = 0

def ck(name, condition):
    global passed
    assert condition, name
    passed += 1

def directory():
    d = tempfile.mkdtemp()
    open(d+'/watchdock.md','w').write(MD)
    open(d+'/docker-compose.yml','w').write(COMP)
    return d

class Deploy:
    def __init__(self): self.n = 0
    def apply(self,*args):
        self.n += 1
        return {'ok':self.n==1,'seconds':0,'output':'offline' if self.n>1 else 'up'}

async def main():
    d=directory(); old=guardrails.snapshot(d)
    open(d+'/docker-compose.yml','w').write(COMP.replace('    image: x','    image: x\n    memswap_limit: -1'))
    ck('swap-limit bypass rejected',not guardrails.validate(old,d,'web')['ok'])
    d=directory(); ctl=Controller([d],Deploy(),None,ScriptedBrain([]),state_dir=tempfile.mkdtemp(),git=False,quiet=True)
    try: ctl.add_deployment(d); duplicate=False
    except ValueError: duplicate=True
    ck('duplicate service fails closed',duplicate)
    w=ctl.watch['web']; inc=Incident('web',w.dep,'latency',time.time()-40,{})
    inc.applied_at=time.time()-20
    w.probes.extend([(inc.first_signal_at+1,1200,True,True)]+[(time.time()-1,600,True,False)]*5)
    ok,detail=await ctl.verify(inc,w)
    ck('partial improvement distinguished',ok and detail.get('partial'))
    w.probes.clear();w.probes.extend([(inc.first_signal_at+1,1200,True,True)]+[(time.time()-10,600,True,False)]*5)
    ok,detail=await ctl.verify(inc,w)
    ck('stale probes cannot certify partial recovery',not ok)
    d=directory(); dep=Deploy(); brain=ScriptedBrain([('set_resources',{'replicas':2}),('apply_fix',{'summary':'scale'})])
    c=Controller([d],dep,None,brain,state_dir=tempfile.mkdtemp(),git=False,quiet=True,max_attempts=2)
    async def bundle(*args): return {'service':'web'}
    async def verify(*args): return False,{'reason':'no recovery'}
    c.bundle=bundle;c.verify=verify
    ww=c.watch['web'];ii=Incident('web',ww.dep,'latency',time.time(),{});ww.incident=ii
    await c._run(ii,ww)
    ck('failed rollback escalates explicitly',ii.outcome=='ESCALATED' and 'ROLLBACK FAILED' in ii.reason)
    ck('failed rollback stops retries',len(ii.attempts)==1 and dep.n==2)
    ck('baseline file actually restored',open(d+'/docker-compose.yml').read()==COMP)
    d=directory(); cc=Controller([d],Deploy(),None,ScriptedBrain([]),state_dir=tempfile.mkdtemp(),git=False,quiet=True)
    ww=cc.watch['web']; ww.cooldown_until=time.time()+60
    cc._open(ww,'oom',time.time(),{'action':'oom'})
    await asyncio.gather(*list(cc.tasks))
    ck('post-fix OOM bypasses cooldown and escalates',ww.incident.outcome=='ESCALATED' and 'POST-FIX RELAPSE' in ww.incident.reason and not ww.incident.attempts)


asyncio.run(main())
print(f'{passed} passed, 0 failed')
