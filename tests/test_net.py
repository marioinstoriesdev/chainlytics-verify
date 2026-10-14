import copy

import pytest

from verify import net
from test_verify import CFG


def test_rpc_url_from_env_overrides_config(monkeypatch):
    c = copy.deepcopy(CFG)
    c["rpc"]["url"] = "https://config.example"
    monkeypatch.setenv(net.RPC_URL_ENV, "https://env.example")
    assert net.rpc_url(c) == "https://env.example"
    monkeypatch.delenv(net.RPC_URL_ENV)
    assert net.rpc_url(c) == "https://config.example"


def test_rpc_without_url_refuses_to_start(monkeypatch):
    c = copy.deepcopy(CFG)
    c["rpc"]["url"] = ""
    monkeypatch.delenv(net.RPC_URL_ENV, raising=False)
    with pytest.raises(SystemExit):
        net.JsonRpc(c)


def test_http_client_returns_error_dict_instead_of_raising():
    c = copy.deepcopy(CFG)
    c["network"]["http_retries"] = 0
    r = net.HttpClient(c)._make_request("http://127.0.0.1:9/never", timeout=0.5)
    assert "error" in r
