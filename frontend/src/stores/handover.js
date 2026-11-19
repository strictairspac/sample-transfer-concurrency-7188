import {handoverApi} from '../api/handover.js';
// items 与 read 路由元数据分开存放，页面顶部展示“这次从哪读”
export const handoverStore={
  items:[],read:null,topology:null,
  async refresh(){
    const data=await handoverApi.list();
    this.items=data.items;this.read=data.read;
    return data;
  },
  async refreshTopology(){this.topology=await handoverApi.topology();return this.topology;},
};
