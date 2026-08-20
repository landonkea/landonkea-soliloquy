from unittest.mock import patch

from soliloquy.notify import _applescript_string, send_desktop_notification


def test_applescript_string_wraps_in_quotes():
    assert _applescript_string("hello") == '"hello"'


def test_applescript_string_escapes_double_quotes():
    assert _applescript_string('she said "hi"') == '"she said \\"hi\\""'


def test_applescript_string_escapes_backslashes_before_quotes():
    assert _applescript_string('a\\b"c') == '"a\\\\b\\"c"'


def test_send_desktop_notification_is_a_no_op_off_darwin():
    with patch("soliloquy.notify.sys") as mock_sys, patch("subprocess.run") as mock_run:
        mock_sys.platform = "linux"
        send_desktop_notification("title", "message")
        mock_run.assert_not_called()


def test_send_desktop_notification_never_raises_even_if_osascript_fails():
    with patch("soliloquy.notify.sys") as mock_sys, \
         patch("shutil.which", return_value="/usr/bin/osascript"), \
         patch("subprocess.run", side_effect=OSError("boom")):
        mock_sys.platform = "darwin"
        send_desktop_notification("title", "message")  # should not raise
