import os,logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from sqlalchemy import text
from src.config.database import Base,engine
from src.models import entities
from src.controllers.auth import router as auth
from src.routes import samples,transfers,trials,audit,handover
from src.middlewares.errors import ErrorMiddleware
from src.middlewares.request_id import RequestIdMiddleware
@asynccontextmanager
async def lifespan(app):
    # 多实例并行启动时，建表/种子用咨询锁串行化，避免并发 DDL 的对象已存在竞争
    with engine.connect() as lock_conn:
        lock_conn.execute(text('SELECT pg_advisory_lock(710202611)'))
        lock_conn.commit()
        try:
            Base.metadata.create_all(engine)
            if os.getenv('SEED_DEMO','true')=='true':
                try:
                    from scripts.seed_demo import seed
                    seed()
                except Exception: logging.exception('seed failed; manual retry is available')
        finally:
            lock_conn.execute(text('SELECT pg_advisory_unlock(710202611)'))
            lock_conn.commit()
    yield
app=FastAPI(title='实验样本流转并发台',lifespan=lifespan)
app.add_middleware(ErrorMiddleware);app.add_middleware(RequestIdMiddleware)
for r in (auth,samples.router,transfers.router,trials.router,audit.router,handover.router): app.include_router(r)
@app.get('/health')
def health():
    with engine.connect() as c: c.execute(text('SELECT 1'))
    from src.services.trials import queue
    queue.ping()
    return {'status':'healthy'}
