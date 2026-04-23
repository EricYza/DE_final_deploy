# 实验室 MicroK8s 部署清单（精简版）

## 0. 已知环境

- `node1 = 10.10.5.21`，作为 control plane
- `node2 = 10.10.5.22`，加入 `node1`
- 两台机器都安装了 `microk8s`
- 你主要通过 `ssh` 登录 `node1`
- 双节点环境下，镜像地址统一用：

```text
10.10.5.21:32000/<image>:<tag>
```

不要再用：

```text
localhost:32000/<image>:<tag>
```

## 1. 本地电脑：把项目传到 node1

```powershell
scp -r F:\DE_final <你的用户名>@10.10.5.21:~
```

含义：把整个项目目录传到 `node1`。

## 2. node1：登录并进入项目目录

```bash
ssh <你的用户名>@10.10.5.21
cd ~/DE_final/PRJ_STABLE_FRAMEWORK
```

含义：登录 `node1`，进入项目目录。

## 3. node1：确认 MicroK8s 集群状态

```bash
microk8s kubectl get nodes -o wide
```

含义：确认 `node1`、`node2` 都在集群里，并且最好都是 `Ready`。

如果 `node2` 还没加入：

```bash
microk8s add-node
```

含义：在 `node1` 生成 join 命令，然后去 `node2` 上执行。

## 4. node1：启用 registry / dns / storage

```bash
microk8s enable dns
microk8s enable storage
microk8s enable registry
```

含义：启用 DNS、存储、内置 registry。

检查 registry：

```bash
microk8s kubectl get svc -A | grep 32000
microk8s kubectl get pods -A | grep registry
```

含义：确认 `32000` 端口的 registry 已经起来。

## 5. node2：配置能从 node1 的 registry 拉镜像

```bash
ssh <你的用户名>@10.10.5.22
```

创建配置目录：

```bash
sudo mkdir -p /var/snap/microk8s/current/args/certs.d/10.10.5.21:32000
```

写入 `hosts.toml`：

```bash
cat <<'EOF' | sudo tee /var/snap/microk8s/current/args/certs.d/10.10.5.21:32000/hosts.toml
server = "http://10.10.5.21:32000"

[host."http://10.10.5.21:32000"]
capabilities = ["pull", "resolve"]
EOF
```

重启 MicroK8s：

```bash
microk8s stop
microk8s start
```

含义：让 `node2` 能从 `10.10.5.21:32000` 拉镜像，避免 `ImagePullBackOff`。

## 6. node1：给 node1 打标签

先看节点名：

```bash
microk8s kubectl get nodes
```

给 `node1` 打标签：

```bash
microk8s kubectl label node <node1-name> node-role.kubernetes.io/storage= --overwrite
microk8s kubectl label node <node1-name> node-role.kubernetes.io/compute= --overwrite
```

含义：先把主要 workload 放在 `node1`，第一轮更稳。

## 7. 代码里明天需要改的地方

### 必改

- `scripts/build_and_push.sh`
- `k8s/airflow.yaml`

把其中的：

```text
localhost:32000
```

改成：

```text
10.10.5.21:32000
```

### 视情况改

- `k8s/postgres.yaml`

如果实验室环境能直接拉公网镜像，可以写：

```text
postgres:16-bookworm
```

如果想也走本地 registry，可以写：

```text
10.10.5.21:32000/postgres:16-bookworm
```

### 一般不用改

- `dags/*.py`
- `common/*.py`

## 8. 两种 build / push 方案

### 方案 A：node1 上有 Docker

先检查：

```bash
docker --version
which docker
```

如果有 Docker，在 `node1` 的项目目录执行：

```bash
docker build -t 10.10.5.21:32000/climate-airflow-stable:latest -f ./docker/airflow/Dockerfile .
docker push 10.10.5.21:32000/climate-airflow-stable:latest
```

含义：在 `node1` 上 build 并推送 Airflow 镜像到 MicroK8s registry。

如果 PostgreSQL 也要走本地 registry：

```bash
docker pull postgres:16-bookworm
docker tag postgres:16-bookworm 10.10.5.21:32000/postgres:16-bookworm
docker push 10.10.5.21:32000/postgres:16-bookworm
```

### 方案 B：node1 上没有 Docker，用你自己的电脑 build / push

在你自己的电脑上执行：

```powershell
docker build -t 10.10.5.21:32000/climate-airflow-stable:latest -f .\PRJ_STABLE_FRAMEWORK\docker\airflow\Dockerfile .\PRJ_STABLE_FRAMEWORK
docker push 10.10.5.21:32000/climate-airflow-stable:latest
```

含义：在你自己的电脑上 build，然后直接推到 `node1` 的 registry。

如果这一步报 insecure registry / HTTP registry 错误，说明你本机 Docker 还需要信任 `10.10.5.21:32000`。

## 9. node1：部署 Kubernetes 资源

```bash
cd ~/DE_final/PRJ_STABLE_FRAMEWORK
microk8s kubectl apply -f ./k8s/namespace.yaml -f ./k8s/secrets.yaml
microk8s kubectl apply -f ./k8s/postgres.yaml -f ./k8s/minio.yaml
microk8s kubectl apply -f ./k8s/airflow.yaml
```

含义：部署 namespace、secrets、PostgreSQL、MinIO、Airflow。

## 10. node1：检查 Pod 是否正常

```bash
microk8s kubectl -n climate-stable get pods -o wide
```

重点看：

- `climate-postgres`
- `climate-minio`
- `climate-airflow`

## 11. node1：只触发 collect 做自动串联测试

```bash
microk8s kubectl -n climate-stable exec deployment/climate-airflow -c airflow-scheduler -- airflow dags trigger climate_collect_weather -e 2026-03-17T05:30:00+00:00 -r collect_auto_20260316
```

含义：只触发 `collect`，后面的 `prepare` 和 `publish` 会自动执行。

## 12. node1：检查 Airflow 三段是否自动串起来

```bash
microk8s kubectl -n climate-stable exec deployment/climate-airflow -c airflow-scheduler -- airflow dags list-runs -d climate_collect_weather
microk8s kubectl -n climate-stable exec deployment/climate-airflow -c airflow-scheduler -- airflow dags list-runs -d climate_prepare_weather
microk8s kubectl -n climate-stable exec deployment/climate-airflow -c airflow-scheduler -- airflow dags list-runs -d climate_publish_analytics
```

含义：确认 `collect` 成功后，`prepare` 和 `publish` 被自动触发并成功。

## 13. node1：检查 MinIO 与 PostgreSQL 结果

检查 `bronze / silver`：

```bash
cat <<'PY' | microk8s kubectl -n climate-stable exec -i deployment/climate-airflow -c airflow-scheduler -- python -
from common.minio_utils import get_s3_client
s3 = get_s3_client()
bronze = s3.list_objects_v2(Bucket="bronze")
silver = s3.list_objects_v2(Bucket="silver")
bronze_count = sum(1 for o in bronze.get("Contents", []) if o["Key"].endswith("20260316.json"))
silver_count = sum(1 for o in silver.get("Contents", []) if o["Key"].endswith("20260316.parquet"))
print(f"bronze_files_20260316 = {bronze_count}")
print(f"silver_files_20260316 = {silver_count}")
PY
```

检查 PostgreSQL：

```bash
microk8s kubectl -n climate-stable exec deployment/climate-postgres -- psql -U climate_user -d climate_analytics -c "select count(*) as rows_2026_03_16 from hourly_observations where timestamp >= '2026-03-16' and timestamp < '2026-03-17';"
```

预期结果：

- `bronze_files_20260316 = 5`
- `silver_files_20260316 = 5`
- PostgreSQL `rows_2026_03_16 = 120`

## 14. 明天最少要记住的 5 件事

1. registry 用 `10.10.5.21:32000`，不要用 `localhost:32000`
2. `node1` 先执行 `microk8s enable registry`
3. `node2` 要配 `hosts.toml`
4. Airflow 镜像要么在 `node1` build/push，要么在你自己的电脑 build/push
5. 只触发 `climate_collect_weather`，后面会自动串到 `prepare/publish`
