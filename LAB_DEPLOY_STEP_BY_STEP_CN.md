# 实验室双节点 MicroK8s 部署步骤（一步一条命令版）

这份文档按今天已经跑通的流程整理。

适用环境：

- `node1 = 10.10.5.21`
- `node2 = 10.10.5.22`
- 用户名固定为 `student`
- `node1` 作为 control plane
- 第一轮部署先把 `compute + storage` 都放在 `node1`
- 你的本地电脑负责 `docker build` 和 `push`
- Airflow 镜像地址固定为：

```text
10.10.5.21:32000/climate-airflow-stable:latest
```

- PostgreSQL 直接从公网拉：

```text
postgres:16-bookworm
```

---

## 0. 把项目传到 node1

本地 PowerShell：

```powershell
cd F:\DE_final_f
```

```powershell
ssh student@10.10.5.21 "mkdir -p ~/DE_final_f"
```

```powershell
scp -r .\PRJ_STABLE_FRAMEWORK student@10.10.5.21:~/DE_final_f/
```

---

## 1. node1：确认 MicroK8s 正常

```bash
ssh student@10.10.5.21
```

```bash
sudo microk8s status --wait-ready
```

```bash
sudo microk8s kubectl get nodes -o wide
```

预期：

- `node1` 和 `node2` 都是 `Ready`

---

## 2. node1：启用 registry

```bash
sudo microk8s enable registry
```

```bash
sudo microk8s kubectl get svc -A | grep 32000
```

```bash
sudo microk8s kubectl get pods -A | grep registry
```

如果刚启用完 Pod 还没起来，等 20 秒后再查：

```bash
sudo microk8s kubectl -n container-registry get pods,pvc -o wide
```

预期：

- `registry` 的 `Service` 出现 `5000:32000/TCP`
- `registry` Pod 最终变成 `Running`

---

## 3. node1：给 node2 生成 join 命令

```bash
sudo microk8s add-node
```

说明：

- 它会打印几条 `join` 命令
- 去 `node2` 上执行那条带 `--worker` 的

如果你的 `node2` 本来就已经在集群里，这一步可以跳过。

---

## 4. node2：加入集群

```bash
ssh student@10.10.5.22
```

```bash
sudo microk8s status --wait-ready
```

把 `node1` 上刚才打印出来的这类命令粘贴执行：

```bash
sudo microk8s join 10.10.5.21:25000/<token>/<hash> --worker
```

---

## 5. node1：配置从 10.10.5.21:32000 拉镜像

```bash
ssh student@10.10.5.21
```

```bash
sudo mkdir -p /var/snap/microk8s/current/args/certs.d/10.10.5.21:32000
```

```bash
cat <<'EOF' | sudo tee /var/snap/microk8s/current/args/certs.d/10.10.5.21:32000/hosts.toml
server = "http://10.10.5.21:32000"

[host."http://10.10.5.21:32000"]
capabilities = ["pull", "resolve"]
EOF
```

```bash
sudo microk8s stop
```

```bash
sudo microk8s start
```

```bash
sudo microk8s status --wait-ready
```

---

## 6. node2：配置从 10.10.5.21:32000 拉镜像

```bash
ssh student@10.10.5.22
```

```bash
sudo mkdir -p /var/snap/microk8s/current/args/certs.d/10.10.5.21:32000
```

```bash
cat <<'EOF' | sudo tee /var/snap/microk8s/current/args/certs.d/10.10.5.21:32000/hosts.toml
server = "http://10.10.5.21:32000"

[host."http://10.10.5.21:32000"]
capabilities = ["pull", "resolve"]
EOF
```

因为 `node2` 是 worker，这里要用 `snap`：

```bash
sudo snap stop microk8s
```

```bash
sudo snap start microk8s
```

然后回到 `node1` 检查：

```bash
ssh student@10.10.5.21
```

```bash
sudo microk8s kubectl get nodes -o wide
```

预期：

- `node1` 和 `node2` 都是 `Ready`

---

## 7. node1：准备目录并打标签

```bash
sudo mkdir -p /var/data/climate-stable/postgres
```

```bash
sudo mkdir -p /var/data/climate-stable/minio
```

```bash
sudo microk8s kubectl label node node1 node-role.kubernetes.io/storage= --overwrite
```

```bash
sudo microk8s kubectl label node node1 node-role.kubernetes.io/compute= --overwrite
```

```bash
sudo microk8s kubectl get nodes --show-labels
```

预期：

- `node1` 具备 `storage` 和 `compute`
- `node2` 暂时不打这两个标签

---

## 8. 本地电脑：第一次配置 Docker buildx 以支持 linux/arm64

这一节通常只需要第一次做。

本地 PowerShell：

```powershell
cd F:\DE_final_f\PRJ_STABLE_FRAMEWORK
```

```powershell
@'
[registry."10.10.5.21:32000"]
  http = true
'@ | Set-Content .\buildkitd.lab.toml
```

```powershell
docker run --privileged --rm tonistiigi/binfmt --install all
```

如果你以前创建过 `armbuilder`，先删掉：

```powershell
docker buildx rm armbuilder
```

然后新建 builder：

```powershell
docker buildx create --name armbuilder --driver docker-container --buildkitd-config .\buildkitd.lab.toml --bootstrap --use
```

```powershell
docker buildx inspect --bootstrap
```

重点看输出里有没有：

```text
linux/arm64
```

说明：

- 只要这台本地电脑的 Docker Desktop 没被重置，这一步下次通常不用重复做

---

## 9. 本地电脑：构建并推送 Airflow 镜像

本地 PowerShell：

```powershell
cd F:\DE_final_f\PRJ_STABLE_FRAMEWORK
```

```powershell
docker buildx build --platform linux/arm64 -t 10.10.5.21:32000/climate-airflow-stable:latest -f .\docker\airflow\Dockerfile . --push
```

如果这里报 `HTTP response to HTTPS client`，说明 builder 没配好，回到上面的 buildx 配置步骤。

---

## 10. node1：部署 namespace、secrets、Postgres、MinIO

```bash
ssh student@10.10.5.21
```

```bash
cd ~/DE_final_f/PRJ_STABLE_FRAMEWORK
```

```bash
sudo microk8s kubectl apply -f ./k8s/namespace.yaml -f ./k8s/secrets.yaml
```

```bash
sudo microk8s kubectl apply -f ./k8s/postgres.yaml -f ./k8s/minio.yaml
```

```bash
sudo microk8s kubectl -n climate-stable get pods -o wide -w
```

看到这两个 Pod 都是 `Running` 后按 `Ctrl+C`：

- `climate-minio`
- `climate-postgres`

---

## 11. node1：部署 Airflow

```bash
sudo microk8s kubectl apply -f ./k8s/airflow.yaml
```

```bash
sudo microk8s kubectl -n climate-stable get pods -o wide -w
```

看到 `climate-airflow` 变成 `2/2 Running` 后按 `Ctrl+C`。

---

## 12. node1：验证 3 个 Pod 和 3 个 DAG

```bash
sudo microk8s kubectl -n climate-stable get pods -o wide
```

```bash
sudo microk8s kubectl -n climate-stable exec deployment/climate-airflow -c airflow-scheduler -- airflow dags list
```

预期能看到：

- `climate_collect_weather`
- `climate_prepare_weather`
- `climate_publish_analytics`

---

## 13. node1：取消 DAG 暂停

```bash
sudo microk8s kubectl -n climate-stable exec deployment/climate-airflow -c airflow-scheduler -- airflow dags unpause climate_collect_weather
```

```bash
sudo microk8s kubectl -n climate-stable exec deployment/climate-airflow -c airflow-scheduler -- airflow dags unpause climate_prepare_weather
```

```bash
sudo microk8s kubectl -n climate-stable exec deployment/climate-airflow -c airflow-scheduler -- airflow dags unpause climate_publish_analytics
```

---

## 14. node1：触发一次端到端测试

测试业务日期：

```text
2026-03-16
```

触发时间要用下一天：

```bash
sudo microk8s kubectl -n climate-stable exec deployment/climate-airflow -c airflow-scheduler -- airflow dags trigger climate_collect_weather -e 2026-03-17T05:30:00+00:00 -r collect_auto_20260316
```

---

## 15. node1：检查测试是否成功

先查 MinIO：

```bash
cat <<'PY' | sudo microk8s kubectl -n climate-stable exec -i deployment/climate-airflow -c airflow-scheduler -- python -
from common.minio_utils import get_s3_client
s3 = get_s3_client()
bronze = s3.list_objects_v2(Bucket="bronze")
silver = s3.list_objects_v2(Bucket="silver")
print("bronze_files_20260316 =", sum(1 for o in bronze.get("Contents", []) if o["Key"].endswith("20260316.json")))
print("silver_files_20260316 =", sum(1 for o in silver.get("Contents", []) if o["Key"].endswith("20260316.parquet")))
PY
```

再查 PostgreSQL：

```bash
sudo microk8s kubectl -n climate-stable exec deployment/climate-postgres -- psql -U climate_user -d climate_analytics -c "select count(*) as rows_2026_03_16 from hourly_observations where timestamp >= '2026-03-16' and timestamp < '2026-03-17'; select timestamp, count(distinct location_id) as locations_per_timestamp from hourly_observations where timestamp >= '2026-03-16' and timestamp < '2026-03-17' group by 1 order by 1 limit 3;"
```

预期：

- `bronze_files_20260316 = 5`
- `silver_files_20260316 = 5`
- `rows_2026_03_16 = 120`
- 每个时间点 `locations_per_timestamp = 5`

---

## 16. 本地电脑：转发 Airflow 和 MinIO 页面

本地 PowerShell 新开一个窗口：

```powershell
ssh -N `
  -L 8080:10.10.5.21:31280 `
  -L 9001:10.10.5.21:30931 `
  -L 9000:10.10.5.21:30930 `
  student@10.10.5.21
```

本地浏览器打开：

- Airflow UI: `http://localhost:8080`
- MinIO Console: `http://localhost:9001`

默认账号如果没有改：

- Airflow: `admin` / `change-me-admin-password`
- MinIO: `minioadmin` / `change-me-minio-password`

---

## 17. 如果老师明天只是 shutdown，不是清盘

你现在这套部署的关键数据会保留：

- PostgreSQL 数据在宿主机目录 `/var/data/climate-stable/postgres`
- MinIO 数据在宿主机目录 `/var/data/climate-stable/minio`

明天开机后先查：

```bash
ssh student@10.10.5.21
```

```bash
sudo microk8s status --wait-ready
```

```bash
sudo microk8s kubectl get nodes -o wide
```

```bash
sudo microk8s kubectl -n climate-stable get pods -o wide
```

如果 Airflow 因镜像问题没起来，再重新执行本地的 build/push，然后在 `node1` 上：

```bash
cd ~/DE_final_f/PRJ_STABLE_FRAMEWORK
```

```bash
sudo microk8s kubectl apply -f ./k8s/airflow.yaml
```

---

## 18. 下次通常哪些步骤不用重新做

通常不用重做：

- `node1/node2` 加入集群
- `registry` addon 启用
- `node1/node2` 的 `hosts.toml`
- `node1` 标签
- 本地 `buildx` 初始化

通常需要重做：

- 如果代码改了，本地重新 build/push Airflow 镜像
- 如果资源被删了，重新 `kubectl apply`
- 如果机器被清盘，从第 0 步重新来

最短复用流程一般是：

1. 本地重新 build/push
2. `node1` 上 `kubectl apply -f ./k8s/airflow.yaml`
3. 检查 Pod
4. 触发测试

