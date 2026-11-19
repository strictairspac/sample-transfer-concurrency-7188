#!/bin/bash
# 主库首次初始化时执行（docker-entrypoint-initdb.d，按文件名排序仅跑一次）。
# 1) 创建仅具 REPLICATION 属性的流复制账号，密码取自容器环境变量。
# 2) 放行 db-replica 容器的远程 replication 连接（默认 pg_hba 只允许本机）。
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'replicator') THEN
        CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD '${REPLICA_PASSWORD:-replica_demo_pw}';
    END IF;
END
\$\$;
SQL
echo "host replication replicator all scram-sha-256" >> "$PGDATA/pg_hba.conf"
