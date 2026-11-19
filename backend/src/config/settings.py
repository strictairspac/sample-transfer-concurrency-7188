import os
DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql+psycopg://locklab:locklab_demo_pw@localhost:17742/locklab')
# 读副本；未配置时读请求全部回主库（单库开发模式）
REPLICA_URL = os.getenv('REPLICA_URL', 'postgresql+psycopg://locklab:locklab_demo_pw@localhost:17743/locklab')
REDIS_URL = os.getenv('REDIS_URL', 'redis://127.0.0.1:17744/0')
JWT_SECRET = os.getenv('JWT_SECRET', 'local-lock-lab-change-before-public-use')
# 演练证据 JSON 输出目录；docker 下为挂载卷 /artifacts，本地裸跑可改到可写目录
ARTIFACTS_DIR = os.getenv('ARTIFACTS_DIR', '/artifacts')
# 副本落后时，等待追平的轮询次数与间隔
REPLICA_WAIT_TRIES = int(os.getenv('REPLICA_WAIT_TRIES', '20'))
REPLICA_WAIT_INTERVAL = float(os.getenv('REPLICA_WAIT_INTERVAL', '0.1'))
# 节点探活失败后的冷却秒数，避免每个请求都重连坏节点
NODE_COOLDOWN_SECONDS = float(os.getenv('NODE_COOLDOWN_SECONDS', '2'))
