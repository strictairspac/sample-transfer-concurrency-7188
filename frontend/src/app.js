import {api,download} from './api/client.js';
import {samplesStore} from './stores/samples.js';import {transfersStore} from './stores/transfers.js';import {trialsStore} from './stores/trials.js';
import {handoverStore} from './stores/handover.js';import {handoverApi} from './api/handover.js';
import {session} from './hooks/session.js';import {feedback} from './hooks/feedback.js';
import {formValues} from './utils/forms.js';import {esc,date} from './utils/html.js';
import {frame} from './components/common/frame.js';import {table} from './components/common/table.js';
import {loginPage} from './pages/login.js';import {samplesPage} from './pages/samples.js';import {transfersPage} from './pages/transfers.js';
import {handoverPage} from './pages/handover.js';
import {trialsPage} from './pages/trials.js';import {evidencePage} from './pages/evidence.js';
import {titles} from './router/routes.js';
let timer=null;const app=document.querySelector('#app');
async function submitForm(id,path,numbers,after){document.querySelector(id)?.addEventListener('submit',async e=>{e.preventDefault();const button=e.target.querySelector('button');button.disabled=true;try{const data=await api(path,'POST',formValues(e.target,numbers));await after(data);feedback('操作完成，记录已保存。');}catch(err){feedback(err.message,true);}finally{button.disabled=false;}})}
async function render(){clearTimeout(timer);if(!session.token){app.innerHTML=loginPage();submitForm('#login','/auth/login',[],async data=>{session.save(data);location.hash='samples';await render()});return;}
try{await api('/auth/me');await Promise.all([samplesStore.refresh(),transfersStore.refresh(),trialsStore.refresh()]);const [page='samples',id]=location.hash.slice(1).split('/');let content;const s=samplesStore.items,t=transfersStore.items,r=trialsStore.items;let trial=null;let handoverPayload=null,topo=null;
if(page==='handover'){[handoverPayload,topo]=await Promise.all([handoverStore.refresh(),handoverStore.refreshTopology()]);content=handoverPage(handoverPayload,s,topo);}
else if(page==='transfers')content=transfersPage(t,s);else if(page==='trials')content=trialsPage(r,t);else if(page==='evidence'){trial=id?await api('/trials/'+id):r[0];content=evidencePage(trial)}else if(page==='audit'){const logs=await api('/audit');content=table(['时间','操作人','动作'],logs.map(x=>[date(x.created_at),esc(x.actor),esc(x.action)]))}else content=samplesPage(s);
const [title,subtitle]=titles[page]||titles.samples;app.innerHTML=frame(page,title,subtitle,content);document.querySelector('#logout').onclick=()=>{session.clear();render()};submitForm('#sample-form','/samples',['quantity'],render);submitForm('#transfer-form','/transfers',['sample_id','quantity'],render);
submitForm('#handover-form','/transfers',['sample_id','quantity'],render);
document.querySelector('#replica-pause')?.addEventListener('click',async()=>{try{await handoverApi.replica('pause');feedback('副本回放已暂停：新登记仅主库可见，读己写将回主库。');await render()}catch(e){feedback(e.message,true)}});
document.querySelector('#replica-resume')?.addEventListener('click',async()=>{try{await handoverApi.replica('resume');feedback('副本回放已恢复，追平后页面自动切回副本。');await render()}catch(e){feedback(e.message,true)}});
submitForm('#trial-form','/trials',['transfer_id','timeout_ms'],async data=>{location.hash='evidence/'+data.id;await render()});document.querySelector('#export')?.addEventListener('click',async e=>{try{await download(e.target.dataset.id);feedback('证据 JSON 已下载')}catch(err){feedback(err.message,true)}});if((page==='evidence'&&trial&&['queued','running'].includes(trial.status))||(page==='trials'&&r.some(x=>['queued','running'].includes(x.status))))timer=setTimeout(render,1500);
}catch(err){if(err.message.includes('登录')||err.message.includes('Not authenticated')){session.clear();await render()}else {if(!document.querySelector('#feedback'))app.innerHTML='<main><div id="feedback"></div><button onclick="location.reload()">重新加载</button></main>';feedback(err.message,true)}}}
window.addEventListener('hashchange',render);render();
