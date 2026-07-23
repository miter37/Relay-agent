@echo off
rem Windows counterpart of mocks/codex. Keep the two in sync.
setlocal
set "RELAY_PROVIDER_NAME=codex"
if not defined RELAY_TEST_PYTHON set "RELAY_TEST_PYTHON=python"
"%RELAY_TEST_PYTHON%" "%~dp0mock_ai_cli.py" %*
exit /b %ERRORLEVEL%
