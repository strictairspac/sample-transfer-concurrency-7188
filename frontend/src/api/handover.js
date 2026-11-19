import {api} from './client.js';
export const handoverApi={
  list:()=>api('/handover/transfers'),
  topology:()=>api('/handover/topology'),
  replica:(action)=>api('/handover/replica/control','POST',{action}),
};
