"""移交台读服务：申请与对应样本量在同一个只读事务快照里一起读出。"""
from sqlalchemy import select
from src.models.entities import Transfer, Sample
from src.services import read_split


def _fetch_handover(conn):
    # 单连接单事务：两条 SELECT 共享同一快照，申请数量与样本台账量按同一已提交时刻对齐
    rows = conn.execute(
        select(
            Transfer.id, Transfer.code, Transfer.destination, Transfer.quantity,
            Transfer.note, Transfer.sample_id,
            Sample.code.label('sample_code'), Sample.title.label('sample_title'),
            Sample.location, Sample.quantity.label('sample_quantity'),
        ).join(Sample, Sample.id == Transfer.sample_id).order_by(Transfer.id)
    ).all()
    return [dict(r._mapping) for r in rows]


def listing(session_id: str):
    items, meta = read_split.read_handover(session_id, _fetch_handover)
    return {'items': items, 'read': meta}
