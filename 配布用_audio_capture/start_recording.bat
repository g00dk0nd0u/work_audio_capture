@echo off
cd /d "%~dp0"
python record_one_click.py
if errorlevel 1 pause
