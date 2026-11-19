#!/usr/bin/env python3
"""读写分离口径验收脚本（无第三方 HTTP 库，urllib 直连两个后端实例）。

覆盖：
  1 平时读走副本，主库照常接单
  2 刚交单的会话换一台后端立即看到新单（读己写，Redis 水位跨实例）
  3 暂停回放窗口登记 -> 换后端查询回主库 -> 追平后切回副本
  4 副本落后但无等待水位的会话仍走副本
  5 单调读：副本恢复后数据不能倒退到会话已读水位之前
  6 副本中断回主库、恢复切回副本；两边都不可读返回 503
  7 单事务快照：申请与对应样本量按同一已提交时刻一起读出
所有阶段带时间戳日志，每处外部调用都有超时，避免静默挂起。
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
import uuid

import psycopg

B1 = os.environ.get('BACKEND_1', 'http://127.0.0.1:19242')
B2 = os.environ.get('BACKEND_2', 'http://127.0.0.1:19243')
PG_BIN = os.environ['PG_BIN']
REDIS_CLI = os.environ.get('REDIS_CLI', 'redis-cli')
REDIS_PORT = os.environ.get('REDIS_PORT', '17744')
PGDATA_P = os.environ['PGDATA_PRIMARY']
PGDATA_R = os.environ['PGDATA_REPLICA']
CONNECT_KW = dict(connect_timeout=5, options='-c statement_timeout=8000')
PRIMARY_DSN = 'host=127.0.0.1 port=17742 user=locklab dbname=locklab connect_timeout=5'
REPLICA_DSN = 'host=127.0.0.1 port=17743 user=locklab dbname=locklab connect_timeout=5'

results = []


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def check(name, cond, detail=''):
    results.append((name, bool(cond), detail))
    log(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")


def call(method, url, token=None, sid=None, body=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if sid:
        headers['X-Session-Id'] = sid
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None,
                                 headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def login(role):
    _, data = call('POST', B1 + '/auth/login',
                   body={'email': f'{role}@qingyu.test', 'password': 'Demo2026!'})
    return data['token']


def pgctl(action, datadir):
    log(f'  pg_ctl {action} {datadir.split("/")[-1]}')
    env = dict(os.environ)
    # -l 把 postgres 输出重定向到文件：否则子进程继承 subprocess 管道并长期持有，
    # communicate() 等不到 EOF 会一直阻塞。就绪与否由 wait_*_ready 连接探活判定。
    logfile = f'/tmp/rwlab/logs/{datadir.split("/")[-1]}-verify.log'
    try:
        out = subprocess.run([f'{PG_BIN}/pg_ctl', '-D', datadir, '-l', logfile,
                              action, '-t', '30'],
                             capture_output=True, text=True, env=env, timeout=45)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f'pg_ctl {action} timed out')
    if out.returncode != 0:
        raise RuntimeError(f'pg_ctl {action} failed: {out.stderr or out.stdout}')


def wait_primary_ready(timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with psycopg.connect(PRIMARY_DSN, **CONNECT_KW) as p:
                if not p.execute('SELECT pg_is_in_recovery()').fetchone()[0]:
                    return
        except psycopg.OperationalError:
            pass
        time.sleep(0.3)
    raise RuntimeError('primary did not become ready')


def replay(action, token):
    status, data = call('POST', B1 + '/handover/replica/control', token=token,
                        body={'action': action})
    assert status == 200, (status, data)
    log(f'  replay {action} -> paused={data["replay_paused"]}')
    return data['replay_paused']


def wait_replay_caught_up(timeout=20):
    deadline = time.time() + timeout
    with psycopg.connect(PRIMARY_DSN, **CONNECT_KW) as p:
        p_lsn = str(p.execute('SELECT pg_current_wal_lsn()').fetchone()[0])
    while time.time() < deadline:
        try:
            with psycopg.connect(REPLICA_DSN, **CONNECT_KW) as r:
                row = r.execute("SELECT pg_last_wal_replay_lsn(), pg_is_wal_replay_paused()").fetchone()
            if row[0] is not None and str(row[0]) >= p_lsn and not row[1]:
                return
        except psycopg.OperationalError:
            pass
        time.sleep(0.3)
    raise RuntimeError('replica did not catch up within timeout')


def wait_replica_ready(timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with psycopg.connect(REPLICA_DSN, **CONNECT_KW) as r:
                if r.execute('SELECT pg_is_in_recovery()').fetchone()[0]:
                    return
        except psycopg.OperationalError:
            pass
        time.sleep(0.3)
    raise RuntimeError('replica did not become ready')


def main():
    log('=== 阶段 0：登录与会话准备 ===')
    op = login('operator')
    admin = login('admin')
    sid_a = str(uuid.uuid4())
    sid_b = str(uuid.uuid4())
    stamp = time.strftime('%H%M%S')
    log(f'sid_a={sid_a} sid_b={sid_b}')

    log('=== 阶段 1：平时读走副本 ===')
    status, data = call('GET', B1 + '/handover/transfers', op, sid_a)
    check('1 平时移交页从副本读取', status == 200 and data['read']['source'] == 'replica',
          f"reason={data['read']['reason']} lsn={data['read']['snapshot_lsn']}")
    check('1 申请与样本量同行读出',
          all('sample_quantity' in x and x['sample_quantity'] > 0 for x in data['items']),
          f"rows={len(data['items'])}")

    log('=== 阶段 2：读己写，跨后端实例 ===')
    code_ryw = f'MOVE-RYW-{stamp}'
    status, created = call('POST', B1 + '/transfers', op, sid_a, {
        'code': code_ryw, 'sample_id': 1, 'destination': '理化检测室',
        'quantity': 10, 'note': '读己写跨后端'})
    check('2 主库照常接单（副本不参与写）', status == 201 and created['code'] == code_ryw)
    status, data = call('GET', B2 + '/handover/transfers', op, sid_a)
    codes = [x['code'] for x in data['items']]
    check('2 换一台后端立即看到刚登记的新单', code_ryw in codes,
          f"source={data['read']['source']} reason={data['read']['reason']}")
    check('2 响应标明本次读取来源', data['read']['source'] in ('replica', 'primary')
          and data['read']['snapshot_lsn'])

    log('=== 阶段 3：暂停回放时登记 → 换后端查询 → 追平后切回 ===')
    paused = replay('pause', admin)
    check('3 副本回放已暂停', paused is True)
    code_pause = f'MOVE-PAU-{stamp}'
    status, _ = call('POST', B1 + '/transfers', op, sid_a, {
        'code': code_pause, 'sample_id': 2, 'destination': '金属分析室',
        'quantity': 5, 'note': '回放暂停窗口登记'})
    check('3 暂停回放期间主库仍可登记', status == 201)
    t0 = time.time()
    status, data = call('GET', B2 + '/handover/transfers', op, sid_a)
    elapsed = time.time() - t0
    codes = [x['code'] for x in data['items']]
    check('3 换后端查询立即看到暂停窗口的新单（回主库）',
          status == 200 and code_pause in codes and data['read']['source'] == 'primary',
          f"reason={data['read']['reason']} 等待={elapsed:.1f}s")
    status, data_b = call('GET', B2 + '/handover/transfers', op, sid_b)
    check('4 副本落后时无等待水位的会话仍走副本',
          data_b['read']['source'] == 'replica'
          and code_pause not in [x['code'] for x in data_b['items']],
          f"paused={data_b['read'].get('replay_paused')}")
    replay('resume', admin)
    wait_replay_caught_up()
    time.sleep(0.5)
    status, data = call('GET', B1 + '/handover/transfers', op, sid_a)
    codes = [x['code'] for x in data['items']]
    check('3 追平后切回副本且新单可见',
          data['read']['source'] == 'replica' and code_pause in codes,
          f"reason={data['read']['reason']} lag={data['read']['replay_lag_bytes']}B")

    log('=== 阶段 5：单调读，水位不可倒退 ===')
    # 暂停回放制造稳定 LSN 缺口：pause 状态不跨重启持久化，因此本场景不重启备库
    replay('pause', admin)
    _, before = call('GET', B1 + '/handover/transfers', op, sid_b)
    check('5 预备：旁观会话在暂停的副本上建立旧读水位', before['read']['source'] == 'replica')
    code_mono = f'MOVE-MONO-{stamp}'
    status, _ = call('POST', B1 + '/transfers', op, sid_a, {
        'code': code_mono, 'sample_id': 3, 'destination': '复核室',
        'quantity': 3, 'note': '单调读演练'})
    check('5 新单已在主库提交', status == 201)
    # sid_a 有读己写等待 -> 回主库；sid_b 无等待 -> 仍走落后的副本
    _, a_read = call('GET', B1 + '/handover/transfers', op, sid_a)
    check('5 登记会话在落后窗口回主库读到新单',
          a_read['read']['source'] == 'primary'
          and code_mono in [x['code'] for x in a_read['items']],
          f"reason={a_read['read']['reason']}")
    _, b_old = call('GET', B1 + '/handover/transfers', op, sid_b)
    check('5 旁观会话仍读落后副本，看不到新单（旧水位）',
          b_old['read']['source'] == 'replica'
          and code_mono not in [x['code'] for x in b_old['items']])
    # 备库中断回主库：会话已读水位推进到主库 LSN
    pgctl('stop', PGDATA_R)
    time.sleep(0.5)
    _, down_read = call('GET', B1 + '/handover/transfers', op, sid_b)
    check('6 副本中断回主库', down_read['read']['source'] == 'primary'
          and code_mono in [x['code'] for x in down_read['items']],
          f"reason={down_read['read']['reason']}")
    pgctl('start', PGDATA_R)
    wait_replica_ready()
    wait_replay_caught_up()
    # 单调读判定的本质是 LSN 水位比较：把会话已读水位预置到主库当前 LSN 之后
    # （等价于"这个会话此前已读过比当前副本更新的快照"，如刚从主库故障切换回来）
    with psycopg.connect(PRIMARY_DSN, **CONNECT_KW) as p:
        future_lsn = str(p.execute(
            "SELECT pg_current_wal_lsn() + 1000000").fetchone()[0])
    redis_env = {**os.environ}
    subprocess.run([REDIS_CLI, '-p', REDIS_PORT, 'set',
                    f'locklab:readsplit:{sid_b}:read_lsn', future_lsn],
                   check=True, env=redis_env)
    log(f'  预置 sid_b 已读水位={future_lsn}（高于当前副本 LSN）')
    time.sleep(2.5)  # 穿过节点冷却，让后端重新尝试副本
    _, stale_try = call('GET', B1 + '/handover/transfers', op, sid_b)
    check('5 副本水位落后于会话已读水位时不倒退（重路由主库）',
          stale_try['read']['source'] == 'primary'
          and code_mono in [x['code'] for x in stale_try['items']],
          f"reason={stale_try['read']['reason']}")
    # 清掉预置水位，恢复正常路由
    subprocess.run([REDIS_CLI, '-p', REDIS_PORT, 'del',
                    f'locklab:readsplit:{sid_b}:read_lsn'], check=True,
                   env=redis_env)
    _, back = call('GET', B1 + '/handover/transfers', op, sid_b)
    check('6 副本恢复后自动切回副本', back['read']['source'] == 'replica'
          and code_mono in [x['code'] for x in back['items']],
          f"reason={back['read']['reason']}")

    log('=== 阶段 6：双库故障与恢复 ===')
    pgctl('stop', PGDATA_R)
    pgctl('stop', PGDATA_P)
    time.sleep(2.5)  # 穿过冷却，让两个节点都被实测判死
    status, err = call('GET', B1 + '/handover/transfers', op, sid_b)
    check('7 主副都不可读时明确报错 503（不伪装成 401）',
          status == 503 and '数据库' in err.get('detail', ''),
          f"status={status} detail={err.get('detail')}")
    # 对照：携带无效 token 且库不可用时仍应是 401，凭证失败与基础设施失败语义分开
    status_bad, _ = call('GET', B1 + '/handover/transfers', token='not-a-valid-jwt', sid=sid_b)
    check('7 无效令牌在库不可用时仍返回 401', status_bad == 401)
    pgctl('start', PGDATA_P)
    pgctl('start', PGDATA_R)
    wait_primary_ready()
    wait_replica_ready()
    wait_replay_caught_up()
    deadline = time.time() + 15
    status, data = 0, {}
    while time.time() < deadline:
        status, data = call('GET', B1 + '/handover/transfers', op, sid_b)
        if status == 200 and data['read']['source'] == 'replica':
            break
        time.sleep(1)
    check('7 数据库恢复后页面自动复原并切回副本', status == 200
          and data['read']['source'] == 'replica', f"last_source={data.get('read', {}).get('source')}")

    log('=== 阶段 7：单事务快照一致性 ===')
    with psycopg.connect(REPLICA_DSN, **CONNECT_KW) as snap, \
            psycopg.connect(PRIMARY_DSN, **CONNECT_KW) as writer:
        snap.execute('BEGIN ISOLATION LEVEL REPEATABLE READ')
        row = snap.execute(
            'SELECT t.quantity, s.quantity FROM transfers t JOIN samples s ON s.id=t.sample_id '
            "WHERE t.code=%s", (code_pause,)).fetchone()
        snap_qty = row[1]
        writer.execute('UPDATE samples SET quantity=quantity+7 WHERE id=2')
        writer.commit()
        row2 = snap.execute(
            'SELECT t.quantity, s.quantity FROM transfers t JOIN samples s ON s.id=t.sample_id '
            "WHERE t.code=%s", (code_pause,)).fetchone()
        snap.rollback()
        wait_replay_caught_up()
        with psycopg.connect(REPLICA_DSN, **CONNECT_KW) as fresh:
            new_qty = fresh.execute('SELECT quantity FROM samples WHERE id=2').fetchone()[0]
    check('8 同一事务快照内申请与样本量保持同一时刻状态',
          row2[1] == snap_qty and new_qty == snap_qty + 7,
          f'snapshot={snap_qty} 新事务={new_qty}')

    log('=== 阶段 8：拓扑可见性 ===')
    status, topo = call('GET', B1 + '/handover/topology', admin)
    check('9 拓扑接口展示两节点状态', status == 200
          and topo['nodes']['primary']['status'] == 'up'
          and topo['nodes']['replica']['status'] == 'up',
          json.dumps(topo['nodes'], ensure_ascii=False))

    failed = [n for n, ok, _ in results if not ok]
    log(f"==== {len(results) - len(failed)}/{len(results)} passed ====")
    if failed:
        log('FAILED: ' + ', '.join(failed))
        sys.exit(1)


if __name__ == '__main__':
    main()
