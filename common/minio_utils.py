"""MinIO helper functions."""

from __future__ import annotations

import hashlib
import io
import json
import logging

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from common.config import (
    BRONZE_BUCKET,
    MINIO_ACCESS_KEY,
    MINIO_ENDPOINT,
    MINIO_SECRET_KEY,
    MINIO_USE_SSL,
    SILVER_BUCKET,
)

logger = logging.getLogger(__name__)


def json_to_bytes(data) -> bytes:
    return json.dumps(
        data,
        default=str,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def compute_sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def get_s3_client():
    protocol = "https" if MINIO_USE_SSL else "http"
    return boto3.client(
        "s3",
        endpoint_url=f"{protocol}://{MINIO_ENDPOINT}",
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


def ensure_buckets(client=None) -> None:
    if client is None:
        client = get_s3_client()
    for bucket in [BRONZE_BUCKET, SILVER_BUCKET]:
        try:
            client.head_bucket(Bucket=bucket)
        except client.exceptions.ClientError:
            client.create_bucket(Bucket=bucket)
            logger.info("Created bucket: %s", bucket)


def upload_json(bucket: str, key: str, data, client=None) -> None:
    if client is None:
        client = get_s3_client()
    body = json_to_bytes(data)
    client.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")


def download_json(bucket: str, key: str, client=None):
    if client is None:
        client = get_s3_client()
    response = client.get_object(Bucket=bucket, Key=key)
    return json.loads(response["Body"].read().decode("utf-8"))


def upload_parquet(bucket: str, key: str, parquet_buffer, client=None) -> None:
    if client is None:
        client = get_s3_client()
    if isinstance(parquet_buffer, bytes):
        parquet_buffer = io.BytesIO(parquet_buffer)
    parquet_buffer.seek(0)
    client.upload_fileobj(parquet_buffer, bucket, key)


def download_bytes(bucket: str, key: str, client=None) -> bytes:
    if client is None:
        client = get_s3_client()
    response = client.get_object(Bucket=bucket, Key=key)
    return response["Body"].read()


def object_exists(bucket: str, key: str, client=None) -> bool:
    if client is None:
        client = get_s3_client()
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as exc:
        error_code = str(exc.response.get("Error", {}).get("Code", ""))
        if error_code in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise
