"""读写分离路由：主库承接全部写入，移交页按会话水位在副本/主库之间选路。

规则（对应演练口径）：
1. 页面读默认走物理流复制副本；副本中断时回主库，恢复后自动切回。
2. 会话刚提交的写入把提交 LSN 记到 Redis；换任意一台后端都能读到该水位，
   读到自己的新单前若副本未追平则短暂等待，等不到回主库，保证读己写。
3. 没有等待水位的会话即使副本落后也仍走副本（只读已提交旧数据）。
4. 每个会话记录已读 LSN 高水位；副本恢复切回时若 LSN 倒退则回主库，
   保证同一会话看到的数据不倒退（单调读）。
5. 两边都不可用时抛 503；每次读返回数据来源与 LSN/延迟，页面据此标明出处。
"""
import time
import logging
import threading
from redis import Redis
from sqlalchemy import text
from fastapi import HTTPException
from src.config.settings import (
    REDIS_URL, REPLICA_WAIT_TRIES, REPLICA_WAIT_INTERVAL, NODE_COOLDOWN_SECONDS,
)
from src.config.database import engine, replica_engine

log = logging.getLogger('read_split')
redis = Redis.from_url(REDIS_URL, decode_responses=True)

# 进程内节点健康冷却（多后端各自维护；Redis 负责跨后端的会话水位）
_health = {'primary': [True, 0.0], 'replica': [True, 0.0]}
_health_lock = threading.Lock()
SERVER_ID = os_env = __import__('os').environ.get('SERVER_ID', 'backend-1')

PENDING_TTL = 24 * 3600
READ_TTL = 24 * 3600


def _key_pending(session_id: str) -> str:
    return f'locklab:readsplit:{session_id}:pending_lsn'


def _key_read(session_id: str) -> str:
    return f'locklab:readsplit:{session_id}:read_lsn'


def _mark(node: str, ok: bool):
    with _health_lock:
        _health[node][0] = ok
        _health[node][1] = 0.0 if ok else time.time()


def _available(node: str) -> bool:
    with _health_lock:
        ok, failed_at = _health[node]
        return ok or (time.time() - failed_at) >= NODE_COOLDOWN_SECONDS


def _lsn_num(conn, lsn_text):
    """'0/3000060' -> int，便于比较水位。"""
    if not lsn_text:
        return 0
    high, low = lsn_text.split('/')
    return (int(high, 16) << 32) + int(low, 16)


def _primary_position(conn):
    return conn.execute(text('SELECT pg_current_wal_lsn()')).scalar_one()


def _replica_position(conn):
    row = conn.execute(text(
        "SELECT pg_last_wal_replay_lsn(), pg_is_wal_replay_paused(), "
        "COALESCE(pg_wal_lsn_diff(pg_last_wal_receive_lsn(), pg_last_wal_replay_lsn()),0)"
    )).one()
    return row[0], bool(row[1]), int(row[2])


def mark_session_write(session_id: str):
    """写入事务提交后调用：把主库提交 LSN 记为该会话的等待水位。"""
    if not session_id:
        return
    try:
        with engine.connect() as conn:
            lsn = _primary_position(conn)
        redis.set(_key_pending(session_id), lsn, ex=PENDING_TTL)
    except Exception:
        log.exception('mark_session_write failed; read-your-write may fall back to primary')


def _get_pending(session_id):
    try:
        return redis.get(_key_pending(session_id))
    except Exception:
        log.exception('redis unavailable when reading pending lsn')
        return None


def _get_read_horizon(session_id):
    try:
        return redis.get(_key_read(session_id))
    except Exception:
        log.exception('redis unavailable when reading read horizon')
        return None


# LSN 文本比较在 Lua 内完成，GET-比较-SET 全程原子，避免并发请求把水位写倒退
_LSN_HORIZON_LUA = """
local function lsn_num(s)
  if not s then return 0 end
  local high, low = string.match(s, '^(%x+)/(%x+)$')
  if not high then return 0 end
  return tonumber(high, 16) * 4294967296 + tonumber(low, 16)
end
local old = redis.call('get', KEYS[1])
if (not old) or (lsn_num(ARGV[1]) >= lsn_num(old)) then
  redis.call('set', KEYS[1], ARGV[1], 'EX', ARGV[2]); return 1
end
return 0
"""
_PENDING_CLEAR_LUA = """
local function lsn_num(s)
  if not s then return 0 end
  local high, low = string.match(s, '^(%x+)/(%x+)$')
  if not high then return 0 end
  return tonumber(high, 16) * 4294967296 + tonumber(low, 16)
end
local pending = redis.call('get', KEYS[1])
if pending and lsn_num(ARGV[1]) >= lsn_num(pending) then
  return redis.call('del', KEYS[1])
end
return 0
"""


def _advance_read_horizon(session_id, lsn_text):
    if not session_id or not lsn_text:
        return
    try:
        redis.eval(_LSN_HORIZON_LUA, 1, _key_read(session_id), lsn_text, READ_TTL)
    except Exception:
        log.exception('advance read horizon failed')


def _clear_pending(session_id, lsn_text):
    """副本已读到不低于等待水位的快照，原子清除读己写标记（期间若又有新提交则保留）。"""
    if not session_id:
        return
    try:
        redis.eval(_PENDING_CLEAR_LUA, 1, _key_pending(session_id), lsn_text)
    except Exception:
        log.exception('clear pending failed')


def route_read(session_id: str):
    """返回 (engine, source 标签, 路由决策说明)。

    source ∈ {'replica','primary'}；两边都不可用时抛 503。
    """
    pending = _get_pending(session_id) if session_id else None
    horizon = _get_read_horizon(session_id) if session_id else None
    replica_ok = replica_engine is not None and _available('replica')

    # 1) 副本可用且没有读己写等待：直接走副本（落后也走，由单调水位兜底）
    if replica_ok and not pending:
        return replica_engine, 'replica', 'replica-default'

    # 2) 有等待水位：探测副本是否追平，短暂轮询
    if replica_ok and pending:
        probe_failed = False
        for attempt in range(REPLICA_WAIT_TRIES):
            try:
                with replica_engine.connect() as conn:
                    replay_lsn, paused, replay_lag = _replica_position(conn)
                if replay_lsn and _lsn_num(None, replay_lsn) >= _lsn_num(None, pending):
                    return replica_engine, 'replica', f'replica-caught-up(try={attempt + 1})'
            except Exception:
                _mark('replica', False)
                probe_failed = True
                log.exception('replica probe failed')
                break
            if attempt < REPLICA_WAIT_TRIES - 1:
                time.sleep(REPLICA_WAIT_INTERVAL)
        # 等不到追平（含暂停回放）或副本探测失败：回主库读己写
        if _available('primary'):
            reason = ('replica-down-fallback-primary' if probe_failed
                      else 'replica-behind-fallback-primary')
            return engine, 'primary', reason

    # 3) 副本不可用：冷却期内直接回主库
    if not replica_ok and _available('primary'):
        return engine, 'primary', 'replica-down-fallback-primary'

    # 4) 主库再探活；仍失败则两边都读不了
    if not _available('primary'):
        try:
            with engine.connect() as conn:
                conn.execute(text('SELECT 1'))
            _mark('primary', True)
            return engine, 'primary', 'primary-recovered'
        except Exception:
            _mark('primary', False)
            raise HTTPException(503, '主库与副本当前都无法读取，请稍后重试')

    return engine, 'primary', 'primary-default'


def read_handover(session_id: str, fetch_callback):
    """在选定节点上以单一事务快照读出移交申请与对应样本量。

    fetch_callback(engine) 在一个连接/事务内完成全部 SELECT，
    保证“查一回就按那一刻已提交的状态”拿到互相对齐的数据。
    """
    chosen, source, reason = route_read(session_id)
    meta = {'source': source, 'reason': reason, 'server_id': SERVER_ID}
    try:
        with chosen.connect() as conn:
            tx = conn.begin()
            items = fetch_callback(conn)
            if source == 'replica':
                replay_lsn, paused, replay_lag = _replica_position(conn)
                meta.update(snapshot_lsn=replay_lsn, replay_paused=paused,
                            replay_lag_bytes=replay_lag, in_recovery=True)
            else:
                primary_lsn = _primary_position(conn)
                meta.update(snapshot_lsn=primary_lsn, in_recovery=False)
            tx.commit()
        _mark(source, True)
    except HTTPException:
        raise
    except Exception:
        # 选中节点读取中途故障：标记并尝试另一节点一次
        _mark(source, False)
        log.exception('read on %s failed, retrying the other node', source)
        other_engine, other_source, other_reason = (
            (engine, 'primary', 'replica-read-error-fallback-primary')
            if source == 'replica' else (replica_engine, 'replica', 'primary-read-error-fallback-replica')
        )
        if other_engine is None or not _available(other_source):
            raise HTTPException(503, '主库与副本当前都无法读取，请稍后重试')
        try:
            with other_engine.connect() as conn:
                tx = conn.begin()
                items = fetch_callback(conn)
                if other_source == 'replica':
                    replay_lsn, paused, replay_lag = _replica_position(conn)
                    meta.update(snapshot_lsn=replay_lsn, replay_paused=paused,
                                replay_lag_bytes=replay_lag, in_recovery=True)
                else:
                    meta.update(snapshot_lsn=_primary_position(conn), in_recovery=False)
                tx.commit()
            _mark(other_source, True)
            meta.update(source=other_source, reason=other_reason)
            source = other_source
        except Exception:
            _mark(other_source, False)
            raise HTTPException(503, '主库与副本当前都无法读取，请稍后重试')

    snapshot = meta.get('snapshot_lsn')
    # 单调读：副本恢复后若其 LSN 低于本会话已读水位，这份数据不能直接交付
    if source == 'replica' and session_id and snapshot:
        horizon = _get_read_horizon(session_id)
        if horizon and _lsn_num(None, snapshot) < _lsn_num(None, horizon):
            log.info('replica lsn %s behind session horizon %s, rerouting to primary', snapshot, horizon)
            items, meta = read_handover_primary(session_id, fetch_callback, meta, 'monotonic-reroute-primary')
        else:
            _advance_read_horizon(session_id, snapshot)
            _clear_pending(session_id, snapshot)
    elif source == 'primary' and session_id and snapshot:
        _advance_read_horizon(session_id, snapshot)
        # 主库读到了自己的新单，读己写等待解除（原子比较，新提交的等待标记保留）
        _clear_pending(session_id, snapshot)
    meta['session_id'] = session_id
    return items, meta


def read_handover_primary(session_id, fetch_callback, meta, reason):
    with engine.connect() as conn:
        tx = conn.begin()
        items = fetch_callback(conn)
        lsn = _primary_position(conn)
        tx.commit()
    _mark('primary', True)
    _advance_read_horizon(session_id, lsn)
    meta.update(source='primary', reason=reason, snapshot_lsn=lsn, in_recovery=False)
    return items, meta


def topology():
    """供页面/脚本展示两个节点的实时状态。"""
    out = {'server_id': SERVER_ID, 'nodes': {}}
    for node, eng in (('primary', engine), ('replica', replica_engine)):
        info = {'configured': eng is not None, 'cooling': not _available(node)}
        if eng is None:
            info['status'] = 'not-configured'
        else:
            try:
                with eng.connect() as conn:
                    if node == 'primary':
                        info.update(status='up', in_recovery=False,
                                    current_lsn=conn.execute(text('SELECT pg_current_wal_lsn()')).scalar_one())
                    else:
                        lsn, paused, lag = _replica_position(conn)
                        info.update(status='up', in_recovery=True, replay_lsn=lsn,
                                    replay_paused=paused, replay_lag_bytes=lag)
                _mark(node, True)
            except Exception as exc:
                _mark(node, False)
                info.update(status='down', error=str(exc)[:120])
        out['nodes'][node] = info
    return out
