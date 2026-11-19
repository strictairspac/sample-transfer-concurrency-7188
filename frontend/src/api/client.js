import {session} from '../hooks/session.js';
// 稳定的工作台会话标识：浏览器本地生成，跨页面刷新保持；
// 后端把读己写水位与已读 LSN 挂在它名下，换任何一台无状态后端都能找回。
export const workstation={
  get id(){
    let v=localStorage.getItem('locklab-workstation-id');
    if(!v){v=(crypto.randomUUID?crypto.randomUUID():'ws-'+Date.now()+'-'+Math.random().toString(16).slice(2));localStorage.setItem('locklab-workstation-id',v);}
    return v;
  }
};
export async function api(path,method='GET',data){
  const res=await fetch('/api'+path,{method,headers:{'Content-Type':'application/json','X-Session-Id':workstation.id,...(session.token?{Authorization:'Bearer '+session.token}:{})},body:data?JSON.stringify(data):undefined});
  const result=await res.json();
  if(!res.ok)throw Error(typeof result.detail==='string'?result.detail:'输入格式有误，请检查数值和必填项');
  return result;
}
export async function download(id){const res=await fetch('/api/trials/'+id+'/export',{headers:{Authorization:'Bearer '+session.token}});if(!res.ok)throw Error('导出失败，请重新登录后重试');const url=URL.createObjectURL(await res.blob());const a=document.createElement('a');a.href=url;a.download='trial-'+id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
