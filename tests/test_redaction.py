import json
import logging

from mcp_patterns.redaction import REDACTED, Redactor, get_logger
from tests.conftest import SECRET_DEV


def test_exact_secret_and_patterns():
    r = Redactor()
    r.register("hunter2-long-secret")
    out = r.text("x hunter2-long-secret y Bearer abc.def.ghi token=abc123 sk-abcdefghijklmnopqrstu")
    for leaked in ("hunter2-long-secret", "abc.def.ghi", "abc123", "sk-abcdefghijkl"):
        assert leaked not in out
    assert REDACTED in out


def test_key_based_dict_redaction_keeps_benign_keys():
    r = Redactor()
    out = r.obj({"api_key": "k", "nested": [{"Authorization": "z"}], "max_tokens": 5, "name": "ok"})
    assert out["api_key"] == REDACTED
    assert out["nested"][0]["Authorization"] == REDACTED
    assert out["max_tokens"] == 5 and out["name"] == "ok"


def test_secret_in_arguments_never_reaches_result_or_logs(call, caplog):
    call("list_tables", workspace="dev")  # resolving registers the token with the redactor
    get_logger().propagate = True
    with caplog.at_level(logging.DEBUG, logger="mcp_patterns"):
        # echoed back in the not_found message
        r = call("describe_table", table=f"oops {SECRET_DEV}")
        r2 = call("describe_table", table="Authorization: Bearer abcdef123456")
    blob = json.dumps([r, r2]) + caplog.text
    assert SECRET_DEV not in blob
    assert "abcdef123456" not in blob
    assert REDACTED in json.dumps(r)
    assert "tool_call" in caplog.text  # logging is on, just clean


def test_logs_record_argument_names_not_values(call, caplog):
    with caplog.at_level(logging.INFO, logger="mcp_patterns"):
        call("describe_table", table="very-specific-value")
    assert "table" in caplog.text and "very-specific-value" not in caplog.text


def test_backend_repr_hides_token(router):
    ctx = router.resolve("dev")
    assert SECRET_DEV not in repr(ctx.backend) and SECRET_DEV not in repr(ctx)
