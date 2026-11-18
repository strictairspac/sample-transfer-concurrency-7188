import {api} from './client.js';
export const trialsApi={list:()=>api('/trials'),create:data=>api('/trials','POST',data)};
