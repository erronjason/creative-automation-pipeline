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


def test_s3_exists_only_treats_not_found_as_missing():
    """An access error is not "missing": answering False would make the pipeline generate a paid duplicate."""
    from botocore.exceptions import ClientError

    class Client:
        def __init__(self, code):
            self.code = code

        def head_object(self, **_):
            raise ClientError({"Error": {"Code": self.code, "Message": "x"}}, "HeadObject")

    assert S3Storage("b", "p", client=Client("404")).exists("k") is False
    assert S3Storage("b", "p", client=Client("NoSuchKey")).exists("k") is False
    with pytest.raises(RuntimeError, match="403"):
        S3Storage("b", "p", client=Client("403")).exists("k")
