# 实验样本流转并发台

青屿检测样本中心（24 人）的样本移交复核工作台。原生 HTML/CSS/JavaScript 前端，Python FastAPI 后端，真实 PostgreSQL 两连接事务与行锁演练。

## 场景和模块

团队原先通过纸质交接单、SQL 截图和 Excel 交接。三处痛点：样本版本难统一、并发移交容易重复、超时后无法确认回执是否回滚。上游为样本接收记录，本站承接样本登记、移交申请、并发复核和证据导出，下游为理化检测室与复核员。

- 样本台账：批次、可用份数、保管位置，可录入。
- 流转申请：关联样本、指定数量和接收实验室。
- 移交台（读写分离）：主库接单、物理流复制副本承接页面读取；会话级读己写与单调读水位，页面标明每次读取来源、快照 LSN 与回放延迟。
- 并发执行：Redis 队列、独立 Python worker、可设锁等待上限。
- 冲突证据：Backend PID、SQLSTATE、回执回滚、数量守恒与 JSON 导出。
- 管理员审计：登录、写入和执行动作。

## 启动

```sh
cp .env.example .env
docker compose up -d --build
docker compose exec -T backend python -m scripts.seed_demo
```

前端 http://localhost:18242 ，后端 http://localhost:19242/docs （实例 2 http://localhost:19243/docs），数据库 localhost:17742（主）/17743（副本）。

## 移交台读写分离口径

写入全部进主库，页面读默认走物理流复制热备，所有选路状态对页面可见（`GET /handover/transfers` 的 `read` 字段：来源、原因、后端实例、快照 LSN、回放暂停/延迟）。

1. **常态读副本**：没有待读己写水位的会话直接读副本；副本落后但无新数据要等时仍走副本，只读已提交旧数据。
2. **读己写**：写事务提交后把提交 LSN 记入 Redis（`X-Session-Id` 标识工作台，浏览器持久化）。下次读先探测副本回放 LSN，追平则读副本，等待超时（默认 20×0.1s）则回主库。水位存 Redis 而非后端内存，**刚交单的会话换到任意一台后端都能立即看到新单**。
3. **单调读**：每个会话维护已读 LSN 高水位；副本故障回主库、恢复后若副本 LSN 低于会话已读水位，重路由主库，**同一会话看到的数据不倒退**。
4. **故障切换**：副本中断回主库，恢复并穿过冷却后自动切回副本；主库也不可用时返回 503（凭证问题仍返回 401，语义分开）。
5. **单事务快照**：移交申请与对应样本台账量在同一只读事务的两条 SELECT 中读出，固定在查询那一刻已提交的状态，互相对齐。
6. **回放演练**：管理员可用 `POST /handover/replica/control {action:pause|resume}` 或移交页按钮暂停/恢复副本回放；`GET /handover/topology` 返回两节点实时状态。

验收（无 docker 环境时用系统包搭主/副库 + Redis 后运行；要求两后端实例与 Redis 可达）：

```sh
python scripts/verify_read_split.py   # 22 项：常态副本、跨后端读己写、暂停窗口、单调读、双故障 503、快照一致性
```

|角色|账号|密码|权限|
|---|---|---|---|
|admin|admin@qingyu.test|Demo2026!|全业务写入、执行、审计|
|operator|operator@qingyu.test|Demo2026!|创建样本、申请、执行、导出|
|viewer|viewer@qingyu.test|Demo2026!|只读、导出；写 API 返回403|

## 一天剧本

09:00 operator 登录；09:05 复核 `QY-261117-A` 近海水质留样 120 份，位置冷藏间 A-03；09:10 查看申请 `MOVE-1117-01`，接收理化检测室 30 份；09:15 新建 `shift-20261117` 流转申请；09:20 以该申请发起 250ms 并发演练；09:25 查看 A 提交 30、B SQLSTATE 55P03 回滚，90+30=120；09:30 导出 `trial-1.json`；09:40 viewer 复核证据；10:00 admin 查看审计。

## 算法与业务边界

这是**真实数据库行锁的受控演练**。每次以样本可用量建立独立 `trial_balances` 镜像行，不扣减原台账。A 使用 `SELECT FOR UPDATE` 持锁并写未提交回执；Python threading.Event 确保 B 在 A 持锁期间运行。B 先写自己的回执，再以指定 `lock_timeout` 请求同一行锁；PostgreSQL 返回 SQLSTATE 55P03 后完整 ROLLBACK。B 结束事件释放 A，A 扣减 remaining、增加 received 并 COMMIT。没有随机结果、sleep 假等待或假 SQL 计划。

输入：流转申请、50–2000ms 锁等待上限。输出：实际 PID、错误码、等待耗时、剩余量、接收量、回执数和 SHA256。口径为 `remaining + received == initial`、remaining非负、仅A回执一条；B回执必须为零。等待耗时受主机负载影响，SHA包含PID和实际耗时因此每次不同。算法输入和最终不变量可复现。失败态包括申请量超限、数据库错误、线程协调超时、队列不可用；页面显示错误并允许创建新演练。

单 worker 顺序运行任务，无生产重试/崩溃恢复调度；worker中断时 running 任务需要人工重建，不能用作生产样本库存扣减。

## 工程说明

|服务|技术|职责|
|---|---|---|
|frontend|原生HTML/CSS/JS + Nginx|4业务页+移交台+登录，反向代理 /api/，请求携带 X-Session-Id|
|backend×2|Python3.11/FastAPI/SQLAlchemy/PyJWT|无状态实例×2；校验、权限、审计、读写分离选路、持久化|
|worker|Python3.11/psycopg|真实双连接锁竞争|
|db|PostgreSQL16|主库：全部写入、会话提交 LSN 来源、读回退|
|db-replica|PostgreSQL16 热备|pg_basebackup 物理流复制，承接移交台只读事务，可暂停回放演练|
|redis|Redis7|持久队列 + 跨后端会话水位（pending LSN / 已读 LSN 高水位）|

目录：`frontend/src/{api,stores,types,components/common,hooks,pages,router,utils,constants}`；`backend/src/{routes,controllers,services,models,schemas,middlewares,utils,constants,config}`；`backend/scripts/seed_demo.py`、`backend/scripts/verify_read_split.py`；`backend/migrations/001_initial.sql`、`002_init_replication.sh`（compose 首启时建复制账号并放行 pg_hba）。
读写分离核心在 `backend/src/services/read_split.py`（LSN 水位、健康冷却、读己写等待、单调读重路由）与 `routes/handover.py`、`services/handover.py`（单事务快照读出）；前端入口为 `pages/handover.js`、`stores/handover.js`、`api/handover.js`。
核心实体 Sample、Transfer、Trial 各有模型、schema、服务、路由、API、store与页面；共享 table/badge/frame 组件。共享枚举 roles/run states 位于后端 `src/constants/enums.py`、模型/权限引用和前端 `src/constants/enums.js`、frame/badge；状态枚举 queued/running/completed/failed。两个后端中间件：request_id/errors。

环境变量见 `.env.example`：COMPOSE_PROJECT_NAME、四端口、DB_PASSWORD、REPLICA_PASSWORD、JWT_SECRET、SEED_DEMO。后端另支持 REPLICA_URL（留空则单库模式，读全走主库）、REPLICA_WAIT_TRIES/REPLICA_WAIT_INTERVAL（追平等待）、NODE_COOLDOWN_SECONDS（故障节点冷却）、SERVER_ID（实例标识，随读取元数据返回）。默认密码仅用于本地演示；JWT8小时有效。seed按自然键仅插入缺失行，不覆盖修改。所有7服务有健康检查，backend/worker依赖健康数据库/Redis，frontend依赖backend；db/redis/artifacts使用命名卷。停止及清空本项目数据：`docker compose down -v --remove-orphans`。

本地开发：Python3.11安装 backend/requirements.txt；设置 DATABASE_URL/REDIS_URL 后 `uvicorn src.main:app --reload`（backend目录）；worker `python -m src.worker`；前端用Nginx同源代理后端。前端双字体经Google Fonts加载，离线使用浏览器字体回退；业务不依赖外部字体。

## License
MIT，见 LICENSE。测试脚本、截图与实际结果位于源码外 `../../test-artifacts/gb-4103/`。
