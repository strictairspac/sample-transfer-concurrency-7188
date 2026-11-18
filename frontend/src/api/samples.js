import {api} from './client.js';
export const samplesApi={list:()=>api('/samples'),create:data=>api('/samples','POST',data)};
