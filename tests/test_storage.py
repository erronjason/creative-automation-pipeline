import boto3
import pytest
from moto import mock_aws

from cap.storage import LocalStorage, S3Storage, open_storage


def test_local_roundtrip_and_traversal_guard(tmp_path):
    s = LocalStorage(tmp_path)
    s.write_bytes("a/b/c.png", b"x")
    assert s.exists("a/b/c.png") and s.read_bytes("a/b/c.png") == b"x"
    assert s.list("a") == ["a/b/c.png"]
    with pytest.raises(ValueError):
        s.write_bytes("../escape.txt", b"no")


@mock_aws
def test_s3_roundtrip():
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket="creative")
    s = S3Storage("creative", "runs", client=client)
    uri = s.write_bytes("camp/p/1x1/en-US.png", b"img")
    assert uri == "s3://creative/runs/camp/p/1x1/en-US.png"
    assert s.exists("camp/p/1x1/en-US.png") and not s.exists("nope")
    assert s.read_bytes("camp/p/1x1/en-US.png") == b"img"
    assert s.list("camp") == ["camp/p/1x1/en-US.png"]


def test_open_storage_dispatch(tmp_path):
    assert isinstance(open_storage(tmp_path), LocalStorage)
