import {transfersApi} from '../api/transfers.js';
export const transfersStore={items:[],async refresh(){this.items=await transfersApi.list();return this.items;}};
