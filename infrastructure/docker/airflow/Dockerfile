# Airflow image for validating/running the GoPulse DAG. The repository is bind-mounted at /opt/gopulse at run time.
FROM apache/airflow:2.10.5-python3.11
USER airflow
# Pipeline dependencies in Airflow's environment (none conflict with Airflow's constraints).
RUN pip install --no-cache-dir "duckdb>=1.1,<3" "jsonschema>=4.21,<5" "pyyaml>=6,<7" "psycopg[binary,pool]>=3.2,<4"
# dbt in an isolated virtualenv: the usual way to keep dbt's pins away from Airflow's.
RUN python -m venv /home/airflow/dbt-venv && /home/airflow/dbt-venv/bin/pip install --no-cache-dir "dbt-duckdb>=1.9,<2"
ENV GOPULSE_DBT_PYTHON=/home/airflow/dbt-venv/bin/python \
    PYTHONPATH=/opt/gopulse \
    AIRFLOW__CORE__DAGS_FOLDER=/opt/gopulse/orchestration/airflow/dags \
    AIRFLOW__CORE__LOAD_EXAMPLES=False \
    AIRFLOW__CORE__EXECUTOR=SequentialExecutor \
    AIRFLOW__LOGGING__LOGGING_LEVEL=INFO
WORKDIR /opt/gopulse
