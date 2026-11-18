import {trialsApi} from '../api/trials.js';
export const trialsStore={items:[],async refresh(){this.items=await trialsApi.list();return this.items;}};
