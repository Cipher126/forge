import asyncio
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]


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
    ) -> None:
        self.bucket = bucket
        self._client: Any = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="us-east-1",
            config=Config(s3={"addressing_style": "path"}),
        )
        self._ready = False
        self._ready_lock = asyncio.Lock()

    async def ensure_bucket(self) -> None:
        if self._ready:
            return
        async with self._ready_lock:
            if self._ready:
                return
            try:
                await asyncio.to_thread(self._client.head_bucket, Bucket=self.bucket)
            except ClientError as error:
                code = error.response.get("Error", {}).get("Code")
                if code not in {"404", "NoSuchBucket", "NotFound"}:
                    raise
                try:
                    await asyncio.to_thread(self._client.create_bucket, Bucket=self.bucket)
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
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
        )

    async def get_json(self, key: str) -> dict[str, Any]:
        await self.ensure_bucket()
        response = await asyncio.to_thread(
            self._client.get_object,
            Bucket=self.bucket,
            Key=key,
        )
        body = await asyncio.to_thread(response["Body"].read)
        value = json.loads(body)
        if not isinstance(value, dict):
            raise ValueError(f"object {key!r} does not contain a JSON object")
        return cast(dict[str, Any], value)

    async def delete(self, key: str) -> None:
        await self.ensure_bucket()
        await asyncio.to_thread(self._client.delete_object, Bucket=self.bucket, Key=key)

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

        return await asyncio.to_thread(list_all)

    async def probe(self) -> bool:
        try:
            await self.ensure_bucket()
            return True
        except Exception:
            return False
