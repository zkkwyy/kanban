@echo off
rem 本地兜底更新(若GitHub Actions无法访问国内数据源时使用)
rem 需已安装 Python 3.11+ 并执行: pip install openpyxl tzdata
cd /d %~dp0
python scripts\update_cn.py --full-cyrjg
python scripts\us_sectors.py
git add data/
git diff --cached --quiet || git commit -m "data: local update %date% %time%"
git push
pause
