"""Real PostgreSQL row-lock experiment. Events coordinate two database connections; no timing simulation."""
import threading,time,hashlib,json
from sqlalchemy import text,select
from src.config.database import engine,Session
from src.models.entities import Trial,Transfer,TrialBalance,Receipt,Sample

def execute(trial_id):
    with Session() as db:
        trial=db.get(Trial,trial_id);transfer=db.get(Transfer,trial.transfer_id);sample=db.get(Sample,transfer.sample_id)
        timeout=trial.timeout_ms;qty=transfer.quantity;initial=sample.quantity
        trial.status='running';db.commit()
    acquired=threading.Event(); contender_done=threading.Event(); traces=[]; errors=[]
    def holder():
        try:
            with engine.connect() as c:
                tx=c.begin();pid=c.execute(text('SELECT pg_backend_pid()')).scalar()
                c.execute(text('SELECT remaining FROM trial_balances WHERE trial_id=:id FOR UPDATE'),{'id':trial_id}).one()
                traces.append({'actor':'A','pid':pid,'event':'row_lock_acquired'})
                # A receipt made before B starts is uncommitted, and B cannot read it.
                c.execute(text("INSERT INTO receipts(trial_id,actor,quantity) VALUES (:id,'A',:qty)"),{'id':trial_id,'qty':qty})
                acquired.set()
                if not contender_done.wait(12): raise RuntimeError('竞争事务未在保护期限内结束')
                changed=c.execute(text('UPDATE trial_balances SET remaining=remaining-:qty,received=received+:qty WHERE trial_id=:id AND remaining>=:qty'),{'id':trial_id,'qty':qty})
                if changed.rowcount!=1: raise RuntimeError('可用量校验失败')
                tx.commit();traces.append({'actor':'A','pid':pid,'event':'committed','quantity':qty})
        except Exception as e: errors.append(str(e));acquired.set();contender_done.set()
    def contender():
        try:
            if not acquired.wait(5): raise RuntimeError('持锁事务未就绪')
            with engine.connect() as c:
                tx=c.begin();pid=c.execute(text('SELECT pg_backend_pid()')).scalar()
                c.execute(text(f"SET LOCAL lock_timeout = '{int(timeout)}ms'"))
                c.execute(text("INSERT INTO receipts(trial_id,actor,quantity) VALUES (:id,'B',:qty)"),{'id':trial_id,'qty':qty})
                t=time.perf_counter()
                try:
                    c.execute(text('SELECT remaining FROM trial_balances WHERE trial_id=:id FOR UPDATE'),{'id':trial_id}).one()
                    raise RuntimeError('竞争连接意外获得锁')
                except Exception as e:
                    state=getattr(getattr(e,'orig',None),'sqlstate',None)
                    tx.rollback()
                    if state!='55P03': raise
                    traces.append({'actor':'B','pid':pid,'event':'rolled_back','sqlstate':state,'wait_ms':round((time.perf_counter()-t)*1000,2),'receipt_insert_rolled_back':True})
        except Exception as e: errors.append(str(e))
        finally: contender_done.set()
    a=threading.Thread(target=holder);b=threading.Thread(target=contender)
    a.start();b.start();a.join(15);b.join(15)
    if a.is_alive() or b.is_alive(): raise RuntimeError('并发试验未及时结束')
    if errors: raise RuntimeError(';'.join(errors))
    with Session() as db:
        balance=db.get(TrialBalance,trial_id)
        receipts=list(db.scalars(select(Receipt).where(Receipt.trial_id==trial_id)))
        invariant=balance.remaining+balance.received==initial and balance.remaining>=0 and len(receipts)==1 and receipts[0].actor=='A'
        result={'mode':'真实 PostgreSQL / 隔离样本镜像账本','sample':sample.code,'initial':initial,'transfer_quantity':qty,'remaining':balance.remaining,'received':balance.received,'receipt_count':len(receipts),'invariant_passed':invariant,'traces':traces,'sql':['SELECT ... FOR UPDATE','SET LOCAL lock_timeout','INSERT INTO receipts','UPDATE trial_balances','ROLLBACK / COMMIT'],'formula':'remaining + received = initial；A 回执 1 条，B 回执回滚为 0 条'}
        result['checksum']=hashlib.sha256(json.dumps(result,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        trial=db.get(Trial,trial_id);trial.result=result;trial.status='completed' if invariant else 'failed';db.commit()
        return result
