import os

from timelapse.cli import parser


def test_serve_cli_defaults_follow_environment(monkeypatch):
    monkeypatch.setenv("TIMELAPSE_HOST", "127.0.0.1")
    monkeypatch.setenv("TIMELAPSE_PORT", "9090")
    monkeypatch.setenv("TIMELAPSE_STATE_DIR", "/tmp/pi-timelapse-state")
    args = parser().parse_args(["serve"])
    assert args.host == "127.0.0.1"
    assert args.port == 9090
    assert args.state_dir == "/tmp/pi-timelapse-state"
    assert os.environ["TIMELAPSE_PORT"] == "9090"
