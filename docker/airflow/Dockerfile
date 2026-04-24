# Author: Ziang Yang
# Description: Docker image definition for the custom Airflow runtime used by the climate
# data pipeline. Installs Python and system dependencies required by the DAGs, copies the
# shared pipeline modules and DAG files into the Airflow image, and prepares the runtime
# environment used in Kubernetes deployments.

FROM apache/airflow:2.9.3-python3.11

USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

USER airflow

COPY docker/airflow/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY common/ /opt/airflow/common/
COPY dags/ /opt/airflow/dags/
COPY main.py /opt/airflow/main.py

ENV PYTHONPATH=/opt/airflow
