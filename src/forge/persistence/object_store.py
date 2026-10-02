import asyncio
import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TypeVar, cast

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

from forge.errors.object_store import ObjectStoreUnavailableError

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    last_modified: datetime


class ObjectStore:
    def __init__(
        self,
        *,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        connect_timeout_seconds: float = 0.5,
        read_timeout_seconds: float = 1.0,
        max_attempts: int = 2,
        operation_timeout_seconds: float = 3.0,
    ) -> None:
        self.bucket = bucket
        self._client: Any = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="us-east-1",
            config=Config(
                connect_timeout=connect_timeout_seconds,
                read_timeout=read_timeout_seconds,
                retries={"max_attempts": max_attempts, "mode": "standard"},
                s3={"addressing_style": "path"},
            ),
        )
        self._ready = False
        self._ready_lock = asyncio.Lock()
        self._operation_timeout_seconds = operation_timeout_seconds

    async def _call(self, function: Callable[..., T], **kwargs: Any) -> T:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(function, **kwargs),
                timeout=self._operation_timeout_seconds,
            )
        except TimeoutError as error:
            raise ObjectStoreUnavailableError(
                f"object-store operation exceeded {self._operation_timeout_seconds:g} seconds"
            ) from error
        except BotoCoreError as error:
            raise ObjectStoreUnavailableError("object-store operation failed") from error

    async def ensure_bucket(self) -> None:
        if self._ready:
            return
        async with self._ready_lock:
            if self._ready:
                return
            try:
                await self._call(self._client.head_bucket, Bucket=self.bucket)
            except ClientError as error:
                code = error.response.get("Error", {}).get("Code")
                if code not in {"404", "NoSuchBucket", "NotFound"}:
                    raise
                try:
                    await self._call(self._client.create_bucket, Bucket=self.bucket)
                except ClientError as create_error:
                    create_code = create_error.response.get("Error", {}).get("Code")
                    if create_code not in {"BucketAlreadyExists", "BucketAlreadyOwnedByYou"}:
                        raise
            self._ready = True

    async def put_json(self, namespace: str, value: Mapping[str, Any]) -> str:
        await self.ensure_bucket()
        body = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        digest = hashlib.sha256(body).hexdigest()
        key = f"{namespace}/{digest}.json"
        await self._put_json_body(key, body)
        return key

    async def put_json_at(self, key: str, value: Mapping[str, Any]) -> None:
        await self.ensure_bucket()
        body = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        await self._put_json_body(key, body)

    async def _put_json_body(self, key: str, body: bytes) -> None:
        await self._call(
            self._client.put_object,
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
        )

    async def get_json(self, key: str) -> dict[str, Any]:
        await self.ensure_bucket()
        response = await self._call(
            self._client.get_object,
            Bucket=self.bucket,
            Key=key,
        )
        body = await self._call(response["Body"].read)
        value = json.loads(body)
        if not isinstance(value, dict):
            raise ValueError(f"object {key!r} does not contain a JSON object")
        return cast(dict[str, Any], value)

    async def delete(self, key: str) -> None:
        await self.ensure_bucket()
        await self._call(self._client.delete_object, Bucket=self.bucket, Key=key)

    async def list_objects(self, prefix: str) -> list[StoredObject]:
        await self.ensure_bucket()

        def list_all() -> list[StoredObject]:
            objects: list[StoredObject] = []
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                objects.extend(
                    StoredObject(key=item["Key"], last_modified=item["LastModified"])
                    for item in page.get("Contents", [])
                )
            return objects

        return await self._call(list_all)

    async def probe(self) -> bool:
        try:
            await self.ensure_bucket()
            return True
        except Exception:
            return False
