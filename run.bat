@echo off
setlocal

cd /d "%~dp0"

echo [%date% %time%] Starting AI Daily Digest...

:: Phase 1: Folo + Notion pipeline (optional)
where node >nul 2>nul
if %errorlevel% equ 0 (
    if exist daily-digest.mjs (
        echo [%date% %time%] Phase 1: Running Folo + Notion pipeline...
        node daily-digest.mjs
        if %errorlevel% neq 0 (
            echo [%date% %time%] WARNING: Phase 1 failed, continuing...
        )
    ) else (
        echo [%date% %time%] Phase 1: Skipped ^(daily-digest.mjs not found^)
    )
) else (
    echo [%date% %time%] Phase 1: Skipped ^(node not found^)
)

:: Phase 2: Python pipeline
echo [%date% %time%] Phase 2: Running Python pipeline...
python main.py %*

echo [%date% %time%] Done!

endlocal
