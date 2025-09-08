import pytest
from browser_chrome_win import is_chrome_path

pytestmark = pytest.mark.unit


def test_is_chrome_path_matches_windows_executable_name():
    assert is_chrome_path("C:/Program Files/Google/Chrome/Application/chrome.exe")
    assert is_chrome_path("chrome.exe")


def test_is_chrome_path_is_false_for_non_matching_names():
    assert not is_chrome_path("")
    assert not is_chrome_path("/usr/bin/google-chrome")
    assert not is_chrome_path("msedge.exe")
