from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from src.config.database import engine, replica_engine
from src.utils.security import current, admin
from src.services import handover as service
from src.services import read_split

router = APIRouter()


def session_id(x_session_id: str | None = Header(default=None)) -> str:
    # 浏览器为每个工作台生成稳定 UUID；换后端实例时靠它在 Redis 里找回会话水位
    return (x_session_id or '').strip()[:80]


@router.get('/handover/transfers')
def listing(sid: str = Depends(session_id), user=Depends(current)):
    return service.listing(sid)


@router.get('/handover/topology')
def topology(user=Depends(current)):
    return read_split.topology()


class ReplicaControl(BaseModel):
    action: str = Field(pattern='^(pause|resume)$')


@router.post('/handover/replica/control')
def replica_control(data: ReplicaControl, user=Depends(admin)):
    """演练用：暂停/恢复备库 WAL 回放，制造可控的副本落后窗口。"""
    if replica_engine is None:
        raise HTTPException(404, '未配置读副本')
    sql = "SELECT pg_wal_replay_pause()" if data.action == 'pause' else "SELECT pg_wal_replay_resume()"
    try:
        with replica_engine.connect() as conn:
            conn.execute(text(sql))
            conn.commit()
            paused = conn.execute(text('SELECT pg_is_wal_replay_paused()')).scalar_one()
    except Exception as exc:
        raise HTTPException(503, f'副本操作失败：{str(exc)[:100]}')
    return {'action': data.action, 'replay_paused': bool(paused)}
