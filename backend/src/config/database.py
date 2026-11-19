from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from .settings import DATABASE_URL, REPLICA_URL
class Base(DeclarativeBase): pass
# 主库：全部写入 + 副本不可用/读己写等待超时时的读回退
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
# 读副本：物理流复制热备，只承接只读事务
replica_engine = create_engine(REPLICA_URL, pool_pre_ping=True) if REPLICA_URL else None
Session = sessionmaker(engine, expire_on_commit=False)
ReplicaSession = sessionmaker(replica_engine, expire_on_commit=False) if replica_engine else None
def db_session():
    with Session() as db: yield db
