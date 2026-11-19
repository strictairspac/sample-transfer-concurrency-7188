import {table} from '../components/common/table.js';
import {esc} from '../utils/html.js';
import {session} from '../hooks/session.js';

const REASON_TEXT={
  'replica-default':'副本常态读取',
  'replica-caught-up':'等待副本追平后读取',
  'replica-behind-fallback-primary':'副本落后，回主库保证读己写',
  'replica-down-fallback-primary':'副本中断，回主库读取',
  'replica-read-error-fallback-primary':'副本读取故障，回主库读取',
  'monotonic-reroute-primary':'副本水位低于会话已读水位，禁止倒退，回主库',
  'primary-default':'主库读取',
  'primary-recovered':'主库恢复',
};

export function readBanner(read){
  if(!read)return '';
  const onReplica=read.source==='replica';
  const lag=read.replay_lag_bytes??0;
  return `<div class="read-banner ${onReplica?'from-replica':'from-primary'}">
    <span class="read-dot"></span>
    <div><b>本次读取：${onReplica?'读副本（物理流复制热备）':'主库（写入节点）'}</b>
    <small>${esc(REASON_TEXT[read.reason]||read.reason)} · 后端实例 ${esc(read.server_id||'-')}
    · 快照 LSN ${esc(read.snapshot_lsn||'-')}${onReplica?` · 回放延迟 ${lag} B${read.replay_paused?' · 回放已暂停':''}`:''}</small></div>
  </div>`;
}

export function topologyPanel(topo){
  if(!topo)return '';
  const node=(key,label)=>{
    const n=topo.nodes[key];
    const up=n.status==='up';
    return `<div class="topo-node ${up?'is-up':'is-down'}">
      <span class="topo-dot"></span><b>${label}</b>
      <small>${up?(n.in_recovery?`热备 · ${n.replay_paused?'回放暂停':'追平中'} · ${esc(n.replay_lsn||'')}`:`主库 · ${esc(n.current_lsn||'')}`):'不可读'+(n.cooling?'（冷却探测中）':'')}</small>
    </div>`;
  };
  return `<div class="topo">${node('primary','主库 PRIMARY')}${node('replica','副本 REPLICA')}</div>`;
}

export function handoverPage(payload,samples,topo){
  const items=payload.items,read=payload.read;
  const isAdmin=session.user.role==='admin';
  const paused=topo?.nodes?.replica?.replay_paused;
  return `<div class="split">
    <section>
      <div class="section-top"><h2>移交台 · 申请与样本量</h2><span>${items.length} 条 / 单事务快照读出</span></div>
      ${readBanner(read)}
      ${table(['申请编号','样本批次 / 位置','移交量','样本台账量','接收方'],items.map(t=>[
        `<strong>${esc(t.code)}</strong><small>${esc(t.note||'')}</small>`,
        `<strong>${esc(t.sample_code)}</strong><small>${esc(t.location)}</small>`,
        `${t.quantity} 份`,
        `<b class="quantity">${t.sample_quantity}</b> 份`,
        esc(t.destination),
      ]))}
      <div class="notice">申请与对应样本台账量在同一个只读事务快照中一起读出，口径固定在查询那一刻已提交的状态；读副本落后不影响主库接单。</div>
      ${topologyPanel(topo)}
      ${isAdmin?`<div class="replica-ctl">
        <span class="eyebrow">REPLICA REPLAY CONTROL（演练用）</span>
        <div class="replica-ctl-row">
          <button id="replica-pause" class="${paused?'is-on':''}" ${paused?'disabled':''}>暂停副本回放</button>
          <button id="replica-resume" class="${!paused?'is-on':''}" ${!paused?'disabled':''}>恢复副本回放</button>
          <span>${paused?'回放暂停中：新登记仅主库可见，读己写自动回主库':'回放正常：页面默认从副本读取'}</span>
        </div></div>`:''}
    </section>
    <section class="form-panel">
      <span class="eyebrow">HANDOVER REGISTER</span>
      <h2>登记移交申请</h2>
      <form id="handover-form">
        <label>申请编号<input name="code" placeholder="shift-20261119" required></label>
        <label>样本批次<select name="sample_id">${samples.map(s=>`<option value="${s.id}">${esc(s.code)} · ${s.quantity} 份</option>`).join('')}</select></label>
        <div class="fields">
          <label>移交份数<input name="quantity" type="number" min="1" value="30" required></label>
          <label>接收实验室<input name="destination" value="理化检测室" required></label>
        </div>
        <label>交接备注<input name="note" value="交接班复核"></label>
        <button class="primary" ${session.user.role==='viewer'?'disabled':''}>保存到主库 +</button>
      </form>
      <p class="help">写入只进主库。提交后本工作台会话登记 LSN 水位，即使立刻换一台后端、副本尚未追平，也会回主库读到这条新单；其他无等待的会话仍按副本当前快照读取。</p>
    </section>
  </div>`;
}
